import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

NOTIFICATION_EVENTS = (
    "booking_confirmation", "payment_reminder", "checkin_reminder",
    "checkout_reminder", "review_request", "invoice_delivery", "cancellation",
    "guest_request_created", "guest_request_resolved",
    "maintenance_ticket_created", "maintenance_ticket_resolved",
    "payment_received", "expense_approval", "daily_closing_completed",
    "housekeeping_task_created", "housekeeping_task_completed",
    "low_stock_alert", "leave_reviewed", "payroll_paid", "property_created",
    "inquiry_created", "inquiry_follow_up_due",
    "guest_request_escalated", "maintenance_ticket_escalated",
)
NOTIFICATION_CHANNELS = ("email", "whatsapp", "sms", "in_app")


class NotificationLog(Base):
    """
    Every notification attempt, successful or not. Also doubles as the
    de-duplication record for the scheduler — e.g. before sending a
    check-in reminder for a booking, the scheduler checks whether a
    'checkin_reminder' row already exists for that booking, so a job that
    runs every hour doesn't re-send the same reminder on every tick.
    """

    __tablename__ = "notification_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    inquiry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inquiries.id"), nullable=True)
    guest_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guest_requests.id"), nullable=True)
    maintenance_ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("maintenance_tickets.id"), nullable=True)
    housekeeping_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("housekeeping_tasks.id"), nullable=True)
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id"), nullable=True)
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leave_requests.id"), nullable=True)
    payroll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payroll.id"), nullable=True)
    # The specific staff member this notification is FOR — distinct from
    # booking_id/inquiry_id/etc (which is WHAT it's about). Used for the
    # in-app inbox: a staff member sees notifications addressed to them,
    # not every notification in the system.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
