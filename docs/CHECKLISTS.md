# Sri Apartment OS — v1.0 Release Checklists

## Installation Checklist

- [ ] Clone/extract the project
- [ ] Docker installed (Docker Desktop or Docker Engine + Compose plugin) — note: this was not verifiable in the environment that built this release (no Docker daemon available there); the Dockerfiles and compose config were validated by simulating their exact build steps in isolated environments instead (see docs/DEPLOYMENT.md) — run a real docker compose build yourself before going further
- [ ] Copy backend/.env.example to backend/.env and review every value
- [ ] Generate a real random JWT_SECRET_KEY (do not use the placeholder)
- [ ] docker compose up --build
- [ ] Confirm http://localhost:8000/health returns {"status": "ok", ...}
- [ ] Confirm http://localhost:5173 loads the login page
- [ ] Log in with the seeded default admin, then immediately change that password or deactivate that account and create a real one

## Configuration Checklist

- [ ] JWT_SECRET_KEY — real random value, not the default
- [ ] CORS_ALLOWED_ORIGINS — set to your real frontend domain(s), not localhost
- [ ] ENVIRONMENT — set to production
- [ ] SMTP_HOST / SMTP_USER / SMTP_PASSWORD / SMTP_FROM_EMAIL — set if you want email sending (invoice/quotation email, automatic reminders) to actually work; otherwise these endpoints will correctly return "not configured" rather than fail silently
- [ ] RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET / RAZORPAY_WEBHOOK_SECRET — set if you want online payment collection to work; webhook signature verification works as soon as the webhook secret is set, independent of the other two
- [ ] ENABLE_SCHEDULER — set to true only after confirming SMTP actually delivers mail (test with a real send first)
- [ ] Business Settings — log in as admin and fill in Settings (company details, bank/UPI, quotation terms/house rules/cancellation policy) to replace the seeded defaults with your real business information
- [ ] Default admin password changed

## Go-Live Checklist

- [ ] All items in Installation and Configuration checklists complete
- [ ] Full backend test suite passes in your actual deployment environment: cd backend && pytest tests/ -v
- [ ] Frontend type-checks and builds cleanly in your environment: cd frontend && npx tsc --noEmit && npm run build
- [ ] HTTPS is terminated in front of the API (this app does not terminate TLS itself — put it behind a reverse proxy or load balancer that does)
- [ ] Database backup taken and restore tested (see docs/DEPLOYMENT.md for the pg_dump/psql commands) — a backup you haven't test-restored is not a backup
- [ ] A real test booking taken through the full workflow (Inquiry to Quotation to Advance to Booking to Check-in to Housekeeping to Payment to Invoice to Check-out) in your live environment, by a real staff member, not just automated tests
- [ ] Confirm rate limiting doesn't block your own team's legitimate testing (5 failed attempts per 5 minutes, per IP+email)
- [ ] If using Razorpay: a real test transaction completed successfully end-to-end, including the webhook actually reaching your server
- [ ] If using email: a real email actually received in a real inbox, not just a 200 response from the endpoint

## Maintenance Checklist (ongoing, not one-time)

- [ ] Database backups running on a schedule (not just the one-time go-live backup)
- [ ] Periodically review Audit Logs and Login History for anything unexpected
- [ ] Periodically review the Inventory low-stock alerts and Staff leave/payroll queues
- [ ] Monitor disk usage of the invoice/quotation PDF storage directories (INVOICE_STORAGE_DIR, INQUIRY_STORAGE_DIR — default /tmp/..., which is NOT persistent storage and NOT backed up; move these to a real persistent volume before relying on historical PDF retrieval)
- [ ] Watch for the JWT_SECRET_KEY / CORS startup warnings in your logs after any redeploy — they'll fire again if an environment variable gets reset
- [ ] Revisit "Known gaps" in docs/RELEASE_NOTES.md and the main README periodically — several are explicitly scoped as "acceptable for now, revisit at scale" (e.g., no Alembic migrations, in-memory rate limiting, no caching layer)

## Deployment Checklist (technical)

- [ ] docker-compose.yml reviewed for your actual infrastructure (this ships with a single-host Postgres+backend+frontend setup — a real production deployment likely wants managed Postgres, e.g. RDS, instead of the bundled Postgres container)
- [ ] Environment variables injected via your platform's secrets management, not committed to a .env file in version control
- [ ] Health check endpoint (/health) wired into your platform's health-check/readiness-probe configuration
- [ ] Logging captured somewhere durable (this app logs to stdout via Python's standard logging — capture it via your platform's log aggregation, e.g. CloudWatch, or a sidecar)
- [ ] CI (.github/workflows/ci.yml) passing on the actual commit being deployed
- [ ] Persistent volume mounted for INVOICE_STORAGE_DIR / INQUIRY_STORAGE_DIR (see Maintenance Checklist above — do not leave these on ephemeral container storage in production)
