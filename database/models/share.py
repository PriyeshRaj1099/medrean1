"""Temporary share links: revocable, expiring, no personal data in the URL.

A share link exposes a read-only, sanitized view of one report (extracted
values + analysis + sources). It never exposes notes, chat history, the
account, or the original file, and it can be revoked at any time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, IdMixin


class ReportShareLink(Base, IdMixin):
    __tablename__ = "report_share_links"
    __table_args__ = (
        Index("ix_report_share_links_report_id", "report_id"),
        Index("ix_report_share_links_user_id", "user_id"),
    )

    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Only the hash of the token is stored; the token itself exists only in
    # the URL that was generated for the user.
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
