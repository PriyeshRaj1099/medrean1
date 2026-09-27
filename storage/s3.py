"""S3-compatible object storage backend (production option).

Requires ``boto3`` (not installed by default):

    pip install boto3

Works with AWS S3, MinIO, Cloudflare R2, Backblaze B2 and other
S3-compatible providers (set STORAGE_ENDPOINT_URL for non-AWS).
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from storage.base import (
    FileTooLargeError,
    HashAccumulator,
    StorageBackend,
    StorageError,
    ensure_safe_key,
)


class S3StorageBackend(StorageBackend):
    def __init__(
        self,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        endpoint_url: str = "",
    ) -> None:
        if not bucket:
            raise StorageError("STORAGE_BUCKET is required when STORAGE_BACKEND=s3")
        try:
            import boto3  # imported lazily so boto3 stays an optional dependency
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise StorageError(
                "boto3 is required for S3 storage: pip install boto3"
            ) from exc

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region or None,
            endpoint_url=endpoint_url or None,
        )

    async def save_stream(
        self, key: str, chunks: AsyncIterator[bytes], max_size: int, content_type: str
    ) -> tuple[int, str]:
        safe_key = ensure_safe_key(key)
        accumulator = HashAccumulator()
        buffer: list[bytes] = []
        async for chunk in chunks:
            accumulator.update(chunk)
            if accumulator.size > max_size:
                raise FileTooLargeError("File exceeds the size limit")
            buffer.append(chunk)

        payload = b"".join(buffer)

        def _upload() -> None:
            self._client.put_object(
                Bucket=self.bucket,
                Key=safe_key,
                Body=payload,
                ContentType=content_type or "application/octet-stream",
                # Never public: files are served through authenticated API routes.
                ACL="private",
            )

        await asyncio.to_thread(_upload)
        return accumulator.size, accumulator.hexdigest

    async def read_bytes(self, key: str) -> bytes:
        safe_key = ensure_safe_key(key)
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=self.bucket, Key=safe_key
            )
        except Exception as exc:  # botocore raises ClientError for missing keys
            raise StorageError("File not found") from exc
        return await asyncio.to_thread(response["Body"].read)

    async def open_read(self, key: str, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        data = await self.read_bytes(key)
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]

    async def delete(self, key: str) -> None:
        safe_key = ensure_safe_key(key)

        def _delete() -> None:
            self._client.delete_object(Bucket=self.bucket, Key=safe_key)

        await asyncio.to_thread(_delete)

    async def exists(self, key: str) -> bool:
        safe_key = ensure_safe_key(key)

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=safe_key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)
