"""Upload validation, extraction pipeline and analysis persistence."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import TEST_USER, upload_and_process
from tests.fixtures.sample_reports import CBC_TEXT


async def test_upload_rejects_non_pdf_content(client: AsyncClient, auth_headers) -> None:
    response = await client.post(
        "/api/reports/upload",
        files={"file": ("fake.pdf", b"this is not a pdf file", "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upload_error"


async def test_upload_rejects_unsupported_type(client: AsyncClient, auth_headers) -> None:
    response = await client.post(
        "/api/reports/upload",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 400


async def test_full_pipeline_extracts_values_and_analysis(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    detail = (await client.get(f"/api/reports/{report_id}", headers=auth_headers)).json()
    assert detail["status"] == "completed"
    assert detail["is_demo"] is False
    assert detail["report_type"] == "Complete Blood Count (CBC)"
    assert detail["report_date"] == "2026-08-12"
    assert detail["page_count"] == 1
    assert detail["detected_test_count"] >= 8

    by_key = {value["test_key"]: value for value in detail["values"] if value["test_key"]}
    assert by_key["hemoglobin"]["status"] == "below"
    assert by_key["hemoglobin"]["value_numeric"] == 11.2
    assert by_key["hemoglobin"]["reference_range"]["low"] == 13.0

    analysis = (
        await client.get(f"/api/reports/{report_id}/analysis", headers=auth_headers)
    ).json()
    assert analysis["status"] == "completed"
    assert analysis["provider"] == "mock"
    assert analysis["counts"]["below"] >= 3
    assert analysis["counts"]["within"] >= 4
    assert analysis["summary"]

    structured = analysis["structured"]
    assert structured["abnormal_results"], "expected abnormal explanations"
    assert structured["questions_for_doctor"], "expected questions for a professional"
    assert analysis["sources"], "expected citations from the seeded knowledge base"
    for source in analysis["sources"]:
        assert source["url"] and source["url"].startswith("https://")
        assert source["source_org"], "sources must name their organisation"

    # Sources must be traceable to retrieved knowledge-base chunks: matched
    # sources carry the chunk's relevance score and snippet.
    assert all(source["relevance_score"] is not None for source in analysis["sources"])
    assert all(source["snippet"] for source in analysis["sources"])


async def test_analysis_is_reused_when_nothing_changed(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    first = (
        await client.post(
            f"/api/reports/{report_id}/analyze", json={"language": "en"}, headers=auth_headers
        )
    ).json()
    second = (
        await client.post(
            f"/api/reports/{report_id}/analyze", json={"language": "en"}, headers=auth_headers
        )
    ).json()
    assert first["id"] == second["id"], "unchanged analysis should be reused"


async def test_editing_results_marks_analysis_stale(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")
    detail = (await client.get(f"/api/reports/{report_id}", headers=auth_headers)).json()
    hemoglobin = next(v for v in detail["values"] if v["test_key"] == "hemoglobin")

    patch = await client.patch(
        f"/api/reports/{report_id}/results",
        json={
            "values": [
                {
                    "id": hemoglobin["id"],
                    "value_numeric": 15.1,
                }
            ]
        },
        headers=auth_headers,
    )
    assert patch.status_code == 200
    updated = next(
        v for v in patch.json()["values"] if v["id"] == hemoglobin["id"]
    )
    assert updated["value_numeric"] == 15.1
    assert updated["status"] == "within"
    assert updated["is_user_edited"] is True
    assert patch.json()["analysis_stale"] is True


async def test_reports_are_private_to_their_owner(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    other = await client.post(
        "/api/auth/register",
        json={"name": "Other", "email": "other@example.com", "password": "Passw0rd123"},
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    response = await client.get(f"/api/reports/{report_id}", headers=other_headers)
    assert response.status_code == 404


async def test_delete_removes_report_and_children(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    delete = await client.delete(f"/api/reports/{report_id}", headers=auth_headers)
    assert delete.status_code == 200

    gone = await client.get(f"/api/reports/{report_id}", headers=auth_headers)
    assert gone.status_code == 404

    listing = (await client.get("/api/reports", headers=auth_headers)).json()
    assert all(item["id"] != report_id for item in listing["items"])


async def test_file_download_is_authenticated(client: AsyncClient, auth_headers) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    anonymous = await client.get(f"/api/reports/{report_id}/file")
    assert anonymous.status_code == 401

    download = await client.get(f"/api/reports/{report_id}/file", headers=auth_headers)
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")


async def test_upload_rate_limited_only_when_enabled(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    # Rate limiting is disabled in the test environment; flip it on and prove
    # the limiter actually guards the upload endpoint.
    from backend.core.config import get_settings
    from backend.core.rate_limit import limiter

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True, raising=False)
    limiter.reset()

    last_status = None
    for index in range(25):  # UPLOAD_RULE allows 20/minute
        response = await client.post(
            "/api/reports/upload",
            files={
                "file": (f"f{index}.pdf", b"not-a-pdf", "application/pdf"),
            },
            headers=auth_headers,
        )
        last_status = response.status_code
    assert last_status == 429
    limiter.reset()
    monkeypatch.setattr(settings, "rate_limit_enabled", False, raising=False)


def test_test_user_constant_is_sane() -> None:
    assert TEST_USER["email"].endswith("@example.com")
