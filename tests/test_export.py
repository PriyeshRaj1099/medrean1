"""PDF summary export."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import upload_and_process
from tests.fixtures.sample_reports import CBC_TEXT


async def test_export_returns_a_real_pdf_with_required_sections(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    response = await client.get(
        f"/api/reports/{report_id}/export/pdf?language=en", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 1500

    import pymupdf

    document = pymupdf.open(stream=response.content, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    lowered = text.lower()

    assert "MediLens AI - Report Summary" in text
    assert "hemoglobin" in lowered
    assert "11.2" in text
    assert "below range" in lowered
    assert "not a diagnosis" in lowered
    assert "page 1" in lowered
    assert "medlineplus.gov" in lowered, "exported sources must be real"
    assert "emergency" in lowered


async def test_export_requires_authentication(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")
    response = await client.get(f"/api/reports/{report_id}/export/pdf")
    assert response.status_code == 401


async def test_export_handles_report_without_analysis(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    from backend.core.config import get_settings
    from backend.jobs.pipeline import process_report_job
    from tests.fixtures.sample_reports import build_pdf_bytes

    monkeypatch.setattr(
        get_settings(), "auto_analyze_after_extraction", False, raising=False
    )

    upload = await client.post(
        "/api/reports/upload",
        files={"file": ("cbc.pdf", build_pdf_bytes(CBC_TEXT), "application/pdf")},
        headers=auth_headers,
    )
    report_id = upload.json()["id"]
    # Extraction only: no knowledge base and auto-analysis disabled.
    await process_report_job(report_id)

    response = await client.get(
        f"/api/reports/{report_id}/export/pdf?language=en", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")

    import pymupdf

    document = pymupdf.open(stream=response.content, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    assert "No AI-generated summary is available" in text
    assert "hemoglobin" in text.lower()
