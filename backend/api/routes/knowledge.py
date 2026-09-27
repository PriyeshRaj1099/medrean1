"""Knowledge-base routes (public source library)."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.deps import CurrentUser, DbSession
from backend.core.errors import NotFoundError
from database.models import KnowledgeDocument
from sqlalchemy import select

logger = logging.getLogger("medilens.knowledge.api")

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeSourceOut(BaseModel):
    id: str
    title: str
    source_org: str | None = None
    url: str | None = None
    topic: str | None = None
    publication_date: str | None = None


@router.get("/sources", response_model=list[KnowledgeSourceOut])
async def list_sources(user: CurrentUser, db: DbSession) -> list[KnowledgeSourceOut]:
    """The trusted sources the analysis cites (indexed documents only)."""
    rows = (
        (
            await db.execute(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.status == "indexed")
                .order_by(KnowledgeDocument.title.asc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [
        KnowledgeSourceOut(
            id=row.id,
            title=row.title,
            source_org=row.source_org,
            url=row.url,
            topic=row.topic,
            publication_date=row.publication_date,
        )
        for row in rows
    ]


@router.get("/sources/{document_id}", response_model=KnowledgeSourceOut)
async def get_source(document_id: str, user: CurrentUser, db: DbSession) -> KnowledgeSourceOut:
    row = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Source not found.")
    return KnowledgeSourceOut(
        id=row.id,
        title=row.title,
        source_org=row.source_org,
        url=row.url,
        topic=row.topic,
        publication_date=row.publication_date,
    )
