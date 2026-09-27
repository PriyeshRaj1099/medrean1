"""Privacy service: full data export and permanent account deletion.

Deletion follows the least-surprise rule: the user confirms with their
password, every row tied to the account is removed (database cascades),
and stored report files are deleted from the storage backend. A final
audit entry (without account link) records that the deletion happened.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.errors import ValidationAppError
from backend.core.security import verify_password
from backend.schemas.common import language_name
from backend.services import audit_service
from database.base import utcnow
from database.models import Analysis, ChatSession, Report, User
from storage.factory import get_storage

logger = logging.getLogger("medilens.privacy")

EXPORT_FORMAT_VERSION = 1

DELETE_PASSWORD_ERROR = "Your password is incorrect. Your account was not deleted."


def _profile_dict(user: User) -> dict[str, Any]:
    return {
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "locale": user.locale,
        "language_name": language_name(user.locale),
        "explanation_level": user.explanation_level,
        "notifications_enabled": user.notifications_enabled,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


async def build_user_export(db: AsyncSession, user: User) -> dict[str, Any]:
    """Everything stored about the account, in a portable JSON structure.

    Never includes password hashes, refresh tokens, reset tokens or raw OCR
    lines. Analyses include their full stored (educational) payload.
    """
    reports = (
        (
            await db.execute(
                select(Report)
                .options(
                    selectinload(Report.values),
                    selectinload(Report.analyses).selectinload(Analysis.sources),
                )
                .where(Report.user_id == user.id)
                .order_by(Report.created_at)
            )
        )
        .scalars()
        .all()
    )
    sessions = (
        (
            await db.execute(
                select(ChatSession)
                .options(selectinload(ChatSession.messages))
                .where(ChatSession.user_id == user.id)
                .order_by(ChatSession.created_at)
            )
        )
        .scalars()
        .all()
    )

    report_items: list[dict[str, Any]] = []
    for report in reports:
        analyses = sorted(report.analyses, key=lambda a: a.created_at or utcnow())
        report_items.append(
            {
                "id": report.id,
                "original_filename": report.original_filename,
                "report_type": report.report_type,
                "report_date": report.report_date.isoformat() if report.report_date else None,
                "status": report.status,
                "language": report.language,
                "is_demo": report.is_demo,
                "is_pinned": report.is_pinned,
                "note": report.note,
                "note_include_in_analysis": report.note_include_in_analysis,
                "page_count": report.page_count,
                "detected_test_count": report.detected_test_count,
                "created_at": report.created_at.isoformat() if report.created_at else None,
                "values": [
                    {
                        "test_name": value.test_name,
                        "test_key": value.test_key,
                        "value_numeric": value.value_numeric,
                        "value_text": value.value_text,
                        "unit": value.unit,
                        "ref_low": value.ref_low,
                        "ref_high": value.ref_high,
                        "ref_text": value.ref_text,
                        "status": value.status,
                        "source_page": value.source_page,
                        "confidence": value.confidence,
                    }
                    for value in sorted(report.values, key=lambda v: v.sort_order)
                ],
                "analyses": [
                    {
                        "status": analysis.status,
                        "provider": analysis.provider,
                        "model": analysis.model,
                        "language": analysis.language,
                        "summary": analysis.summary,
                        "created_at": analysis.created_at.isoformat()
                        if analysis.created_at
                        else None,
                        "structured": analysis.payload or None,
                        "sources": [
                            {
                                "title": source.title,
                                "source_org": source.source_org,
                                "url": source.url,
                                "publication_date": source.publication_date,
                            }
                            for source in analysis.sources
                        ],
                    }
                    for analysis in analyses
                ],
            }
        )

    chat_items = [
        {
            "id": session.id,
            "title": session.title,
            "language": session.language,
            "report_id": session.report_id,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "language": message.language,
                    "safety_blocked": message.safety_blocked,
                    "created_at": message.created_at.isoformat()
                    if message.created_at
                    else None,
                }
                for message in session.messages
            ],
        }
        for session in sessions
    ]

    return {
        "format": "medilens-export",
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": utcnow().isoformat(),
        "notice": (
            "This file contains the data stored about your MediLens AI account. "
            "It is educational information, not a medical record. "
            "Keep it somewhere private."
        ),
        "profile": _profile_dict(user),
        "reports": report_items,
        "chat_sessions": chat_items,
    }


async def delete_user_account(db: AsyncSession, user: User, password: str) -> None:
    """Permanently delete the account and every stored report file."""
    if not verify_password(password, user.password_hash):
        raise ValidationAppError(DELETE_PASSWORD_ERROR, code="invalid_password")

    user_id = user.id
    storage_keys = [
        key
        for key in (
            await db.execute(
                select(Report.storage_key).where(
                    Report.user_id == user_id, Report.is_demo.is_(False)
                )
            )
        ).scalars().all()
        if key
    ]

    await db.delete(user)
    await db.commit()

    # Record the deletion (entity reference only; no account link remains).
    await audit_service.record(
        db,
        "auth.account_deleted",
        user_id=None,
        entity_type="user",
        entity_id=user_id,
        detail={"reports_removed": len(storage_keys)},
    )

    storage = get_storage()
    for key in storage_keys:
        try:
            await storage.delete(key)
        except Exception:
            logger.warning(
                "account_storage_cleanup_failed",
                extra={"event": "account_storage_cleanup_failed"},
            )


__all__ = [
    "build_user_export",
    "delete_user_account",
    "EXPORT_FORMAT_VERSION",
    "DELETE_PASSWORD_ERROR",
]
