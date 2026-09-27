# Architecture

How MediLens AI is put together: the module map, the processing pipeline, the
provider abstractions and the RAG flow. For setup see [SETUP.md](SETUP.md); for
the wire format see [API.md](API.md).

---

## 1. Big picture

```
Browser ── Next.js (SSR + client)  ──/api/* proxy──▶  FastAPI backend  ──▶  PostgreSQL / SQLite
   │            port 3000                              port 8000                │
   │                                                                           ├── uploads (local / S3-compatible)
   └── same-origin: no CORS, HttpOnly refresh cookie just works                ├── knowledge base (RAG)
                                                                               └── audit log
```

- The **browser only talks to the Next.js origin.** `next.config.ts` rewrites
  `/api/:path*` to the backend (`BACKEND_URL`), so cookies, CSRF origin checks
  and error handling all behave as a single origin.
- The **backend is stateless** apart from the database, the storage directory and
  the in-process job queue (single-worker friendly; a Redis-backed queue can slot
  in behind the same interface — see `backend/jobs/queue.py`).

---

## 2. Modules

### Backend (`backend/`)

| Module | Responsibility |
| --- | --- |
| `core/config.py` | Pydantic-settings; every env var, defaults, derived paths |
| `core/logging_config.py` | Structured logging (plain or JSON), request context |
| `core/errors.py` | App exceptions + the single error-envelope handler |
| `core/middleware.py` | Request id/context, body-size limit, security headers |
| `core/rate_limit.py` | Fixed-window in-memory limiter (swappable for Redis) |
| `core/security.py` | bcrypt hashing, JWT issue/verify helpers |
| `api/deps.py` | Auth dependencies (`CurrentUser`, `CurrentAdmin`), ownership checks |
| `api/routes/*` | Thin HTTP layer: validate → call service → shape response |
| `services/*` | Business logic: auth, report lifecycle, analysis, chat, compare, dashboard, export, demo, knowledge, audit |
| `jobs/queue.py` | Async background queue driving report processing |
| `schemas/*` | Pydantic request/response contracts (mirrored in `frontend/src/lib/types.ts`) |
| `main.py` | App factory: lifespan (schema, dirs, logging), middleware, routers |

### Processing pipeline packages (repo root, importable by backend and scripts)

| Package | Responsibility |
| --- | --- |
| `document_processing/pdf_extractor.py` | PyMuPDF text extraction + OCR fallback per page; records extraction method & OCR confidence |
| `document_processing/cleaner.py` | Normalises whitespace, strips boilerplate, repairs common OCR artefacts |
| `document_processing/segmenter.py` | Splits pages into candidate test-value segments |
| `document_processing/medical_parser.py` | Extracts test name / value / unit / reference range with per-value confidence; `lab_tests.py` holds the test-name lexicon |
| `ocr/` | `base.py` interface, `tesseract_provider.py`, `paddle_provider.py`, `factory.py` (config-driven) |
| `ai/` | `base.py` provider interface, `mock_provider.py` (offline deterministic), `openai_compatible.py`, `registry.py`, `safety.py` (guardrail rules), `prompt_loader.py` |
| `rag/` | `chunking.py`, `kb_loader.py` (seed import + re-index), `vector_store.py` (DB-backed cosine store), `retriever.py` (top-k + threshold + metadata filters), `knowledge/seed_knowledge.json` |
| `prompts/` | Markdown prompt templates for analysis (system/user/repair) and chat |

### Frontend (`frontend/src/`)

