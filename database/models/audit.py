"""Security/audit trail. Never stores report contents, secrets or PII payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, IdMixin

AUDIT_ACTIONS = (
    "auth.register",
    "auth.login",
    "auth.login_failed",
    "auth.logout",
    "auth.password_reset_requested",
    "auth.password_reset_completed",
    "auth.profile_updated",
    "report.uploaded",
    "report.processed",
    "report.processing_failed",
    "report.values_edited",
    "report.analyzed",
    "report.analysis_failed",
    "report.deleted",
    "report.file_downloaded",
    "report.exported_pdf",
    "chat.message",
    "chat.message_blocked",
    "demo.loaded",
    "knowledge.document_added",
    "knowledge.document_deleted",
    "knowledge.reindexed",
    "user.account_deleted",
)


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action", "action"),
    )

    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    # Non-sensitive structured context only (ids, counts, error codes).
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
