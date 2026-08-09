"""
All new financial tracking. Deliberately separate tables from the existing
Payment/Invoice/Booking models — nothing here modifies how revenue,
payments, or invoices are recorded or calculated. Security deposits and
owner withdrawals in particular are tracked as pure cash-movement ledgers
that never feed into the revenue/profit calculations in reports/service.py.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

DEFAULT_EXPENSE_CATEGORIES = ("electricity", "water", "laundry", "cleaning", "internet", "salary", "maintenance", "purchases", "other")
# Kept for backward compatibility with existing imports elsewhere in the codebase — but see note
# on Expense.category below: this is now a suggested list, not an enforced constraint.
EXPENSE_CATEGORIES = DEFAULT_EXPENSE_CATEGORIES
EXPENSE_APPROVAL_STATUSES = ("pending", "approved", "rejected")
DEPOSIT_STATUSES = ("held", "refunded", "partially_refunded")
OTA_CHANNELS_FOR_COMMISSION = ("booking_com", "airbnb", "agoda", "direct")


class Expense(Base):
    __tablename__ = "expenses"
    # NOTE: deliberately no CheckConstraint on category anymore. The
    # business asked for custom expense categories — DEFAULT_EXPENSE_CATEGORIES
    # above is what the UI offers as quick-pick suggestions, but any
    # free-text category is accepted and stored as typed. A malicious or
    # malformed category is a display/reporting nuisance, not a data-
    # integrity risk, so a hard DB constraint isn't the right trade-off
    # once "type your own category" is a real requirement.

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Null = a central/common expense not tied to one apartment (matches
    # the business's own spreadsheet, which splits expenses into per-flat
    # rows like electricity/maintenance AND a "Central" row for shared
    # costs like staff salaries or GST/tax/commission). Setting this lets
    # the apartment-wise P&L attribute an expense to the right unit instead
    # of every expense being business-wide by default.
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    approval_status: Mapped[str] = mapped_column(String(10), default="approved")  # most expenses are just recorded after the fact; "pending" is opt-in for whoever wants a review step
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")


class ExpenseAttachment(Base):
    """A bill/receipt attached to an expense — same local-disk-storage
    pattern and durability caveat as BookingAttachment."""

    __tablename__ = "expense_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expense_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("expenses.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UtilityReading(Base):
    """
    A meter-reading-based utility bill. unit_id is nullable — null means
    this bill is shared across multiple apartments rather than billed to
    one; use UtilityReadingAllocation to record how a shared bill splits
    across units (equal split by default, or explicit custom amounts).
    """

    __tablename__ = "utility_readings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    utility_type: Mapped[str] = mapped_column(String(30), default="electricity")
    meter_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reading_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_units: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    closing_units: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    rate_per_unit: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")

    @property
    def units_consumed(self) -> float:
        return float(self.closing_units) - float(self.opening_units)

    @property
    def amount(self) -> float:
        return round(self.units_consumed * float(self.rate_per_unit), 2)


class UtilityReadingAllocation(Base):
    """
    How a shared utility bill (UtilityReading.unit_id is null) splits
    across specific apartments. share_amount is explicit — if the business
    wants an equal split, the API computes and stores that as explicit
    amounts at creation time rather than re-deriving it differently on
    every read, so a later reading count/order change doesn't silently
    reshuffle an already-recorded split.
    """

    __tablename__ = "utility_reading_allocations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    utility_reading_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("utility_readings.id"), nullable=False)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    share_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    unit = relationship("Unit")


class PurchaseRecord(Base):
    """
    A purchase register entry with a free-text item description — distinct
    from inventory/models.py::StockMovement, which is tied to a specific
    standing inventory item and quantity. This is for ad-hoc purchases
    (matches the business's own spreadsheet's 'Purchase' table) — not
    every purchased item needs to exist as a tracked inventory SKU first.
    category is free-text (like Expense.category) to support custom
    categories rather than a fixed list.
    """

    __tablename__ = "purchase_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    item_description: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. groceries, cleaning items, furniture — free text
    quantity: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # quantity*unit_price if both given, else entered directly
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")


class DailyClosing(Base):
    """
    A formal end-of-day cash-register closing — opening balance, total
    cash in/out for the day, computed closing balance, who closed it and
    when. Once closed, a day is meant to be locked (the API enforces this:
    only one closing per date, and re-closing an already-closed date is
    rejected) — this is the 'close the books' step the business's paper/
    Excel workflow currently does manually at day's end.
    """

    __tablename__ = "daily_closings"
    __table_args__ = (UniqueConstraint("closing_date", name="uq_daily_closing_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    closing_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    total_cash_in: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    total_cash_out: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    closing_balance: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("category", "period_year", "period_month", name="uq_budget_category_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    period_year: Mapped[int] = mapped_column(nullable=False)
    period_month: Mapped[int] = mapped_column(nullable=False)
    budgeted_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)


class SecurityDeposit(Base):
    """
    A refundable security deposit tied to a booking, tracked entirely
    separately from Payment/revenue — a deposit is not income, it's money
    held on the guest's behalf, and must never appear in revenue reports.
    Booking.security_deposit (the existing flat field) still records the
    amount collected at booking time for backward-compat display; this
    table is the actual lifecycle ledger (received -> held -> refunded /
    partially refunded) and the source of truth for pending refunds.
    """

    __tablename__ = "security_deposits"
    __table_args__ = (CheckConstraint(f"status IN {DEPOSIT_STATUSES}", name="ck_deposit_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="held")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    refund_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    booking = relationship("Booking")

    @property
    def pending_refund_amount(self) -> float:
        return float(self.amount) - float(self.refund_amount)


class OwnerWithdrawal(Base):
    """
    Owner drawings — tracked as its own independent cash-movement ledger.
    NEVER subtracted in the revenue/profit calculations in
    reports/service.py — an owner taking cash out doesn't change how much
    the business earned, only how much cash remains in it.
    """

    __tablename__ = "owner_withdrawals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    withdrawal_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class OTACommission(Base):
    """
    Commission record for a booking made through an OTA channel (or
    'direct', recorded with 0% for completeness). Kept as its own table
    rather than columns on Booking so a commission rate can be recorded or
    corrected after the fact without touching the booking record, and so a
    booking with no commission record yet means 'not recorded', not '0%'.
    """

    __tablename__ = "ota_commissions"
    __table_args__ = (
        CheckConstraint(f"channel IN {OTA_CHANNELS_FOR_COMMISSION}", name="ck_commission_channel"),
        UniqueConstraint("booking_id", name="uq_commission_booking"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    commission_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    commission_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    ota_gst_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)  # GST the OTA charges us on their commission invoice
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    booking = relationship("Booking")


LAUNDRY_ITEM_TYPES = ("bedsheet", "pillow_cover", "bath_towel", "face_towel", "blanket", "curtains", "other")


class LaundryEntry(Base):
    """
    The Laundry Register — a real, separate register matching the
    business's own Excel workflow (bedsheet/pillow cover/bath towel/face
    towel/blanket/curtains counts, each with its own quantity and rate).
    Distinct from a generic Expense — this tracks exactly which items and
    how many, not just a lump-sum "laundry" cost, so the monthly summary
    can show item-level totals the way the Excel sheet does.
    """

    __tablename__ = "laundry_entries"
    __table_args__ = (CheckConstraint(f"item_type IN {LAUNDRY_ITEM_TYPES}", name="ck_laundry_item_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    rate_per_item: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id"), nullable=True)  # null = central/shared laundry, same attribution pattern as Expense.unit_id
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")

    @property
    def total_amount(self) -> float:
        return round(self.quantity * float(self.rate_per_item), 2)