| Area | Responsibility |
| --- | --- |
| `lib/api.ts` | Fetch wrapper: in-memory access token, single-flight refresh, 401 retry, XHR upload with progress, file download, `ApiError` |
| `lib/auth.tsx` | Auth context: silent session bootstrap from the refresh cookie, login/register/logout, user adoption |
| `lib/i18n/` | Three dictionaries (`en`/`hi`/`hinglish`) with `{placeholder}` interpolation; persisted locale; server-side user locale sync |
| `lib/types.ts` | TypeScript mirror of every backend schema — kept in sync by hand |
| `components/ui/` | Design-system primitives (button, card, input, select, dialog, dropdown, tabs, tooltip…) on Radix |
| `components/shared/` | Status badges (icon + text, never colour alone), disclaimer notes, page widgets, theme/language switches |
| `components/reports/` | Upload dropzone, pipeline status view, editable values table, analysis view, trend chart, compare table |
| `components/chat/` | Chat panel (sessions, message thread, safety-block styling, sources) |
| `app/` | App Router pages: landing, auth flow, dashboard, reports (list/new/detail), trends, compare, chat, settings, admin, error pages |

---

## 3. Report processing pipeline

```
upload ─▶ validate (type, size, magic bytes)  ─▶ store file ─▶ Report row (status: uploaded)
   │
   ▼ background job (backend/jobs/queue.py)
processing   ── read + integrity check
extracting   ── per page: PyMuPDF text layer → (fallback) OCR provider
             ── clean → segment → medical parser → ExtractedValue rows
                (test name, value, unit, printed reference range,
                 confidence, source page, raw line)
extracted    ── values stored; status recalculated (within/below/above/unknown)
analyzing    ── RAG retrieval → LLM provider (structured JSON) → Analysis row
completed    ── (or) failed with a human-readable status_message + error_code
```

- **Follow the status** via `GET /api/reports/{id}/status`; the UI polls it and
  renders a staged progress view.
- **Edits:** `PATCH /reports/{id}/results` updates values, recalculates each
  status against the printed range, marks values `is_user_edited`, and flags the
  existing analysis as **stale** (`analysis_stale=true`) so the UI offers
  "Update explanation".
- **Reprocessing** (`POST /reports/{id}/process`) re-runs the whole pipeline from
  the stored original file and replaces the extracted values.
- When `AUTO_ANALYZE_AFTER_EXTRACTION=true` (default) the analysis job is
  enqueued automatically at the end of extraction.

### Extraction confidence

- Every value carries `confidence` (0–1): text-layer matches score highest, OCR
  matches are damped by the page's OCR confidence, ambiguous numeric/range
  layouts lose points.
- Values below `LOW_CONFIDENCE_THRESHOLD` (default 0.7) are flagged in the API
  and highlighted in the UI with a "verify this value" affordance.

---

## 4. Provider abstractions (no fake implementations)

Three pluggable seams, each with a real offline implementation and a real remote
implementation — and the active one is always visible (`AI_PROVIDER`, response
`provider` field, UI label):

| Seam | Interface | `mock` | Remote |
| --- | --- | --- | --- |
| LLM | `ai/base.py` | `mock_provider.py` — deterministic engine built from extracted values + retrieved chunks; schema-valid structured output | `openai_compatible.py` — any OpenAI-compatible chat API (OpenAI, Azure-compatible gateways, OpenRouter, Groq, Ollama, vLLM) |
| Embeddings | `ai/registry.py` | deterministic local hashing embeddings (offline, stable) | OpenAI-compatible embeddings endpoint |
| OCR | `ocr/base.py` | — | Tesseract (`pytesseract`) and PaddleOCR providers; `none` disables OCR |
| Storage | `backend/services` storage layer | local filesystem (`backend/var/uploads`) | S3-compatible object storage (`STORAGE_BACKEND=s3`) |

The **mock LLM is not a stub**: it produces the complete analysis contract
(summary, abnormal/normal results, educational explanations, questions,
limitations, citations) and is labelled `provider: "mock"` everywhere. Switching
to a real provider changes no prompts, no schema and no UI.

---

## 5. RAG flow

