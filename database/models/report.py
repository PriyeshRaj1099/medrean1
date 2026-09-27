"""Uploaded reports, extracted page text and structured laboratory values."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from database.models.analysis import Analysis
    from database.models.user import User

# Processing lifecycle (kept as plain strings for portability across backends).
REPORT_STATUSES = (
    "uploaded",    # stored, queued
    "processing",  # worker picked it up
    "extracting",  # text / OCR extraction running
    "extracted",   # structured values ready for review or analysis
    "analyzing",   # RAG + LLM analysis running
    "completed",   # analysis finished
    "failed",      # see status_message
)

# Value classification against the range printed on the report.
VALUE_STATUSES = ("within", "below", "above", "unknown")

EXTRACTION_METHODS = ("pdf_text", "ocr")


class Report(Base, IdMixin, TimestampMixin):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_user_id", "user_id"),
        Index("ix_reports_status", "status"),
        Index("ix_reports_user_pinned", "user_id", "is_pinned"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="uploaded", nullable=False)
    status_message: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(50))

    report_type: Mapped[str | None] = mapped_column(String(120))
    report_date: Mapped[date | None] = mapped_column(Date)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detected_test_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # User organisation aids: pinned reports sort first; notes are private and
    # are only sent to the AI provider when the user explicitly opts in.
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    note_include_in_analysis: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="reports")
    pages: Mapped[list["ReportPage"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )
    values: Mapped[list["ExtractedValue"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExtractedValue.sort_order",
    )
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Analysis.created_at",
    )

    @property
    def has_note(self) -> bool:
        """True when a non-empty private note exists (summary payloads hide the text)."""
        return bool(self.note and self.note.strip())


class ReportPage(Base, IdMixin):
    __tablename__ = "report_pages"
    __table_args__ = (Index("ix_report_pages_report_id", "report_id"),)

    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(20), default="pdf_text", nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    report: Mapped[Report] = relationship(back_populates="pages")


class ExtractedValue(Base, IdMixin, TimestampMixin):
    """One laboratory measurement, always tied to the report it came from."""

    __tablename__ = "extracted_values"
    __table_args__ = (
        Index("ix_extracted_values_report_id", "report_id"),
        Index("ix_extracted_values_test_key", "test_key"),
    )

    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    test_name: Mapped[str] = mapped_column(String(150), nullable=False)
    test_key: Mapped[str | None] = mapped_column(String(80))
    value_numeric: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(60))

    ref_low: Mapped[float | None] = mapped_column(Float)
    ref_high: Mapped[float | None] = mapped_column(Float)
    ref_text: Mapped[str | None] = mapped_column(String(120))

    status: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    # Explicit H/L flag printed by the laboratory, if any.
    report_flag: Mapped[str | None] = mapped_column(String(20))

    source_page: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    raw_line: Mapped[str | None] = mapped_column(Text)
    is_user_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    report: Mapped[Report] = relationship(back_populates="values")
