"""Curated medical knowledge base documents and their embedded chunks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, IdMixin, TimestampMixin

DOCUMENT_STATUSES = ("pending", "indexed", "failed")


class KnowledgeDocument(Base, IdMixin, TimestampMixin):
    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_knowledge_documents_topic", "topic"),)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_org: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(500))
    publication_date: Mapped[str | None] = mapped_column(String(40))
    # Editorial version of the document content (e.g. "1.0"), for change tracking.
    version: Mapped[str | None] = mapped_column(String(40))
    license_note: Mapped[str | None] = mapped_column(String(300))
    topic: Mapped[str | None] = mapped_column(String(80))
    checksum: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class KnowledgeChunk(Base, IdMixin):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_document_id", "document_id"),
        Index("ix_knowledge_chunks_topic", "topic"),
    )

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(JSON)
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    topic: Mapped[str | None] = mapped_column(String(80))
    keywords: Mapped[str | None] = mapped_column(String(300))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
