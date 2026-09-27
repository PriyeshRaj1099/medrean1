"""Demo mode: clearly-labelled synthetic reports.

Covers both demo flavours: the authenticated "load a copy" flow and the
public "Try Demo" guest flow (sample + stateless chat, no account needed).
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_demo_kinds_are_public_and_described(client: AsyncClient) -> None:
    response = await client.get("/api/demo/kinds")
    assert response.status_code == 200
    kinds = response.json()
    keys = {item["kind"] for item in kinds}
    assert {"cbc", "lipid", "thyroid", "metabolic"} <= keys
    for item in kinds:
        assert item["title"]
        assert item["synthetic"] is True


async def test_loading_demo_report_parses_the_same_pipeline(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    response = await client.post(
        "/api/demo/load", json={"kind": "cbc"}, headers=auth_headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["report"]["is_demo"] is True
    assert "synthetic" in body["notice"].lower()

    report_id = body["report"]["id"]
    detail = (await client.get(f"/api/reports/{report_id}", headers=auth_headers)).json()
    assert detail["is_demo"] is True
    assert detail["detected_test_count"] >= 8
    assert detail["report_type"] == "Complete Blood Count (CBC)"

    by_key = {value["test_key"]: value for value in detail["values"] if value["test_key"]}
    assert by_key["hemoglobin"]["status"] == "below"


async def test_loading_demo_report_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/demo/load", json={"kind": "cbc"})
    assert response.status_code == 401


async def test_unknown_demo_kind_is_rejected(client: AsyncClient, auth_headers) -> None:
    response = await client.post(
        "/api/demo/load", json={"kind": "nonsense"}, headers=auth_headers
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Guest "Try Demo" flow: no account required
# ---------------------------------------------------------------------------
async def test_guest_sample_is_public_and_fully_analyzed(
    client: AsyncClient, seeded_kb
) -> None:
    response = await client.get("/api/demo/samples/cbc")
    assert response.status_code == 200
    body = response.json()
    assert body["synthetic"] is True
    assert "synthetic" in body["notice"].lower()

    report = body["report"]
    assert report["detected_test_count"] >= 8
    analysis = report["analysis"]
    assert analysis["status"] == "completed"
    structured = analysis["structured"]
    # Per-test explanations and doctor questions arrive with the sample too.
    assert len(structured["test_explanations"]) == len(report["values"])
    assert 3 <= len(structured["questions_for_doctor"]) <= 7


async def test_guest_sample_speaks_indian_languages(client: AsyncClient, seeded_kb) -> None:
    hindi = (await client.get("/api/demo/samples/cbc?language=hi")).json()
    assert hindi["report"]["language"] == "hi"
    summary = hindi["report"]["analysis"]["structured"]["summary"]
    assert any("\u0900" <= char <= "\u097F" for char in summary)


async def test_guest_sample_is_cached_across_visits(client: AsyncClient, seeded_kb) -> None:
    first = (await client.get("/api/demo/samples/lipid")).json()
    second = (await client.get("/api/demo/samples/lipid")).json()
    assert first["report"]["created_at"] == second["report"]["created_at"]


async def test_guest_chat_is_public_and_labeled(client: AsyncClient, seeded_kb) -> None:
    response = await client.post(
        "/api/demo/chat",
        json={"kind": "cbc", "message": "What does hemoglobin mean?", "language": "en"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"].strip()
    assert body["disclaimer"]
