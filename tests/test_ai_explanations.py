"""Per-test explanations, explanation level (simple/detailed) and language coverage.

Every detected value must carry a "what does this test mean" entry, the
requested explanation level must shape the payload (and force a re-run when it
changes), and every supported language template must be complete and free of
mixed-script text.
"""

from __future__ import annotations

from httpx import AsyncClient

from ai.base import AnalysisRequest, ValueContext
from ai.mock_provider import TEMPLATES, MockAIProvider
from ai.safety import EMERGENCY_ADDENDUM, INJECTION_REFUSAL, SAFE_REFUSAL
from backend.schemas.common import SUPPORTED_LANGUAGES
from tests.conftest import upload_and_process
from tests.fixtures.sample_reports import CBC_TEXT
from tests.script_checks import LANGUAGE_SCRIPTS, present_scripts

PROVIDER = MockAIProvider()


def _values() -> list[ValueContext]:
    return [
        ValueContext(
            test_name="Hemoglobin", test_key="hemoglobin", value_numeric=9.8,
            value_text=None, unit="g/dL", ref_low=13.0, ref_high=17.0,
            ref_text=None, status="below",
        ),
        ValueContext(
            test_name="WBC", test_key="wbc", value_numeric=14500,
            value_text=None, unit="/uL", ref_low=4000, ref_high=11000,
            ref_text=None, status="above",
        ),
        ValueContext(
            test_name="Platelets", test_key="platelets", value_numeric=250000,
            value_text=None, unit="/uL", ref_low=150000, ref_high=450000,
            ref_text=None, status="within",
        ),
        ValueContext(
            test_name="Unranged Analyte", test_key=None, value_numeric=42,
            value_text=None, unit=None, ref_low=None, ref_high=None,
            ref_text=None, status="unknown",
        ),
    ]


def _request(language: str = "en", level: str = "simple") -> AnalysisRequest:
    return AnalysisRequest(
        values=_values(), report_type="cbc", report_date=None,
        language=language, explanation_level=level,
    )


# ---------------------------------------------------------------------------
# Template completeness
# ---------------------------------------------------------------------------
def test_all_supported_languages_have_identical_template_keys() -> None:
    en_keys = set(TEMPLATES["en"].keys())
    assert len(en_keys) == 28
    for language in SUPPORTED_LANGUAGES:
        assert language in TEMPLATES, f"missing templates for {language}"
        assert set(TEMPLATES[language].keys()) == en_keys, language


async def test_every_language_renders_without_errors() -> None:
    for language in SUPPORTED_LANGUAGES:
        for level in ("simple", "detailed"):
            structured = await PROVIDER.generate_structured_analysis(
                _request(language, level)
            )
            assert len(structured.test_explanations) == len(_values())
            assert structured.summary.strip()
            for item in structured.test_explanations:
                assert item.meaning.strip(), (language, item.test_name)
                assert "  " not in item.meaning, (language, "double space")


async def test_no_language_contains_mixed_script_text() -> None:
    for language, expected_script in LANGUAGE_SCRIPTS.items():
        structured = await PROVIDER.generate_structured_analysis(_request(language))
        prose = " ".join(
            [
                structured.summary,
                *[item.meaning for item in structured.test_explanations],
                *[item.what_it_measures for item in structured.test_explanations],
                *structured.questions_for_doctor,
            ]
        )
        assert present_scripts(prose) == {expected_script}, language

    for latin_language in ("en", "hinglish"):
        structured = await PROVIDER.generate_structured_analysis(
            _request(latin_language)
        )
        prose = " ".join(
            [structured.summary, *[item.meaning for item in structured.test_explanations]]
        )
        assert not present_scripts(prose), latin_language


# ---------------------------------------------------------------------------
# Safety messages in every language
# ---------------------------------------------------------------------------
def test_safety_messages_exist_for_every_language() -> None:
    for language in SUPPORTED_LANGUAGES:
        assert SAFE_REFUSAL.get(language), language
        assert INJECTION_REFUSAL.get(language), language
        assert EMERGENCY_ADDENDUM.get(language), language


def test_safety_messages_have_no_mixed_script_text() -> None:
    for language, expected_script in LANGUAGE_SCRIPTS.items():
        prose = " ".join(
            [
                SAFE_REFUSAL[language],
                INJECTION_REFUSAL[language],
                EMERGENCY_ADDENDUM[language],
            ]
        )
        assert present_scripts(prose) == {expected_script}, language


