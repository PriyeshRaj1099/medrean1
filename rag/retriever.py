"""Retriever: embeds a query and returns grounded knowledge-base chunks."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ai.base import AIProvider, RetrievedChunk, ValueContext
from backend.core.config import get_settings
from rag.vector_store import DatabaseVectorStore, VectorStore

logger = logging.getLogger("medilens.rag")


def build_history_query(report_type: str | None, values: list[ValueContext]) -> str:
    """Compose a retrieval query from a report's structure (no patient data)."""
    parts: list[str] = []
    if report_type:
        parts.append(f"laboratory test panel: {report_type}.")
    abnormal = [v for v in values if v.status in ("below", "above")]
    if abnormal:
        descriptions = [
            f"{v.test_name} is {'below' if v.status == 'below' else 'above'} the reference range"
            for v in abnormal
        ]
        parts.append("values outside the printed reference range: " + "; ".join(descriptions) + ".")
    normal_names = [v.test_name for v in values if v.status == "within"]
    if normal_names:
        parts.append("tests within range: " + ", ".join(normal_names[:12]) + ".")
    if not parts:
        parts.append("general explanation of laboratory test results and reference ranges")
    return " ".join(parts)


class Retriever:
    def __init__(
        self,
        db: AsyncSession,
        embedding_provider: AIProvider,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.db = db
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store or DatabaseVectorStore(db)

    async def retrieve(
        self,
        query_text: str,
        *,
        top_k: int | None = None,
        min_score: float | None = None,
        topics: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        settings = get_settings()
        k = top_k or settings.rag_top_k
        threshold = settings.rag_min_score if min_score is None else min_score

        try:
            embeddings = await self.embedding_provider.create_embedding([query_text])
        except Exception:
            logger.exception("embedding_failed", extra={"event": "rag_embedding_failed"})
            return []

        if not embeddings:
            return []

        try:
            results = await self.vector_store.search(
                embeddings[0], top_k=k, min_score=threshold, topics=topics
            )
        except Exception:
            logger.exception("vector_search_failed", extra={"event": "rag_search_failed"})
            return []

        return [
            RetrievedChunk(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                title=result.title,
                source_org=result.source_org,
                url=result.url,
                publication_date=result.publication_date,
                topic=result.topic,
                content=result.content,
                score=result.score,
            )
            for result in results
        ]
