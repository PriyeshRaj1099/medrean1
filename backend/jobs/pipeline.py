"""Report processing pipeline.

Flow (each step updates the persisted report status so the UI can follow along):

    uploaded -> processing -> extracting -> extracted -> analyzing -> completed
                                                        \\-> failed (with a safe message)

The pipeline runs in background jobs that own their own database sessions; the
API request that enqueues a job never shares its session with the pipeline.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.errors import AppError
from backend.core.progress import stage_message
from backend.services import analysis_service, audit_service
from database.base import utcnow
from database.models import ExtractedValue, Report, ReportPage, User
from database.session import get_session_factory
from document_processing.cleaner import clean_text
from document_processing.medical_parser import PageInput, classify_values, parse_pages
from document_processing.pdf_extractor import extract_image, extract_pdf
from document_processing.segmenter import collect_section_titles, segment_page_text
from storage.factory import get_storage

logger = logging.getLogger("medilens.pipeline")

GENERIC_FAILURE_MESSAGE = (
    "Something went wrong while processing this document. "
    "Please try again, or upload a clearer copy."
)


async def _set_status(
    db: AsyncSession,
    report: Report,
    status: str,
    *,
    message: str | None = None,
    error_code: str | None = None,
) -> None:
    report.status = status
    report.status_message = message[:500] if message else None
    report.error_code = error_code
    await db.commit()


async def _fail(db: AsyncSession, report: Report, code: str, message: str) -> None:
    report.status = "failed"
    report.status_message = message[:500]
    report.error_code = code
    report.processing_finished_at = utcnow()
    await db.commit()
    await audit_service.record(
        db,
        "report.processing_failed",
        user_id=report.user_id,
        entity_type="report",
        entity_id=report.id,
        detail={"error_code": code},
    )


async def _announce(db: AsyncSession, report: Report, stage: str) -> None:
    """Publish a localized one-line progress message for the current phase."""
    report.status_message = stage_message(report.language or "en", stage)[:500]
    await db.commit()


async def extract_and_parse(db: AsyncSession, report: Report) -> None:
    """Run extraction (PDF text / OCR) + parsing, then persist pages and values."""
    settings = get_settings()
    storage = get_storage()

    try:
        data = await storage.read_bytes(report.storage_key)
    except Exception as exc:
        logger.exception("storage_read_failed", extra={"event": "storage_read_failed"})
        raise AppError(
            "The stored file could not be read. Please upload the report again."
        ) from exc

    if report.content_type == "application/pdf":
        outcome = await extract_pdf(data)
    else:
        outcome = await extract_image(data)

    # Cleaning + segmentation feed the parser (section titles improve report
    # type detection); the parser itself only ever trusts ranges printed in text.
    page_inputs: list[PageInput] = []
    section_titles: list[str] = []
    for page in outcome.pages:
        cleaned = clean_text(page.text)
        sections = segment_page_text(cleaned, page.page_number)
        section_titles.extend(collect_section_titles(sections))
        page_inputs.append(
            PageInput(
                page_number=page.page_number,
                text=cleaned,
                method=page.method,
                ocr_confidence=page.ocr_confidence,
            )
        )

    # Staged, honest progress: parsing finds the values first, then the
    # reference-range comparison runs as its own visible phase.
    await _announce(db, report, "finding_values")
    parse_result = parse_pages(
        page_inputs, section_titles=section_titles, defer_status=True
    )
    await _announce(db, report, "checking_ranges")
    classify_values(parse_result.values)

    # Replace any previous extraction output (supports re-processing).
    await db.execute(delete(ExtractedValue).where(ExtractedValue.report_id == report.id))
    await db.execute(delete(ReportPage).where(ReportPage.report_id == report.id))
    await db.flush()

    now = utcnow()
    for page in outcome.pages:
        db.add(
            ReportPage(
                report_id=report.id,
                page_number=page.page_number,
                text=clean_text(page.text),
                extraction_method=page.method,
                ocr_confidence=page.ocr_confidence,
                created_at=now,
            )
        )

    for parsed in parse_result.values:
        db.add(
            ExtractedValue(
                report_id=report.id,
                sort_order=parsed.sort_order,
                test_name=parsed.test_name,
                test_key=parsed.test_key,
                value_numeric=parsed.value_numeric,
                value_text=parsed.value_text,
                unit=parsed.unit,
                ref_low=parsed.ref_low,
                ref_high=parsed.ref_high,
                ref_text=parsed.ref_text,
                status=parsed.status,
                report_flag=parsed.report_flag,
                source_page=parsed.source_page,
                confidence=parsed.confidence,
                raw_line=parsed.raw_line,
                is_user_edited=False,
            )
        )

    report.page_count = outcome.page_count
    report.detected_test_count = len(parse_result.values)
    report.report_type = parse_result.report_type or report.report_type
    report.report_date = parse_result.report_date or report.report_date

    message: str | None = None
    if outcome.ocr_note:
        message = outcome.ocr_note
    elif parse_result.warnings:
        message = parse_result.warnings[0]
    report.analysis_stale = True
    report.processing_finished_at = now
    await _set_status(db, report, "extracted", message=message)

    await audit_service.record(
        db,
        "report.processed",
        user_id=report.user_id,
        entity_type="report",
        entity_id=report.id,
        detail={
            "pages": outcome.page_count,
            "values": len(parse_result.values),
            "used_ocr": outcome.used_ocr,
        },
    )


async def process_report_job(report_id: str) -> None:
    """Background job: extract + parse, then optionally auto-analyze."""
    settings = get_settings()
    session_factory = get_session_factory()
    language = "en"

    async with session_factory() as db:
        report = (
            await db.execute(select(Report).where(Report.id == report_id))
        ).scalar_one_or_none()
        if report is None:
            logger.warning(
                "process job: report %s no longer exists", report_id,
                extra={"event": "job_report_missing", "report_id": report_id},
            )
            return
        if report.is_demo:
            # Demo reports are produced fully-formed by the demo service.
            return

        language = report.language or "en"
        report.processing_started_at = utcnow()
        await _set_status(db, report, "processing")

        # Text extraction / OCR genuinely starts here: say so, in the
        # language the report owner chose.
        await _set_status(
            db, report, "extracting", message=stage_message(language, "reading")
        )
        try:
            await extract_and_parse(db, report)
        except AppError as exc:
            await _fail(db, report, exc.code, exc.message)
            return
        except Exception:
            logger.exception(
                "processing_failed", extra={"event": "processing_failed", "report_id": report.id}
            )
            await _fail(db, report, "processing_failed", GENERIC_FAILURE_MESSAGE)
            return

    if settings.auto_analyze_after_extraction:
        await analyze_report_job(report_id, language=language)


async def _user_explanation_level(db: AsyncSession, user_id: str) -> str:
    """The owner's saved reading preference (defaults to "simple")."""
    level = (
        await db.execute(select(User.explanation_level).where(User.id == user_id))
    ).scalar_one_or_none()
    return level if level in ("simple", "detailed") else "simple"


async def analyze_report_job(
    report_id: str,
    language: str = "en",
    force: bool = False,
    explanation_level: str | None = None,
) -> None:
    """Background job: run the AI analysis when the report has values."""
    session_factory = get_session_factory()
    async with session_factory() as db:
        report = (
            await db.execute(select(Report).where(Report.id == report_id))
        ).scalar_one_or_none()
        if report is None:
            return
        level = explanation_level or await _user_explanation_level(db, report.user_id)
        try:
            await analysis_service.run_analysis(
                db, report, language=language, explanation_level=level, force=force
            )
        except Exception:
            # run_analysis persists a failed analysis row + report status itself.
            logger.exception(
                "auto_analysis_failed",
                extra={"event": "auto_analysis_failed", "report_id": report_id},
            )


def reset_report_for_reprocessing(report: Report) -> None:
    """Prepare an existing report row for a fresh extraction run."""
    report.status = "uploaded"
    report.status_message = None
    report.error_code = None
    report.processing_started_at = None
    report.processing_finished_at = None
    report.detected_test_count = 0
    report.analysis_stale = True
