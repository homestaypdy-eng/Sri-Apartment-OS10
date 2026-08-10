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
]

# Property files (documents/photos) is a genuinely new table, not an
# ALTER on an existing one — create_all in seed.py handles new tables
# automatically since PropertyFile is a real ORM model, so no explicit
# CREATE TABLE statement is needed here.


def apply_additive_migrations(engine: Engine) -> None:
    with engine.connect() as conn:
        for stmt in MIGRATION_STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()
