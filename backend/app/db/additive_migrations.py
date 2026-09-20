"""
Additive ALTER TABLE statements for columns added to already-existing
tables after their initial creation — same idempotent-DDL pattern as
db/indexes.py, needed because this project has no Alembic migrations yet
(see README known gaps). Every statement here is a pure addition: a new
nullable column with no default that could silently change existing rows'
meaning. Safe to run repeatedly against a database that already has some
or all of these columns.
"""
from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATION_STATEMENTS = [
    # Outstanding Payments report needs a due date to compare against
    # "today" for the overdue flag. Nullable — existing bookings simply
    # have no due date until one is set, rather than a fabricated default.
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS due_date DATE",
    # Reservation management: staff-only internal notes and guest-stated
    # special requests, both free text and both nullable.
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS internal_notes TEXT",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS special_requests TEXT",
    # Guest address, for the full guest-details requirement.
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS address VARCHAR(500)",
    # Expense approval workflow.
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS approval_status VARCHAR(10) NOT NULL DEFAULT 'approved'",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES users(id)",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ",
    # Purchase register: category, quantity, unit price, invoice number.
    "ALTER TABLE purchase_records ADD COLUMN IF NOT EXISTS category VARCHAR(50)",
    "ALTER TABLE purchase_records ADD COLUMN IF NOT EXISTS quantity NUMERIC(10,2)",
    "ALTER TABLE purchase_records ADD COLUMN IF NOT EXISTS unit_price NUMERIC(10,2)",
    "ALTER TABLE purchase_records ADD COLUMN IF NOT EXISTS invoice_number VARCHAR(100)",
    # Expense categories were originally a fixed CHECK-constrained list;
    # the business asked for custom categories, so the model no longer
    # defines this constraint — but a constraint added by an earlier
    # create_all() isn't automatically dropped when a model changes, so it
    # has to be dropped explicitly here or it silently keeps rejecting any
    # category outside the original 7, even though the app no longer
    # intends to restrict it.
    "ALTER TABLE expenses DROP CONSTRAINT IF EXISTS ck_expense_category",
    # Audit log: structured previous/new value fields, in addition to the
    # existing free-text "details" summary.
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS previous_value TEXT",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS new_value TEXT",
    # Guest ID proof number (passport/Aadhaar/driving license number) —
    # id_proof_type and id_proof_url already existed; this is the actual
    # document number itself, which was missing.
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS id_proof_number VARCHAR(50)",
    # Guest profile expansion (V8 request)
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS date_of_birth DATE",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS gender VARCHAR(20)",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS nationality VARCHAR(60)",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS occupation VARCHAR(100)",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS whatsapp_number VARCHAR(20)",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR(150)",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS emergency_contact_phone VARCHAR(20)",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS photo_url VARCHAR(500)",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS notes TEXT",
    "ALTER TABLE guests ADD COLUMN IF NOT EXISTS merged_into_guest_id UUID REFERENCES guests(id)",
    # Booking guest-count/timing fields (V8 request)
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS adults INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS children INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS extra_bed BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS arrival_time VARCHAR(10)",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS departure_time VARCHAR(10)",
    # Property identification fields (V8 request)
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS property_code VARCHAR(30)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS property_type VARCHAR(50)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS owner_name VARCHAR(150)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS owner_phone VARCHAR(20)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS owner_email VARCHAR(255)",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS google_maps_url VARCHAR(500)",
    # Unit identification fields (V8 request)
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS unit_code VARCHAR(30)",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS floor VARCHAR(20)",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS flat_number VARCHAR(20)",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS amenities TEXT",
    # Payment refunds (Billing module gap fix)
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_of_payment_id UUID REFERENCES payments(id)",
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_reason VARCHAR(255)",
    # Guest Requests: priority, staff assignment, guest feedback
    "ALTER TABLE guest_requests ADD COLUMN IF NOT EXISTS priority VARCHAR(10) NOT NULL DEFAULT 'medium'",
    "ALTER TABLE guest_requests ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES users(id)",
    "ALTER TABLE guest_requests ADD COLUMN IF NOT EXISTS guest_feedback_rating INTEGER",
    "ALTER TABLE guest_requests ADD COLUMN IF NOT EXISTS guest_feedback_text TEXT",
    # Inventory: brand + expiry (Module 9 gap fix)
    "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS brand VARCHAR(100)",
    "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS expiry_date DATE",
    # Notifications: link to guest requests (Guest Requests module completion)
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS guest_request_id UUID REFERENCES guest_requests(id)",
    # Maintenance -> Finance integration: link a resolved ticket's cost to
    # a real Expense record (Maintenance module completion).
    "ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS linked_expense_id UUID REFERENCES expenses(id)",
    # Maintenance -> Notifications integration.
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS maintenance_ticket_id UUID REFERENCES maintenance_tickets(id)",
    # Housekeeping/Inventory: real consumption tracking (which apartment,
    # which housekeeping task) instead of free-text convention.
    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS unit_id UUID REFERENCES units(id)",
    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS housekeeping_task_id UUID REFERENCES housekeeping_tasks(id)",
    # Housekeeping/Maintenance -> Notifications (module completion).
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS housekeeping_task_id UUID REFERENCES housekeeping_tasks(id)",
    # Inventory module completion: SKU, Maintenance integration, Finance
    # (Purchase Register) integration, low-stock notifications.
    "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS sku VARCHAR(50) UNIQUE",
    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS maintenance_ticket_id UUID REFERENCES maintenance_tickets(id)",
    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS linked_purchase_record_id UUID REFERENCES purchase_records(id)",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS inventory_item_id UUID REFERENCES inventory_items(id)",
    # Staff module completion: Finance (payroll -> Expense) and
    # Notifications (leave review, payroll paid) integration.
    "ALTER TABLE payroll ADD COLUMN IF NOT EXISTS linked_expense_id UUID REFERENCES expenses(id)",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS leave_request_id UUID REFERENCES leave_requests(id)",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS payroll_id UUID REFERENCES payroll(id)",
    # Enquiries module completion: source/priority/assignment/lost reason,
    # Notifications (created, follow-up reminder).
    "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'phone'",
    "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS priority VARCHAR(10) NOT NULL DEFAULT 'medium'",
    "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES users(id)",
    "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS lost_reason VARCHAR(255)",
    # Notifications module completion: in-app inbox (user_id/read_at).
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id)",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ",
    # Settings module completion: theme accent color, WhatsApp business number.
    "ALTER TABLE business_settings ADD COLUMN IF NOT EXISTS theme_color VARCHAR(7) NOT NULL DEFAULT '#0f172a'",
    "ALTER TABLE business_settings ADD COLUMN IF NOT EXISTS whatsapp_business_number VARCHAR(20)",
    # Payment gateway: link a GatewayOrder to the BookingHold that initiated it.
    # booking_holds must already exist (created by create_all) before this FK is added.
    "ALTER TABLE gateway_orders ADD COLUMN IF NOT EXISTS hold_id UUID REFERENCES booking_holds(id)",
    # Property/Unit soft-deactivation (Edit + Deactivate/Reactivate CRUD
    # completion). Defaults every existing row to active so nothing already
    # in production silently disappears from booking pickers/availability.
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
    # Data integrity/concurrency hardening: defense-in-depth CHECK
    # constraints backstopping the row-locked stock_in/stock_out and
    # refund_deposit handlers.
    "ALTER TABLE inventory_items DROP CONSTRAINT IF EXISTS ck_inventory_stock_nonneg",
    "ALTER TABLE inventory_items ADD CONSTRAINT ck_inventory_stock_nonneg CHECK (current_stock >= 0)",
    "ALTER TABLE security_deposits DROP CONSTRAINT IF EXISTS ck_deposit_refund_not_exceed_amount",
    "ALTER TABLE security_deposits ADD CONSTRAINT ck_deposit_refund_not_exceed_amount CHECK (refund_amount <= amount)",
    # Phase 15I: policy lifecycle governance. `status` distinguishes a
    # temporary disable from a permanent retirement; the existing
    # release_expired_hold row (created in Phase 15G, before this column
    # existed) gets 'ACTIVE' by default, which is correct and changes
    # nothing about its actual authorization semantics.
    "ALTER TABLE policies ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'",
    # Database-level append-only enforcement for policy_audit_entries,
    # in addition to (not instead of) the application-level guarantee
    # (no update/delete function exists in governance.py at all). This
    # table is new in Phase 15I — create_all() creates it before this
    # runs (see seed.py's execution order: create_all, then
    # apply_indexes, then apply_additive_migrations), so it's always
    # safe for this trigger creation to assume the table already exists.
    """
    CREATE OR REPLACE FUNCTION prevent_policy_audit_mutation() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'policy_audit_entries is append-only: % is not permitted', TG_OP;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_policy_audit_append_only ON policy_audit_entries",
    """
    CREATE TRIGGER trg_policy_audit_append_only
    BEFORE UPDATE OR DELETE ON policy_audit_entries
    FOR EACH ROW EXECUTE FUNCTION prevent_policy_audit_mutation()
    """,
    # Phase 16A: NOTIFY_STAFF fields on the existing notification_logs
    # table (reused rather than creating a new "staff_notifications"
    # table — see notifications/models.py's own comment). All nullable;
    # every pre-existing row is unaffected. action_id references
    # action_proposals, which must already exist by the time this runs
    # (create_all creates both tables before apply_additive_migrations
    # runs — see seed.py's execution order).
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS action_id VARCHAR(200) REFERENCES action_proposals(action_id)",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS category VARCHAR(30)",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS severity VARCHAR(20)",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS message TEXT",
    "ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS source_id VARCHAR(200)",
    # Reservation management: Edit + Archive completion. updated_at backs
    # optimistic-concurrency (stale-update) protection on booking edits;
    # existing rows default to now() at migration time, which is correct
    # (there's no earlier "last modified" moment to recover). is_archived
    # is a soft-delete distinct from both Cancel (status transition) and
    # the existing hard DELETE (still blocked whenever a financial record
    # exists) — every existing booking defaults to not archived.
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS archived_by UUID REFERENCES users(id)",
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS archive_reason VARCHAR(255)",
    # Phase 18A: Staff Operations foundation. started_at lets housekeeping
    # tasks record when work actually began, not just when it was created
    # vs completed — nullable, existing tasks simply have no start time.
    "ALTER TABLE housekeeping_tasks ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
    # category is app-layer validated (see maintenance/models.py
    # TICKET_CATEGORIES), same convention as priority/status — nullable so
    # existing tickets are simply uncategorized, not backfilled with a guess.
    "ALTER TABLE maintenance_tickets ADD COLUMN IF NOT EXISTS category VARCHAR(20)",
    # Widen guest_requests.request_type to also accept the staff-operational
    # types (Phase 18A) alongside the existing guest-self-service ones — one
    # request queue/workflow for both, not two parallel tables. No existing
    # row's request_type value is affected; this only permits new values.
    "ALTER TABLE guest_requests DROP CONSTRAINT IF EXISTS ck_guest_request_type",
    """
    ALTER TABLE guest_requests ADD CONSTRAINT ck_guest_request_type
        CHECK (request_type IN ('service', 'complaint', 'extend_stay', 'towels', 'linens', 'cleaning', 'water', 'maintenance', 'check_in_help', 'check_out_help', 'other'))
    """,
    # Phase 19E: MMT added to both OTA channel vocabularies. Same reasoning
    # as ck_guest_request_type above — a CHECK constraint baked in by an
    # earlier create_all() on an already-existing table isn't automatically
    # widened when the model's tuple changes, so it has to be re-applied
    # explicitly or every "mmt" insert keeps failing against the old list.
    # No existing row's channel value is affected; this only permits a new one.
    "ALTER TABLE ota_channel_configs DROP CONSTRAINT IF EXISTS ck_ota_channel",
    "ALTER TABLE ota_channel_configs ADD CONSTRAINT ck_ota_channel CHECK (channel IN ('booking_com', 'airbnb', 'agoda', 'mmt'))",
    "ALTER TABLE ota_commissions DROP CONSTRAINT IF EXISTS ck_commission_channel",
    "ALTER TABLE ota_commissions ADD CONSTRAINT ck_commission_channel CHECK (channel IN ('booking_com', 'airbnb', 'agoda', 'direct', 'mmt'))",
    # Multi-accommodation-type readiness (Phase 1): long-form listing copy
    # for a unit, for the public website — nullable, no default, so every
    # existing unit (including the 6 real production apartments) simply has
    # no description until one is set via the admin UI.
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS description TEXT",
    # Staff Management: post/designation defaults + employee-specific
    # overrides (see staff/models.py StaffDesignation/StaffProfile and
    # staff/hr_policy.py for the resolution order). All nullable — an
    # existing staff_profiles row simply has no designation link and no
    # overrides until an admin sets them, and resolves to "unconfigured"
    # rather than any invented default (see hr_policy.resolve_employee_policy).
    "ALTER TABLE staff_profiles ADD COLUMN IF NOT EXISTS designation_id UUID REFERENCES staff_designations(id)",
    # Index on the column just added above — deliberately placed here
    # rather than in db/indexes.py: apply_indexes() runs BEFORE
    # apply_additive_migrations() in seed.py, so an index on this column
    # would fail on any database upgraded from before this column existed.
    "CREATE INDEX IF NOT EXISTS ix_staff_profiles_designation_id ON staff_profiles (designation_id)",
    "ALTER TABLE staff_profiles ADD COLUMN IF NOT EXISTS salary_override NUMERIC(10,2)",
    "ALTER TABLE staff_profiles ADD COLUMN IF NOT EXISTS shift_start_override TIME",
    "ALTER TABLE staff_profiles ADD COLUMN IF NOT EXISTS shift_end_override TIME",
    "ALTER TABLE staff_profiles ADD COLUMN IF NOT EXISTS lunch_break_override_minutes INTEGER",
    "ALTER TABLE staff_profiles ADD COLUMN IF NOT EXISTS standard_working_hours_override NUMERIC(4,2)",
    "ALTER TABLE staff_profiles ADD COLUMN IF NOT EXISTS leave_entitlement_override INTEGER",
    # Attendance: computed-hours snapshot, written once at check-out time
    # (see staff/router.py _maybe_compute_attendance_hours). Nullable —
    # stays NULL until both check_in_time and check_out_time are known,
    # and existing rows recorded before this feature simply have no
    # computed hours rather than a backfilled/guessed value.
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS total_presence_minutes INTEGER",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS lunch_break_minutes_applied INTEGER",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS working_minutes INTEGER",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS overtime_minutes INTEGER",

    # RBAC Phase A: immutable snapshot of the actor's role name at the
    # moment an audit-log row was written (see AuditLog.actor_role's own
    # docstring). Nullable — rows written before this column existed
    # simply have no recorded role, rather than a backfilled guess.
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_role VARCHAR(50)",

    # Phase 3E: customer-account phone number (public-website Sign Up now
    # requires it; Login accepts email OR phone as the identifier). Nullable
    # — a CustomerAccount created before this column existed keeps no phone
    # rather than a fabricated one (see CustomerAccount.phone's own
    # docstring). The partial unique index is declared identically at the
    # ORM level (CustomerAccount.__table_args__) for a fresh database; this
    # statement is what brings an already-existing table up to the same
    # constraint without needing Alembic.
    "ALTER TABLE customer_accounts ADD COLUMN IF NOT EXISTS phone VARCHAR(20)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_customer_accounts_phone_unique ON customer_accounts (phone) WHERE phone IS NOT NULL",

    # Long-stay absolute nightly rate (see Unit model + pricing.py): a
    # stay of long_stay_min_nights or more is billed at long_stay_rate for
    # every night, not a percentage discount. NOT NULL DEFAULT backfills
    # every existing unit to the business's current policy (30 nights /
    # Rs.2,000) atomically in the same statement, same pattern already
    # used above for bookings.adults/children and expenses.approval_status.
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS long_stay_min_nights INTEGER NOT NULL DEFAULT 30",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS long_stay_rate NUMERIC(10,2) NOT NULL DEFAULT 2000",

    # Zotel channel-manager sync (see app/modules/zotel/): the stable
    # external identity (Zotel's transaction_id) an imported reservation
    # is reconciled against on every future sync, so the same reservation
    # fetched repeatedly updates/skips the same Booking instead of ever
    # creating a second one. Nullable — every existing booking (direct/
    # walk-in/phone/website) simply has none, same reasoning as every
    # other optional identifier column added here. Unique only when set,
    # same partial-index pattern as customer_accounts.phone above.
    "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS external_reservation_id VARCHAR(100)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_bookings_external_reservation_id_unique ON bookings (external_reservation_id) WHERE external_reservation_id IS NOT NULL",
    # Booking-notification audit: second owner contact, additive alongside
    # the existing contact_email/whatsapp_business_number (which remain the
    # primary contact, unchanged). Nullable so a fresh install without a
    # second contact isn't forced to have one; this specific installation's
    # existing singleton row is backfilled separately below since a new
    # column's Python-side default never applies to a pre-existing row.
    "ALTER TABLE business_settings ADD COLUMN IF NOT EXISTS secondary_contact_email VARCHAR(255)",
    "ALTER TABLE business_settings ADD COLUMN IF NOT EXISTS secondary_whatsapp_number VARCHAR(20)",
    "UPDATE business_settings SET secondary_contact_email = 'sriapartmentpondy@gmail.com' WHERE secondary_contact_email IS NULL",
    "UPDATE business_settings SET secondary_whatsapp_number = '+919361946138' WHERE secondary_whatsapp_number IS NULL",
    # Admin-control phase 2: structured unit details. All nullable, no
    # backfill — an existing unit's amenities/description text is left
    # exactly as-is; these are additive fields an admin fills in going
    # forward, with the website falling back to its prior hardcoded
    # behavior for any unit that hasn't set them (see PublicUnitOut).
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS bedroom_count INTEGER",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS bathroom_count INTEGER",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS house_rules TEXT",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS checkin_time VARCHAR(20)",
    "ALTER TABLE units ADD COLUMN IF NOT EXISTS checkout_time VARCHAR(20)",
    # Admin-control phase 2: photo reorder + primary/cover photo. Defaults
    # (0 / false) reproduce today's existing display order (insertion
    # order, no photo marked primary) for every already-uploaded photo.
    "ALTER TABLE unit_photos ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE unit_photos ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT false",
]

# Property files (documents/photos) is a genuinely new table, not an
# ALTER on an existing one — create_all in seed.py handles new tables
# automatically since PropertyFile is a real ORM model, so no explicit
# CREATE TABLE statement is needed here.

# Phase 19C: Loan and LoanEmiPayment (loans, loan_emi_payments) are also
# genuinely new tables, not ALTERs — same create_all handling as
# PropertyFile above. No DROP/DELETE/TRUNCATE anywhere in this phase.

# StaffDesignation (staff_designations) is likewise a genuinely new table,
# not an ALTER — same create_all handling. It must exist before the
# staff_profiles.designation_id ALTER above runs (create_all always runs
# before apply_additive_migrations in seed.py, so the FK target exists in time).


def apply_additive_migrations(engine: Engine) -> None:
    with engine.connect() as conn:
        for stmt in MIGRATION_STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()


def migrate_legacy_bank_settings(engine: Engine) -> None:
    """
    One-time data migration for the RBAC Phase A BankSettings split:
    business_settings used to store bank_name/bank_account_name/
    bank_account_number/bank_ifsc/bank_branch/upi_id directly (readable by
    admin+staff via GET /settings). Those columns are never dropped — this
    project does no destructive migrations (see this module's own
    docstring) — so a database that already had them keeps them at rest,
    unused by the ORM going forward. This copies their current values into
    the new bank_settings singleton row once, so a real, already-configured
    deployment doesn't silently lose its bank details behind the split.

    A fresh database's business_settings table never had these columns at
    all (the model no longer declares them, so create_all never creates
    them there) — this is a no-op in that case. Safe to call every startup:
    it only ever does something the first time bank_settings is empty AND
    the legacy columns still exist on business_settings.
    """
    with engine.connect() as conn:
        legacy_column_exists = conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'business_settings' AND column_name = 'bank_account_number'"
        )).fetchone()
        if not legacy_column_exists:
            return  # fresh schema — BusinessSettings never had bank columns

        bank_settings_table_exists = conn.execute(text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'bank_settings'"
        )).fetchone()
        if not bank_settings_table_exists:
            return  # create_all hasn't run yet in this call order — nothing to migrate into

        already_migrated = conn.execute(text("SELECT 1 FROM bank_settings LIMIT 1")).fetchone()
        if already_migrated:
            return

        legacy_row = conn.execute(text(
            "SELECT bank_name, bank_account_name, bank_account_number, bank_ifsc, bank_branch, upi_id "
            "FROM business_settings LIMIT 1"
        )).mappings().fetchone()
        if legacy_row is None:
            return  # business_settings singleton row doesn't exist yet either

        conn.execute(
            text(
                "INSERT INTO bank_settings "
                "(id, bank_name, bank_account_name, bank_account_number, bank_ifsc, bank_branch, upi_id, updated_at) "
                "VALUES (:id, :bank_name, :bank_account_name, :bank_account_number, :bank_ifsc, :bank_branch, :upi_id, now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": "00000000-0000-0000-0000-000000000002", **dict(legacy_row)},
        )
        conn.commit()


def migrate_customer_phone_numbers_to_e164(engine: Engine) -> None:
    """
    One-time data migration for the worldwide-customer phone redesign:
    customer_accounts.phone used to store the Guest/booking-side bare
    10-digit Indian convention (no country code — see
    portal.auth_service.normalize_phone's own docstring), because that's
    the only normalizer that existed when Phase 3E first added the
    column. New sign ups/logins now go through
    customer_auth.phone_service.normalize_phone_e164 instead, which
    stores the full international E.164 form (e.g. "+919876543210"). This
    backfills any ALREADY-STORED legacy bare-digit value to the
    equivalent E.164 form of the SAME real number, so it can still be
    found by the new normalizer's lookups and can never collide with a
    new E.164-formatted duplicate of the same number.

    Deliberately conservative: only rewrites a value that is unambiguous
    under the OLD convention (exactly 10 digits, no "+", which is the
    only shape that convention ever produced — see the CustomerAccount.
    phone column's own docstring) using the SAME default_country_code
    ("91") that convention was always implicitly India-only for. A value
    that's already E.164 (starts with "+") is left untouched — this is
    what makes the migration idempotent and safe to run on every startup:
    after the first run, no bare-10-digit rows remain, so every
    subsequent run is a no-op. Never touches rows with phone IS NULL
    (never fabricates a phone number for an account that never had one),
    and never touches any other table (Guest.phone, staff, etc. keep
    their own existing convention entirely unchanged — this migration is
    scoped to customer_accounts alone).
    """
    import phonenumbers

    # The old convention (portal.auth_service.normalize_phone) was always
    # implicitly India-only — see its own docstring — regardless of what
    # settings.default_country_code happens to be set to, so the region
    # this backfill assumes for a legacy bare-10-digit value is the fixed
    # ISO code "IN", not a re-derivation of that setting.
    LEGACY_REGION = "IN"

    with engine.connect() as conn:
        table_exists = conn.execute(text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'customer_accounts'"
        )).fetchone()
        if not table_exists:
            return  # create_all hasn't run yet in this call order

        legacy_rows = conn.execute(text(
            "SELECT id, phone FROM customer_accounts "
            "WHERE phone IS NOT NULL AND phone !~ '^\\+' AND phone ~ '^[0-9]{10}$'"
        )).mappings().fetchall()
        if not legacy_rows:
            return  # nothing in the old bare-digit shape — first run already done, or a fresh DB

        for row in legacy_rows:
            try:
                parsed = phonenumbers.parse(row["phone"], LEGACY_REGION)
                if not phonenumbers.is_valid_number(parsed):
                    continue  # leave anything that doesn't cleanly convert untouched, never guess
                e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            except phonenumbers.NumberParseException:
                continue

            conn.execute(
                text("UPDATE customer_accounts SET phone = :e164 WHERE id = :id"),
                {"e164": e164, "id": row["id"]},
            )
        conn.commit()
