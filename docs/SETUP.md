# Setup Guide

Everything needed to run MediLens AI locally, with PostgreSQL, or via Docker.
See the [README](../README.md) for the overview and the medical disclaimer.

---

## 1. Prerequisites

| Tool | Version | Needed for |
| --- | --- | --- |
| Python | 3.12+ | backend, tests, scripts |
| Node.js | 20+ (22 recommended) | frontend |
| PostgreSQL | 15+ | production-like database (optional locally — SQLite is the default) |
| Tesseract OCR | latest | scanned / photographed reports (optional — text PDFs work without it) |
| Docker + Compose | latest | the container stack (optional) |

---

## 2. Backend (local development)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cp .env.example .env                 # then edit at least JWT_SECRET
```

**Create the schema.** Two options:

```bash
alembic upgrade head                 # recommended, works for SQLite and PostgreSQL
```

or simply start the app with `AUTO_CREATE_SCHEMA=true` (default in
`.env.example`) — the lifespan hook creates tables from the models. Use Alembic
for anything production-like.

**Index the knowledge base (one-time / on-change).** The RAG knowledge base ships
as `rag/knowledge/seed_knowledge.json` and is *not* indexed automatically at
startup:

```bash
python scripts/seed_knowledge.py            # index new/changed documents
python scripts/seed_knowledge.py --force    # full re-chunk + re-embed
```

Admins can trigger the same re-index from the Admin page later.

**Run:**

```bash
uvicorn backend.main:app --reload --port 8000
```

- API base: `http://127.0.0.1:8000/api`
- Interactive docs (non-production only): `http://127.0.0.1:8000/api/docs`
- Health: `http://127.0.0.1:8000/health`

### Optional: admin account

```bash
python scripts/create_admin.py --email admin@example.com --password "Str0ngPass1" --name "Admin"
python scripts/create_admin.py --email existing@example.com --promote
```

### Optional: sample PDF for manual upload testing

```bash
python scripts/generate_sample_pdf.py
```

This writes a synthetic laboratory-report PDF; the same generator is used by the
test suite. (Demo reports loadable in the UI are created server-side — no file
needed.)

### Using PostgreSQL locally

Set in `.env`:

```
DATABASE_URL=postgresql+asyncpg://medilens:medilens@localhost:5432/medilens
AUTO_CREATE_SCHEMA=false
```

then `alembic upgrade head`. The Alembic environment reads `DATABASE_URL` from the
application settings, so migrations always target the same database as the app.

---

