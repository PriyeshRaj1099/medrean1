"""Audit logging service. Only non-sensitive metadata is stored."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.base import utcnow
from database.models import AuditLog

logger = logging.getLogger("medilens.audit")


async def record(
    db: AsyncSession,
    action: str,
    *,
    user_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    """Persist an audit entry. `detail` must never contain report contents."""
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip_address,
        user_agent=user_agent,
        detail=detail or None,
        created_at=utcnow(),
    )
    db.add(entry)
    if commit:
        await db.commit()
    logger.info(
        "audit: %s", action, extra={"event": action, "user_id": user_id, "report_id": entity_id}
    )
