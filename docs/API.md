# API Reference

Base URL: `http://127.0.0.1:8000` (dev). Everything except `GET /health` lives
under the `/api` prefix. Interactive OpenAPI docs are served at `/api/docs` when
`ENVIRONMENT != production`.

All request/response bodies are JSON unless stated otherwise.

---

## Authentication model

- **Access token** — JWT (HS256) returned by register/login/refresh, sent as
  `Authorization: Bearer <token>`. Short-lived (default 15 min) and held **in
  memory only** by the frontend.
- **Refresh session** — a rotating opaque token stored by the server and set as
  an HttpOnly cookie (`medilens_refresh`, `path=/api/auth`, SameSite=Lax,
  `Secure` in production). `POST /api/auth/refresh` rotates it; logout,
  password change and session revocation invalidate it.
- The shipped frontend calls the API through the Next.js origin (`/api/*` is
  proxied), so the cookie flow works without any CORS configuration.

## Error envelope

Every application error — validation included — is returned in the same shape:

```json
{
  "error": {
    "code": "not_found",
    "message": "The requested resource was not found.",
    "detail": null,
    "request_id": "f63ec771323548b4"
  }
}
```

| Code | HTTP | Meaning |
| --- | --- | --- |
| `validation_error` | 422 | Invalid payload/fields (`detail` lists field errors) |
| `unauthenticated` | 401 | Missing/expired access token or refresh session |
| `forbidden` | 403 | Insufficient role (e.g. admin endpoints) |
| `not_found` | 404 | Unknown id / no analysis yet |
| `rate_limited` | 429 | Rate limit exceeded (fixed-window, in-memory) |
| `upload_error` | 400 | File type/size/integrity rejected |
| `processing_error` | 4xx/5xx | Pipeline failure |
| `ai_provider_error` | 502 | The configured AI provider failed |
| `internal_error` | 500 | Unexpected server error |

Response headers include `X-Request-ID` — quote it when reporting an issue; it is
also attached to the structured logs and the audit trail.

Rate limits (fixed window, per key; disabled with `RATE_LIMIT_ENABLED=false`):
auth 12/min · uploads 20/min · process/analyze 30/min · chat 40/min · export 15/min.

---

## Health

| Method | Path | Auth | Response |
| --- | --- | --- | --- |
| GET | `/health` | — | `{status, version, environment, checks}` |

`checks` reports per-subsystem status (database, storage, providers).

---

## Auth — `/api/auth`

| Method | Path | Auth | Body → Response |
| --- | --- | --- | --- |
| POST | `/register` | — | `{name, email, password, locale?}` → **201** `TokenResponse` |
| POST | `/login` | — | `{email, password}` → `TokenResponse` |
| POST | `/refresh` | cookie | — → `{access_token, token_type, expires_at}` (rotates the cookie) |
| POST | `/logout` | cookie | — → `{message}` (revokes the session, clears the cookie) |
| GET | `/me` | bearer | — → `UserOut` |
| PATCH | `/me` | bearer | `{name?, locale?}` → `UserOut` |
| POST | `/change-password` | bearer | `{current_password, new_password}` → `{message}` (**revokes all sessions**) |
| POST | `/forgot-password` | — | `{email}` → `{message, dev_reset_token?, dev_reset_url?}` |
| POST | `/reset-password` | — | `{token, new_password}` → `{message}` |
| GET | `/sessions` | bearer | — → `AuthSessionOut[]` |
| DELETE | `/sessions/{id}` | bearer | — → `{message}` |

`TokenResponse`: `{access_token, token_type: "bearer", expires_at, user}` where
`user = {id, name, email, role, locale, created_at, last_login_at}`.

`POST /forgot-password` always returns the same generic message (no account
enumeration). `dev_reset_token`/`dev_reset_url` are only included when
`AUTH_DEV_RETURN_RESET_TOKEN=true` (development helper — see docs/SETUP.md).

---

