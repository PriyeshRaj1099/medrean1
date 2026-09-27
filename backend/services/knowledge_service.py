"""Knowledge-base service: stats, listing, ingestion, deletion, re-indexing."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.registry import get_embedding_provider
from backend.core.errors import NotFoundError, ValidationAppError
from backend.schemas.admin import (
    KnowledgeBaseStats,
    KnowledgeDocumentCreate,
    KnowledgeDocumentOut,
    ReindexResponse,
)
from backend.services import audit_service
from database.models import KnowledgeChunk, KnowledgeDocument
from rag.kb_loader import KnowledgeDocSpec, index_document, index_seed_knowledge

logger = logging.getLogger("medilens.knowledge")


async def stats(db: AsyncSession) -> KnowledgeBaseStats:
    rows = (
        await db.execute(
            select(KnowledgeDocument.status, func.count()).group_by(KnowledgeDocument.status)
        )
    ).all()
    by_status = {status: count for status, count in rows}
    chunks_total = (
        await db.execute(select(func.count()).select_from(KnowledgeChunk))
    ).scalar_one()
    provider = get_embedding_provider()
    return KnowledgeBaseStats(
        documents_total=sum(by_status.values()),
        documents_indexed=int(by_status.get("indexed", 0)),
        documents_pending=int(by_status.get("pending", 0)),
        documents_failed=int(by_status.get("failed", 0)),
        chunks_total=int(chunks_total or 0),
        embedding_provider=provider.name,
        embedding_model=provider.model or "unknown",
    )


async def list_documents(
    db: AsyncSession, *, limit: int = 50, offset: int = 0
) -> tuple[list[KnowledgeDocumentOut], int]:
    total = (
        await db.execute(select(func.count()).select_from(KnowledgeDocument))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(KnowledgeDocument)
                .order_by(KnowledgeDocument.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [KnowledgeDocumentOut.model_validate(row) for row in rows], int(total or 0)


async def get_document(db: AsyncSession, document_id: str) -> KnowledgeDocument:
    document = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("Knowledge document not found.")
    return document


async def create_document(
    db: AsyncSession, payload: KnowledgeDocumentCreate, *, created_by: str | None
) -> KnowledgeDocumentOut:
    """Ingest an admin-provided document (chunked + embedded + indexed)."""
    spec = KnowledgeDocSpec(
        title=payload.title.strip(),
        topic=payload.topic.strip().lower(),
        source_org=(payload.source_org or "").strip() or None,
        url=(payload.url or "").strip() or None,
        publication_date=(payload.publication_date or "").strip() or None,
        license_note=(payload.license_note or "").strip() or None,
        content=payload.content.strip(),
        version=(payload.version or "").strip() or None,
        keywords=[keyword.strip() for keyword in payload.keywords if keyword.strip()],
    )
    try:
        document, _indexed = await index_document(
            db, spec, get_embedding_provider(), created_by=created_by, force=True
        )
    except Exception as exc:
        logger.exception("knowledge_index_failed", extra={"event": "knowledge_index_failed"})
        raise ValidationAppError(
            "The document could not be indexed (embedding provider error). "
            "Please check the provider configuration and try again.",
            code="index_failed",
        ) from exc

    await audit_service.record(
        db,
        "knowledge.document_added",
        user_id=created_by,
        entity_type="knowledge_document",
        entity_id=document.id,
        detail={"topic": spec.topic, "chunks": document.chunk_count},
    )
    return KnowledgeDocumentOut.model_validate(document)


async def delete_document(db: AsyncSession, document: KnowledgeDocument, *, actor_id: str | None) -> None:
    document_id = document.id
    title = document.title
    await db.delete(document)
    await db.commit()
    await audit_service.record(
        db,
        "knowledge.document_deleted",
        user_id=actor_id,
        entity_type="knowledge_document",
        entity_id=document_id,
        detail={"title": title[:120]},
    )


async def reindex_seed(db: AsyncSession, *, actor_id: str | None) -> ReindexResponse:
    """Force re-chunk + re-embed of all bundled seed documents."""
    try:
        result = await index_seed_knowledge(
            db, get_embedding_provider(), force=True, created_by=actor_id
        )
    except Exception as exc:
        logger.exception("knowledge_reindex_failed", extra={"event": "knowledge_reindex_failed"})
        raise ValidationAppError(
            "Re-indexing failed (embedding provider error). Please try again.",
            code="reindex_failed",
        ) from exc

    await audit_service.record(
        db,
        "knowledge.reindexed",
        user_id=actor_id,
        entity_type="knowledge_base",
        detail=result,
    )
    return ReindexResponse(
        documents_total=result.get("documents_total", 0),
        documents_reindexed=result.get("documents_indexed", 0),
        chunks_total=result.get("chunks_total", 0),
        message="Knowledge base re-indexed successfully.",
    )
