# Security & Privacy

MediLens AI handles medical documents, so the security model is deliberately
explicit. This document describes what is implemented; the production-hardening
checklist at the end lists what operators must still do.

---

## 1. Authentication

- **Passwords** are hashed with **bcrypt** (`backend/core/security.py`); the
  plaintext is never stored or logged. Password change requires the current
  password and revokes **all** sessions.
- **Access tokens** are short-lived JWTs (HS256, default 15 min) sent as
  `Authorization: Bearer`. The frontend keeps them **in memory only** — never in
  localStorage — so XSS cannot exfiltrate a long-lived credential.
- **Refresh sessions** are opaque random tokens stored server-side (`UserSession`
  rows) and delivered as an **HttpOnly** cookie (`medilens_refresh`) scoped to
  `path=/api/auth`, `SameSite=Lax`, `Secure` when `ENVIRONMENT=production`.
  Refreshing **rotates** the token; logout, password change, reset and explicit
  session revocation invalidate server-side state immediately.
- **Password reset** uses single-use, expiring tokens (default 30 min); the
  forgot-password endpoint always returns the same generic response, so account
  existence cannot be probed. No email provider is bundled — see
  [SETUP.md](SETUP.md) §6.
- Sessions are listed (with device/last-used/expiry) and individually revocable
  from Settings.

## 2. CSRF, XSS and transport headers

- **CSRF:** the only cookie-authenticated endpoints are `/api/auth/refresh` and
  `/api/auth/logout`; both verify the request `Origin` before acting
  (`verify_request_origin`), and the cookie is `SameSite=Lax` + path-scoped. All
  data endpoints use the `Authorization` header, which browsers never attach
  cross-site automatically.
- **XSS:** React escapes all rendered content; the app renders no
  `dangerouslySetInnerHTML`. Uploaded documents are never served as HTML —
  originals are streamed with their original content type and
  `X-Content-Type-Options: nosniff`.
- **Response headers** (`SecurityHeadersMiddleware`): `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`
  (geo/mic/camera off), `Cross-Origin-Opener-Policy: same-origin`,
  `Cross-Origin-Resource-Policy: same-site`, a restrictive CSP
  (`default-src 'none'; frame-ancestors 'none'`), plus **HSTS** in production.
- **Body-size guard:** requests declaring more than the upload limit +1 MB of
  multipart overhead are rejected with `413` before touching handlers.

## 3. Rate limiting

Fixed-window, in-memory limiter (`backend/core/rate_limit.py`), keyed per user or
per client IP where unauthenticated:

| Rule | Limit |
| --- | --- |
| Login / register / password reset | 12 / minute |
| Report upload | 20 / minute |
| Process / analyze | 30 / minute |
| Chat messages | 40 / minute |
| PDF export | 15 / minute |

The interface is intentionally tiny so a Redis-backed limiter can replace it for
multi-worker deployments. `RATE_LIMIT_ENABLED=false` disables it (tests only).

## 4. File handling

- Uploads are validated **before** storage: extension + declared MIME allow-list
  (PDF/JPG/PNG), size limit (`MAX_UPLOAD_SIZE_MB`, default 20), and content
  sniffing (magic bytes) — renamed or corrupted files are rejected with a clear
  message.
- Files are stored under server-controlled names in the storage backend (local
  directory in dev, S3-compatible in production), **not** in any web-served
  directory. Downloads go through `GET /api/reports/{id}/file`, which enforces
  ownership and writes an audit entry.
- Every report endpoint is ownership-scoped: a report id belonging to another
  user yields `404` (no existence oracle).
- Deleting a report deletes its stored file, pages, values, analyses and
  citations (cascade).

## 5. Audit trail & observability

Sensitive actions are written to the `AuditLog` table with actor, entity, IP and
user agent where applicable: register, login, failed login, logout, password
reset requested/completed, profile updated, report uploaded/analyzed/deleted/
file-downloaded, knowledge document changes. Requests carry an `X-Request-ID`
that appears in responses, logs and error envelopes. Logs are available in plain
or JSON form (`LOG_JSON`); **request bodies and query strings are not logged**,
and file contents are never logged.

## 6. Data privacy

- Uploaded reports, extracted values, analyses and chat history are visible only
  to the owning account (admin endpoints expose aggregate counts and, for user
  management, basic account metadata — not report contents).
- The only outbound calls are to the **AI/embedding provider you configure**.
  With the default `mock` providers, the stack is fully offline.
- Deleting a report removes the original file and all derived data. Data
  retention is user-driven (`DATA_RETENTION_DAYS=0` = keep until deleted).
- Demo reports and all test fixtures are entirely synthetic.

## 7. Medical-safety guardrails (security-adjacent)

The product-level safety rules are part of the threat model — an unguarded
medical assistant itself is a harm:

- Prompts forbid diagnosis, prescriptions, certainty claims and invented
  citations.
- `ai/safety.py` blocks chat requests seeking a diagnosis or medication advice
  and returns a safe refusal (`safety_blocked: true`).
- Analyses are grounded in extracted values (with confidence) + retrieved,
  cited public-health sources only; when retrieval finds nothing,
  `retrieval_note` says so and no source is fabricated.
- The UI carries the disclaimer on data-heavy pages, an emergency notice near
  results, neutral status/compare wording, low-confidence value flags and a
  "Demo" label on synthetic data. Exports include the disclaimer.

## 8. Production-hardening checklist

- [ ] Set a strong `JWT_SECRET` (e.g. `python -c "import secrets; print(secrets.token_urlsafe(64))"`).
- [ ] `ENVIRONMENT=production` (enables Secure cookies + HSTS; disables `/api/docs`).
- [ ] Serve over **HTTPS/TLS** (reverse proxy) — the refresh cookie requires it.
- [ ] `LOG_JSON=true` and ship logs to your aggregator; keep `X-Request-ID` correlation.
- [ ] Keep `AUTH_DEV_RETURN_RESET_TOKEN=false` and wire a real email sender.
- [ ] Restrict `CORS_ORIGINS` to your real frontend origin(s).
- [ ] Use PostgreSQL with dedicated credentials; run `alembic upgrade head`;
      `AUTO_CREATE_SCHEMA=false`.
- [ ] Move uploads to S3-compatible storage with a **private** bucket
      (`STORAGE_BACKEND=s3`); keep bucket credentials out of the image.
- [ ] Provide AI/embedding keys via the platform's secret manager, never in
      `.env` files in the repo or the image.
- [ ] Replace the in-memory rate limiter with a shared store if you run more than
      one worker.
- [ ] Back up the database and storage volume; test a restore.
- [ ] Review the medical disclaimer text with a qualified professional for your
      jurisdiction before public launch.