# ---------------------------------------------------------------------------
# Per-test explanations
# ---------------------------------------------------------------------------
async def test_every_value_gets_an_explanation_entry() -> None:
    structured = await PROVIDER.generate_structured_analysis(_request())
    assert [item.test_name for item in structured.test_explanations] == [
        value.test_name for value in _values()
    ]
    statuses = [item.status for item in structured.test_explanations]
    assert statuses == ["below", "above", "within", "unknown"]
    assert structured.test_explanations[0].source_ids == []  # no retrieval in unit test
    assert structured.test_explanations[3].meaning  # unknown range still explained


async def test_simple_level_keeps_meanings_short() -> None:
    structured = await PROVIDER.generate_structured_analysis(_request(level="simple"))
    for item in structured.test_explanations:
        assert item.general_factors is None
        assert len(item.meaning) < 320, item.meaning


async def test_detailed_level_adds_background_factors() -> None:
    detailed = await PROVIDER.generate_structured_analysis(_request(level="detailed"))
    for item in detailed.test_explanations:
        assert item.general_factors
        assert item.general_factors in item.meaning


async def test_questions_are_always_between_three_and_seven() -> None:
    # No abnormal values -> generic questions keep the minimum of three.
    normal_request = AnalysisRequest(
        values=[_values()[2]], report_type="cbc", report_date=None,
        language="en", explanation_level="simple",
    )
    structured = await PROVIDER.generate_structured_analysis(normal_request)
    assert len(structured.questions_for_doctor) == 3

    # Many abnormal values -> one question per value, capped, never above seven.
    many = [
        ValueContext(
            test_name=f"Marker {index}", test_key=None, value_numeric=float(index),
            value_text=None, unit=None, ref_low=0.0, ref_high=1.0,
            ref_text=None, status="above",
        )
        for index in range(12)
    ]
    crowded = AnalysisRequest(
        values=many, report_type="cbc", report_date=None,
        language="en", explanation_level="simple",
    )
    structured = await PROVIDER.generate_structured_analysis(crowded)
    questions = structured.questions_for_doctor
    assert 5 <= len(questions) <= 7
    assert any("Marker 0" in question for question in questions)


async def test_unknown_limitation_uses_localized_template() -> None:
    structured = await PROVIDER.generate_structured_analysis(_request("hi"))
    assert any("वर्गीकृत" in note for note in structured.limitations)


# ---------------------------------------------------------------------------
# End-to-end wiring
# ---------------------------------------------------------------------------
async def test_analysis_contains_explanations_for_every_value_end_to_end(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")
    detail = (await client.get(f"/api/reports/{report_id}", headers=auth_headers)).json()
    analysis = (
        await client.get(f"/api/reports/{report_id}/analysis", headers=auth_headers)
    ).json()

    assert analysis["status"] == "completed"
    assert analysis["explanation_level"] == "simple"
    structured = analysis["structured"]
    assert len(structured["test_explanations"]) == len(detail["values"])
    assert all(item["meaning"].strip() for item in structured["test_explanations"])
    assert all(item["what_it_measures"].strip() for item in structured["test_explanations"])
    assert 3 <= len(structured["questions_for_doctor"]) <= 7
    assert all(item["general_factors"] is None for item in structured["test_explanations"])
    # Below-range values from the sample report must carry citations.
    below = [item for item in structured["test_explanations"] if item["status"] == "below"]
    assert below and any(item["source_ids"] for item in below)


async def test_detailed_level_reruns_and_expands(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")
    simple = (
        await client.get(f"/api/reports/{report_id}/analysis", headers=auth_headers)
    ).json()
    assert simple["explanation_level"] == "simple"

    response = await client.post(
        f"/api/reports/{report_id}/analyze",
        json={"language": "en", "explanation_level": "detailed"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    detailed = response.json()
    assert detailed["id"] != simple["id"], "level change must re-run the analysis"
    assert detailed["explanation_level"] == "detailed"
    assert all(
        item["general_factors"]
        for item in detailed["structured"]["test_explanations"]
    )

    again = (
        await client.post(
            f"/api/reports/{report_id}/analyze",
            json={"language": "en", "explanation_level": "detailed"},
            headers=auth_headers,
        )
    ).json()
    assert again["id"] == detailed["id"], "same level should reuse the analysis"


async def test_hindi_analysis_produces_devanagari_explanations(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")
    response = await client.post(
        f"/api/reports/{report_id}/analyze",
        json={"language": "hi", "explanation_level": "simple"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["language"] == "hi"
    explanations = body["structured"]["test_explanations"]
    assert explanations
    assert all(
        present_scripts(item["meaning"]) == {"devanagari"} for item in explanations
    )
