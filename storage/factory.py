"""Storage backend factory (configured via STORAGE_BACKEND)."""

from __future__ import annotations

from functools import lru_cache

from backend.core.config import get_settings
from storage.base import StorageBackend
from storage.local import LocalStorageBackend


@lru_cache
def get_storage() -> StorageBackend:
    settings = get_settings()
    if settings.storage_backend == "s3":
        from storage.s3 import S3StorageBackend

        return S3StorageBackend(
            bucket=settings.storage_bucket,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key,
            region=settings.storage_region,
            endpoint_url=settings.storage_endpoint_url,
        )
    return LocalStorageBackend(settings.storage_local_dir)
