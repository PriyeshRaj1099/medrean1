# MediLens AI — Intelligent Medical Report Analyzer

MediLens AI helps people **understand their own lab reports**. You upload a PDF or
photo of a blood test; MediLens reads every value, compares it with the reference
range **printed on your own report**, and explains what the values mean in plain
language — in English, Hindi or Hinglish — backed by real, cited public-health
sources (WHO, NIH/MedlinePlus, CDC).

> ### ⚠️ Important medical disclaimer
>
> MediLens AI is an **educational and informational tool only**. It is **not a
> medical device**, does **not diagnose** any condition, does **not prescribe or
> recommend medication or dosages**, and is **not a substitute for a qualified
> healthcare professional**. Always discuss your results with your doctor.
>
> **For urgent symptoms** (e.g. chest pain, difficulty breathing, severe
> bleeding, confusion), contact your local **emergency services immediately**.
>
> Demo reports and test fixtures in this repository are **entirely synthetic** —
> they do not belong to any real person.

---

## What it does

| Capability | Description |
| --- | --- |
| **Upload & validation** | Drag-and-drop PDF / JPG / PNG (max 20 MB), validated by type, size and file integrity before processing. |
| **Text & OCR extraction** | Text-layer PDFs via PyMuPDF; scanned documents/photos via pluggable OCR (Tesseract or PaddleOCR), with per-page OCR confidence. |
| **Value extraction** | Test name, numeric/text value, unit and the **printed reference range** are extracted with a confidence score and the source page; every value is editable ("Edit Results"). |
| **AI analysis** | Structured JSON explanation: summary, values outside/within the printed range, educational context, questions to ask your doctor, limitations and cited sources. |
| **RAG with real sources** | A seeded knowledge base of public-health documents (WHO / NIH-MedlinePlus / CDC) is chunked, embedded and retrieved for citations. Sources are **never invented** — if nothing relevant is found, the app says so. |
| **Chat assistant** | Ask general questions about lab tests or a specific report. Safety guardrails block diagnosis/prescription requests instead of answering them. |
| **Trends** | Follow a value across reports over time with a neutral chart (reference band shown; direction never labelled good/bad). |
| **Compare** | Side-by-side comparison of 2–6 reports with strictly neutral change wording ("stayed at", "increased", "decreased"). |
| **PDF export** | Downloadable plain-summary PDF (with the disclaimer) for each report. |
| **Multilingual** | Interface and AI explanations in **English, Hindi and Hinglish**. |
| **Dashboard & history** | Stats, searchable/filterable report history, delete (removes the stored file too). |
| **Demo mode** | Four clearly-labelled synthetic demo reports (CBC, lipid, thyroid, metabolic) for exploring the full flow safely. |
| **Admin** | System stats, user overview, knowledge-base browser with document add/delete and one-click re-index. |

### What MediLens will never do

- Diagnose a condition or confirm that you have one
- Recommend, start or stop a medication or dosage
- Tell you a result is "nothing to worry about"
- Replace an examination by a qualified healthcare professional

---

## Tech stack

- **Frontend** — Next.js 16 (App Router, Turbopack) · React 19 · TypeScript (strict) ·
  Tailwind CSS v4 · Radix UI primitives · Recharts · next-themes · sonner.
  The browser talks only to the Next.js origin; `/api/*` is proxied to the backend
  (same-origin, no CORS, HttpOnly refresh cookie works unchanged).
- **Backend** — Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic.
- **Database** — PostgreSQL in production (`asyncpg`); SQLite (`aiosqlite`) for
  zero-setup local development and tests.
- **Document processing** — PyMuPDF (text PDFs), Tesseract / PaddleOCR (scans), Pillow.
- **AI** — provider abstraction with two implementations: a deterministic **offline
  mock engine** (no API key, clearly labelled) and an **OpenAI-compatible** provider
  for real models. Prompts live as separate Markdown files in `/prompts`.
- **RAG** — in-repo chunking, embeddings (mock hashing or OpenAI-compatible),
  database-backed vector store, seed knowledge base with metadata filtering.
- **Auth** — JWT access tokens (in-memory on the client) + rotating refresh
  sessions in an HttpOnly cookie; password hashing with bcrypt.

---

## Repository layout

