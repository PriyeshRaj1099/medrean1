"""FastAPI application factory for MediLens AI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import database.models  # noqa: F401  (registers all tables on Base.metadata)
from backend.api.routes import (
    admin,
    analysis,
    auth,
    chat,
    dashboard,
    demo,
    export,
    health,
    knowledge,
    reports,
    shared,
)
from backend.core.config import get_settings
from backend.core.errors import register_exception_handlers
from backend.core.logging_config import configure_logging
from backend.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from backend.jobs.queue import get_job_queue
from database.base import Base
from database.session import dispose_engine, get_engine

logger = logging.getLogger("medilens.app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging()
    settings.ensure_runtime_dirs()

    if settings.auto_create_schema:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database schema ensured", extra={"event": "schema_ready"})

    logger.info(
        "MediLens AI started (env=%s, ai_provider=%s, embedding_provider=%s, storage=%s)",
        settings.environment,
        settings.ai_provider,
        settings.embedding_provider,
        settings.storage_backend,
        extra={"event": "app_started"},
    )
    yield
    await get_job_queue().shutdown()
    await dispose_engine()
    logger.info("MediLens AI stopped", extra={"event": "app_stopped"})


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description=(
            "Educational medical report analysis API. Not a diagnostic device — "
            "see /docs and the project README for the medical disclaimer."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )

    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    register_exception_handlers(app)

    # Health lives at the root per spec; everything else under /api.
    app.include_router(health.router)
    api = settings.api_prefix
    app.include_router(auth.router, prefix=api)
    app.include_router(reports.router, prefix=api)
    app.include_router(shared.router, prefix=api)
    app.include_router(analysis.router, prefix=api)
    app.include_router(chat.router, prefix=api)
    app.include_router(dashboard.router, prefix=api)
    app.include_router(export.router, prefix=api)
    app.include_router(demo.router, prefix=api)
    app.include_router(knowledge.router, prefix=api)
    app.include_router(admin.router, prefix=api)
    return app


app = create_app()
