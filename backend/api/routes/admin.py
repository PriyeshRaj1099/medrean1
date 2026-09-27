"""Admin routes: system stats, user overview and knowledge-base management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from backend.api.deps import CurrentAdmin, DbSession
from backend.schemas.admin import (
    AdminSystemStats,
    KnowledgeDocumentCreate,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentOut,
    ReindexResponse,
)
from backend.schemas.user import MessageResponse
from backend.services import knowledge_service
from database.models import Analysis, ChatMessage, Report, User

logger = logging.getLogger("medilens.admin.api")

router = APIRouter(prefix="/admin", tags=["admin"])


async def _count(db: DbSession, model, *conditions) -> int:
    query = select(func.count()).select_from(model)
    if conditions:
        query = query.where(*conditions)
    return int((await db.execute(query)).scalar_one() or 0)


@router.get("/stats", response_model=AdminSystemStats)
async def system_stats(admin: CurrentAdmin, db: DbSession) -> AdminSystemStats:
    return AdminSystemStats(
        users_total=await _count(db, User),
        reports_total=await _count(db, Report),
        reports_completed=await _count(db, Report, Report.status == "completed"),
        reports_failed=await _count(db, Report, Report.status == "failed"),
        analyses_total=await _count(db, Analysis),
        chat_messages_total=await _count(db, ChatMessage),
        knowledge=await knowledge_service.stats(db),
    )


@router.get("/users")
async def list_users(
    admin: CurrentAdmin,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    users = (
        (
            await db.execute(
                select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    counts = dict(
        (
            await db.execute(
                select(Report.user_id, func.count()).group_by(Report.user_id)
            )
        ).all()
    )
    return [
        {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "reports_count": int(counts.get(user.id, 0)),
        }
        for user in users
    ]


@router.get("/knowledge/documents", response_model=KnowledgeDocumentListResponse)
async def list_documents(
    admin: CurrentAdmin,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> KnowledgeDocumentListResponse:
    items, total = await knowledge_service.list_documents(db, limit=limit, offset=offset)
    return KnowledgeDocumentListResponse(items=items, total=total)


@router.post(
    "/knowledge/documents",
    response_model=KnowledgeDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    payload: KnowledgeDocumentCreate, admin: CurrentAdmin, db: DbSession
) -> KnowledgeDocumentOut:
    return await knowledge_service.create_document(db, payload, created_by=admin.id)


@router.delete("/knowledge/documents/{document_id}", response_model=MessageResponse)
async def delete_document(
    document_id: str, admin: CurrentAdmin, db: DbSession
) -> MessageResponse:
    document = await knowledge_service.get_document(db, document_id)
    await knowledge_service.delete_document(db, document, actor_id=admin.id)
    return MessageResponse(message="Knowledge document deleted.")


@router.post("/knowledge/reindex", response_model=ReindexResponse)
async def reindex_knowledge(admin: CurrentAdmin, db: DbSession) -> ReindexResponse:
    return await knowledge_service.reindex_seed(db, actor_id=admin.id)