## Reports — `/api/reports`

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| POST | `/upload` | bearer | multipart `file` (PDF/JPG/PNG, ≤ `MAX_UPLOAD_SIZE_MB`). **201** `ReportSummaryOut`. Processing continues in the background |
| GET | `` | bearer | Query: `search`, `status`, `report_type`, `limit` (1–100), `offset` → `{items, total, limit, offset}` |
| POST | `/compare` | bearer | `{report_ids: [2–6 ids]}` → `CompareResponse` (see below) |
| GET | `/{report_id}` | bearer | → `ReportDetailOut` (summary + `values[]` + `pages[]`) |
| GET | `/{report_id}/status` | bearer | → `{report_id, status, status_message, error_code, detected_test_count}` |
| GET | `/{report_id}/file` | bearer | streams the original uploaded file |
| PATCH | `/{report_id}/results` | bearer | `{values: [{id, test_name?, value_numeric?, value_text?, unit?, ref_low?, ref_high?, ref_text?}]}` → full `ReportDetailOut`. Statuses are recalculated against the range and the analysis is flagged stale |
| POST | `/{report_id}/process` | bearer | re-runs the extraction pipeline from the stored file |
| DELETE | `/{report_id}` | bearer | deletes the report **and its stored file**, values, analyses |
| GET | `/{report_id}/export/pdf` | bearer | Query: `language` (`en`·`hi`·`hinglish`) → `application/pdf` summary with disclaimer |

**Pipeline statuses:** `uploaded → processing → extracting → extracted →
analyzing → completed` (or `failed`). Poll `/{id}/status` while in progress.

**Value statuses** (compared to the range printed on the report):
`within` · `below` · `above` · `unknown`.

Each extracted value carries `confidence` (0–1), `source_page`, `raw_line`,
`is_user_edited` and `reference_range {low, high, text}`. Values below
`LOW_CONFIDENCE_THRESHOLD` (default 0.7) are flagged for manual verification.

### Compare response (neutral wording by design)

```json
{
  "reports": [{ "id": "...", "original_filename": "...", "report_date": "2026-05-02", "created_at": "..." }],
  "rows": [
    {
      "test_key": "hemoglobin",
      "test_name": "Hemoglobin",
      "unit": "g/dL",
      "entries": [{ "report_id": "...", "value_numeric": 11.2, "reference_range": {"low": 12, "high": 16, "text": "12–16"}, "status": "below", "...": "..." }],
      "change": "Reported value changed from 11.2 to 12.4 g/dL",
      "delta": 1.2
    }
  ],
  "notes": ["This comparison shows only the values as printed on each report. …"]
}
```

Change wording is strictly descriptive — "stayed at", "increased", "decreased",
"changed from X to Y" — never "improved"/"worsened".

---

## Analysis — `/api/reports/{id}/...`

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/{report_id}/analyze` | `{language: en|hi|hinglish, force?: bool}` → `AnalysisOut`. **Blocks until the analysis completes.** `force=true` re-runs even if a fresh analysis exists |
| POST | `/{report_id}/analysis/refresh` | same body → `{report_id, status: "queued"}` — background re-analysis (used after value edits) |
| GET | `/{report_id}/analysis` | latest `AnalysisOut` (404 when none exists) |
| GET | `/{report_id}/analyses` | `AnalysisOut[]` history |
| GET | `/{report_id}/analysis/structured` | just the `StructuredAnalysis` payload |
| GET | `/{report_id}/sources` | `AnalysisSourceOut[]` (retrieved citations with snippets/scores) |

`AnalysisOut`:

```json
{
  "id": "...", "report_id": "...", "status": "completed",
  "provider": "mock", "model": null, "language": "en",
  "summary": "Plain-language summary …",
  "counts": { "within": 5, "below": 4, "above": 0, "unknown": 0, "low_confidence": 1 },
  "structured": {
    "summary": "...", "report_type": "Complete Blood Count",
    "abnormal_results": [{
      "test_name": "Hemoglobin", "value": "11.2", "unit": "g/dL",
      "reference_range": {"low": 12, "high": 16, "text": "12–16"},
      "status": "below",
      "explanation": "Educational explanation …",
      "source_ids": [1, 3], "confidence": 0.95, "low_confidence": false
    }],
    "normal_results": [{"test_name": "…", "value": "…", "unit": "…", "reference_range": {"…": "…"}}],
    "educational_explanations": [{"topic": "…", "text": "…", "source_ids": [2]}],
    "questions_for_doctor": ["…"],
    "limitations": ["…"],
    "sources": [{"id": 1, "title": "…", "source_org": "MedlinePlus (NIH)", "url": "https://…", "publication_date": null}],
    "retrieval_note": "Explains when no relevant knowledge-base material was found."
  },
  "sources": [{"title": "…", "source_org": "…", "url": "…", "topic": "…", "snippet": "…", "relevance_score": 0.61}],
  "duration_ms": 812, "created_at": "…"
}
```

- `source_ids` point at `structured.sources[].id`; when retrieval finds nothing
  relevant, `sources` is empty, `retrieval_note` says so, and the analysis never
  fabricates a citation.
- The provider name (`mock` or the configured model) is always exposed — the UI
  labels mock output clearly.

---

## Chat — `/api/chat`

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/messages` | `{message (1–2000 chars), session_id?, report_id?, language?}` → `{session_id, reply: ChatMessageOut, disclaimer}` |
| GET | `/sessions` | `ChatSessionOut[]` (most recent first) |
| GET | `/sessions/{id}` | `ChatSessionDetailOut` (with `messages[]`) |
| DELETE | `/sessions/{id}` | `{message}` |

