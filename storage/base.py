"""Storage provider interface.

The application never exposes storage paths publicly; files are streamed
through authenticated API endpoints after an ownership check.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator


class StorageError(Exception):
    """Raised when a storage operation fails."""


class FileTooLargeError(StorageError):
    """Raised while streaming when a file exceeds the configured limit."""


class StorageBackend(ABC):
    """Minimal object-storage contract used by the report pipeline."""

    @abstractmethod
    async def save_stream(
        self, key: str, chunks: AsyncIterator[bytes], max_size: int, content_type: str
    ) -> tuple[int, str]:
        """Persist a stream. Returns (size_bytes, sha256_hex).

        Raises FileTooLargeError when the stream exceeds ``max_size``.
        """

    @abstractmethod
    async def read_bytes(self, key: str) -> bytes:
        ...

    @abstractmethod
    async def open_read(self, key: str, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        ...

    @staticmethod
    def build_key(user_id: str, report_id: str, suffix: str) -> str:
        """Namespace keys per user; suffix includes a leading dot (e.g. '.pdf')."""
        safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        return f"users/{user_id}/reports/{report_id}{safe_suffix.lower()}"


class HashAccumulator:
    """Tracks sha256 and byte count while a stream is written."""

    def __init__(self) -> None:
        self._hasher = hashlib.sha256()
        self.size = 0

    def update(self, chunk: bytes) -> None:
        self._hasher.update(chunk)
        self.size += len(chunk)

    @property
    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


def ensure_safe_key(key: str) -> str:
    """Reject traversal attempts and absolute paths in storage keys."""
    normalized = key.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise StorageError("Invalid storage key")
    if Path(normalized).is_absolute() or ":" in normalized:
        raise StorageError("Invalid storage key")
    return normalized
