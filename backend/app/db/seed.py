"""
Bootstrap script: creates DB tables, seeds roles + baseline RBAC permissions,
and creates a default admin user. Run once after migrations (or standalone
in dev): `python -m app.db.seed`
"""
from sqlalchemy import select
from sqlalchemy import text as _text

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
from app.modules.settings.models import BusinessSettings  # noqa: F401
from app.modules.inventory.models import Supplier, InventoryItem, StockMovement  # noqa: F401
from app.modules.staff.models import Attendance, LeaveRequest, Payroll, Shift, Holiday, StaffApartmentAssignment, StaffPerformanceNote  # noqa: F401
from app.modules.audit.models import AuditLog, LoginEvent  # noqa: F401
from app.modules.notifications.models import NotificationLog  # noqa: F401
from app.modules.payment_gateway.models import GatewayOrder, WebhookEvent, Refund  # noqa: F401
from app.modules.ota.models import OTAChannelConfig, OTAListingMapping, OTASyncLog  # noqa: F401
from app.modules.portal.models import GuestRequest  # noqa: F401
from app.modules.public_booking.models import PromoCode, BookingHold  # noqa: F401
from app.modules.finance.models import Expense, ExpenseAttachment, Budget, SecurityDeposit, OwnerWithdrawal, OTACommission, UtilityReading, UtilityReadingAllocation, PurchaseRecord, DailyClosing, LaundryEntry  # noqa: F401
from app.db.additive_migrations import apply_additive_migrations

ROLE_PERMISSIONS = {
    "admin": [("*", "*")],  # wildcard: full access, checked specially in RBAC if needed later
    "staff": [
        ("bookings", "create"),
        ("bookings", "read"),
        ("bookings", "update"),
        ("guests", "create"),
        ("guests", "read"),
        ("units", "read"),
    ],
}


def run():
    with engine.connect() as conn:
        conn.execute(_text("CREATE EXTENSION IF NOT EXISTS btree_gist;"))
        conn.execute(_text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    apply_indexes(engine)
    apply_additive_migrations(engine)

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
            db.add(
                User(
                    name="Sri Apartment Homestay Admin",
                    email="homestaypdy@gmail.com",
                    password_hash=hash_password("Sri@2026"),
                    role_id=admin_role.id,
                )
            )
            db.commit()
            print("Seeded default admin: homestaypdy@gmail.com / Sri@2026 (CHANGE THIS AFTER FIRST LOGIN)")
        else:
            print("Admin already exists, skipping.")

        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
