"""Report routes: upload, list, detail, download, edit values, delete, compare."""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from backend.api.deps import (
    CurrentUser,
    DbSession,
    client_ip,
    ensure_not_demo,
    get_owned_report,
)
from backend.core.rate_limit import PROCESS_RULE, SHARE_RULE, UPLOAD_RULE, limiter
from backend.schemas.analysis import ProcessingStatusOut
from backend.schemas.report import (
    CompareRequest,
    CompareResponse,
    NoteUpdateRequest,
    PinUpdateRequest,
    ReportDetailOut,
    ReportListResponse,
    ReportSummaryOut,
    ResultsUpdateRequest,
)
from backend.schemas.share import (
    ShareLinkCreateRequest,
    ShareLinkCreatedOut,
    ShareLinkOut,
)
from backend.schemas.user import MessageResponse
from backend.services import (
    audit_service,
    compare_service,
    report_service,
    share_service,
)

logger = logging.getLogger("medilens.reports.api")

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/upload", response_model=ReportSummaryOut, status_code=status.HTTP_201_CREATED)
async def upload_report(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(..., description="PDF, JPG or PNG laboratory report"),
) -> ReportSummaryOut:
    limiter.check(f"upload:{user.id}", UPLOAD_RULE)
    ensure_not_demo(user, "cannot upload your own reports")
    report = await report_service.create_report_from_upload(db, user, file)
    return report_service.report_to_summary(report)


# Declared before /{report_id} so "compare" is never captured as an id.
@router.post("/compare", response_model=CompareResponse)
async def compare_reports(
    payload: CompareRequest, user: CurrentUser, db: DbSession
) -> CompareResponse:
    return await compare_service.compare_reports(db, user, payload.report_ids)


@router.get("", response_model=ReportListResponse)
async def list_reports(
    user: CurrentUser,
    db: DbSession,
    search: str | None = Query(default=None, max_length=120),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    report_type: str | None = Query(default=None, max_length=120),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    pinned: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReportListResponse:
    items, total = await report_service.list_reports(
        db,
        user,
        search=search,
        status=status_filter,
        report_type=report_type,
        date_from=date_from,
        date_to=date_to,
        pinned_only=pinned,
        limit=limit,
        offset=offset,
    )
    return ReportListResponse(
        items=[report_service.report_to_summary(report) for report in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{report_id}", response_model=ReportDetailOut)
async def report_detail(report_id: str, user: CurrentUser, db: DbSession) -> ReportDetailOut:
    report = await report_service.get_report_with_children(db, report_id, user.id)
    return report_service.report_to_detail(report)


@router.get("/{report_id}/status", response_model=ProcessingStatusOut)
async def report_status(report_id: str, user: CurrentUser, db: DbSession) -> ProcessingStatusOut:
    report = await get_owned_report(report_id, user, db)
    return ProcessingStatusOut(
        report_id=report.id,
        status=report.status,
        status_message=report.status_message,
        error_code=report.error_code,
        detected_test_count=report.detected_test_count,
    )


@router.get("/{report_id}/file")
async def download_report_file(report_id: str, user: CurrentUser, db: DbSession, request: Request):
    report = await get_owned_report(report_id, user, db)
    stream = await report_service.open_report_file(report)
    safe_name = report_service.sanitize_filename(report.original_filename)
    ascii_name = safe_name.encode("ascii", "ignore").decode() or "report"
    disposition = (
        f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(safe_name)}'
    )
    await audit_service.record(
        db,
        "report.file_downloaded",
        user_id=user.id,
        entity_type="report",
        entity_id=report.id,
        ip_address=client_ip(request),
    )
    return StreamingResponse(
        stream,
        media_type=report.content_type,
        headers={"Content-Disposition": disposition},
    )


@router.patch("/{report_id}/results", response_model=ReportDetailOut)
async def update_results(
    report_id: str,
    payload: ResultsUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> ReportDetailOut:
    report = await report_service.get_report_with_children(db, report_id, user.id)
    await report_service.update_results(db, report, payload)
    refreshed = await report_service.get_report_with_children(db, report_id, user.id)
    return report_service.report_to_detail(refreshed)


@router.patch("/{report_id}/pin", response_model=ReportSummaryOut)
async def update_pin(
    report_id: str,
    payload: PinUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> ReportSummaryOut:
    """Pin or unpin a report (pinned reports sort first)."""
    report = await get_owned_report(report_id, user, db)
    updated = await report_service.set_pinned(db, report, payload.is_pinned)
    return report_service.report_to_summary(updated)


@router.patch("/{report_id}/note", response_model=ReportDetailOut)
async def update_note(
    report_id: str,
    payload: NoteUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> ReportDetailOut:
    """Save the private note; it is sent to the AI only with explicit opt-in."""
    report = await report_service.get_report_with_children(db, report_id, user.id)
    await report_service.set_note(
        db, report, payload.note, include_in_analysis=payload.include_in_analysis
    )
    refreshed = await report_service.get_report_with_children(db, report_id, user.id)
    return report_service.report_to_detail(refreshed)


@router.post(
    "/{report_id}/shares", response_model=ShareLinkCreatedOut, status_code=status.HTTP_201_CREATED
)
async def create_share_link(
    report_id: str,
    payload: ShareLinkCreateRequest,
    user: CurrentUser,
    db: DbSession,
) -> ShareLinkCreatedOut:
    """Create an expiring, revocable, sanitized share link for this report."""
    limiter.check(f"share:{user.id}", SHARE_RULE)
    report = await get_owned_report(report_id, user, db)
    return await share_service.create_share_link(
        db, report, user.id, expires_in_days=payload.expires_in_days
    )


@router.get("/{report_id}/shares", response_model=list[ShareLinkOut])
async def list_share_links(report_id: str, user: CurrentUser, db: DbSession) -> list[ShareLinkOut]:
    report = await get_owned_report(report_id, user, db)
    return await share_service.list_share_links(db, report.id, user.id)


@router.delete("/{report_id}/shares/{share_id}", response_model=ShareLinkOut)
async def revoke_share_link(
    report_id: str, share_id: str, user: CurrentUser, db: DbSession
) -> ShareLinkOut:
    report = await get_owned_report(report_id, user, db)
    return await share_service.revoke_share_link(db, report.id, user.id, share_id)


@router.post("/{report_id}/process", response_model=ProcessingStatusOut)
async def reprocess_report(
    report_id: str, user: CurrentUser, db: DbSession
) -> ProcessingStatusOut:
    limiter.check(f"process:{user.id}", PROCESS_RULE)
    report = await get_owned_report(report_id, user, db)
    await report_service.reprocess_report(db, report)
    await db.refresh(report)
    return ProcessingStatusOut(
        report_id=report.id,
        status=report.status,
        status_message="Processing has been restarted.",
        error_code=None,
        detected_test_count=report.detected_test_count,
    )


@router.delete("/{report_id}", response_model=MessageResponse)
async def delete_report(report_id: str, user: CurrentUser, db: DbSession) -> MessageResponse:
    report = await get_owned_report(report_id, user, db)
    await report_service.delete_report(db, report)
    return MessageResponse(message="Report deleted.")
