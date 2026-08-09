# Sri Apartment OS — Troubleshooting Guide

Practical fixes for the problems most likely to actually come up, based on what was found and fixed during this project's own testing — not a generic checklist.

## "Invalid or expired token" / gets logged out unexpectedly

- **Normal cause:** access tokens expire after 15 minutes of inactivity by design (`ACCESS_TOKEN_EXPIRE_MINUTES` in `app/core/config.py`). Log back in.
- **If it happens immediately after a page refresh or opening a bookmarked link while genuinely still logged in:** this was a real bug (a race condition between the auth check and reading the session from browser storage) that was found and fixed earlier in this project — if you're running a version older than that fix, upgrade (see Upgrade Procedure in `DEPLOYMENT.md`).

## "Too many failed login attempts. Try again in a few minutes." (HTTP 429)

This is the login rate limiter working as intended (5 failed attempts per 5-minute window, per IP+email combination) — not a bug. A correct password immediately clears the counter for that account. If you're testing and need to reset it sooner, restarting the backend process clears it (it's in-memory, not persisted — see `app/shared/rate_limit.py`).

## A report/export button downloads a .pdf or .xlsx that won't open / is empty

- Check the backend logs (`docker compose logs backend`) for the actual error — the global exception handler logs full stack traces server-side even though the client only sees a generic message.
- A known past issue: PDF generation crashed outright if any text going into it contained certain non-Latin1 characters (em-dashes, curly quotes). This was fixed with a text-sanitization pass in `app/modules/reports/service.py::_pdf_safe()` — if you're on an older version, this is the likely cause of a report failing to generate at all.

## A number on a report doesn't match what I expected

1. Check the date range first — several reports default to "this month" and will correctly show zero/different numbers if your data is in a different period.
2. For Apartment P&L specifically: only expenses/utility bills/purchases explicitly tagged to that unit count toward its profit figure. Central/shared expenses (no unit assigned) intentionally do NOT appear in any single apartment's P&L — they belong in the business-wide numbers instead. This is documented behavior, not a bug, but it's worth checking if a number looks "too small" for an apartment.
3. For GST figures: "GST Input Credit" is explicitly an approximation (GST collected on ITC-eligible sales), not a full purchase-side input-tax-credit reconciliation — this system doesn't track GST paid on vendor purchases, so it can't compute true ITC. Treat it as a starting point for your accountant, not a filing-ready number.

## Uploaded photos/attachments/generated invoices disappeared after a redeploy

This means the upload directories weren't mounted as persistent volumes. Check that your docker-compose.yml has the uploads_* named volumes attached to the backend service (see the shipped docker-compose.yml for the correct configuration) — without them, every file lives only inside the container's own filesystem, which is wiped on every rebuild.

## "This booking has already been converted... details are locked" (409) when trying to edit

This is intentional: once an inquiry converts to a real booking with a payment attached, its original quotation details are locked to protect the financial record from silently drifting from what the guest actually agreed to and paid for. To change something after conversion, edit the Booking record directly (Bookings page, Edit), not the original Inquiry.

## Trying to delete a booking/unit/property and getting a database error instead of it just deleting

This is also intentional, enforced at the database level (not just the API) — you cannot delete a unit, guest, or property that has any real bookings, payments, or expenses tied to it, specifically so a mistaken delete can't silently destroy financial history. Cancel the booking (status change) instead of deleting it, or remove the specific child records first if you genuinely need to delete the parent.

## Custom expense category isn't being accepted

If you're on an older version: expense categories used to be restricted to a fixed list. This was intentionally changed to free-text to support custom categories — if you still see this restriction, you're on an old version and should upgrade (see DEPLOYMENT.md).

## The backend container won't start at all

Check docker compose logs backend — the two most likely causes:
1. .env is missing or doesn't have POSTGRES_PASSWORD/JWT_SECRET_KEY set — Compose will show a clear "variable is not set" error rather than starting with an insecure default.
2. Postgres isn't healthy yet — the backend waits for Postgres's health check, but if Postgres itself is failing (check docker compose logs db), the backend will never start.

## Something else / a genuine bug

Check docker compose logs backend for the actual stack trace (logged server-side even for errors the user sees as a generic message), and check the Audit Log (Audit page, admin only) for what actually happened immediately before the problem — every booking/finance/staff/housekeeping/maintenance mutation is logged there with who did what and when.