```
medrean/
├── ai/                    # LLM + embedding providers, prompt loader, safety rules
├── backend/
│   ├── api/               # FastAPI routers: auth, reports, analysis, chat, dashboard,
│   │                      #   export, demo, knowledge, admin, health
│   ├── core/              # config, logging, errors, security, middleware, rate limiting
│   ├── jobs/              # background job queue (in-process, async)
│   ├── schemas/           # Pydantic request/response models
│   └── services/          # business logic (auth, reports, analysis, chat, compare,
│                          #   dashboard, export, demo, knowledge, audit)
├── database/
│   ├── migrations/        # Alembic environment + revisions
│   └── models/            # SQLAlchemy models (users, reports, values, analyses, chat, KB)
├── document_processing/   # PDF text extraction, cleaning, segmentation, medical parser
├── docs/                  # SETUP · API · ARCHITECTURE · SECURITY · TESTING
├── frontend/              # Next.js application (src/app, src/components, src/lib)
├── ocr/                   # OCR provider interface + Tesseract / PaddleOCR providers
├── prompts/               # Versioned prompt templates (analysis + chat)
├── rag/                   # chunking, embeddings store, retriever, seed knowledge base
├── scripts/               # seed_knowledge.py · create_admin.py · generate_sample_pdf.py
├── tests/                 # pytest suite + synthetic fixtures
├── docker-compose.yml     # PostgreSQL + backend + frontend
└── .env.example           # every configuration variable, documented
```

---

## Quickstart (local development)

