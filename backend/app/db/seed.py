"""
Bootstrap script: creates DB tables, seeds roles + baseline RBAC permissions,
and creates a default admin user. Run once after migrations (or standalone
in dev): `python -m app.db.seed`
"""
from sqlalchemy import select
from sqlalchemy import text as _text

from app.core.config import settings
from app.core.security import hash_password
from app.db.indexes import apply_indexes
from app.db.session import Base, SessionLocal, engine
from app.modules.auth.models import Permission, PasswordResetToken, Role, User, UserSession

# Import models so they register on Base.metadata before create_all
from app.modules.properties.models import Property, PropertyFile, Unit, UnitPhoto  # noqa: F401
from app.modules.bookings.models import Booking, BookingAttachment, Guest, GuestDocument  # noqa: F401
from app.modules.payments.models import Payment, Invoice, InvoiceCounter, CreditNote, CreditNoteCounter  # noqa: F401
from app.modules.housekeeping.models import HousekeepingTask, HousekeepingPhoto  # noqa: F401
from app.modules.maintenance.models import MaintenanceTicket, MaintenancePhoto  # noqa: F401
from app.modules.inquiries.models import Inquiry, InquiryAttachment, InquiryCounter, InquiryCommunication  # noqa: F401
from app.modules.settings.models import BankSettings, BusinessSettings  # noqa: F401
from app.modules.inventory.models import Supplier, InventoryItem, StockMovement  # noqa: F401
from app.modules.staff.models import Attendance, LeaveRequest, Payroll, Shift, Holiday, StaffApartmentAssignment, StaffPerformanceNote, StaffProfile, StaffAdvance  # noqa: F401
from app.modules.audit.models import AuditLog, LoginEvent  # noqa: F401
from app.modules.notifications.models import NotificationLog  # noqa: F401
from app.modules.payment_gateway.models import GatewayOrder, WebhookEvent, Refund  # noqa: F401
from app.modules.ota.models import OTAChannelConfig, OTAListingMapping, OTASyncLog  # noqa: F401
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncLog, ZotelSyncRecord  # noqa: F401
from app.modules.portal.models import GuestRequest  # noqa: F401
from app.modules.messaging.models import Conversation, ConversationMessage  # noqa: F401
# Phase 1 of the guest-auth redesign — additive foundation only, not yet
# wired to any endpoint (see auth_models.py's module docstring).
from app.modules.portal.auth_models import OtpChallenge, GuestIdentity, GuestSession  # noqa: F401
# Phase 3A-3E of the customer-auth foundation (see customer_auth/models.py's
# module docstring for each table's purpose).
from app.modules.customer_auth.models import CustomerAccount, CustomerSession, CustomerPasswordResetToken, CustomerLoginOtpChallenge, CustomerSocialIdentity  # noqa: F401
from app.modules.public_booking.models import PromoCode, BookingHold  # noqa: F401
from app.modules.finance.models import Expense, ExpenseAttachment, Budget, SecurityDeposit, OwnerWithdrawal, OTACommission, OTAPayoutReceipt, UtilityReading, UtilityReadingAllocation, PurchaseRecord, DailyClosing, LaundryEntry, Loan, LoanEmiPayment  # noqa: F401
from app.modules.automation.models import PolicyRow, ActionProposalRow, PolicyDecisionRow, ApprovalRow, AuditEntryRow, PolicyAuditEntryRow  # noqa: F401
from app.db.additive_migrations import apply_additive_migrations, migrate_business_settings_defaults, migrate_customer_phone_numbers_to_e164, migrate_legacy_bank_settings

# RBAC finding I-5: this dict only seeds rows into the dormant Permission
# table (see Permission's docstring in app/modules/auth/models.py) — it has
# no runtime authorization effect. Real access control is, and remains,
# enforced entirely through require_role(...) in app/shared/rbac.py, which
# checks role NAMES against each route's declared allow-list and never
# reads these rows. Nothing here needs to change for a role's actual API
# access to change — that happens exclusively via require_role(...) edits
# in each module's router.py.
ROLE_PERMISSIONS = {
    # "*"/"*" is stored data only — there is no wildcard-matching logic
    # anywhere in this codebase today, so this row has no runtime meaning.
    # Admin's actual full access comes from require_role(...) allow-lists
    # explicitly including "admin" throughout the codebase, not from this.
    "admin": [("*", "*")],
    "staff": [
        ("bookings", "create"),
        ("bookings", "read"),
        ("bookings", "update"),
        ("guests", "create"),
        ("guests", "read"),
        ("units", "read"),
    ],
    # Manager is intentionally seeded with zero rows here — and always
    # will be under the current design, since this table isn't consulted
    # by anything. Manager's real, extensive operational access (bookings,
    # guests, finance views, pricing, staff_ops, etc.) was granted entirely
    # by adding "manager" to the relevant require_role(...) allow-lists
    # directly in each module's router.py, independent of this table.
    # Populating rows here would have no effect on what Manager can
    # actually do unless this table were later wired into enforcement,
    # which would be a separate, deliberate RBAC project, not a data change.
    "manager": [],
}


def run():
    with engine.connect() as conn:
        conn.execute(_text("CREATE EXTENSION IF NOT EXISTS btree_gist;"))
        conn.execute(_text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    apply_indexes(engine)
    apply_additive_migrations(engine)
    migrate_business_settings_defaults(engine)
    migrate_legacy_bank_settings(engine)
    migrate_customer_phone_numbers_to_e164(engine)

    db = SessionLocal()
    try:
        for role_name, perms in ROLE_PERMISSIONS.items():
            role = db.scalar(select(Role).where(Role.name == role_name))
            if role is None:
                role = Role(name=role_name)
                db.add(role)
                db.flush()
            for resource, action in perms:
                exists = db.scalar(
                    select(Permission).where(
                        Permission.role_id == role.id,
                        Permission.resource == resource,
                        Permission.action == action,
                    )
                )
                if not exists:
                    db.add(Permission(role_id=role.id, resource=resource, action=action))

        db.commit()

        admin_role = db.scalar(select(Role).where(Role.name == "admin"))
        existing_admin = db.scalar(select(User).where(User.email == "homestaypdy@gmail.com"))
        if not existing_admin:
            # RBAC Phase A hardening: no hardcoded default password here or
            # anywhere else in source. bootstrap_admin_password comes only
            # from settings/.env (see core/config.py's own docstring on this
            # field) — a deployment (or a test harness) that hasn't set it
            # gets a clear, loud failure instead of a guessable, previously-
            # hardcoded literal. The password's actual value is never
            # printed, logged, or otherwise persisted anywhere by this
            # function — only its presence/absence is checked.
            if not settings.bootstrap_admin_password:
                raise RuntimeError(
                    "No admin account exists yet and BOOTSTRAP_ADMIN_PASSWORD is not set. "
                    "Set BOOTSTRAP_ADMIN_PASSWORD in your environment/.env before running "
                    "seed for the first time — this creates the initial admin account. "
                    "It is not read again after that first run; rotate/remove it afterward "
                    "if you like."
                )
            db.add(
                User(
                    name="Sri Apartment Homestay Admin",
                    email="homestaypdy@gmail.com",
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role_id=admin_role.id,
                )
            )
            db.commit()
            print("Seeded default admin: homestaypdy@gmail.com (password from BOOTSTRAP_ADMIN_PASSWORD — not printed here; change it after first login)")
        else:
            print("Admin already exists, skipping.")

        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
