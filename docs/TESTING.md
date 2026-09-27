# Testing & Verification

How to run the automated suite, what it covers, and how to verify the stack
manually. The suite runs **fully offline**: a throwaway SQLite database, the
local storage backend, the deterministic mock AI/embedding providers and
**synthetic documents only** — no network, no API keys, no OCR binaries, no real
patient data.

---

## 1. Running the suite

From the repository root, with the backend dependencies installed
(see [SETUP.md](SETUP.md)):

```bash
pytest                      # 49 tests, quiet mode (pytest.ini)
pytest tests/test_auth_flow.py            # a single module
pytest -k "safety or neutral"             # filter by name
pytest -x -vv                             # stop at first failure, verbose
```

`pytest.ini` sets `testpaths = tests`, `asyncio_mode = auto` (async test
functions need no decorator) and quiet output.

Frontend checks (from `frontend/`):

```bash
npm run lint                # ESLint
npm run build               # TypeScript type-check + production build
```

---

## 2. Environment the suite pins

`tests/conftest.py` sets these **before importing any application module**, so
tests can never hit your development database, your `.env` or a real provider:

| Variable | Test value | Why |
| --- | --- | --- |
| `ENVIRONMENT` | `test` | Keeps production-only behaviour (Secure cookies, HSTS) off |
| `DATABASE_URL` | SQLite file in a temp dir | Throwaway schema per test |
| `STORAGE_LOCAL_DIR` | temp dir | Uploads never touch `backend/var` |
| `AI_PROVIDER` / `EMBEDDING_PROVIDER` | `mock` | Deterministic, offline, no keys |
| `AUTH_DEV_RETURN_RESET_TOKEN` | `true` | Lets the reset cycle be tested |
| `RATE_LIMIT_ENABLED` | `false` | Limits are tested explicitly, not incidentally |
| `AUTO_ANALYZE_AFTER_EXTRACTION` | `true` | Pipeline reaches `completed` in one job |

**Automatic fixtures (every test):**

- `fresh_db` — drops and recreates the full schema, resets the rate limiter and
  disposes the engine afterwards (each async test may get its own event loop).
- `no_background_jobs` — replaces `enqueue_processing` / `enqueue_analysis` with
  no-ops, so the pipeline is driven explicitly via `process_report_job(report_id)`
  and never races the test.

**Opt-in fixtures:**

| Fixture | Provides |
| --- | --- |
| `client` | `httpx.AsyncClient` wired to the real ASGI app (no socket) |
| `auth_headers` | Registers `TEST_USER` (`test.user@example.com`) and returns its Bearer header |
| `db_session` | Direct SQLAlchemy async session for service-level asserts |
| `seeded_kb` | Indexes the bundled WHO/NIH/CDC knowledge base (with `force=True`) so RAG citations are non-empty |

**Helpers:** `upload_and_process(client, headers, text)` renders synthetic text
into a real PDF (`tests/fixtures/sample_reports.py::build_pdf_bytes`, via fpdf),
uploads it and runs the pipeline — returning the report id.

---

## 3. What each module covers

| Module | Focus |
| --- | --- |
| `test_auth_flow.py` (9) | Register/login/me, duplicate email, weak password, wrong password, 401 on protected routes, refresh rotation + logout revocation, full forgot → reset → old-password-rejected → replay-rejected cycle, unknown email gets a generic response, profile update, change password |
| `test_upload_pipeline.py` (10) | Rejects non-PDF content (magic bytes) and unsupported types; full upload → extract → analyze flow (statuses, ranges, counts, sources with score + snippet); analysis reuse when nothing changed; `PATCH /results` recalculates status, marks `is_user_edited`, sets `analysis_stale`; ownership isolation (404 for other users); cascade delete; authenticated file download; rate limiter actually fires when enabled; fixture sanity |
| `test_parser.py` (6) | Deterministic parsing: CBC/Lipid values, units, ranges, statuses (`below`/`within`/`above`), confidence ≥ 0.7, source pages; high-only ranges; thousands separators; `compute_status` boundaries (inclusive edges); cleaner drops page furniture, normalises en-dashes and non-breaking spaces; multi-page source attribution |
| `test_storage.py` (4) | Local backend round-trip (streamed save with size + SHA-256, read, chunked read, delete, idempotent delete); max-size enforcement; user-namespaced keys; path-traversal rejection |
| `test_chat_safety.py` (5) | Educational question answered (`safety_blocked=false`, disclaimer present); prescription request blocked; diagnosis request blocked; report context + session continuity (4 messages persisted); session privacy + deletion |
| `test_compare_trends.py` (5) | Neutral compare wording (no "improved/worse/better/good/bad" anywhere; "changed from X to Y"; neutral note present); foreign reports rejected; trends with 2 chronological points; dashboard stats (totals, outside-range count, recent reports); history search + status filter |
| `test_export.py` (3) | Export is a real PDF containing the summary title, the value, "below range", the disclaimer ("not a diagnosis"), page citation, **real** source URL (`medlineplus.gov`) and the emergency note; auth required; report without analysis still exports with an explicit "No AI-generated summary" line |
| `test_demo_mode.py` (4) | `/api/demo/kinds` public, four kinds, all flagged synthetic; loading `cbc` parses through the same pipeline (`is_demo=true`, ≥ 8 tests); load requires auth; unknown kind → 422 |
| `test_health.py` (3) | `/health` checks database + configured providers; security headers present; unknown route → structured error envelope with `request_id` |

