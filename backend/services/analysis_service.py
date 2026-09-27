"""Analysis service: RAG retrieval + AI provider call + persistence."""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai.base import (
    AnalysisRequest,
    AIProvider,
    RetrievedChunk,
    ValueContext,
)
from ai.registry import get_ai_provider, get_embedding_provider
from ai.safety import looks_like_prompt_injection, sanitize_structured_analysis
from backend.core.config import get_settings
from backend.core.errors import (
    AIProviderError,
    AppError,
    ProcessingError,
    ValidationAppError,
)
from backend.core.progress import stage_message
from backend.schemas.analysis import (
    AnalysisCounts,
    AnalysisOut,
    AnalysisSourceOut,
    StructuredAnalysis,
)
from backend.schemas.common import SUPPORTED_LANGUAGES
from backend.services import audit_service
from database.base import utcnow
from database.models import Analysis, AnalysisSource, ExtractedValue, Report
from rag.retriever import Retriever, build_history_query

logger = logging.getLogger("medilens.analysis")

ANALYSIS_FAILED_MESSAGE = (
    "The analysis could not be completed right now. "
    "Please try again in a moment."
)

SNIPPET_LENGTH = 300


def _value_to_context(value: ExtractedValue) -> ValueContext:
    return ValueContext(
        test_name=value.test_name,
        test_key=value.test_key,
        value_numeric=value.value_numeric,
        value_text=value.value_text,
        unit=value.unit,
        ref_low=value.ref_low,
        ref_high=value.ref_high,
        ref_text=value.ref_text,
        status=value.status,
        confidence=value.confidence,
        source_page=value.source_page,
    )


async def load_values(db: AsyncSession, report_id: str) -> list[ExtractedValue]:
    rows = (
        await db.execute(
            select(ExtractedValue)
            .where(ExtractedValue.report_id == report_id)
            .order_by(ExtractedValue.sort_order)
        )
    ).scalars().all()
    return list(rows)


