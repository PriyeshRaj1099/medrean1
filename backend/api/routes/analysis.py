"""Analysis routes: run analysis, fetch results, history and sources."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.api.deps import CurrentUser, DbSession, get_owned_report
from backend.core.errors import NotFoundError, ValidationAppError
from backend.core.rate_limit import PROCESS_RULE, limiter
from backend.schemas.analysis import AnalysisOut, AnalysisSourceOut, AnalyzeRequest, StructuredAnalysis
from backend.services import analysis_service, audit_service, report_service

logger = logging.getLogger("medilens.analysis.api")

router = APIRouter(tags=["analysis"])

IN_PROGRESS_STATUSES = ("uploaded", "processing", "extracting")


@router.post("/reports/{report_id}/analyze", response_model=AnalysisOut)
async def analyze_report(
    report_id: str,
    payload: AnalyzeRequest,
    user: CurrentUser,
    db: DbSession,
) -> AnalysisOut:
    """Run (or re-run) the AI analysis for a report. Blocks until it completes."""
    limiter.check(f"analyze:{user.id}", PROCESS_RULE)
    report = await get_owned_report(report_id, user, db)
    if report.status in IN_PROGRESS_STATUSES:
        raise ValidationAppError(
            "This report is still being processed. Please try again in a moment.",
            code="processing_in_progress",
        )
    analysis = await analysis_service.run_analysis(
        db,
        report,
        language=payload.language,
        explanation_level=payload.explanation_level,
        force=payload.force,
    )
    return analysis_service.analysis_to_out(analysis)


@router.get("/reports/{report_id}/analysis", response_model=AnalysisOut)
async def latest_analysis(report_id: str, user: CurrentUser, db: DbSession) -> AnalysisOut:
    report = await get_owned_report(report_id, user, db)
    analysis = await analysis_service.get_latest_analysis(db, report.id)
    if analysis is None:
        raise NotFoundError("No analysis is available for this report yet.")
    return analysis_service.analysis_to_out(analysis)


@router.get("/reports/{report_id}/analyses", response_model=list[AnalysisOut])
async def analysis_history(report_id: str, user: CurrentUser, db: DbSession) -> list[AnalysisOut]:
    report = await get_owned_report(report_id, user, db)
    analyses = await analysis_service.list_analyses(db, report.id)
    return [analysis_service.analysis_to_out(analysis) for analysis in analyses]


@router.get("/reports/{report_id}/sources", response_model=list[AnalysisSourceOut])
async def analysis_sources(report_id: str, user: CurrentUser, db: DbSession) -> list[AnalysisSourceOut]:
    report = await get_owned_report(report_id, user, db)
    analysis = await analysis_service.get_latest_analysis(db, report.id)
    if analysis is None:
        raise NotFoundError("No analysis is available for this report yet.")
    return [AnalysisSourceOut.model_validate(source) for source in analysis.sources]


@router.get("/reports/{report_id}/analysis/structured", response_model=StructuredAnalysis)
async def structured_analysis(report_id: str, user: CurrentUser, db: DbSession) -> StructuredAnalysis:
    report = await get_owned_report(report_id, user, db)
    analysis = await analysis_service.get_latest_analysis(db, report.id)
    if analysis is None or not analysis.payload:
        raise NotFoundError("No analysis is available for this report yet.")
    return StructuredAnalysis.model_validate(analysis.payload)


@router.post("/reports/{report_id}/analysis/refresh")
async def refresh_analysis(report_id: str, payload: AnalyzeRequest, user: CurrentUser, db: DbSession) -> dict:
    """Queue a background re-analysis (used when values were edited)."""
    limiter.check(f"analyze:{user.id}", PROCESS_RULE)
    report = await get_owned_report(report_id, user, db)
    if report.status in IN_PROGRESS_STATUSES:
        raise ValidationAppError(
            "This report is still being processed. Please try again in a moment.",
            code="processing_in_progress",
        )
    await report_service.enqueue_analysis(
        report.id,
        language=payload.language,
        force=True,
        explanation_level=payload.explanation_level,
    )
    await audit_service.record(
        db,
        "report.analyzed",
        user_id=user.id,
        entity_type="report",
        entity_id=report.id,
        detail={
            "queued": True,
            "language": payload.language,
            "explanation_level": payload.explanation_level,
        },
    )
    return {"report_id": report.id, "status": "queued"}
