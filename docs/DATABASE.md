# Sri Apartment OS — Database Reference

PostgreSQL 16. Schema is created via SQLAlchemy `Base.metadata.create_all()` (no Alembic migrations yet — see README known gaps). All primary keys are UUIDs.

## Core Tables & Relationships

```
roles ──┬─< permissions
        └─< users ──┬─< inquiries (created_by)
                     ├─< bookings (created_by)
                     ├─< audit_logs
                     ├─< login_events
                     ├─< attendance / leave_requests / payroll / shifts
                     └─< notification triggers (created_by on Payment, etc.)

properties ──< units ──┬─< bookings
                        ├─< housekeeping_tasks
                        ├─< maintenance_tickets
                        └─< ota_listing_mappings

guests ──< bookings ──┬─< payments ──< refunds
                       ├─< invoices (1:1)
                       └─< guest_requests

inquiries ──┬─< inquiry_communications
            ├──> bookings (nullable FK, set on conversion)
            └──> guests (nullable FK, set on conversion)

business_settings          -- singleton (fixed UUID), no relationships
inventory_items ──< stock_movements ──> suppliers
gateway_orders / webhook_events        -- Razorpay
ota_channel_configs / ota_sync_logs
promo_codes                            -- referenced by bookings.promo_code_used (string, not FK)
```

## Key Constraints Worth Knowing

| Constraint | Table | Purpose |
|---|---|---|
| `EXCLUDE USING gist (unit_id WITH =, stay_range WITH &&)` | `bookings` | Makes overlapping bookings for the same unit physically impossible at the DB level |
| `ck_inquiry_status` / `ck_inquiry_lead_status` | `inquiries` | Two independently-constrained status columns — document lifecycle vs. sales pipeline stage |
| `uq_attendance_user_date` | `attendance` | One attendance record per user per day |
| `uq_payroll_user_period` | `payroll` | One payroll record per user per month/year |
| `uq_holiday_date` | `holidays` | One holiday entry per calendar date |
| `ck_ota_channel` | `ota_channel_configs`, `ota_listing_mappings`, `ota_sync_logs` | Restricts to `booking_com` / `airbnb` / `agoda` |

## Indexes Added for Reporting Performance

- `ix_bookings_check_in` — supports date-range report queries
- `ix_bookings_unit_check_in` — supports per-unit performance queries

## Notable Design Choices

- **`Invoice` and `Inquiry` both have their own GST fields** (`gst_rate_percent`, `cgst`, `sgst`, `igst`) rather than referencing a shared config — because the applicable rate depends on the tariff at the time of that specific transaction, not a fixed business-wide setting. Storing it per-record means a rate change later never silently rewrites historical documents.
- **`BusinessSettings` is a genuine singleton** (fixed UUID `00000000-0000-0000-0000-000000000001`), not a general-purpose settings table — there's exactly one business, so there should be exactly one settings row, enforced by always reading/writing that fixed ID rather than trusting a `WHERE` clause to only ever match one row.
- **`NotificationLog` doubles as the deduplication record** for the reminder scheduler — see `notifications/service.py::_already_sent()`. There's no separate "reminders sent" table; the log *is* the source of truth for "has this already been attempted."
