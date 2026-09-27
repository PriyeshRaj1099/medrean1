"""Knowledge base loader and indexer.

Seed documents live in rag/knowledge/seed_knowledge.json. Each document is a
concise educational summary written for this project, with a real source
reference (organisation, URL, publication date when known). Full text is NOT
copied from the sources — see the `license_note` on each document.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.base import AIProvider
from backend.core.config import get_settings
from database.models import KnowledgeChunk, KnowledgeDocument
from rag.chunking import chunk_text

logger = logging.getLogger("medilens.rag.kb")

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
SEED_FILE = KNOWLEDGE_DIR / "seed_knowledge.json"


@dataclass
class KnowledgeDocSpec:
    title: str
    topic: str
    source_org: str | None
    url: str | None
    publication_date: str | None
    license_note: str | None
    content: str
    version: str | None = None
    keywords: list[str] = field(default_factory=list)

    @property
    def checksum(self) -> str:
        payload = f"{self.title}|{self.topic}|{self.content}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_seed_documents(path: Path | None = None) -> list[KnowledgeDocSpec]:
    source = path or SEED_FILE
    if not source.exists():
        logger.warning("seed knowledge file not found: %s", source)
        return []
    raw = json.loads(source.read_text(encoding="utf-8"))
    return [
        KnowledgeDocSpec(
            title=item["title"],
            topic=item["topic"],
            source_org=item.get("source_org"),
            url=item.get("url"),
            publication_date=item.get("publication_date"),
            version=item.get("version"),
            license_note=item.get("license_note"),
            content=item["content"],
            keywords=list(item.get("keywords", [])),
        )
        for item in raw
    ]


async def _embed_chunks(
    embedding_provider: AIProvider, chunks: list[str]
) -> list[list[float]]:
    embeddings: list[list[float]] = []
    batch_size = 32
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        embeddings.extend(await embedding_provider.create_embedding(batch))
    return embeddings


def _refresh_metadata(document: KnowledgeDocument, spec: KnowledgeDocSpec) -> bool:
    """Copy editorial metadata from a spec onto a row; True when it changed."""
    changed = False
    for attr in ("title", "source_org", "url", "publication_date", "version", "license_note", "topic"):
        value = getattr(spec, attr)
        if getattr(document, attr) != value:
            setattr(document, attr, value)
            changed = True
    return changed


async def index_document(
    db: AsyncSession,
    spec: KnowledgeDocSpec,
    embedding_provider: AIProvider,
    *,
    created_by: str | None = None,
    force: bool = False,
    match_url: bool = False,
) -> tuple[KnowledgeDocument, bool]:
    """Create or refresh one knowledge document. Returns (document, indexed).

    Documents are identified by content checksum. When `match_url` is set
    (bundled seed loading), a stable URL also identifies a document, so edited
    seed content updates its row in place instead of leaving a stale duplicate.
    """
    settings = get_settings()
    existing = (
        await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.checksum == spec.checksum)
        )
    ).scalar_one_or_none()
    if existing is None and match_url and spec.url:
        existing = (
            await db.execute(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.url == spec.url)
                .order_by(KnowledgeDocument.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if (
        existing is not None
        and existing.checksum == spec.checksum
        and existing.status == "indexed"
        and not force
    ):
        # Unchanged content: refresh metadata only, no re-embedding needed.
        if _refresh_metadata(existing, spec):
            await db.commit()
        return existing, False

    document = existing or KnowledgeDocument(
        title=spec.title,
        source_org=spec.source_org,
        url=spec.url,
        publication_date=spec.publication_date,
        version=spec.version,
        license_note=spec.license_note,
        topic=spec.topic,
        checksum=spec.checksum,
        status="pending",
        created_by=created_by,
    )
    if existing is not None:
        # Re-index: drop old chunks, refresh metadata, re-key to the new content.
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
        _refresh_metadata(document, spec)
        document.checksum = spec.checksum
        document.error_message = None
    db.add(document)
    await db.flush()

    chunks = chunk_text(
        spec.content, chunk_size=settings.rag_chunk_size, overlap=settings.rag_chunk_overlap
    )
    if not chunks:
        document.status = "failed"
        document.error_message = "Document content is empty after chunking."
        await db.commit()
        return document, False

    try:
        embeddings = await _embed_chunks(embedding_provider, chunks)
    except Exception as exc:
        document.status = "failed"
        document.error_message = f"Embedding failed: {type(exc).__name__}"
        await db.commit()
        raise

    chunk_metadata = {
        "title": spec.title,
        "topic": spec.topic,
        "source_org": spec.source_org,
        "url": spec.url,
        "publication_date": spec.publication_date,
        "version": spec.version,
    }
    for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        db.add(
            KnowledgeChunk(
                document_id=document.id,
                chunk_index=index,
                content=chunk,
                embedding=embedding,
                embedding_model=getattr(embedding_provider, "model", None),
                topic=spec.topic,
                keywords=", ".join(spec.keywords) if spec.keywords else None,
                metadata_json=dict(chunk_metadata),
            )
        )
    document.status = "indexed"
    document.chunk_count = len(chunks)
    await db.commit()
    await db.refresh(document)
    return document, True


async def index_seed_knowledge(
    db: AsyncSession,
    embedding_provider: AIProvider,
    *,
    force: bool = False,
    created_by: str | None = None,
) -> dict[str, int]:
    """Index all seed documents. Skips already-indexed unchanged documents."""
    specs = load_seed_documents()
    indexed = 0
    for spec in specs:
        _document, was_indexed = await index_document(
            db, spec, embedding_provider, created_by=created_by, force=force, match_url=True
        )
        if was_indexed:
            indexed += 1
    total_chunks = (
        await db.execute(select(func.count()).select_from(KnowledgeChunk))
    ).scalar_one()
    return {
        "documents_total": len(specs),
        "documents_indexed": indexed,
        "chunks_total": int(total_chunks or 0),
    }
