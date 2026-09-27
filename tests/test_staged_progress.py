"""Staged loading messages: localized, honest and in the right order.

The processing pipeline publishes one-line progress messages while it works
("Reading your report...", "Finding laboratory values...", "Checking the
reference ranges...", "Preparing your explanation...", "Finalizing your
report..."). Each message is only ever set while that phase is genuinely
running, so the UI can never claim the AI is doing something it is not.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import backend.jobs.pipeline as pipeline_module
import backend.services.analysis_service as analysis_module
from backend.core.progress import STAGE_MESSAGES, STAGES, stage_message
from backend.schemas.common import SUPPORTED_LANGUAGES
from document_processing.cleaner import clean_text
from document_processing.medical_parser import PageInput, classify_values, parse_pages
from tests.conftest import upload_and_process
from tests.fixtures.sample_reports import CBC_TEXT
from tests.script_checks import LANGUAGE_SCRIPTS, present_scripts

SPEC_WORDING = {
    "reading": "Reading your report...",
    "finding_values": "Finding laboratory values...",
    "checking_ranges": "Checking the reference ranges...",
    "preparing_explanation": "Preparing your explanation...",
    "finalizing": "Finalizing your report...",
}


# ---------------------------------------------------------------------------
# Message catalog
# ---------------------------------------------------------------------------
def test_every_language_covers_every_stage() -> None:
    assert set(STAGE_MESSAGES) == set(SUPPORTED_LANGUAGES)
    for language in SUPPORTED_LANGUAGES:
        assert set(STAGE_MESSAGES[language]) == set(STAGES), language
        for stage in STAGES:
            message = STAGE_MESSAGES[language][stage]
            assert message, (language, stage)
            assert message.strip() == message, (language, stage)
            assert message.endswith("..."), (language, stage)


def test_english_messages_match_the_spec_wording() -> None:
    assert STAGE_MESSAGES["en"] == SPEC_WORDING
    assert list(STAGES) == list(SPEC_WORDING)


def test_stage_messages_stay_in_one_script() -> None:
    for language in SUPPORTED_LANGUAGES:
        prose = " ".join(STAGE_MESSAGES[language][stage] for stage in STAGES)
        if language in LANGUAGE_SCRIPTS:
            assert present_scripts(prose) == {LANGUAGE_SCRIPTS[language]}, language
        else:
            assert not present_scripts(prose), language


def test_unknown_language_falls_back_to_english() -> None:
    for stage in STAGES:
        assert stage_message("zz", stage) == STAGE_MESSAGES["en"][stage]
    with pytest.raises(KeyError):
        stage_message("en", "not-a-stage")


# ---------------------------------------------------------------------------
# Parser phases
# ---------------------------------------------------------------------------
def _pages() -> list[PageInput]:
    return [PageInput(page_number=1, text=clean_text(CBC_TEXT))]


def test_deferred_parse_defers_the_range_check() -> None:
    pages = _pages()
    deferred = parse_pages(pages, defer_status=True)
    assert deferred.values
    # "Finding laboratory values": values exist, nothing has been compared yet.
    assert all(value.status == "unknown" for value in deferred.values)

    # "Checking the reference ranges": the comparison runs as its own phase.
    classify_values(deferred.values)
    immediate = parse_pages(pages)
    assert [value.test_name for value in deferred.values] == [
        value.test_name for value in immediate.values
    ]
    assert [value.status for value in deferred.values] == [
        value.status for value in immediate.values
    ]
    assert any(value.status != "unknown" for value in deferred.values)
    assert deferred.report_type == immediate.report_type


# ---------------------------------------------------------------------------
# End-to-end wiring
# ---------------------------------------------------------------------------
async def test_pipeline_announces_the_five_stages_in_order(
    client: AsyncClient, auth_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple[str, str]] = []

    def spy(language: str, stage: str) -> str:
        recorded.append((language, stage))
        return STAGE_MESSAGES.get(language, STAGE_MESSAGES["en"])[stage]

    monkeypatch.setattr(pipeline_module, "stage_message", spy)
    monkeypatch.setattr(analysis_module, "stage_message", spy)

    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    # Exactly the five staged messages, in the order the work really happens,
    # in the report owner's language.
    assert [stage for _, stage in recorded] == list(STAGES)
    assert {language for language, _ in recorded} == {"en"}

    status = (
        await client.get(f"/api/reports/{report_id}/status", headers=auth_headers)
    ).json()
    assert status["status"] == "completed"
    assert status["status_message"] is None


async def test_progress_messages_are_localized_for_the_report_language(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    languages: list[str] = []

    def spy(language: str, stage: str) -> str:
        languages.append(language)
        return STAGE_MESSAGES.get(language, STAGE_MESSAGES["en"])[stage]

    monkeypatch.setattr(pipeline_module, "stage_message", spy)
    monkeypatch.setattr(analysis_module, "stage_message", spy)

    # Register a Hindi-preferring user and upload through the API.
    response = await client.post(
        "/api/auth/register",
        json={
            "name": "Hindi User",
            "email": "hindi.user@example.com",
            "password": "Passw0rd123",
            "locale": "hi",
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    await upload_and_process(client, headers, CBC_TEXT, "cbc.pdf")

    assert set(languages) == {"hi"}
