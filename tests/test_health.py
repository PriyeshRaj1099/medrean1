"""Health endpoint and baseline middleware behaviour."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_reports_ok_with_database(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["ai_provider"] in ("mock", "openai")
    assert body["checks"]["storage_backend"] in ("local", "s3")


async def test_security_headers_are_present(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"


async def test_unknown_route_returns_structured_error(client: AsyncClient) -> None:
    response = await client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "request_id" in body["error"]
