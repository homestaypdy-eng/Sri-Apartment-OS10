import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import DDL
from sqlalchemy.event import listens_for

from app.db.session import Base

DISCOUNT_TYPES = ("percent", "flat")

HOLD_STATUSES = ("pending_payment", "paid", "converted", "expired", "cancelled")


class PromoCode(Base):
    __tablename__ = "promo_codes"
    __table_args__ = (CheckConstraint(f"discount_type IN {DISCOUNT_TYPES}", name="ck_promo_discount_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    discount_type: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # percent (0-100) or flat rupee amount
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    max_uses: Mapped[int | None] = mapped_column(nullable=True)  # null = unlimited
    used_count: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def is_valid_today(self) -> bool:
        today = date.today()
        if not self.active:
            return False
        if not (self.valid_from <= today <= self.valid_to):
            return False
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return False
        return True

    def compute_discount(self, subtotal: float) -> float:
        if self.discount_type == "percent":
            return round(subtotal * float(self.value) / 100, 2)
        return min(float(self.value), subtotal)  # flat discount never exceeds the subtotal


class BookingHold(Base):
    """
    A temporary reservation hold created when a guest initiates the online
    payment flow. The unit is reserved for HOLD_TTL_MINUTES minutes while
    the guest completes payment. A hold becomes a confirmed Booking only
    after server-side Razorpay payment verification succeeds.

    State machine:
      pending_payment → paid (payment verified) → converted (booking created)
      pending_payment → expired (TTL exceeded before payment)
      pending_payment → cancelled (manual or failed payment)
      paid → cancelled (booking creation race failure — rare)
    """

    __tablename__ = "booking_holds"
    __table_args__ = (
        CheckConstraint(f"status IN {HOLD_STATUSES}", name="ck_hold_status"),
        CheckConstraint("check_out > check_in", name="ck_hold_checkout_after_checkin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)

    # Guest info captured at hold creation; used to look up or create Guest on conversion
    guest_name: Mapped[str] = mapped_column(String(150), nullable=False)
    guest_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Dates
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    nights: Mapped[int] = mapped_column(nullable=False)

    # Pricing — all computed server-side at hold creation, never accepted from client
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)       # pre-discount
    discount_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    promo_code_used: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    sgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    igst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    gst_rate_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    advance_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # = total
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # State
    status: Mapped[str] = mapped_column(String(20), default="pending_payment")

    # Payment gateway linkage
    gateway_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gateway_orders.id"), nullable=True
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Set to the resulting booking's id on successful conversion
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bookings.id"), nullable=True
    )

    # Timestamps
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# --- Overlap-safe hold constraint ---
# Mirrors the bookings EXCLUDE constraint: no two active holds (pending_payment
# or paid) may overlap for the same unit. This is the database-level guard
# against two simultaneous guests locking the same dates.
_add_hold_range_and_exclude = DDL(
    """
    ALTER TABLE booking_holds ADD COLUMN IF NOT EXISTS hold_range daterange
        GENERATED ALWAYS AS (daterange(check_in, check_out, '[)')) STORED;

    ALTER TABLE booking_holds ADD CONSTRAINT no_overlapping_holds
        EXCLUDE USING gist (
            unit_id WITH =,
            hold_range WITH &&
        ) WHERE (status IN ('pending_payment', 'paid'));

    CREATE INDEX IF NOT EXISTS ix_holds_unit_checkin ON booking_holds (unit_id, check_in);
    CREATE INDEX IF NOT EXISTS ix_holds_status ON booking_holds (status);
    CREATE INDEX IF NOT EXISTS ix_holds_expires_at ON booking_holds (expires_at);
    """
)


@listens_for(BookingHold.__table__, "after_create")
def _apply_hold_overlap_constraint(target, connection, **kw):
    connection.execute(_add_hold_range_and_exclude)
