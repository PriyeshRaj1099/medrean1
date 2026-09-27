"""Knowledge base: spec-topic coverage, metadata and test-key grounding.

Spec section 27 asks for documents organised by topic (CBC, blood glucose,
lipid profile, liver function, kidney function, thyroid, electrolytes,
vitamins, urinalysis, general laboratory testing), each storing source,
title, topic, date, URL and document version metadata. The analysis and chat
layers must also be able to ground every laboratory test in those documents.
"""

from __future__ import annotations

import re
from dataclasses import replace

from sqlalchemy import func, select

from ai.base import AnalysisRequest, ChatRequest, RetrievedChunk, ValueContext
from ai.mock_provider import MockAIProvider
from ai.registry import get_embedding_provider
from backend.schemas.admin import KnowledgeDocumentCreate
from backend.services import knowledge_service
from database.models import KnowledgeChunk, KnowledgeDocument
from document_processing.lab_tests import LAB_TESTS
from rag.kb_loader import index_document, index_seed_knowledge, load_seed_documents

PROVIDER = MockAIProvider()

# Every spec-27 topic category must be served by at least one seed topic.
SPEC_TOPIC_COVERAGE = {
    "CBC": {"cbc", "hemoglobin"},
    "Blood glucose": {"glucose", "hba1c"},
    "Lipid profile": {"lipid", "triglycerides"},
    "Liver function": {"liver"},
    "Kidney function": {"kidney"},
    "Thyroid": {"thyroid"},
    "Electrolytes": {"electrolytes"},
    "Vitamins": {"vitamin_d", "vitamins"},
    "Urinalysis": {"urinalysis"},
    "General laboratory testing": {"general", "inflammation"},
}


def _kidney_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="chunk-kidney-1",
        document_id="doc-kidney",
        title="Creatinine and BUN (kidney) tests (MedlinePlus)",
        source_org="MedlinePlus, U.S. National Library of Medicine (NIH)",
        url="https://medlineplus.gov/lab-tests/creatinine-test/",
        publication_date=None,
        topic="kidney",
        content=(
            "The kidneys filter waste products from the blood. Creatinine is a waste "
            "product from normal muscle activity that healthy kidneys remove steadily."
        ),
        score=0.9,
    )


# ---------------------------------------------------------------------------
# Seed corpus: coverage + metadata
# ---------------------------------------------------------------------------
def test_seed_documents_cover_every_spec_topic() -> None:
    topics = {spec.topic for spec in load_seed_documents()}
    for category, accepted in SPEC_TOPIC_COVERAGE.items():
        assert topics & accepted, f"no seed document covers {category}"


def test_every_seed_document_stores_full_metadata() -> None:
    for spec in load_seed_documents():
        assert len(spec.title) >= 3
        assert spec.topic
        assert spec.source_org, spec.title
        assert spec.url and spec.url.startswith("https://"), spec.title
        assert spec.license_note, spec.title
        assert spec.version, f"{spec.title} must carry a document version"
        assert spec.content.strip()
        if spec.publication_date is not None:
            assert re.match(r"\d{4}", spec.publication_date), spec.title


def test_seed_documents_use_unique_topics_and_urls() -> None:
    specs = load_seed_documents()
    topics = [spec.topic for spec in specs]
    urls = [spec.url for spec in specs]
    assert len(topics) == len(set(topics))
    assert len(urls) == len(set(urls))


def test_every_lab_test_key_resolves_to_a_seed_topic() -> None:
    topics = {spec.topic for spec in load_seed_documents()}
    for test in LAB_TESTS:
        assert test.key in topics or test.topic in topics, test.key


