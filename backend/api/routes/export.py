"""PDF export route for report summaries."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response

from backend.api.deps import CurrentUser, DbSession
from backend.core.rate_limit import EXPORT_RULE, limiter
from backend.schemas.common import SupportedLanguage
from backend.services import analysis_service, audit_service, export_service, report_service
from database.base import utcnow

logger = logging.getLogger("medilens.export.api")

router = APIRouter(prefix="/reports", tags=["export"])


@router.get("/{report_id}/export/pdf")
async def export_report_pdf(
    report_id: str,
    user: CurrentUser,
    db: DbSession,
    language: SupportedLanguage | None = None,
) -> Response:
    """Export the report values + analysis as a PDF summary (with disclaimer)."""
    limiter.check(f"export:{user.id}", EXPORT_RULE)

    report = await report_service.get_report_with_children(db, report_id, user.id)
    analyses = await analysis_service.list_analyses(db, report.id)

    selected = None
    requested = language or report.language or "en"
    for analysis in analyses:
        if analysis.status == "completed" and analysis.language == requested:
            selected = analysis
            break
    if selected is None:
        selected = next((a for a in analyses if a.status == "completed"), None)
    export_language = selected.language if selected is not None else requested

    detail = report_service.report_to_detail(report)
    analysis_out = analysis_service.analysis_to_out(selected) if selected is not None else None

    pdf_bytes = await asyncio.to_thread(
        export_service.generate_report_pdf,
        detail,
        analysis_out,
        language=export_language,
        generated_at=utcnow(),
    )

    await audit_service.record(
        db,
        "report.exported_pdf",
        user_id=user.id,
        entity_type="report",
        entity_id=report.id,
        detail={"language": export_language, "bytes": len(pdf_bytes)},
    )

    base_name = report_service.sanitize_filename(report.original_filename).rsplit(".", 1)[0]
    filename = f"medilens-{base_name or 'report'}-summary.pdf"
    ascii_name = filename.encode("ascii", "ignore").decode() or "medilens-summary.pdf"
    disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )
