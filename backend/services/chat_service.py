"""Chat assistant service: report-grounded Q&A with safety guardrails."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.base import AIProvider, ChatAnswer
from ai.base import ChatRequest as AIChatRequest
from ai.base import ChatTurn
from ai.registry import get_ai_provider, get_embedding_provider
from ai.safety import clean_model_text, injection_refusal, looks_like_prompt_injection
from backend.core.errors import NotFoundError
from backend.schemas.chat import ChatRequest
from backend.schemas.common import MEDICAL_DISCLAIMER, SUPPORTED_LANGUAGES
from backend.services import audit_service, report_service
from database.base import utcnow
from database.models import ChatMessage, ChatSession, Report, User
from rag.retriever import Retriever

logger = logging.getLogger("medilens.chat")

HISTORY_LIMIT = 10
DEFAULT_TITLE = "New conversation"


async def _owned_session(db: AsyncSession, session_id: str, user: User) -> ChatSession:
    session = (
        await db.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise NotFoundError("Chat session not found.")
    return session


async def _owned_report(db: AsyncSession, report_id: str, user: User) -> Report:
    report = (
        await db.execute(
            select(Report).where(Report.id == report_id, Report.user_id == user.id)
        )
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError("Report not found.")
    return report


async def build_report_context(db: AsyncSession, report: Report) -> dict[str, Any]:
    """Compact, PII-free summary of the report handed to the AI provider."""
    loaded = await report_service.get_report_with_children(db, report.id, report.user_id)
    items: list[dict[str, Any]] = []
    for value in sorted(loaded.values, key=lambda v: v.sort_order):
        items.append(
            {
                "test_name": value.test_name,
                "test_key": value.test_key,
                "value": value.value_numeric if value.value_numeric is not None else value.value_text,
                "unit": value.unit,
                "reference_range": value.ref_text
                or (
                    f"{value.ref_low:g} - {value.ref_high:g}"
                    if value.ref_low is not None and value.ref_high is not None
                    else None
                ),
                "status": value.status,
            }
        )
    outside = [item for item in items if item["status"] in ("below", "above")]
    return {
        "available": bool(items),
        "report_type": report.report_type,
        "report_date": report.report_date.isoformat() if report.report_date else None,
        "values": items,
        "values_outside": outside,
    }


async def _history(db: AsyncSession, session_id: str) -> list[ChatTurn]:
    rows = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return [ChatTurn(role=row.role, content=row.content) for row in reversed(list(rows))]


async def generate_chat_answer(provider: AIProvider, request: AIChatRequest) -> ChatAnswer:
    """Shared, guarded path for every chat reply (signed-in site + guest demo).

    Prompt-injection attempts are refused without calling the provider, and a
    provider's answer is stripped of markup/control characters before it is
    stored or returned.
    """
    if looks_like_prompt_injection(request.message):
        logger.warning(
            "chat_prompt_injection_blocked", extra={"event": "chat_prompt_injection_blocked"}
        )
        return ChatAnswer(
            content=injection_refusal(request.language), sources=[], safety_blocked=True
        )
    answer = await provider.generate_chat_response(request)
    answer.content = clean_model_text(answer.content, max_length=6000)
    return answer


async def send_message(db: AsyncSession, user: User, payload: ChatRequest) -> tuple[ChatSession, ChatMessage]:
    """Persist the user message, generate a grounded reply, persist it, return both."""
    language = payload.language or (user.locale if user.locale in SUPPORTED_LANGUAGES else "en")

    if payload.session_id:
        session = await _owned_session(db, payload.session_id, user)
        if payload.report_id and payload.report_id != session.report_id:
            session.report_id = payload.report_id
    else:
        if payload.report_id:
            await _owned_report(db, payload.report_id, user)
        session = ChatSession(
            user_id=user.id,
            report_id=payload.report_id,
            title=payload.message.strip()[:60] or DEFAULT_TITLE,
            language=language,
        )
        db.add(session)
        await db.flush()

    session.language = language
    if session.title == DEFAULT_TITLE:
        session.title = payload.message.strip()[:60] or DEFAULT_TITLE
    session.updated_at = utcnow()

    user_message = ChatMessage(
        session_id=session.id,
        role="user",
        content=payload.message.strip(),
        language=language,
        created_at=utcnow(),
    )
    db.add(user_message)
    await db.commit()

    history = await _history(db, session.id)

    context: dict[str, Any] | None = None
    if session.report_id:
        try:
            report = await _owned_report(db, session.report_id, user)
            context = await build_report_context(db, report)
        except NotFoundError:
            session.report_id = None

    query_parts = [payload.message]
    if context and context.get("values"):
        names = [item["test_name"] for item in context["values"][:12]]
        query_parts.append("laboratory tests: " + ", ".join(names) + ".")
    retriever = Retriever(db, get_embedding_provider())
    retrieved = await retriever.retrieve(" ".join(query_parts))

    provider = get_ai_provider()
    answer = await generate_chat_answer(
        provider,
        AIChatRequest(
            message=payload.message,
            history=history,
            language=language,
            report_context=context,
            retrieved=retrieved,
        ),
    )

    assistant_message = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=answer.content,
        language=language,
        sources=answer.sources or None,
        safety_blocked=answer.safety_blocked,
        created_at=utcnow(),
    )
    db.add(assistant_message)
    session.updated_at = utcnow()
    await db.commit()
    await db.refresh(assistant_message)
    await db.refresh(session)

    await audit_service.record(
        db,
        "chat.message_blocked" if answer.safety_blocked else "chat.message",
        user_id=user.id,
        entity_type="chat_session",
        entity_id=session.id,
        detail={"report_id": session.report_id, "language": language},
    )
    return session, assistant_message


async def list_sessions(db: AsyncSession, user: User, limit: int = 30) -> list[ChatSession]:
    rows = (
        (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.user_id == user.id)
                .order_by(ChatSession.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_session_detail(db: AsyncSession, user: User, session_id: str) -> ChatSession:
    session = await _owned_session(db, session_id, user)
    await db.refresh(session, attribute_names=["messages"])
    return session


async def delete_session(db: AsyncSession, user: User, session_id: str) -> None:
    session = await _owned_session(db, session_id, user)
    await db.delete(session)
    await db.commit()


def chat_disclaimer() -> str:
    return MEDICAL_DISCLAIMER
