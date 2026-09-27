"""Deterministic parsing of laboratory values, ranges and dates."""

from __future__ import annotations

from datetime import date

from document_processing.cleaner import clean_text
from document_processing.medical_parser import PageInput, compute_status, parse_pages
from tests.fixtures.sample_reports import CBC_TEXT, LIPID_TEXT


def _parse(text: str):
    return parse_pages([PageInput(page_number=1, text=clean_text(text))])


def _by_key(result) -> dict:
    return {value.test_key: value for value in result.values if value.test_key}


def test_cbc_values_statuses_and_ranges() -> None:
    result = _parse(CBC_TEXT)
    values = _by_key(result)

    hemoglobin = values["hemoglobin"]
    assert hemoglobin.value_numeric == 11.2
    assert hemoglobin.unit == "g/dL"
    assert hemoglobin.ref_low == 13.0
    assert hemoglobin.ref_high == 17.0
    assert hemoglobin.status == "below"
    assert hemoglobin.confidence >= 0.7
    assert hemoglobin.source_page == 1

    assert values["wbc"].status == "within"
    assert values["platelets"].status == "within"
    assert values["rbc"].status == "below"
    assert values["hematocrit"].status == "below"
    assert values["mchc"].status == "below"

    assert result.report_type == "Complete Blood Count (CBC)"
    assert result.report_date == date(2026, 8, 12)
    assert len(result.values) >= 9


def test_high_only_ranges_mark_values_above() -> None:
    result = _parse(LIPID_TEXT)
    values = _by_key(result)

    cholesterol = values["cholesterol_total"]
    assert cholesterol.value_numeric == 224
    assert cholesterol.ref_low is None
    assert cholesterol.ref_high == 200
    assert cholesterol.status == "above"

    assert values["triglycerides"].status == "above"
    assert values["ldl"].status == "above"
    assert values["hdl"].status == "below"

    assert result.report_type == "Lipid Profile"


def test_thousands_separators_are_parsed() -> None:
    values = _by_key(_parse(CBC_TEXT))
    assert values["platelets"].value_numeric == 210000
    assert values["wbc"].value_numeric == 7200


def test_compute_status_boundaries() -> None:
    assert compute_status(5.0, None, 10.0) == "within"
    assert compute_status(10.0, None, 10.0) == "within"
    assert compute_status(10.1, None, 10.0) == "above"
    assert compute_status(3.0, 3.0, 10.0) == "within"
    assert compute_status(2.9, 3.0, 10.0) == "below"
    assert compute_status(None, 3.0, 10.0) == "unknown"
    assert compute_status(5.0, None, None) == "unknown"


def test_clean_text_drops_page_furniture_and_normalises_unicode() -> None:
    messy = "Hemoglobin   11.2   g/dL\nPage 1 of 2\n-----\n13.0\u201317.0\n10.8\u00a0g/dL"
    cleaned = clean_text(messy)
    assert "Page 1 of 2" not in cleaned
    assert "-----" not in cleaned
    assert "13.0-17.0" in cleaned  # en-dash normalised
    assert "10.8 g/dL" in cleaned  # non-breaking space normalised
    assert "Hemoglobin" in cleaned


def test_multiple_pages_preserve_source_page() -> None:
    result = parse_pages(
        [
            PageInput(page_number=1, text=clean_text("Lipid Profile\n")),
            PageInput(page_number=2, text=clean_text(LIPID_TEXT)),
        ]
    )
    assert result.values
    assert all(value.source_page == 2 for value in result.values)