# ---------------------------------------------------------------------------
# Indexing: version + chunk metadata, in-place updates
# ---------------------------------------------------------------------------
async def test_indexing_records_version_and_chunk_metadata(db_session) -> None:
    await index_seed_knowledge(db_session, get_embedding_provider(), force=True)
    document = (
        await db_session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.topic == "urinalysis")
        )
    ).scalar_one()
    assert document.status == "indexed"
    assert document.version == "1.0"

    chunks = (
        (
            await db_session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert chunks and document.chunk_count == len(chunks)
    for chunk in chunks:
        metadata = chunk.metadata_json or {}
        assert metadata["version"] == "1.0"
        assert metadata["topic"] == "urinalysis"
        assert metadata["url"] == document.url
        assert metadata["source_org"] == document.source_org
        assert metadata["title"] == document.title
        assert "publication_date" in metadata


async def test_reseeding_edited_documents_updates_rows_in_place(db_session) -> None:
    specs = load_seed_documents()
    await index_seed_knowledge(db_session, get_embedding_provider())

    target = next(spec for spec in specs if spec.topic == "urinalysis")
    before = (
        await db_session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.url == target.url)
        )
    ).scalar_one()

    edited = replace(
        target,
        content=f"{target.content}\n\nEditorial note added in a later version.",
        version="1.1",
    )
    document, indexed = await index_document(
        db_session, edited, get_embedding_provider(), match_url=True
    )
    assert indexed is True
    assert document.id == before.id, "edited seed content must update the row in place"
    assert document.version == "1.1"
    assert document.checksum == edited.checksum

    total = (
        await db_session.execute(select(func.count()).select_from(KnowledgeDocument))
    ).scalar_one()
    assert total == len(specs), "re-seeding must not leave stale duplicate documents"


# ---------------------------------------------------------------------------
# Grounding: test key -> knowledge-base topic bridge
# ---------------------------------------------------------------------------
async def test_analysis_grounds_shared_topic_tests_in_kb_documents() -> None:
    request = AnalysisRequest(
        values=[
            ValueContext(
                test_name="Creatinine", test_key="creatinine", value_numeric=1.9,
                value_text=None, unit="mg/dL", ref_low=0.6, ref_high=1.3,
                ref_text=None, status="above",
            )
        ],
        report_type="kidney",
        report_date=None,
        language="en",
        explanation_level="detailed",
        retrieved=[_kidney_chunk()],
    )

    structured = await PROVIDER.generate_structured_analysis(request)

    explanation = structured.test_explanations[0]
    assert explanation.source_ids == [1], "creatinine is grounded by the kidney topic"
    assert explanation.what_it_measures.startswith("The kidneys filter")
    assert structured.abnormal_results[0].source_ids == [1]
    assert structured.educational_explanations[0].topic == "Creatinine"
    assert structured.educational_explanations[0].source_ids == [1]


async def test_chat_grounds_answers_through_shared_topics() -> None:
    request = ChatRequest(
        message="What does my creatinine mean?",
        history=[],
        language="en",
        report_context={
            "available": True,
            "values": [
                {
                    "test_name": "Creatinine",
                    "test_key": "creatinine",
                    "value": "1.9",
                    "unit": "mg/dL",
                    "reference_range": "0.6 - 1.3",
                }
            ],
        },
        retrieved=[_kidney_chunk()],
    )

    answer = await PROVIDER.generate_chat_response(request)

    assert answer.sources and answer.sources[0]["url"] == _kidney_chunk().url
    assert "kidneys filter" in answer.content
    assert "Creatinine" in answer.content  # the matching report value is attached


# ---------------------------------------------------------------------------
# Admin ingestion: metadata round-trip
# ---------------------------------------------------------------------------
async def test_admin_document_round_trips_metadata_into_chunks(db_session) -> None:
    payload = KnowledgeDocumentCreate(
        title="Custom topic explainer (test fixture)",
        topic="custom",
        source_org="Example Health Organisation",
        url="https://example.org/custom-topic",
        publication_date="2024-01-01",
        version="2.4",
        license_note="Test fixture content written for the MediLens test suite.",
        content="A short educational paragraph about a custom laboratory topic. " * 3,
        keywords=["custom"],
    )

    document = await knowledge_service.create_document(db_session, payload, created_by=None)
    assert document.version == "2.4"
    assert document.publication_date == "2024-01-01"

    chunks = (
        (
            await db_session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert chunks
    assert all(chunk.metadata_json["version"] == "2.4" for chunk in chunks)
    assert all(chunk.metadata_json["source_org"] == "Example Health Organisation" for chunk in chunks)
