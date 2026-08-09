import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

GATEWAY_ORDER_STATUSES = ("created", "paid", "failed", "refunded", "partially_refunded")


class GatewayOrder(Base):
    """
    A payment-gateway order (Razorpay 'order' concept). `gateway_order_id`
    is null until a real order is created against the live Razorpay API —
    which requires RAZORPAY_KEY_ID/SECRET this environment doesn't have.
    See payment_gateway/service.py for exactly what that boundary is.
    """

    __tablename__ = "gateway_orders"
    __table_args__ = (CheckConstraint(f"status IN {GATEWAY_ORDER_STATUSES}", name="ck_gateway_order_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    inquiry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inquiries.id"), nullable=True)
    hold_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("booking_holds.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    gateway_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created")
    receipt: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WebhookEvent(Base):
    """Every incoming Razorpay webhook call — signature-verified or not,
    logged for audit and debugging (e.g. 'why didn't this payment update?')."""

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    signature_valid: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # raw JSON body, for audit/debugging
    processed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gateway_refund_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
