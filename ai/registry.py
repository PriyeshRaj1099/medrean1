"""AI provider factory (configured via AI_PROVIDER / EMBEDDING_PROVIDER)."""

from __future__ import annotations

from functools import lru_cache

from ai.base import AIProvider
from backend.core.config import get_settings


@lru_cache
def get_ai_provider() -> AIProvider:
    """The LLM provider used for analysis and chat."""
    settings = get_settings()
    if settings.ai_provider == "openai":
        from ai.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider()
    from ai.mock_provider import MockAIProvider

    return MockAIProvider(embedding_dimensions=settings.embedding_dimensions)


@lru_cache
def get_embedding_provider() -> AIProvider:
    """The provider used for RAG embeddings (may differ from the LLM provider)."""
    settings = get_settings()
    if settings.embedding_provider == "openai":
        from ai.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider()
    from ai.mock_provider import MockAIProvider

    return MockAIProvider(embedding_dimensions=settings.embedding_dimensions)


def reset_providers() -> None:
    """Clear cached providers (used by tests after settings changes)."""
    get_ai_provider.cache_clear()
    get_embedding_provider.cache_clear()
