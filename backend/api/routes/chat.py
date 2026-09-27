"""Chat assistant routes: sessions and messages."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.api.deps import CurrentUser, DbSession
from backend.core.rate_limit import CHAT_RULE, limiter
from backend.schemas.chat import (
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ChatSessionDetailOut,
    ChatSessionOut,
)
from backend.schemas.common import MEDICAL_DISCLAIMER
from backend.schemas.user import MessageResponse
from backend.services import chat_service

logger = logging.getLogger("medilens.chat.api")

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages", response_model=ChatResponse)
async def send_message(payload: ChatRequest, user: CurrentUser, db: DbSession) -> ChatResponse:
    limiter.check(f"chat:{user.id}", CHAT_RULE)
    session, reply = await chat_service.send_message(db, user, payload)
    return ChatResponse(
        session_id=session.id,
        reply=ChatMessageOut.model_validate(reply),
        disclaimer=MEDICAL_DISCLAIMER,
    )


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_sessions(user: CurrentUser, db: DbSession) -> list[ChatSessionOut]:
    sessions = await chat_service.list_sessions(db, user)
    return [ChatSessionOut.model_validate(session) for session in sessions]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetailOut)
async def session_detail(session_id: str, user: CurrentUser, db: DbSession) -> ChatSessionDetailOut:
    session = await chat_service.get_session_detail(db, user, session_id)
    return ChatSessionDetailOut.model_validate(session)


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def delete_session(session_id: str, user: CurrentUser, db: DbSession) -> MessageResponse:
    await chat_service.delete_session(db, user, session_id)
    return MessageResponse(message="Conversation deleted.")