```
seed_knowledge.json ──▶ chunking.py (≈900 chars, 150 overlap)
                     ──▶ embeddings provider ──▶ KnowledgeChunk rows (vector + metadata)
user report topics ──▶ retriever.py: top-k similarity (RAG_TOP_K=6, min score 0.12)
                     ├─ metadata filters: topic, organisation
                     └─ chunk text + source metadata ──▶ prompt context
LLM output ──▶ source_ids ──▶ AnalysisSource rows (title, org, url, snippet, score)
```

- **No invented sources, ever.** Citations come only from retrieved chunks. If
  retrieval yields nothing relevant, `sources` is empty and the analysis carries
  a `retrieval_note` saying no knowledge-base material matched.
- The bundled knowledge base (`rag/knowledge/seed_knowledge.json`) contains
  documents derived from openly-licensed public-health resources (WHO,
  NIH/MedlinePlus, CDC), each with organisation, URL, publication date and a
  license note.
- Indexing is explicit: `python scripts/seed_knowledge.py` (or the Admin
  re-index button). Admins can add/remove documents at runtime — new documents
  are chunked and embedded on creation.
- The vector store is the application database (cosine similarity over stored
  embeddings); `VECTOR_DATABASE_URL` is reserved for swapping in pgvector/Chroma
  behind `vector_store.py` without touching callers.

---

## 6. Database schema (SQLAlchemy models)

```
User ─┬─ UserSession            (refresh sessions; revocable)
      ├─ PasswordResetToken     (single-use reset tokens)
      ├─ Report ─┬─ ReportPage          (per-page extracted text + method/confidence)
      │          ├─ ExtractedValue      (name, value, unit, range, status, confidence…)
      │          └─ Analysis ─┬─ AnalysisSource (citations)
      │                       └─ payload (structured JSON)
      ├─ ChatSession ── ChatMessage     (role, content, sources, safety_blocked)
      └─ AuditLog                       (auth + sensitive actions)

KnowledgeDocument ── KnowledgeChunk  (text, embedding vector, metadata)
```

All child rows reference their parent with cascade deletes — deleting a report
removes its pages, values, analyses, citations and the stored file. Indexes cover
the foreign keys and the common query paths (user reports by status/date,
knowledge chunks by document). Migrations live in `database/migrations/`
(Alembic, async engine, `DATABASE_URL` taken from application settings).

---

## 7. Medical-safety layers (defence in depth)

1. **Prompt layer** (`prompts/*.md`) — system prompts forbid diagnosis,
   prescription, certainty claims and invented citations; require educational
   framing, printed-range comparisons, uncertainty language and emergency
   guidance where relevant.
2. **Safety rules** (`ai/safety.py`) — pre/post checks: chat requests asking for
   diagnoses or medication advice are blocked before they reach the model; the
   response is a safe refusal with `safety_blocked: true`.
3. **Data layer** — analyses are only generated from extracted values (with
   confidence) and retrieved chunks (with sources); no free-form recall.
4. **UI layer** — disclaimers on data-heavy pages ("Educational information
   only — not a diagnosis"), emergency notice near results, neutral status
   wording (within/below/above), neutral comparison wording, demo data clearly
   labelled, low-confidence values flagged for verification.
5. **Export layer** — the summary PDF always includes the disclaimer text.

---

## 8. Failure and degradation behaviour

| Failure | Behaviour |
| --- | --- |
| AI provider down / bad key | `ai_provider_error` with a human-readable message; extraction results remain available; retry the analysis |
| OCR not installed | Clear extraction error on scanned files (`OCR_PROVIDER=none` skips OCR deliberately) |
| Knowledge base empty | Analysis still runs; sources empty; `retrieval_note` explains |
| LLM returns malformed JSON | One repair pass with a repair prompt; if still invalid → `ai_provider_error` (never a fabricated analysis) |
| Upload of a corrupted/encrypted PDF | Rejected at validation with a specific message |
| Transient polling failure in the UI | Treated as retryable; the UI backs off and continues polling |
