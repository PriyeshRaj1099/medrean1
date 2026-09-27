"""Shared pytest fixtures.

The suite runs against a throwaway SQLite database, the local storage backend
and the deterministic mock AI/embedding providers, so no network access, API
keys or OCR binaries are required. All sample documents are synthetic.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Configure the environment BEFORE any backend module is imported.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="medilens_tests_"))
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP_ROOT / 'test.db').as_posix()}"
os.environ["STORAGE_LOCAL_DIR"] = str(_TMP_ROOT / "uploads")
os.environ["AI_PROVIDER"] = "mock"
os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ["AUTH_DEV_RETURN_RESET_TOKEN"] = "true"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["AUTO_ANALYZE_AFTER_EXTRACTION"] = "true"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from ai.registry import get_embedding_provider  # noqa: E402
from backend.core.config import get_settings  # noqa: E402
from backend.core.rate_limit import limiter  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.services import report_service  # noqa: E402
from database.base import Base  # noqa: E402
from database.session import dispose_engine, get_engine, get_session_factory  # noqa: E402
from rag.kb_loader import index_seed_knowledge  # noqa: E402

TEST_USER = {
    "name": "Test User",
    "email": "test.user@example.com",
    "password": "Passw0rd123",
}


@pytest.fixture(autouse=True)
async def fresh_db():
    """Give every test a clean schema and a fresh rate limiter."""
    limiter.reset()
    get_settings().ensure_runtime_dirs()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Each test may run in its own event loop; dispose so pooled connections
    # from this loop are never reused in another one.
    await dispose_engine()


@pytest.fixture(autouse=True)
def no_background_jobs(monkeypatch: pytest.MonkeyPatch):
    """Keep processing deterministic: tests drive the pipeline explicitly."""

    async def _noop(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(report_service, "enqueue_processing", _noop)
    monkeypatch.setattr(report_service, "enqueue_analysis", _noop)


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://tests.local") as http:
        yield http


@pytest.fixture
async def db_session():
    factory = get_session_factory()
    async with factory() as session:
        yield session


@pytest.fixture
async def seeded_kb(db_session):
    """Index the bundled knowledge-base documents into the test database."""
    result = await index_seed_knowledge(db_session, get_embedding_provider(), force=True)
    assert result["chunks_total"] > 0
    return result


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/auth/register", json=TEST_USER)
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def upload_and_process(
    client: AsyncClient, headers: dict[str, str], text: str, filename: str = "sample.pdf"
) -> str:
    """Upload synthetic PDF text and run the full pipeline; returns report id."""
    from backend.jobs.pipeline import process_report_job
    from tests.fixtures.sample_reports import build_pdf_bytes

    response = await client.post(
        "/api/reports/upload",
        files={"file": (filename, build_pdf_bytes(text), "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    report_id = response.json()["id"]
    await process_report_job(report_id)
    return report_id
