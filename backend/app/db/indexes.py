"""
Foreign-key indexes added after a real database review (this file exists
because of that review, not speculatively) — PostgreSQL does NOT
automatically index foreign key columns, only primary keys and explicit
unique constraints. A query against pg_constraint/pg_index found 35 FK
columns with no supporting index; the ones below were confirmed via grep
to actually appear in .where(...) filters in the codebase (not
blanket-applied to all 35 — indexing a column nobody filters on just adds
write overhead for no read benefit).

Applied as raw DDL (CREATE INDEX IF NOT EXISTS) rather than SQLAlchemy
index=True on the model columns, because this project has no Alembic
migrations yet (schema is created via create_all, which only applies to
new tables/columns, not retroactively to already-created ones in an
existing database) — see README known gaps. This function is idempotent
and safe to run against a database that already has some or all of these
indexes.
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

INDEX_STATEMENTS = [
    # Payments — looked up by booking on every payment-list and every
    # reminder-scan's "has this booking been paid" check
    "CREATE INDEX IF NOT EXISTS ix_payments_booking_id ON payments (booking_id)",
    # Notification dedup check (_already_sent) queries this on every
    # scheduler tick, for every active booking — the single most
    # frequently-hit FK lookup in the whole system once the scheduler
    # is enabled
    "CREATE INDEX IF NOT EXISTS ix_notification_logs_booking_id ON notification_logs (booking_id)",
    # Housekeeping/maintenance task lists filtered by unit
    "CREATE INDEX IF NOT EXISTS ix_housekeeping_tasks_unit_id ON housekeeping_tasks (unit_id)",
    "CREATE INDEX IF NOT EXISTS ix_maintenance_tickets_unit_id ON maintenance_tickets (unit_id)",
    # Guest portal — "my bookings" and staff guest-request filtering
    "CREATE INDEX IF NOT EXISTS ix_bookings_guest_id ON bookings (guest_id)",
    "CREATE INDEX IF NOT EXISTS ix_guest_requests_guest_id ON guest_requests (guest_id)",
    "CREATE INDEX IF NOT EXISTS ix_guest_requests_booking_id ON guest_requests (booking_id)",
    # Inquiry communications log, filtered by inquiry
    "CREATE INDEX IF NOT EXISTS ix_inquiry_communications_inquiry_id ON inquiry_communications (inquiry_id)",
    # Staff module — every attendance/leave/payroll/shift list can filter by user
    "CREATE INDEX IF NOT EXISTS ix_attendance_user_id ON attendance (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_leave_requests_user_id ON leave_requests (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_payroll_user_id ON payroll (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_shifts_user_id ON shifts (user_id)",
    # NOTE: ix_staff_profiles_designation_id is NOT here — it's in
    # additive_migrations.py instead, because seed.py runs apply_indexes()
    # BEFORE apply_additive_migrations(), and designation_id is itself
    # added by an additive migration. Indexing it here would fail on any
    # database upgraded from before this column existed (column doesn't
    # exist yet when apply_indexes runs). See additive_migrations.py.
    # Audit/login history, filtered by user on every "view this user's activity" query
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_login_events_user_id ON login_events (user_id)",
    # Inventory stock movement history per item
    "CREATE INDEX IF NOT EXISTS ix_stock_movements_item_id ON stock_movements (item_id)",

    # --- Added after the Business Management System refactor's own
    # tables started appearing in real filters (apartment P&L, dashboard
    # apartment performance, report exports, booking timeline) without
    # any of them having been indexed yet.
    "CREATE INDEX IF NOT EXISTS ix_expenses_unit_id ON expenses (unit_id)",
    "CREATE INDEX IF NOT EXISTS ix_expenses_expense_date ON expenses (expense_date)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_records_unit_id ON purchase_records (unit_id)",
    "CREATE INDEX IF NOT EXISTS ix_purchase_records_purchase_date ON purchase_records (purchase_date)",
    "CREATE INDEX IF NOT EXISTS ix_utility_readings_unit_id ON utility_readings (unit_id)",
    "CREATE INDEX IF NOT EXISTS ix_utility_readings_reading_date ON utility_readings (reading_date)",
    "CREATE INDEX IF NOT EXISTS ix_security_deposits_booking_id ON security_deposits (booking_id)",
    "CREATE INDEX IF NOT EXISTS ix_owner_withdrawals_withdrawal_date ON owner_withdrawals (withdrawal_date)",
    "CREATE INDEX IF NOT EXISTS ix_staff_apartment_assignments_user_id ON staff_apartment_assignments (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_staff_apartment_assignments_unit_id ON staff_apartment_assignments (unit_id)",
    "CREATE INDEX IF NOT EXISTS ix_staff_performance_notes_user_id ON staff_performance_notes (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_booking_attachments_booking_id ON booking_attachments (booking_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_attachments_expense_id ON expense_attachments (expense_id)",
    "CREATE INDEX IF NOT EXISTS ix_housekeeping_photos_task_id ON housekeeping_photos (task_id)",
    "CREATE INDEX IF NOT EXISTS ix_maintenance_photos_ticket_id ON maintenance_photos (ticket_id)",
    # The booking timeline endpoint and the new /audit/logs entity filters
    # both filter on (entity_type, entity_id) together — a composite index
    # matches that access pattern directly, rather than two separate
    # single-column indexes the planner would have to intersect.
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_entity ON audit_logs (entity_type, entity_id)",
]


def apply_indexes(bind: Engine | Connection) -> None:
    """Apply the reviewed index set, joining an existing migration transaction when given a Connection."""
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            apply_indexes(conn)
        return

    for stmt in INDEX_STATEMENTS:
        bind.execute(text(stmt))
