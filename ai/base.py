"""AI provider abstraction: structured analysis, chat, embeddings."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backend.schemas.analysis import StructuredAnalysis


@dataclass
class ValueContext:
    """A single extracted laboratory value, serialised for the AI layer."""

    test_name: str
    test_key: str | None
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None
    status: str  # within | below | above | unknown
    confidence: float = 0.9
    source_page: int = 1

    @property
    def value_display(self) -> str:
        if self.value_numeric is not None:
            formatted = f"{self.value_numeric:g}"
            return formatted
        return self.value_text or "—"

    @property
    def range_display(self) -> str:
        if self.ref_low is not None and self.ref_high is not None:
            return f"{self.ref_low:g} - {self.ref_high:g}"
        if self.ref_high is not None:
            return f"< {self.ref_high:g}"
        if self.ref_low is not None:
            return f"> {self.ref_low:g}"
        return self.ref_text or "Not provided on the report"


@dataclass
class RetrievedChunk:
    """A knowledge-base chunk supplied to the provider as grounding context."""

    chunk_id: str
    document_id: str
    title: str
    source_org: str | None
    url: str | None
    publication_date: str | None
    topic: str | None
    content: str
    score: float


@dataclass
class AnalysisRequest:
    values: list[ValueContext]
    report_type: str | None
    report_date: date | None
    language: str  # see backend.schemas.common.SUPPORTED_LANGUAGES
    explanation_level: str = "simple"  # simple | detailed (user preference)
    retrieved: list[RetrievedChunk] = field(default_factory=list)
    low_confidence_count: int = 0
    # Private user note, included ONLY when the user explicitly opted in.
    user_note: str | None = None
    # True when instruction-like text was detected inside document values or
    # the note (prompt-injection attempt). The provider is told to treat such
    # text strictly as data.
    injection_suspected: bool = False


@dataclass
class ChatTurn:
    role: str  # user | assistant
    content: str


@dataclass
class ChatRequest:
    message: str
    history: list[ChatTurn]
    language: str
    report_context: dict[str, Any] | None = None  # structured summary of the report
    retrieved: list[RetrievedChunk] = field(default_factory=list)


@dataclass
class ChatAnswer:
    content: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    safety_blocked: bool = False


class AIProviderError(Exception):
    """Raised when a provider call fails after retries."""


class AIProvider(ABC):
    """Contract every AI backend must satisfy."""

    name: str = "base"
    model: str | None = None

    @abstractmethod
    async def generate_structured_analysis(self, request: AnalysisRequest) -> StructuredAnalysis:
        """Return the validated structured analysis payload."""

    @abstractmethod
    async def generate_chat_response(self, request: ChatRequest) -> ChatAnswer:
        """Answer a user question grounded in report data + retrieved chunks."""

    @abstractmethod
    async def create_embedding(self, texts: list[str]) -> list[list[float]]:
        """Create one embedding vector per input text."""

    @property
    def is_llm_backed(self) -> bool:
        """True for providers that call a real external LLM API."""
        return False
