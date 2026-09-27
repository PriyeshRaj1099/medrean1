"""Vector store abstraction.

The default `DatabaseVectorStore` keeps embeddings in the `knowledge_chunks`
table (JSON column) and computes cosine similarity in Python. For the intended
knowledge-base sizes this is fast and portable (works on SQLite and PostgreSQL).

The interface is deliberately small so a pgvector or Chroma adapter can replace
this class without touching the retriever or services. Set VECTOR_DATABASE_URL
and drop in an adapter when the KB grows.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import KnowledgeChunk, KnowledgeDocument


@dataclass
class VectorSearchResult:
    chunk_id: str
    document_id: str
    content: str
    score: float
    topic: str | None
    title: str
    source_org: str | None
    url: str | None
    publication_date: str | None


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


class VectorStore(ABC):
    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        min_score: float,
        topics: list[str] | None = None,
    ) -> list[VectorSearchResult]:
        ...


class DatabaseVectorStore(VectorStore):
    """Cosine similarity over embeddings stored in the database."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        min_score: float,
        topics: list[str] | None = None,
    ) -> list[VectorSearchResult]:
        statement = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeChunk.embedding.is_not(None))
            .where(KnowledgeDocument.status == "indexed")
        )
        if topics:
            statement = statement.where(KnowledgeChunk.topic.in_(topics))

        rows = (await self.db.execute(statement)).all()
        scored: list[VectorSearchResult] = []
        for chunk, document in rows:
            embedding = chunk.embedding or []
            score = cosine_similarity(query_embedding, embedding)
            if score < min_score:
                continue
            scored.append(
                VectorSearchResult(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    content=chunk.content,
                    score=round(score, 4),
                    topic=chunk.topic,
                    title=document.title,
                    source_org=document.source_org,
                    url=document.url,
                    publication_date=document.publication_date,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(1, top_k)]