async def get_latest_analysis(db: AsyncSession, report_id: str) -> Analysis | None:
    return (
        await db.execute(
            select(Analysis)
            .options(selectinload(Analysis.sources))
            .where(Analysis.report_id == report_id)
            .order_by(Analysis.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_analyses(db: AsyncSession, report_id: str, limit: int = 20) -> list[Analysis]:
    rows = (
        await db.execute(
            select(Analysis)
            .options(selectinload(Analysis.sources))
            .where(Analysis.report_id == report_id)
            .order_by(Analysis.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


def analysis_to_out(analysis: Analysis) -> AnalysisOut:
    structured: StructuredAnalysis | None = None
    if analysis.payload:
        try:
            structured = StructuredAnalysis.model_validate(analysis.payload)
        except Exception:
            logger.warning(
                "stored analysis payload failed validation",
                extra={"event": "analysis_payload_invalid", "analysis_id": analysis.id},
            )
    return AnalysisOut(
        id=analysis.id,
        report_id=analysis.report_id,
        status=analysis.status,
        provider=analysis.provider,
        model=analysis.model,
        language=analysis.language,
        explanation_level=(analysis.payload or {}).get("explanation_level", "simple"),
        report_type=analysis.report_type,
        summary=analysis.summary,
        counts=AnalysisCounts(
            within=analysis.within_count,
            below=analysis.below_count,
            above=analysis.above_count,
            unknown=analysis.unknown_count,
            low_confidence=analysis.low_confidence_count,
        ),
        structured=structured,
        sources=[AnalysisSourceOut.model_validate(source) for source in analysis.sources],
        duration_ms=analysis.duration_ms,
        created_at=analysis.created_at,
    )


def _persist_sources(
    db: AsyncSession,
    analysis_id: str,
    structured: StructuredAnalysis,
    retrieved: list[RetrievedChunk],
) -> None:
    """Copy citations into the analysis; sources are never invented."""
    by_url = {chunk.url: chunk for chunk in retrieved if chunk.url}
    by_title = {chunk.title: chunk for chunk in retrieved}
    for reference in structured.sources:
        chunk = None
        if reference.url and reference.url in by_url:
            chunk = by_url[reference.url]
        elif reference.title in by_title:
            chunk = by_title[reference.title]
        db.add(
            AnalysisSource(
                analysis_id=analysis_id,
                chunk_id=chunk.chunk_id if chunk else None,
                title=reference.title[:300],
                source_org=chunk.source_org if chunk else reference.source_org,
                url=chunk.url if chunk else reference.url,
                publication_date=chunk.publication_date if chunk else reference.publication_date,
                topic=chunk.topic if chunk else None,
                snippet=(chunk.content[:SNIPPET_LENGTH] if chunk else None),
                relevance_score=chunk.score if chunk else None,
            )
        )


async def run_analysis(
    db: AsyncSession,
    report: Report,
    *,
    language: str = "en",
    explanation_level: str = "simple",
    force: bool = False,
    provider: AIProvider | None = None,
) -> Analysis:
    """Run (or reuse) the AI analysis and persist it. Raises AIProviderError on failure."""
    settings = get_settings()
    if language not in SUPPORTED_LANGUAGES:
        language = "en"
    if explanation_level not in ("simple", "detailed"):
        explanation_level = "simple"

    values = await load_values(db, report.id)
    if not values:
        raise ValidationAppError(
            "No laboratory values have been detected for this report yet, "
            "so there is nothing to analyze.",
            code="no_values",
        )

    latest = await get_latest_analysis(db, report.id)
    latest_level = (
        (latest.payload or {}).get("explanation_level", "simple")
        if latest is not None and latest.payload
        else "simple"
    )
    if (
        latest is not None
        and latest.status == "completed"
        and latest.language == language
        and latest_level == explanation_level
        and not force
        and not report.analysis_stale
    ):
        return latest

    low_confidence_count = sum(
        1 for value in values if value.confidence < settings.low_confidence_threshold
    )
    contexts = [_value_to_context(value) for value in values]
    report_type = report.report_type

    # The private note is sent to the AI ONLY when the user explicitly opted in.
    user_note: str | None = None
    if report.note_include_in_analysis and report.note:
        user_note = report.note.strip()[:2000] or None

    # Prompt-injection defense: document values and the private note are DATA,
    # never instructions. Instruction-like text is detected, logged, and the
    # provider is told to treat the whole block as data.
    injection_suspected = looks_like_prompt_injection(
        "\n".join(
            part
            for value in values
            for part in (value.test_name, value.value_text, value.ref_text)
            if part
        )
    ) or looks_like_prompt_injection(user_note)
    if injection_suspected:
        logger.warning(
            "prompt_injection_suspected",
            extra={"event": "prompt_injection_suspected", "report_id": report.id},
        )

    query = build_history_query(report_type, contexts)
    retriever = Retriever(db, get_embedding_provider())
    retrieved = await retriever.retrieve(query)

    active_provider = provider or get_ai_provider()
    request = AnalysisRequest(
        values=contexts,
        report_type=report_type,
        report_date=report.report_date,
        language=language,
        explanation_level=explanation_level,
        retrieved=retrieved,
        low_confidence_count=low_confidence_count,
        user_note=user_note,
        injection_suspected=injection_suspected,
    )

    report.status = "analyzing"
    report.status_message = stage_message(language, "preparing_explanation")
    report.error_code = None
    await db.commit()

    started = time.perf_counter()
    try:
        structured = await active_provider.generate_structured_analysis(request)
    except Exception as exc:
        logger.exception(
            "analysis_provider_failed",
            extra={"event": "analysis_provider_failed", "report_id": report.id},
        )
        await _record_failure(db, report, active_provider, language, latest, exc)
        raise AIProviderError(ANALYSIS_FAILED_MESSAGE) from exc
    duration_ms = int((time.perf_counter() - started) * 1000)

    # Mirror the requested level onto the stored payload so reuse checks work.
    structured.explanation_level = explanation_level

    # Defense-in-depth: strip markup/active content and clamp sizes before
    # the payload is validated, stored and served.
    structured = sanitize_structured_analysis(structured)

    counts = AnalysisCounts(low_confidence=low_confidence_count)
    for value in values:
        if value.status == "within":
            counts.within += 1
        elif value.status == "below":
            counts.below += 1
        elif value.status == "above":
            counts.above += 1
        else:
            counts.unknown += 1

    # The provider has answered; assembling and saving the report is the
    # last genuinely-running phase, so it gets its own honest message.
    report.status_message = stage_message(language, "finalizing")
    await db.commit()

    try:
        analysis = Analysis(
            report_id=report.id,
            status="completed",
            provider=active_provider.name,
            model=active_provider.model,
            language=language,
            report_type=structured.report_type or report_type,
            summary=structured.summary,
            within_count=counts.within,
            below_count=counts.below,
            above_count=counts.above,
            unknown_count=counts.unknown,
            low_confidence_count=counts.low_confidence,
            payload=structured.model_dump(),
            duration_ms=duration_ms,
            finished_at=utcnow(),
        )
        db.add(analysis)
        await db.flush()
        _persist_sources(db, analysis.id, structured, retrieved)

        report.report_type = analysis.report_type or report.report_type
        report.status = "completed"
        report.status_message = None
        report.error_code = None
        report.analysis_stale = False
        report.processing_finished_at = utcnow()
        await db.commit()
    except AppError:
        raise
    except Exception as exc:
        # Never leave the report stuck in "analyzing" if persistence fails.
        logger.exception(
            "analysis_persist_failed",
            extra={"event": "analysis_persist_failed", "report_id": report.id},
        )
        await _record_failure(db, report, active_provider, language, latest, exc)
        raise ProcessingError(
            "The analysis was generated but could not be saved. Please try again."
        ) from exc

    await db.refresh(analysis)
    loaded = await get_latest_analysis(db, report.id)

    await audit_service.record(
        db,
        "report.analyzed",
        user_id=report.user_id,
        entity_type="report",
        entity_id=report.id,
        detail={
            "provider": active_provider.name,
            "language": language,
            "explanation_level": explanation_level,
            "sources": len(structured.sources),
            "duration_ms": duration_ms,
            "injection_suspected": injection_suspected,
        },
    )
    return loaded or analysis


async def _record_failure(
    db: AsyncSession,
    report: Report,
    provider: AIProvider,
    language: str,
    previous: Analysis | None,
    exc: Exception,
) -> None:
    # Read everything we need BEFORE the rollback expires the instances.
    report_id = report.id
    user_id = report.user_id
    previous_completed = previous is not None and previous.status == "completed"

    try:
        # Clear any aborted transaction so the failure record can be saved.
        await db.rollback()
        current = (
            await db.execute(select(Report).where(Report.id == report_id))
        ).scalar_one_or_none()

        failed = Analysis(
            report_id=report_id,
            status="failed",
            provider=provider.name,
            model=provider.model,
            language=language,
            error_message=f"{type(exc).__name__}: {str(exc)[:400]}",
            payload={},
            finished_at=utcnow(),
        )
        db.add(failed)

        if current is not None:
            if previous_completed:
                # Keep the previous, still-valid analysis visible.
                current.status = "completed"
                current.status_message = (
                    "The latest re-analysis attempt failed. Showing the previous analysis."
                )
                current.analysis_stale = True
            else:
                current.status = "failed"
                current.error_code = "analysis_failed"
                current.status_message = ANALYSIS_FAILED_MESSAGE
                current.processing_finished_at = utcnow()
        await db.commit()
    except Exception:
        logger.exception(
            "analysis_failure_record_failed",
            extra={"event": "analysis_failure_record_failed", "report_id": report_id},
        )
        return
    await audit_service.record(
        db,
        "report.analysis_failed",
        user_id=user_id,
        entity_type="report",
        entity_id=report_id,
        detail={"provider": provider.name},
    )