`ChatMessageOut`: `{id, role: "user"|"assistant", content, language, sources?,
safety_blocked, created_at}`.

Safety guardrails: requests for a diagnosis, medication/dosage advice or other
prohibited content are **blocked** — the reply comes back with
`safety_blocked: true` and a safe refusal that redirects to a healthcare
professional. General educational questions are answered, optionally grounded in
the knowledge base (`sources`) and, when `report_id` is passed, the report's
values.

---

## Dashboard — `/api/dashboard`

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/stats` | `{reports_total, reports_analyzed, reports_processing, values_outside_range, values_low_confidence, last_analysis_at, last_analysis_report_id, recent_reports[]}` |
| GET | `/trends` | Query `test_key` (repeatable, ≤6). → `{available_tests[], series[], notes[]}` |

`series[]` contains per-test points (`value_numeric`, `ref_low`, `ref_high`,
`status`, `report_date`, `report_name`) ordered chronologically. The frontend
renders neutral lines with the printed reference band; when no `test_key` is
supplied the backend picks up to three trendable tests. Only reports with status
`extracted`/`completed` contribute points.

---

## Demo — `/api/demo`

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/kinds` | — | `[{kind, title, description, synthetic: true}]` — cbc · lipid · thyroid · metabolic |
| POST | `/load` | bearer | `{kind}` → **201** `{report, notice}`. Creates a clearly-labelled synthetic report and runs the normal pipeline |

Demo data is entirely synthetic; the notice and the UI badge make that explicit.

---

## Knowledge — `/api/knowledge`

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/sources` | Trusted (indexed) knowledge-base sources any signed-in user can see |
| GET | `/sources/{document_id}` | Single source |

---

## Admin — `/api/admin` (role `admin` required)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/stats` | `{users_total, reports_total, reports_completed, reports_failed, analyses_total, chat_messages_total, knowledge: {documents_total, documents_indexed, documents_pending, documents_failed, chunks_total, embedding_provider, embedding_model}}` |
| GET | `/users` | Query `limit` (≤200), `offset` → `AdminUserOut[]` (incl. `reports_count`) |
| GET | `/knowledge/documents` | Query `limit`, `offset` → `{items, total}` |
| POST | `/knowledge/documents` | `{title, source_org?, url?, publication_date?, license_note?, topic, keywords?, content (≥50 chars)}` → **201** `KnowledgeDocumentOut` (chunked + embedded immediately) |
| DELETE | `/knowledge/documents/{id}` | Removes a document and its chunks |
| POST | `/knowledge/reindex` | Re-chunks + re-embeds the bundled seed documents → `{documents_total, documents_reindexed, chunks_total, message}` |

Promote a user to admin with `python scripts/create_admin.py --email … --promote`.
