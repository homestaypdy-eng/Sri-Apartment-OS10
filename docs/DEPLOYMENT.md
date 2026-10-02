# Sri Apartment OS — Deployment Guide

## Quick Start (Docker Compose — recommended)

```bash
cp .env.example .env
# Edit .env: set a real POSTGRES_PASSWORD and JWT_SECRET_KEY at minimum.
# Generate a real secret with: python3 -c "import secrets; print(secrets.token_urlsafe(64))"

docker compose up --build
```

- Backend: `http://localhost:8000` (interactive API docs at `/docs`)
- Frontend: `http://localhost:5173`
- Default admin: `homestaypdy@gmail.com` / `Sri@2026` — **change this immediately after first login**

Postgres, backend, and frontend all start together; the backend waits for Postgres's health check before running migrations/seed, and the frontend waits for the backend's health check before starting.

**`docker compose up` will refuse to start** if `POSTGRES_PASSWORD` or `JWT_SECRET_KEY` aren't set in `.env` — this is intentional (Docker Compose's `${VAR:?message}` syntax), so a real deployment can't accidentally run with the old hardcoded placeholder values that used to live directly in `docker-compose.yml`.

## Environment Variables

All variables and their defaults are documented in `.env.example` at the project root. Copy it to `.env` (gitignored — never commit your real one) before running Compose. The important categories:

