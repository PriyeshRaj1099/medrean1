"""Local filesystem storage backend (default for development)."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator

from storage.base import (
    FileTooLargeError,
    HashAccumulator,
    StorageBackend,
    StorageError,
    ensure_safe_key,
)


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        safe = ensure_safe_key(key)
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise StorageError("Invalid storage key")
        return path

    async def save_stream(
        self, key: str, chunks: AsyncIterator[bytes], max_size: int, content_type: str
    ) -> tuple[int, str]:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        accumulator = HashAccumulator()

        def _write() -> None:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, delete=False, suffix=".part"
            ) as tmp:
                try:
                    tmp_path = Path(tmp.name)
                    for chunk in sync_chunks:
                        accumulator.update(chunk)
                        if accumulator.size > max_size:
                            raise FileTooLargeError("File exceeds the size limit")
                        tmp.write(chunk)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                except BaseException:
                    tmp_path.unlink(missing_ok=True)
                    raise
            os.replace(tmp_path, path)

        # Drain the async iterator into a list inside the event loop, then write
        # in a worker thread (keeps the sync file IO off the loop). The size
        # limit is enforced while draining so oversized uploads fail fast.
        sync_chunks: list[bytes] = []
        drained = 0
        async for chunk in chunks:
            drained += len(chunk)
            if drained > max_size:
                raise FileTooLargeError("File exceeds the size limit")
            sync_chunks.append(chunk)
        await asyncio.to_thread(_write)
        return accumulator.size, accumulator.hexdigest

    async def read_bytes(self, key: str) -> bytes:
        path = self._resolve(key)

        def _read() -> bytes:
            if not path.exists():
                raise StorageError("File not found")
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def open_read(self, key: str, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError("File not found")
        with path.open("rb") as handle:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                yield chunk

    async def delete(self, key: str) -> None:
        path = self._resolve(key)

        def _delete() -> None:
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def exists(self, key: str) -> bool:
        path = self._resolve(key)
        return await asyncio.to_thread(path.exists)
