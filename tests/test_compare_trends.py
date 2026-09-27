"""Report comparison (neutral wording), trends and dashboard statistics."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import upload_and_process
from tests.fixtures.sample_reports import CBC_FOLLOWUP_TEXT, CBC_TEXT

NEUTRAL_FORBIDDEN = ("improved", "worsened", "better", "worse", "good", "bad")


async def test_compare_uses_neutral_descriptive_wording(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    first = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc-1.pdf")
    second = await upload_and_process(
        client, auth_headers, CBC_FOLLOWUP_TEXT, "cbc-2.pdf"
    )

    response = await client.post(
        "/api/reports/compare",
        json={"report_ids": [first, second]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()

    assert len(body["reports"]) == 2
    rows = {row["test_key"]: row for row in body["rows"]}

    hemoglobin = rows["hemoglobin"]
    assert "changed from" in hemoglobin["change"]
    assert "11.2" in hemoglobin["change"] and "12.4" in hemoglobin["change"]
    assert hemoglobin["unit"] == "g/dL"
    assert hemoglobin["delta"] is not None

    wbc = rows["wbc"]
    assert "changed from" in wbc["change"]

    for row in body["rows"]:
        lowered = row["change"].lower()
        assert not any(word in lowered for word in NEUTRAL_FORBIDDEN), row["change"]

    assert any("does not by itself indicate" in note for note in body["notes"])


async def test_compare_rejects_foreign_reports(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    first = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc-1.pdf")

    other = await client.post(
        "/api/auth/register",
        json={"name": "Other", "email": "other@example.com", "password": "Passw0rd123"},
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    response = await client.post(
        "/api/reports/compare",
        json={"report_ids": [first, first]},
        headers=other_headers,
    )
    assert response.status_code in (404, 422)


async def test_trends_include_two_points_across_reports(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    await upload_and_process(client, auth_headers, CBC_TEXT, "cbc-1.pdf")
    await upload_and_process(client, auth_headers, CBC_FOLLOWUP_TEXT, "cbc-2.pdf")

    response = await client.get("/api/dashboard/trends", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    series = {item["test_key"]: item for item in body["series"]}
    assert "hemoglobin" in series
    points = series["hemoglobin"]["points"]
    assert len(points) == 2
    # Chronological order by report date.
    assert points[0]["report_date"] == "2026-08-12"
    assert points[1]["report_date"] == "2026-11-12"
    assert [p["value_numeric"] for p in points] == [11.2, 12.4]

    assert body["notes"], "trends must carry interpretation notes"
    assert any("not" in note.lower() and "diagnos" in note.lower() for note in body["notes"])


async def test_dashboard_stats_count_out_of_range_values(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    await upload_and_process(client, auth_headers, CBC_TEXT, "cbc-1.pdf")
    await upload_and_process(client, auth_headers, CBC_FOLLOWUP_TEXT, "cbc-2.pdf")

    response = await client.get("/api/dashboard/stats", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["reports_total"] == 2
    assert body["reports_analyzed"] == 2
    assert body["reports_processing"] == 0
    # Both CBCs report hemoglobin, RBC, hematocrit and MCHC below range.
    assert body["values_outside_range"] >= 4
    assert body["values_low_confidence"] == 0
    assert len(body["recent_reports"]) == 2
    assert body["recent_reports"][0]["id"] == body["last_analysis_report_id"]


async def test_history_search_and_filters(client: AsyncClient, auth_headers) -> None:
    await upload_and_process(client, auth_headers, CBC_TEXT, "first-report.pdf")

    listing = await client.get("/api/reports", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    filtered = await client.get(
        "/api/reports?search=first-report&status=completed", headers=auth_headers
    )
    assert filtered.json()["total"] == 1

    no_match = await client.get(
        "/api/reports?search=does-not-exist", headers=auth_headers
    )
    assert no_match.json()["total"] == 0