| Category | Variables | Behavior if unset |
|---|---|---|
| Database | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose refuses to start without `POSTGRES_PASSWORD` set |
| Core | `JWT_SECRET_KEY`, `CORS_ALLOWED_ORIGINS` | Compose refuses to start without `JWT_SECRET_KEY`; a startup warning also fires if `CORS_ALLOWED_ORIGINS` still includes `localhost` while `ENVIRONMENT=production` |
| Email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` | Email-send endpoints return `501 Not Configured` — no silent failure, no fake success |
| Razorpay | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | Order creation/refunds return `501`; webhook signature verification works independently once `RAZORPAY_WEBHOOK_SECRET` is set |
| Scheduler | `ENABLE_SCHEDULER`, `REMINDER_SCAN_INTERVAL_MINUTES` | Automatic reminder emails simply don't run (manual trigger via `POST /api/v1/notifications/scan-reminders` still works) |
| Frontend | `VITE_API_BASE_URL` | Build-time only (baked into the JS bundle at build) — must be the URL your users' browsers can actually reach, not just what's reachable from inside Docker |

## Manual (non-Docker) Setup

```bash
# Backend
cd backend
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/sri_apartment_os"
export JWT_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
python -m app.db.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run build   # or `npm run dev` for local development
```

Postgres needs the `btree_gist` and `pgcrypto` extensions — `app.db.seed` creates them automatically if the connecting user has permission (the default Docker Postgres image does).

## Health Checks

- `GET /health` — basic liveness (app name, environment)
- Docker Compose's backend health check hits this endpoint every 10s

## CI/CD

`.github/workflows/ci.yml` runs on every push/PR to `main`:
1. Spins up a real Postgres service container
2. Seeds the schema
3. Runs the full backend pytest suite
4. Separately: frontend type-check (`tsc --noEmit`) and production build

This is a genuinely runnable workflow (not a placeholder) — push it to a GitHub repo and it works as-is.

## Backup & Restore

**This exact procedure was tested during the production-readiness review** — a real backup was taken, restored into a fresh database, and its row counts verified to match, not just documented from assumption.

```bash
# Backup (run from anywhere with psql/pg_dump installed, pointed at your real connection)
pg_dump "postgresql://postgres:YOUR_PASSWORD@<host>:5432/sri_apartment_os" > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore into a NEW, empty database (never restore directly over a live one —
# create a fresh database first, verify the restored data, then swap connection strings)
createdb -U postgres -h <host> sri_apartment_os_restored
psql "postgresql://postgres:YOUR_PASSWORD@<host>:5432/sri_apartment_os_restored" -c "CREATE EXTENSION IF NOT EXISTS btree_gist; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
psql "postgresql://postgres:YOUR_PASSWORD@<host>:5432/sri_apartment_os_restored" < backup_20260710_120000.sql

# Verify before cutting over — compare a few real counts
psql "postgresql://postgres:YOUR_PASSWORD@<host>:5432/sri_apartment_os_restored" -c "SELECT count(*) FROM bookings; SELECT count(*) FROM payments;"
```

For managed Postgres (RDS, Cloud SQL, etc.), use the provider's automated snapshot feature instead of relying on manual `pg_dump` for your primary backup strategy — this runbook is for self-hosted deployments, ad-hoc backups, or as a supplement to provider snapshots.

**Backup schedule recommendation:** at minimum, run the backup command above weekly via cron once real guest/financial data exists, and after any major batch operation (bulk imports, mass edits). Store the `.sql` file somewhere other than the same disk as the database itself.

**Uploaded files (attachments, photos, generated PDFs, and guest ID documents)** are stored on local disk in named Docker volumes (`uploads_reports`, `uploads_attachments`, `uploads_hk_photos`, `uploads_maint_photos`, `uploads_expense_attachments`, `uploads_guest_docs` — see `docker-compose.yml`). `pg_dump` does **not** back these up — they need a separate file-level backup (e.g. `docker run --rm -v uploads_attachments:/data -v $(pwd):/backup alpine tar czf /backup/attachments_backup.tar.gz /data`, repeated per volume) or, for real production durability, migrating to S3-compatible object storage instead of local volumes (see "Known Limitations" in the README).

## Rollback Procedure

If a deployment introduces a problem:

1. **Application code rollback** (no schema changes involved): redeploy the previous Docker image / git commit. The current schema migration does not drop columns or rewrite business rows, but it does replace selected constraints and the policy audit trigger. Review those schema definitions before a rollback; an older application version should not be assumed compatible with every newer constraint.
2. **Database rollback** (if a bad migration or bulk operation corrupted data): restore from the most recent backup taken before the problem, following the Restore procedure above, into a new database, verify it, then repoint the application's `DATABASE_URL` at it.
3. **Never** roll back application code to a version *older* than your most recent schema change if that change removed or renamed a column the old code doesn't expect — check `app/db/additive_migrations.py`'s history first. In practice this hasn't happened in this project's migration history (every change so far has been purely additive), but it's the one rollback scenario worth checking before assuming "redeploy the old version" is safe.

## Railway Schema Migration Procedure

For Railway, use the one-off **Pre-deploy Command** for schema work:

```text
python -m app.db.migrate
```

Leave Railway's custom **Start Command unset**. The image `CMD` in
`backend/Dockerfile` starts Uvicorn directly, and should remain responsible
only for serving the application. Do not use `python -m app.db.seed` as a
Railway pre-deploy or start command: it also bootstraps users/RBAC and runs
legacy data migrations. Do not use the Compose `command` as evidence for
Railway behavior; Compose overrides apply only to Compose.

The migration command registers ORM metadata, creates the required
PostgreSQL extensions, creates missing tables, applies reviewed indexes and
the schema statements in `app.db.additive_migrations`, and exits non-zero
on failure. PostgreSQL runs the DDL in a transaction. The additive migration
set includes some schema-level constraint and trigger replacements; it is
not literally limited to `ADD COLUMN`. It does not run the separate
business-settings defaults, legacy bank-settings copy, customer phone
normalization, user bootstrap, or RBAC/reference-data seed paths. Review the
schema preflight and take a provider snapshot before configuring or running
this command in a Railway environment.

The repository does not configure Railway. After a reviewed staging rehearsal,
set only the Pre-deploy Command above and keep Start Command unset. Keep
`ENABLE_SCHEDULER=false` or unset during the migration/deployment.

## Local Upgrade Procedure

1. Pull the new code / image.
2. Take a real backup first (see above) — this costs a few minutes and is the difference between "annoying" and "catastrophic" if something goes wrong.
3. `docker compose up --build` — the local Compose override runs the full development bootstrap (`python -m app.db.seed`) before Uvicorn. This is for local development, not Railway deployment.
4. Run the health check (`curl http://localhost:8000/health`) and spot-check one real workflow (e.g., view an existing booking) before considering the upgrade complete.

## Monitoring & Logging

- Structured, timestamped logging is configured (`logging.basicConfig` in `app/main.py`) — visible in `docker compose logs backend` or wherever stdout is captured. Format: `<timestamp> <level> <logger name>: <message>`.
- A global exception handler logs the full stack trace of any unhandled error server-side while returning a generic, non-leaking message to the client — verified during the production-readiness review that normal `404`/`401` responses pass through unaffected, and only genuine unhandled exceptions are caught by it.
- **Not included**: APM/tracing (Sentry, DataDog), log aggregation (CloudWatch, ELK), uptime alerting. These are standard additions for any production FastAPI deployment and aren't specific to this system — wiring in Sentry, for example, is typically a few lines once you have a DSN, but isn't done here since it requires an external account this environment doesn't have.

## Production Hardening Checklist

- [ ] Copy `.env.example` to `.env` and set a real `POSTGRES_PASSWORD` and `JWT_SECRET_KEY` (Compose will refuse to start otherwise)
- [ ] Set real `SMTP_*` and `RAZORPAY_*` credentials if using those features
- [ ] Put the API and frontend behind HTTPS (terminate TLS at a load balancer/reverse proxy — not handled by the app or its bundled nginx config itself; add HSTS at that layer once TLS is live, not before)
- [x] Rate limiting on login is implemented (in-memory, single-process — see `app/shared/rate_limit.py`'s own docstring for the honest limitation: it resets on restart and doesn't share state across multiple backend replicas; a multi-replica deployment should replace it with a Redis-backed limiter)
- [ ] Add Alembic migrations before making further *destructive* schema changes in production (`create_all` plus the current migration-only command can add schema and replace reviewed constraints/triggers, but are not designed for column renames/drops)
- [ ] Add refresh-token revocation (Redis-backed) if compromised-token scenarios matter for your risk profile
- [ ] Set up file-level backup for the upload volumes (see Backup & Restore above) — `pg_dump` alone does not cover these
- [ ] Enable the reminder scheduler (`ENABLE_SCHEDULER=true`) only after confirming SMTP delivery works
