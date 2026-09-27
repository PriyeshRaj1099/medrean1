"""Storage backend: round-trips, size limits and key safety."""

from __future__ import annotations

import pytest

from storage.base import FileTooLargeError, StorageError, ensure_safe_key
from storage.factory import get_storage


async def _chunks(data: bytes, size: int = 7):
    for offset in range(0, len(data), size):
        yield data[offset : offset + size]


async def test_local_storage_round_trip() -> None:
    storage = get_storage()
    key = "users/u1/reports/r1.pdf"
    payload = b"".join(bytes([i % 251]) for i in range(500))

    size, digest = await storage.save_stream(key, _chunks(payload), 10_000, "application/pdf")
    assert size == len(payload)
    assert len(digest) == 64

    assert await storage.exists(key) is True
    assert await storage.read_bytes(key) == payload
    assert b"".join([chunk async for chunk in storage.open_read(key, chunk_size=64)]) == payload

    await storage.delete(key)
    assert await storage.exists(key) is False

    # Deleting a missing key is tolerated (used by report deletion).
    await storage.delete(key)


async def test_local_storage_enforces_max_size() -> None:
    storage = get_storage()
    key = "users/u1/reports/too-big.bin"
    with pytest.raises(FileTooLargeError):
        await storage.save_stream(key, _chunks(b"x" * 100), 10, "application/octet-stream")
    assert await storage.exists(key) is False


async def test_build_key_namespaces_by_user() -> None:
    from storage.base import StorageBackend

    assert (
        StorageBackend.build_key("user-1", "report-2", "pdf")
        == "users/user-1/reports/report-2.pdf"
    )
    assert StorageBackend.build_key("u", "r", ".PDF") == "users/u/reports/r.pdf"


def test_ensure_safe_key_rejects_traversal() -> None:
    assert ensure_safe_key("/users/a/b.pdf") == "users/a/b.pdf"
    with pytest.raises(StorageError):
        ensure_safe_key("../etc/passwd")
    with pytest.raises(StorageError):
        ensure_safe_key("users/../../escape.pdf")
    with pytest.raises(StorageError):
        ensure_safe_key("C:/Windows/system32")

    with pytest.raises(StorageError):
        ensure_safe_key("")