## 3. Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
```

The browser only talks to the Next.js origin. `/api/*` is rewritten to the
backend (`BACKEND_URL`, default `http://127.0.0.1:8000`), which keeps everything
same-origin — no CORS, and the HttpOnly refresh cookie (`path=/api/auth`) is
stored and sent normally.

| Script | Purpose |
| --- | --- |
| `npm run dev` | development server (Turbopack) |
| `npm run build` | type-check + production build (standalone output) |
| `npm run start` | serve the production build |
| `npm run lint` | ESLint |

To point the dev server at another backend:

```bash
# frontend/.env.local
BACKEND_URL=http://192.168.1.20:8000
```

> The rewrite target is read from `process.env.BACKEND_URL` when Next.js starts.
> In the **Docker** image it is baked in at build time via a build arg — see
> `docker-compose.yml`.

---

## 4. Docker (full stack)

```bash
docker compose up --build
# frontend → http://localhost:3000
# backend  → http://localhost:8000/api   (health: http://localhost:8000/health)
```

What the stack does:

1. **db** — PostgreSQL 16 with a named volume and a `pg_isready` healthcheck.
2. **backend** — builds `backend/Dockerfile` (installs Tesseract), runs
   `alembic upgrade head`, then serves uvicorn. Waits for the database to be
   healthy. Uploads persist in the `backend_var` volume.
3. **frontend** — builds `frontend/Dockerfile` (multi-stage, Next.js standalone
   server). `BACKEND_URL=http://backend:8000` is compiled into the `/api/*`
   rewrite. Waits for the backend healthcheck.

Notes:

- `ENVIRONMENT` defaults to `development` in the Compose file **so that the
  refresh cookie works over plain-HTTP localhost**. Behind TLS (any real
  deployment) set `ENVIRONMENT=production` — the refresh cookie becomes
  `Secure`-only, which is the correct behaviour.
- Set `JWT_SECRET` in the shell or a root `.env` before using anything beyond
  local testing.
- AI and embeddings default to the offline mock providers, so no API key is
  needed. To use a real provider, export the variables and rebuild:

  ```bash
  AI_PROVIDER=openai AI_API_KEY=sk-... AI_MODEL=gpt-4o-mini docker compose up --build
  ```

- Index the knowledge base inside the running backend container once:

  ```bash
  docker compose exec backend python scripts/seed_knowledge.py
  ```

- Docker images are provided but should be smoke-tested on your machine /
  CI before production use; the Compose file is aimed at local full-stack runs.

---

## 5. Environment variables

The annotated master list is [`.env.example`](../.env.example). Frequently used:

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `production` enables safe-cookie mode, JSON logs recommended, hides API docs |
| `DEBUG` | `true` | turn off for production |
| `FRONTEND_URL` | `http://localhost:3000` | used in reset links |
| `DATABASE_URL` | SQLite in `backend/var/` | `postgresql+asyncpg://…` for PostgreSQL |
| `AUTO_CREATE_SCHEMA` | `true` | dev convenience; `false` + Alembic in production |
| `JWT_SECRET` | — | **must be set**; long random value |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | `15` / `30` | token lifetimes |
| `AUTH_DEV_RETURN_RESET_TOKEN` | `false` | dev-only password-reset helper (no email provider bundled) |
| `CORS_ORIGINS` | `http://localhost:3000` | only matters for non-proxied API clients |
| `STORAGE_BACKEND` / `STORAGE_LOCAL_DIR` | `local` / `backend/var/uploads` | or `s3` + bucket/keys |
| `MAX_UPLOAD_SIZE_MB` | `20` | enforced by backend and UI |
| `AI_PROVIDER` / `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` | `mock` | `openai` = any OpenAI-compatible API |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | `mock` / `text-embedding-3-small` | `openai` for real embeddings |
| `OCR_PROVIDER` / `OCR_LANGUAGES` / `OCR_DPI` | `tesseract` / `eng` / `250` | `paddle` or `none` as alternatives |
| `RAG_TOP_K` / `RAG_MIN_SCORE` / `RAG_CHUNK_SIZE` | `6` / `0.12` / `900` | retrieval tuning |
| `LOW_CONFIDENCE_THRESHOLD` | `0.7` | values below are flagged "verify this value" |
| `AUTO_ANALYZE_AFTER_EXTRACTION` | `true` | run analysis automatically once extraction completes |
| `LOGGING_LEVEL` / `LOG_JSON` | `INFO` / `false` | structured logs for production |
| `RATE_LIMIT_ENABLED` | `true` | in-memory fixed-window limiter |

---

## 6. Email / password resets

No email provider is bundled. In development, set
`AUTH_DEV_RETURN_RESET_TOKEN=true` and the forgot-password response (and the UI)
will surface a ready-to-use reset link. In production, plug an email sender into
`backend/services/auth_service.py`'s token flow and keep the flag off — the API
already returns a generic response either way (no account enumeration).

---

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `401` immediately after login | Check that the frontend talks to the backend through the Next.js proxy (`/api/*`) and that `BACKEND_URL` points at the API. Direct cross-origin calls skip the refresh cookie path (`/api/auth`). |
| Login works but the session never restores after refresh | The refresh cookie is `Secure` in production mode. Over plain HTTP set `ENVIRONMENT=development`, or serve through HTTPS. |
| Scanned PDFs fail with an extraction error | Install Tesseract (or set `OCR_PROVIDER=paddle` / `image` support) — photos and scans need OCR. |
| Analysis returns "no sources found" | The knowledge base has not been indexed: run `python scripts/seed_knowledge.py`. |
| `429 Too many requests` while testing | In-memory rate limiter; wait a minute or set `RATE_LIMIT_ENABLED=false` for the test run. |
| Upload rejected | Check the file type (PDF/JPG/PNG), the 20 MB limit, and that the file is not corrupted/encrypted. |
| `docker compose` build fails on the frontend | Ensure Docker has enough memory and that `frontend/package-lock.json` is present (it is committed). |
