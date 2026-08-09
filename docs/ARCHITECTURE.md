# Sri Apartment OS — Architecture

## System Diagram

```
                         ┌─────────────────────────┐
                         │   React Admin Frontend    │
                         │  (staff/admin, port 5173) │
                         └────────────┬───────────────┘
                                      │ REST (JWT)
┌──────────────┐    ┌────────────────▼────────────────┐    ┌───────────────────┐
│ Guest browser │───▶│         FastAPI Backend          │◀───│  Website / OTA     │
│ (Guest Portal │    │         (port 8000)               │    │  (public endpoints,│
│  magic-link)  │    └────────────────┬───────────────────┘    │  no auth needed)   │
└──────────────┘                     │                        └───────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
┌───────▼────────┐          ┌─────────▼─────────┐          ┌────────▼────────┐
│  PostgreSQL 16   │          │  In-process        │          │  External (all   │
│  (all app data,  │          │  APScheduler       │          │  honestly gated  │
│  EXCLUDE constr. │          │  (reminder emails, │          │  behind "not     │
│  for overlap-    │          │  off by default)   │          │  configured"     │
│  safe bookings)  │          └────────────────────┘          │  until real      │
└─────────────────┘                                          │  credentials     │
                                                               │  are set):        │
                                                               │  - SMTP (email)   │
                                                               │  - Razorpay        │
                                                               │  - Booking.com/    │
                                                               │    Airbnb/Agoda    │
                                                               │    (mock adapter)  │
                                                               └───────────────────┘
```

## Module Map (backend)

| Module | Responsibility |
|---|---|
| `auth` | Users, roles, JWT login/refresh, register |
| `properties` | Properties, units |
| `bookings` | Guests, overlap-safe bookings, status state machine |
| `payments` | Payments, GST invoices (tariff-slab logic), PDF |
| `users` | Admin user management |
| `housekeeping` | Cleaning tasks, room status |
| `maintenance` | Maintenance tickets |
| `reports` | Occupancy/ADR/RevPAR/GST/sales-funnel aggregation, PDF/Excel export |
| `inquiries` | Pre-booking quotations, PDF matching business template, WhatsApp/email send, lead pipeline, conversion-to-booking |
| `settings` | Editable business config (company info, bank/UPI, document text) |
| `inventory` | Stock items, suppliers, stock movements, low-stock alerts |
| `staff` | Attendance, leave, payroll, shifts, holidays |
| `audit` | Audit log + login/session history |
| `notifications` | Unified email dispatch + APScheduler-driven automatic reminders |
| `payment_gateway` | Razorpay order/webhook/refund (webhook signature verification is real; order creation is honestly gated) |
| `ota` | OTA channel config, listing mappings, sync logs, mock adapter |
| `portal` | Guest-facing magic-link auth, bookings/invoices/requests/complaints |
| `public_booking` | Unauthenticated availability/pricing/promo/booking for a website widget |

## Design Principles Followed Throughout

1. **Modular monolith, not microservices.** All modules live in one FastAPI app sharing one Postgres database. This is the right choice at this scale — it avoids distributed-systems complexity (network calls between services, eventual consistency, service discovery) that this business doesn't need yet. Module boundaries are still respected in code (each has its own models/schemas/router), so extraction later remains possible.

2. **The database enforces what the application must never violate.** The clearest example: double-booking is prevented by a Postgres `EXCLUDE USING gist` constraint on `bookings`, not just application-level checks. Even a bug in the API code, a race condition, or a direct SQL insert cannot create an overlapping booking.

3. **Real integrations are real; unavailable integrations say so.** Where a feature needs external credentials this environment doesn't have (SMTP, Razorpay, OTA partner APIs), the code path is genuinely implemented and will work the moment real credentials are configured — but it returns an honest `501 Not Configured` rather than a fake success. This is applied consistently: email, Razorpay order creation/refunds, and the OTA adapter (the OTA case additionally requires a business partnership with each platform, which no code change can provide).

4. **Additive, not destructive, evolution.** Later phases added `lead_status` alongside (not replacing) `status` on inquiries, added `promo_code_used` as a nullable column on bookings, and built Settings as a new source of truth without rewiring existing tested PDF generation — each phase's existing test suite kept passing throughout, verified by rerunning it after every change.
