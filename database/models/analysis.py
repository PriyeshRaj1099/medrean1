"""AI analysis results and the knowledge-base sources they cite."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from database.models.report import Report

ANALYSIS_STATUSES = ("completed", "failed")


class Analysis(Base, IdMixin, TimestampMixin):
    __tablename__ = "analyses"
    __table_args__ = (Index("ix_analyses_report_id", "report_id"),)

    report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="completed", nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    report_type: Mapped[str | None] = mapped_column(String(120))
    summary: Mapped[str | None] = mapped_column(Text)

    within_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    below_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    above_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    low_confidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Full structured analysis payload (educational explanations, questions,
    # limitations, limitations flags, retrieval diagnostics, ...).
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    report: Mapped["Report"] = relationship(back_populates="analyses")
    sources: Mapped[list["AnalysisSource"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", passive_deletes=True
    )


class AnalysisSource(Base, IdMixin):
    """A citation the analysis used. Never invented — copied from retrieved chunks."""

    __tablename__ = "analysis_sources"
    __table_args__ = (Index("ix_analysis_sources_analysis_id", "analysis_id"),)

    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("knowledge_chunks.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_org: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(500))
    publication_date: Mapped[str | None] = mapped_column(String(40))
    topic: Mapped[str | None] = mapped_column(String(80))
    snippet: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[float | None] = mapped_column()

    analysis: Mapped[Analysis] = relationship(back_populates="sources")