**Prerequisites:** Python 3.12+, Node.js 20+ (22 recommended), and — only if you
want OCR for scanned/photographed reports — the Tesseract binary
([install guide](https://tesseract-ocr.github.io/)). Text-layer PDFs work without it.

### 1. Backend

```bash
# from the repository root
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r backend/requirements.txt -r backend/requirements-dev.txt

cp .env.example .env               # then edit JWT_SECRET (see below)
alembic upgrade head               # create the database schema

python scripts/seed_knowledge.py     # index the bundled WHO/NIH/CDC documents (RAG)
python scripts/seed_demo_account.py  # create/verify the shared demo account (idempotent)

uvicorn backend.main:app --reload --port 8000
```

- API: `http://127.0.0.1:8000/api` · interactive docs: `http://127.0.0.1:8000/api/docs`
- Health: `http://127.0.0.1:8000/health`
- Generate a strong secret: `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- The default dev database is SQLite (`backend/var/medilens_dev.db`) and
  `AUTO_CREATE_SCHEMA=true` also creates tables on startup — convenient locally;
  run `alembic upgrade head` for PostgreSQL or production use.
- **Re-indexing the knowledge base is a one-time (or on-change) step** — it is not
  run automatically on startup. Admins can also trigger it from the Admin page.

Optional admin account (needed for the Admin section):

```bash
python scripts/create_admin.py --email admin@example.com --password "Str0ngPass1" --name "Admin"
# or promote an existing account:
python scripts/create_admin.py --email you@example.com --promote
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000, proxies /api to http://127.0.0.1:8000
```

That's it — open `http://localhost:3000`, create an account, and either upload a
report or load one of the synthetic demo reports.

---

## Configuration

All configuration is environment-based; see [`.env.example`](.env.example) for the
full annotated list. The most important variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | SQLite file in `backend/var/` | `postgresql+asyncpg://…` in production |
| `JWT_SECRET` | — (**must be set**) | Signing key for access/refresh tokens |
| `AI_PROVIDER` | `mock` | `mock` (offline, deterministic) or `openai` (any OpenAI-compatible API) |
| `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` | — / OpenAI / `gpt-4o-mini` | Real LLM configuration (OpenAI, Azure-compatible gateways, OpenRouter, Groq, Ollama, vLLM…) |
| `EMBEDDING_PROVIDER` | `mock` | `mock` (offline hashing) or `openai` |
| `OCR_PROVIDER` | `tesseract` | `tesseract`, `paddle` or `none` |
| `STORAGE_BACKEND` | `local` | `local` (files under `backend/var/uploads`) or `s3` (S3-compatible) |
| `MAX_UPLOAD_SIZE_MB` | `20` | Upload size limit (enforced by backend and UI) |
| `AUTH_DEV_RETURN_RESET_TOKEN` | `false` | Dev-only: return the password-reset link in the forgot-password response (no email provider is bundled). **Never enable in production.** |
| `DEMO_ACCOUNT_ENABLED` | `true` | Shared demo account on the sign-in page (create it with `python scripts/seed_demo_account.py`) |
| `LOG_JSON` | `false` | Structured JSON logs (recommended in production) |

### AI providers

- **`mock` (default)** — a deterministic, offline engine that produces complete,
  schema-valid structured analyses from the extracted values and the retrieved
  knowledge base. It is clearly labelled as `mock` in the API response and on the
  UI. Nothing is faked silently: the provider name is always visible.
- **`openai`** — set `AI_PROVIDER=openai`, `AI_API_KEY` and optionally
  `AI_BASE_URL` / `AI_MODEL` to use any OpenAI-compatible chat-completions API.
  The same prompt files (`/prompts/*.md`) and the same structured-JSON contract
  are used for both providers.

### OCR

Scanned reports (photos, scan-only PDFs) need OCR:

- **Tesseract** — install the binary and keep `OCR_PROVIDER=tesseract`.
- **PaddleOCR** — `pip install paddleocr paddlepaddle` and set `OCR_PROVIDER=paddle`.
- **`none`** — disable OCR; such files will fail with a clear error message.

---

## Demo mode

No report handy? The landing/upload pages can load **synthetic demo reports**
(CBC, lipid panel, thyroid, metabolic panel). Demo reports are clearly labelled
with a `Demo` badge and a notice that the data is entirely synthetic.

### Shared demo account

For a guided first look without signing up, the sign-in page offers a
**Try demo account** button (also reachable as `/login?demo=1`). It fills in the
publicly documented shared account — and signing in still goes through the
normal authentication pipeline, exactly like a real account:

| | |
| --- | --- |
| Email | `demo@medilens.ai` |
| Password | `MediLens@Demo2026!` |

The account is created (or verified) by an **idempotent seed** — run it during
setup and again after every deployment:

```bash
python scripts/seed_demo_account.py                    # create if missing, verify otherwise
python scripts/seed_demo_account.py --reset-password   # restore the documented password
```

- The seed **never duplicates** the account and **never overwrites** its password
  unless `--reset-password` is passed. It also creates (or reuses) four fully
  analyzed synthetic reports.
- The account carries the non-admin role `demo`: it can explore reports,
  analyses, trends and chat, but it **cannot upload reports, change its
  password, delete itself or manage sessions** — those actions return a clear
  "create your own free account" message. It can never access the Admin section.
- The frontend fetches the credentials from `GET /api/demo/account` at runtime
  (never hardcoded in the UI). Configure or disable the account with the
  `DEMO_ACCOUNT_*` variables in `.env` (`DEMO_ACCOUNT_ENABLED=false` hides it).
- **Deployments need a persistent database.** On SQLite inside a throwaway
  container the account — like every other account — disappears on redeploy.
  Use PostgreSQL in production and run the seed as part of deployment.

---

## Docker

The full stack (PostgreSQL + backend + frontend) runs with Compose. Docker images
are provided but, like any container artifact, they should be smoke-tested on your
machine before production use:

```bash
docker compose up --build
# frontend → http://localhost:3000
# backend  → http://localhost:8000/api  (health: /health)
```

- The frontend image bundles the backend URL into the `/api/*` proxy at **build
  time** (`BACKEND_URL` build arg, default `http://backend:8000`).
- The backend container runs `alembic upgrade head` before starting.
- `ENVIRONMENT` defaults to `development` in Compose so the refresh cookie also
  works over plain-HTTP localhost. Behind TLS, set `ENVIRONMENT=production` —
  the refresh cookie then becomes `Secure`-only (as it should be).
- Keep `AI_PROVIDER=mock` / `EMBEDDING_PROVIDER=mock` to run without any API key,
  or pass the keys through your shell environment / a root `.env` file.
- Uploads persist in the `backend_var` volume; the database in `db_data`.

---

## Testing

```bash
pytest                      # 118 tests: auth, demo account, upload→pipeline, parser,
                            #   chat safety, compare/trends, export, storage, health,
                            #   frontend i18n + auth-UI source checks
cd frontend
npm run lint                # ESLint
npm run build               # type-check + production build
```

The test suite uses synthetic fixtures only (including generated sample PDFs) and
runs fully offline with the mock AI/embedding providers. See
[docs/TESTING.md](docs/TESTING.md) for details.

---

## Documentation

| Document | Contents |
| --- | --- |
| [docs/SETUP.md](docs/SETUP.md) | Detailed setup, database options, scripts, troubleshooting |
| [docs/API.md](docs/API.md) | REST API reference (auth, reports, analysis, chat, trends, admin) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, processing pipeline, providers, RAG flow |
| [docs/SECURITY.md](docs/SECURITY.md) | Authentication model, privacy, medical-safety guardrails |
| [docs/TESTING.md](docs/TESTING.md) | Test suite layout and how to verify each milestone |

---

## Privacy summary

Your reports are visible only to your account. Uploaded files are stored privately
(local directory in development, S3-compatible storage in production), sensitive
actions are written to an audit log, and deleting a report also deletes its stored
file, extracted values and analyses. No data is sold or shared, and the app never
sends your data anywhere except the AI/embedding provider **you** configure.

## License & attribution

Knowledge-base documents are derived from public-domain / openly-licensed public
health resources (WHO, NIH/MedlinePlus, CDC) — each entry carries its source
organisation, URL and a license note. MediLens AI is an educational project; use
it responsibly and always involve a qualified healthcare professional in medical
decisions.
