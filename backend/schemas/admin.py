"""Knowledge base (admin) schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    source_org: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    publication_date: str | None = Field(default=None, max_length=40)
    version: str | None = Field(default=None, max_length=40)
    license_note: str | None = Field(default=None, max_length=300)
    topic: str = Field(min_length=2, max_length=80)
    keywords: list[str] = []
    content: str = Field(min_length=50, max_length=200_000)


class KnowledgeDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source_org: str | None = None
    url: str | None = None
    publication_date: str | None = None
    version: str | None = None
    license_note: str | None = None
    topic: str | None = None
    status: str
    chunk_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentListResponse(BaseModel):
    items: list[KnowledgeDocumentOut]
    total: int


class KnowledgeBaseStats(BaseModel):
    documents_total: int
    documents_indexed: int
    documents_pending: int
    documents_failed: int
    chunks_total: int
    embedding_provider: str
    embedding_model: str


class AdminSystemStats(BaseModel):
    users_total: int
    reports_total: int
    reports_completed: int
    reports_failed: int
    analyses_total: int
    chat_messages_total: int
    knowledge: KnowledgeBaseStats


class ReindexResponse(BaseModel):
    documents_total: int
    documents_reindexed: int
    chunks_total: int
    message: str
