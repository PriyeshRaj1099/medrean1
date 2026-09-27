"""Chat assistant schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import SupportedLanguage


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    report_id: str | None = None
    language: SupportedLanguage | None = None


class ChatSourceOut(BaseModel):
    title: str
    source_org: str | None = None
    url: str | None = None
    topic: str | None = None
    snippet: str | None = None


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    language: str
    sources: list[ChatSourceOut] | None = None
    safety_blocked: bool
    created_at: datetime


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    report_id: str | None = None
    title: str
    language: str
    created_at: datetime
    updated_at: datetime


class ChatSessionDetailOut(ChatSessionOut):
    messages: list[ChatMessageOut] = []


class ChatResponse(BaseModel):
    session_id: str
    reply: ChatMessageOut
    disclaimer: str
