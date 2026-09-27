"""Report service: upload validation, listing, editing, deletion, re-processing."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import get_settings
from backend.core.errors import NotFoundError, UploadError, ValidationAppError
from backend.jobs.pipeline import process_report_job, reset_report_for_reprocessing
from backend.jobs.queue import get_job_queue
from backend.schemas.common import SUPPORTED_LANGUAGES
from backend.schemas.report import (
    ExtractedValueOut,
    ReferenceRangeOut,
    ReportDetailOut,
    ReportPageOut,
    ReportSummaryOut,
    ResultsUpdateRequest,
)
from backend.services import audit_service
from database.base import new_id, utcnow
from database.models import ExtractedValue, Report, ReportPage, User
from document_processing.medical_parser import compute_status
from storage.base import FileTooLargeError, StorageBackend, StorageError
from storage.factory import get_storage

logger = logging.getLogger("medilens.reports")

# ----------------------------------------------------------------------
# Upload validation: declared type AND file signature must agree.
# ----------------------------------------------------------------------
SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
)

ALLOWED_CONTENT_TYPES = {content_type for _, content_type, _ in SIGNATURES}
GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}

UPLOAD_CHUNK_SIZE = 1024 * 1024


def _detect_signature(head: bytes) -> tuple[str, str] | None:
    for signature, content_type, suffix in SIGNATURES:
        if head.startswith(signature):
            return content_type, suffix
    return None


def sanitize_filename(name: str | None) -> str:
    """Strip any path components and control characters from an upload name."""
    if not name:
        return "report"
    cleaned = Path(name.replace("\\", "/")).name
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in {'"', "\x00"})
    cleaned = cleaned.strip() or "report"
    return cleaned[:255]


async def _upload_chunks(first: bytes, upload: UploadFile) -> AsyncIterator[bytes]:
    yield first
    while True:
        chunk = await upload.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


async def create_report_from_upload(db: AsyncSession, user: User, upload: UploadFile) -> Report:
    """Validate and persist an uploaded file, then queue the processing job."""
    settings = get_settings()

    head = await upload.read(64 * 1024)
    if not head:
        raise UploadError("The uploaded file is empty.")

    detected = _detect_signature(head)
    if detected is None:
        raise UploadError(
            "Unsupported file type. Please upload a laboratory report as PDF, JPG or PNG."
        )
    detected_type, suffix = detected

    declared = (upload.content_type or "").lower().split(";")[0].strip()
    if declared not in GENERIC_CONTENT_TYPES and declared not in ALLOWED_CONTENT_TYPES:
        raise UploadError(
            "Unsupported file type. Please upload a laboratory report as PDF, JPG or PNG."
        )
    if declared not in GENERIC_CONTENT_TYPES and declared != detected_type:
        raise UploadError(
            "The file content does not match its declared type. "
            "Please upload the original file without renaming or converting it."
        )

    report_id = new_id()
    storage_key = StorageBackend.build_key(user.id, report_id, suffix)
    storage = get_storage()

    try:
        file_size, file_sha256 = await storage.save_stream(
            storage_key,
            _upload_chunks(head, upload),
            settings.max_upload_size_bytes,
            detected_type,
        )
    except FileTooLargeError as exc:
        raise UploadError(
            f"This file is larger than the {settings.max_upload_size_mb} MB limit. "
            "Please upload a smaller file."
        ) from exc
    except StorageError as exc:
        logger.exception("upload_storage_failed", extra={"event": "upload_storage_failed"})
        raise UploadError("The file could not be stored. Please try again.") from exc

    language = user.locale if user.locale in SUPPORTED_LANGUAGES else "en"
    report = Report(
        id=report_id,
        user_id=user.id,
        original_filename=sanitize_filename(upload.filename),
        storage_key=storage_key,
        content_type=detected_type,
        file_size=file_size,
        file_sha256=file_sha256,
        status="uploaded",
        language=language,
        page_count=0,
        detected_test_count=0,
        is_demo=False,
        analysis_stale=False,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    await audit_service.record(
        db,
        "report.uploaded",
        user_id=user.id,
        entity_type="report",
        entity_id=report.id,
        detail={"content_type": detected_type, "size_bytes": file_size},
    )
    await enqueue_processing(report.id)
    return report


async def enqueue_processing(report_id: str) -> None:
    queue = get_job_queue()
    await queue.enqueue(f"process:{report_id}", lambda: process_report_job(report_id))


async def enqueue_analysis(
    report_id: str,
    language: str = "en",
    force: bool = False,
    explanation_level: str | None = None,
) -> None:
    from backend.jobs.pipeline import analyze_report_job

    queue = get_job_queue()
    await queue.enqueue(
        f"analyze:{report_id}",
        lambda: analyze_report_job(
            report_id, language=language, force=force, explanation_level=explanation_level
        ),
    )


async def list_reports(
    db: AsyncSession,
    user: User,
    *,
    search: str | None = None,
    status: str | None = None,
    report_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    pinned_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Report], int]:
    conditions = [Report.user_id == user.id]
    if search:
        conditions.append(Report.original_filename.ilike(f"%{search.strip()}%"))
    if status:
        conditions.append(Report.status == status)
    if report_type:
        conditions.append(Report.report_type == report_type)
    if pinned_only:
        conditions.append(Report.is_pinned.is_(True))
    # Smart date search: prefer the report's own date, fall back to upload day.
    effective_date = func.coalesce(Report.report_date, cast(Report.created_at, Date))
    if date_from is not None:
        conditions.append(effective_date >= date_from)
    if date_to is not None:
        conditions.append(effective_date <= date_to)

    total = (
        await db.execute(select(func.count()).select_from(Report).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(Report)
                .where(*conditions)
                .order_by(Report.is_pinned.desc(), Report.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), int(total or 0)


async def get_report_with_children(db: AsyncSession, report_id: str, user_id: str) -> Report:
    report = (
        await db.execute(
            select(Report)
            .options(selectinload(Report.values), selectinload(Report.pages))
            .where(Report.id == report_id, Report.user_id == user_id)
        )
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError("Report not found.")
    return report


# ----------------------------------------------------------------------
# Serialisation helpers (models -> API schemas)
# ----------------------------------------------------------------------
def value_to_out(value: ExtractedValue) -> ExtractedValueOut:
    return ExtractedValueOut(
        id=value.id,
        test_name=value.test_name,
        test_key=value.test_key,
        value_numeric=value.value_numeric,
        value_text=value.value_text,
        unit=value.unit,
        reference_range=ReferenceRangeOut(
            low=value.ref_low, high=value.ref_high, text=value.ref_text
        ),
        status=value.status,
        report_flag=value.report_flag,
        source_page=value.source_page,
        confidence=value.confidence,
        raw_line=value.raw_line,
        is_user_edited=value.is_user_edited,
        sort_order=value.sort_order,
    )


def report_to_summary(report: Report) -> ReportSummaryOut:
    return ReportSummaryOut.model_validate(report)


def report_to_detail(report: Report) -> ReportDetailOut:
    return ReportDetailOut(
        **report_to_summary(report).model_dump(),
        note=report.note,
        note_include_in_analysis=bool(report.note_include_in_analysis),
        values=[value_to_out(value) for value in sorted(report.values, key=lambda v: v.sort_order)],
        pages=[
            ReportPageOut(
                page_number=page.page_number,
                extraction_method=page.extraction_method,
                ocr_confidence=page.ocr_confidence,
                text=page.text,
            )
            for page in sorted(report.pages, key=lambda p: p.page_number)
        ],
    )


# ----------------------------------------------------------------------
# Mutations
# ----------------------------------------------------------------------
async def delete_report(db: AsyncSession, report: Report) -> None:
    if report.storage_key and not report.is_demo:
        storage = get_storage()
        try:
            await storage.delete(report.storage_key)
        except Exception:
            logger.warning(
                "storage_delete_failed",
                extra={"event": "storage_delete_failed", "report_id": report.id},
            )
    report_id = report.id
    user_id = report.user_id
    await db.delete(report)
    await db.commit()
    await audit_service.record(
        db, "report.deleted", user_id=user_id, entity_type="report", entity_id=report_id
    )


async def set_pinned(db: AsyncSession, report: Report, is_pinned: bool) -> Report:
    """Pin or unpin a report (pinned reports sort first in every list)."""
    if bool(report.is_pinned) != bool(is_pinned):
        report.is_pinned = bool(is_pinned)
        report.pinned_at = utcnow() if is_pinned else None
        await db.commit()
        await db.refresh(report)
        await audit_service.record(
            db,
            "report.pinned" if is_pinned else "report.unpinned",
            user_id=report.user_id,
            entity_type="report",
            entity_id=report.id,
        )
    return report


async def set_note(
    db: AsyncSession,
    report: Report,
    note: str | None,
    *,
    include_in_analysis: bool,
) -> Report:
    """Save the private note. Note text itself is never written to audit logs."""
    cleaned = (note or "").strip() or None
    include = bool(include_in_analysis) and cleaned is not None
    if report.note != cleaned or bool(report.note_include_in_analysis) != include:
        report.note = cleaned
        report.note_include_in_analysis = include
        await db.commit()
        await db.refresh(report)
        await audit_service.record(
            db,
            "report.note_updated",
            user_id=report.user_id,
            entity_type="report",
            entity_id=report.id,
            detail={"has_note": cleaned is not None, "include_in_analysis": include},
        )
    return report


async def update_results(db: AsyncSession, report: Report, payload: ResultsUpdateRequest) -> None:
    """Apply user corrections to extracted values and re-classify them."""
    by_id = {value.id: value for value in report.values}
    unknown_ids = [item.id for item in payload.values if item.id not in by_id]
    if unknown_ids:
        raise ValidationAppError(
            "Some values could not be found on this report.", code="value_not_found"
        )

    edited = 0
    for item in payload.values:
        value = by_id[item.id]
        fields = item.model_dump(exclude_unset=True)
        fields.pop("id", None)
        changed = False
        for field, new_value in fields.items():
            if getattr(value, field) != new_value:
                setattr(value, field, new_value)
                changed = True
        if not changed:
            continue
        if value.test_name is not None:
            value.test_name = value.test_name.strip()[:150]
        value.status = compute_status(value.value_numeric, value.ref_low, value.ref_high)
        value.is_user_edited = True
        edited += 1

    if edited:
        report.analysis_stale = True
        await db.commit()
        await audit_service.record(
            db,
            "report.values_edited",
            user_id=report.user_id,
            entity_type="report",
            entity_id=report.id,
            detail={"edited_values": edited},
        )


async def reprocess_report(db: AsyncSession, report: Report) -> None:
    """Re-run extraction on the stored file (deletes previous results)."""
    if report.is_demo:
        raise ValidationAppError(
            "Demo reports cannot be re-processed.", code="demo_report"
        )
    if report.status in ("processing", "extracting", "analyzing"):
        raise ValidationAppError(
            "This report is already being processed. Please wait for it to finish.",
            code="already_processing",
        )
    reset_report_for_reprocessing(report)
    await db.commit()
    await enqueue_processing(report.id)


async def open_report_file(report: Report, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
    if report.is_demo or not report.storage_key:
        raise NotFoundError("Demo reports do not have a downloadable source file.")
    storage = get_storage()
    return storage.open_read(report.storage_key, chunk_size)
