"""Report sharing service: create, list, revoke and resolve secure links.

Design notes:
* Only the SHA-256 hash of the link token is stored; a database leak does not
  expose working links.
* Links always expire (1-30 days) and can be revoked at any time.
* The anonymous view is sanitized: no filename, notes, chat, account or file.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import get_settings
from backend.core.errors import NotFoundError
from backend.core.security import generate_opaque_token, hash_opaque_token
from backend.schemas.common import MEDICAL_DISCLAIMER
from backend.schemas.report import ExtractedValueOut, ReferenceRangeOut
from backend.schemas.share import (
    ShareLinkCreatedOut,
    ShareLinkOut,
    SharedReportOut,
    SharedReportView,
)
from backend.services import analysis_service, audit_service
from database.base import utcnow
from database.models import Report, ReportShareLink

logger = logging.getLogger("medilens.shares")

SHARE_NOTICE = (
    "You are viewing a shared, read-only analysis of a laboratory report. "
    "The link expires automatically and its owner can revoke it at any time."
)

INACTIVE_LINK_MESSAGE = (
    "This share link is not available. It may have expired or been revoked by its owner."
)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def link_is_active(link: ReportShareLink, now: datetime | None = None) -> bool:
    if link.revoked_at is not None:
        return False
    return _as_utc(link.expires_at) > (now or utcnow())


def share_link_to_out(link: ReportShareLink) -> ShareLinkOut:
    return ShareLinkOut(
        id=link.id,
        created_at=link.created_at,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        last_viewed_at=link.last_viewed_at,
        view_count=link.view_count,
        active=link_is_active(link),
    )


async def create_share_link(
    db: AsyncSession,
    report: Report,
    user_id: str,
    *,
    expires_in_days: int,
) -> ShareLinkCreatedOut:
    """Create a share link for one report. The raw token is returned once."""
    settings = get_settings()
    days = max(1, min(int(expires_in_days), settings.share_link_max_expire_days))
    token = generate_opaque_token()
    link = ReportShareLink(
        report_id=report.id,
        user_id=user_id,
        token_hash=hash_opaque_token(token),
        expires_at=utcnow() + timedelta(days=days),
        view_count=0,
        created_at=utcnow(),
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)

    await audit_service.record(
        db,
        "share.created",
        user_id=user_id,
        entity_type="report",
        entity_id=report.id,
        detail={"expires_in_days": days},
    )
    url = f"{settings.frontend_url}/shared/{token}"
    base = share_link_to_out(link)
    return ShareLinkCreatedOut(**base.model_dump(), token=token, url=url)


async def list_share_links(
    db: AsyncSession, report_id: str, user_id: str, limit: int = 50
) -> list[ShareLinkOut]:
    rows = (
        await db.execute(
            select(ReportShareLink)
            .where(
                ReportShareLink.report_id == report_id,
                ReportShareLink.user_id == user_id,
            )
            .order_by(ReportShareLink.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [share_link_to_out(link) for link in rows]


async def revoke_share_link(
    db: AsyncSession, report_id: str, user_id: str, share_id: str
) -> ShareLinkOut:
    link = (
        await db.execute(
            select(ReportShareLink).where(
                ReportShareLink.id == share_id,
                ReportShareLink.report_id == report_id,
                ReportShareLink.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFoundError("Share link not found.")
    if link.revoked_at is None:
        link.revoked_at = utcnow()
        await db.commit()
        await db.refresh(link)
        await audit_service.record(
            db,
            "share.revoked",
            user_id=user_id,
            entity_type="report",
            entity_id=report_id,
            detail={"share_id": share_id},
        )
    return share_link_to_out(link)


def sanitized_values(report: Report) -> list[ExtractedValueOut]:
    """Copy of the extracted values without raw OCR lines or edit markers."""
    return [
        ExtractedValueOut(
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
            raw_line=None,
            is_user_edited=False,
            sort_order=value.sort_order,
        )
        for value in sorted(report.values, key=lambda v: v.sort_order)
    ]


async def resolve_shared_report(db: AsyncSession, token: str) -> SharedReportView:
    """Resolve a raw share token into a sanitized read-only view."""
    if not token or len(token) > 128:
        raise NotFoundError(INACTIVE_LINK_MESSAGE)
    token_hash = hash_opaque_token(token)
    link = (
        await db.execute(
            select(ReportShareLink).where(ReportShareLink.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    now = utcnow()
    if link is None or not link_is_active(link, now):
        raise NotFoundError(INACTIVE_LINK_MESSAGE)

    report = (
        await db.execute(
            select(Report)
            .options(selectinload(Report.values))
            .where(Report.id == link.report_id)
        )
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError(INACTIVE_LINK_MESSAGE)

    # Best-effort view accounting; never blocks the shared view itself.
    try:
        link.view_count += 1
        link.last_viewed_at = now
        await db.commit()
    except Exception:
        logger.warning(
            "share_view_count_failed",
            extra={"event": "share_view_count_failed", "share_id": link.id},
        )
        await db.rollback()

    analysis = await analysis_service.get_latest_analysis(db, report.id)
    analysis_out = None
    if analysis is not None and analysis.status == "completed":
        analysis_out = analysis_service.analysis_to_out(analysis)

    return SharedReportView(
        share=share_link_to_out(link),
        report=SharedReportOut(
            report_type=report.report_type,
            report_date=report.report_date,
            language=report.language,
            page_count=report.page_count,
            detected_test_count=report.detected_test_count,
            created_at=report.created_at,
            values=sanitized_values(report),
            analysis=analysis_out,
        ),
        notice=SHARE_NOTICE,
        disclaimer=MEDICAL_DISCLAIMER,
    )
