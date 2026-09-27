"""Application configuration, loaded from environment variables / `.env`."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo layout: <root>/backend/core/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "backend" / "var"

DEFAULT_JWT_SECRET = "change-me-to-a-long-random-value"

Environment = Literal["development", "production", "test"]


def _resolve_path(value: str | os.PathLike[str], *, base: Path = PROJECT_ROOT) -> Path:
    """Resolve a path; relative paths are anchored at the repository root."""
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application ----
    app_name: str = "MediLens AI"
    environment: Environment = "development"
    debug: bool = True
    frontend_url: str = "http://localhost:3000"
    api_prefix: str = "/api"

    # ---- Database ----
    database_url: str = f"sqlite+aiosqlite:///{(RUNTIME_DIR / 'medilens_dev.db').as_posix()}"
    auto_create_schema: bool = True
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ---- Auth ----
    jwt_secret: str = DEFAULT_JWT_SECRET
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    # Without "Remember me" the server-side session expires quickly.
    session_refresh_expire_hours: int = 24
    password_reset_token_expire_minutes: int = 30
    auth_dev_return_reset_token: bool = False
    refresh_cookie_name: str = "medilens_refresh"
    refresh_cookie_path: str = "/api/auth"

    # ---- Demo account (shared, publicly documented credentials) ----
    # Created by the idempotent seed `scripts/seed_demo_account.py`. These are
    # demo-only credentials and must never be reused for admin or real accounts.
    demo_account_enabled: bool = True
    demo_account_email: str = "demo@medilens.ai"
    demo_account_password: str = "MediLens@Demo2026!"
    demo_account_name: str = "Demo Explorer"

    # ---- Sharing (secure, expiring, revocable public links) ----
    share_link_expire_days: int = 7
    share_link_max_expire_days: int = 30

    # ---- Google sign-in (disabled while client id/secret are empty) ----
    google_client_id: str = ""
    google_client_secret: str = ""

    # ---- CORS ----
    cors_origins: list[str] = ["http://localhost:3000"]

    # ---- Storage ----
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: Path = RUNTIME_DIR / "uploads"
    storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_endpoint_url: str = ""
    storage_region: str = "us-east-1"
    max_upload_size_mb: int = 20

    # ---- AI ----
    ai_provider: Literal["mock", "openai"] = "mock"
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = 90.0
    ai_max_output_tokens: int = 4000

    # ---- Embeddings ----
    embedding_provider: Literal["mock", "openai"] = "mock"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 512

    # ---- OCR ----
    ocr_provider: Literal["tesseract", "paddle", "none"] = "tesseract"
    ocr_languages: str = "eng"
    ocr_dpi: int = 250
    ocr_min_text_chars: int = 40

    # ---- RAG ----
    rag_top_k: int = 6
    rag_min_score: float = 0.12
    rag_chunk_size: int = 900
    rag_chunk_overlap: int = 150
    vector_database_url: str = ""

    # ---- Pipeline ----
    low_confidence_threshold: float = 0.7
    auto_analyze_after_extraction: bool = True
    data_retention_days: int = 0

    # ---- Observability ----
    logging_level: str = "INFO"
    log_json: bool = False
    rate_limit_enabled: bool = True

    # ---- Paths (derived, overridable for tests) ----
    prompts_dir: Path = PROJECT_ROOT / "prompts"
    export_dir: Path = RUNTIME_DIR / "exports"

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return json.loads(raw)
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        return value

    @field_validator("storage_local_dir", "prompts_dir", "export_dir", mode="before")
    @classmethod
    def _resolve_paths(cls, value: Any) -> Any:
        if isinstance(value, (str, os.PathLike)):
            return _resolve_path(value)
        return value

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        if self.environment == "production":
            problems: list[str] = []
            if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be a long random value in production")
            if self.auto_create_schema:
                problems.append("AUTO_CREATE_SCHEMA must be false in production (use Alembic)")
            if self.auth_dev_return_reset_token:
                problems.append("AUTH_DEV_RETURN_RESET_TOKEN must be false in production")
            if not self.cors_origins or any("*" in o for o in self.cors_origins):
                problems.append("CORS_ORIGINS must list explicit origins in production")
            if problems:
                raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def google_signin_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def cookie_secure(self) -> bool:
        return self.is_production

    def ensure_runtime_dirs(self) -> None:
        for directory in (RUNTIME_DIR, self.storage_local_dir, self.export_dir):
            Path(directory).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