Total: **49 tests.**

---

## 4. What the automated suite does not cover

The suite is offline by design. These paths are **verified manually** (see §5)
or exercised only in production configuration:

| Not covered | Why | How to verify |
| --- | --- | --- |
| OCR (Tesseract/Paddle) on scanned images | OCR binaries are optional and machine-specific | Upload a scanned/photographed report with a real provider installed |
| Real LLM / embedding endpoints | Require API keys and network | Set `AI_PROVIDER=openai` + key; run one analysis and confirm `provider` in the response |
| S3-compatible storage | Requires a bucket | Set `STORAGE_BACKEND=s3` + credentials; upload and download once |
| Email delivery for password reset | No email provider is bundled | With `AUTH_DEV_RETURN_RESET_TOKEN=true`, use the returned link |
| Browser E2E (clicks, file pickers) | Kept out of the suite | Use the manual smoke flow in §5 |

---

## 5. Manual smoke verification

### 5.1 Backend starts and serves

```bash
uvicorn backend.main:app --port 8000
curl http://127.0.0.1:8000/health          # {"status":"ok", ...}
curl http://127.0.0.1:8000/api/docs        # development only (disabled in production)
```

### 5.2 End-to-end through the frontend proxy

With the backend running and the frontend dev server (`npm run dev`) on
port 3000, using `http://localhost:3000/api/...` (same paths as
[API.md](API.md)):

1. Register a new account; confirm you land signed in.
2. Load the CBC demo report; watch the status move
   `uploaded → processing → extracting → extracted → analyzing → completed`.
3. Open the report: values table shows statuses, the explanation shows counts,
   sources are clickable and the disclaimer is visible.
4. Edit one value → status recalculates, "analysis out of date" banner appears →
   rerun the analysis.
5. Compare the demo report with a second report (2–6): wording must stay neutral.
6. Trends: pick a test present in both reports; the chart shows a reference band
   and never labels direction as good/bad.
7. Chat: ask "What does hemoglobin measure?" (answered) and "Should I take iron
   tablets?" (blocked with `safety_blocked: true`).
8. Export the summary PDF; confirm the disclaimer and source URLs are inside.
9. Delete the report; confirm the dashboard counts decrease.

### 5.3 Frontend production build

```bash
cd frontend
npm run build               # must stay green (type-check + build)
npm run start               # optional: serve the production build locally
```

### 5.4 Docker stack (first run on a new machine)

```bash
docker compose up --build
# frontend → http://localhost:3000, backend → http://localhost:8000/health
```

Images are provided but, like any container artifact, should be smoke-tested on
the machine that will run them. Compose defaults `ENVIRONMENT=development` so the
refresh cookie works over plain-HTTP localhost; behind TLS set
`ENVIRONMENT=production`.

---

## 6. Milestone verification record

Each milestone was verified before it was considered done:

| Area | Evidence |
| --- | --- |
| M1–M2 core + auth | Test suite auth module + manual register/login through the proxy |
| M3 upload | Upload validation tests + real 20 MB limits |
| M4–M6 extraction | Parser tests + a real generated PDF processed end-to-end |
| M7–M8 AI + RAG | Mock provider produces the full contract; seeded KB yields scored, cited sources |
| M9 pipeline | Status walk observed live (demo report) |
| M10 chat | Safety-block tests + manual blocked question |
| M11–M13 API extras | Compare/trends/export/demo/admin tests (49/49 green) |
| Frontend | `npm run build` green; dev-server smoke of landing/auth/dashboard/report/trends/export |
| Docker + docs | Compose file validated; images untested on the authoring machine (no Docker available) — run §5.4 |

---

## 7. Continuous integration sketch

Any CI runner that has Python 3.12 and Node 20+ can run, in order:

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
pytest --junitxml=report.xml

cd frontend
npm ci
npm run lint
npm run build
```

No secrets are required: the suite uses mock providers, SQLite and local storage.
