import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

PAYMENT_MODES = ("upi", "card", "bank_transfer", "cash", "razorpay")
PAYMENT_STATUSES = ("pending", "completed", "failed", "refunded")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # A refund is its own Payment row with a NEGATIVE amount, not a mutation
    # of the original — this way every existing "total paid" calculation
    # (which just sums Payment.amount for a booking) automatically nets
    # out refunds correctly with zero special-casing anywhere else in the
    # codebase, and the refund itself has its own timestamp/audit trail
    # rather than silently rewriting history.
    refund_of_payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    refund_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Client-supplied, optional — lets a caller safely retry the exact same
    # manual-refund request without creating a second refund row. Only ever
    # set on a refund row (refund_of_payment_id IS NOT NULL); a real payment
    # row never has one. Enforced unique at the DB level, partial (see
    # additive_migrations.py's ux_payments_idempotency_key).
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    reference_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    booking = relationship("Booking")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), unique=True, nullable=False)
    invoice_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    cgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    sgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    igst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    gst_rate_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    itc_available: Mapped[bool] = mapped_column(default=False)
    hsn_code: Mapped[str] = mapped_column(String(10), default="996311")  # accommodation services
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    booking = relationship("Booking")


class InvoiceCounter(Base):
    """Sequential, financial-year-scoped invoice numbering (Indian GST requires this)."""

    __tablename__ = "invoice_counters"

    financial_year: Mapped[str] = mapped_column(String(9), primary_key=True)  # e.g. "2026-2027"
    last_number: Mapped[int] = mapped_column(default=0)


class CreditNote(Base):
    """
    A GST-compliant credit note against a real invoice — issued when an
    already-invoiced amount needs to be reduced (a refund against an
    invoiced booking, a post-invoice correction). Distinct from
    Payment.refund_of_payment_id, which tracks the actual money movement:
    a credit note is the formal DOCUMENT required by GST law whenever
    invoiced value is reduced, not just a record that cash moved.
    GST breakdown is proportional to the original invoice's rate, so a
    partial credit note against a part-CGST/SGST invoice stays correct.
    """

    __tablename__ = "credit_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    credit_note_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # the reduction, excluding GST
    cgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    sgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    igst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # amount + gst portion
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    invoice = relationship("Invoice")


class CreditNoteCounter(Base):
    """Sequential, financial-year-scoped credit note numbering, same
    pattern as InvoiceCounter."""

    __tablename__ = "credit_note_counters"

    financial_year: Mapped[str] = mapped_column(String(9), primary_key=True)
    last_number: Mapped[int] = mapped_column(default=0)
