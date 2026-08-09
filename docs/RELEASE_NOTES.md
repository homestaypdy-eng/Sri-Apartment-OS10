# Sri Apartment OS — Release Notes

## Version 1.0.0 — Production Release

This release marks the transition from feature development to production-readiness hardening. **No new features were added in this release** — the scope was a full audit: code cleanup, security hardening, database review, and documentation completeness, verified against the full existing test suite throughout.

### What changed in this release

**Security**
- Login endpoint now rate-limited (5 failed attempts per IP+email per 5-minute window, resetting on a successful login) — closes a previously-documented gap
- Password complexity enforced at registration (must contain at least one letter and one digit, in addition to the existing 8-character minimum)
- Baseline security response headers added to every request (X-Content-Type-Options: nosniff, X-Frame-Options: DENY, Referrer-Policy: strict-origin-when-cross-origin)
- CORS allowed origins are now configurable via CORS_ALLOWED_ORIGINS instead of hardcoded — defaults unchanged for local dev
- Startup warnings added: the app now logs a clear warning if JWT_SECRET_KEY is still the default placeholder, or if CORS still allows localhost while ENVIRONMENT=production
- Full manual review confirmed: no SQL injection risk (all raw SQL is parameterized), no unsafe dangerouslySetInnerHTML usage, zero unused/dead imports across the entire backend (verified with pyflakes), frontend already enforces zero unused locals via strict TypeScript config

**Database**
- 15 new indexes added on foreign-key columns confirmed (via pg_constraint/pg_index introspection, not guesswork) to be missing supporting indexes AND actually used in query filters — verified with EXPLAIN that the query planner correctly uses each new index
- Confirmed: zero DELETE endpoints exist anywhere in the API, so there is no orphan-record risk from deletion in this release

**Settings — now genuinely the source of truth**
- Business Settings (company name, address, GST number, bank/UPI details, signatory, quotation terms/house rules/cancellation policy) now flow directly into generated pre-booking quotation PDFs — verified by changing a Settings value and confirming it appears in the extracted PDF text, not just asserting the endpoint returns 200
- GST tax invoices intentionally continue to source company/GSTIN details from each booking's own Property record rather than global Settings, since a multi-property business would otherwise have every invoice show one property's GSTIN regardless of which property the stay was actually at — this is a deliberate design choice, documented in code

**API & Documentation**
- Confirmed: all 76 documented API paths have complete OpenAPI response documentation (verified programmatically, not visually spot-checked)
- Version bumped to 1.0.0 in both the FastAPI app metadata and the /health endpoint response

**Frontend**
- Global keyboard-accessible focus indicators added (:focus-visible outline) across every interactive element site-wide
- Reusable Skeleton/Spinner/EmptyState components added and applied to the Dashboard and Bookings pages as the reference pattern (not retrofitted to all ~20 pages in this pass — see Known Gaps)

**Business workflow QA**
- The full requested chain — Inquiry to Quotation to Advance Payment to Booking to Check-in to Housekeeping to Maintenance to Payment to GST Invoice to Check-out to Review — was run end-to-end against a live server in one continuous trace, not verified as isolated steps. Every status transition, integration point (checkout auto-creating a housekeeping task, unit dirty flag), and dollar amount matched expectations exactly.

### Test suite

151 backend tests passing (up from 148 before this release — 3 new tests added specifically proving Settings drives PDF content, plus 7 security-hardening tests). Verified stable by rerunning against the same database with no reset in between.

### Known limitations of this release (stated plainly)

- **UI polish was applied as a proven pattern on 2 pages (Dashboard, Bookings), not uniformly across all ~20 pages.** The reusable components (Skeleton, Spinner, EmptyState) exist and work; retrofitting every remaining page is straightforward follow-up work using the same pattern, not additional design.
- **No real device/browser QA was performed** — this sandbox has no Playwright-capable browser access and no physical devices. All frontend verification is via strict type-checking, production builds, and live API traces matching each page's exact data flow.
- **No load/performance testing was performed** — no k6/Locust runs, no measured API response time percentiles under load. The database indexing work is evidence-based (confirmed missing, confirmed used in filters, confirmed picked up by the query planner) but its real-world performance impact at production data volumes hasn't been measured.
- **Rate limiting is in-memory and per-process** — stated in the module's own docstring. A multi-replica production deployment needs a shared (Redis-backed) limiter instead.
- **CSRF, XSS, and SQL injection were reviewed, not penetration-tested.** The review found no issues in the current code (parameterized queries throughout, React's default escaping, token-based auth with no CSRF-vulnerable cookie flows) — but a review is not the same guarantee as a professional penetration test.
- **Alembic migrations still don't exist** — schema changes still go through create_all plus the new apply_indexes() idempotent DDL script. This is more scalable than before (the index script proves the idempotent-DDL pattern works) but a real migration tool is still recommended before further schema changes in a live production database.
- All gaps carried over from Phases 0-9 (documented in the main README) still apply: OTA is a mock adapter, Razorpay order creation needs real credentials, SMS isn't implemented, no MFA, etc.
