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
    # Phase 16A: the one event_type NOTIFY_STAFF ever writes here.
    "automation_alert",
    # Phase 2A guest auth hardening: OTP delivery attempts (phone or
    # email — the channel column already distinguishes them). Never
    # carries the OTP code/message itself, only recipient + success/
    # error_message, same as every other row in this table.
    "guest_otp_delivery",
    # Phase 3D customer password reset: delivery attempts for the reset
    # email. Never carries the raw reset token/link, only recipient +
    # success/error_message, same as every other row in this table.
    "customer_password_reset",
    # Phase 3E: customer login second-factor OTP delivery attempts (email
    # or SMS — the channel column distinguishes them). Never carries the
    # OTP code itself, only recipient + success/error_message.
    "customer_login_otp",
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

    # Phase 16A: NOTIFY_STAFF fields, added via additive_migrations.py
    # for the already-deployed table. All nullable — every pre-existing
    # notification_logs row has none of these and that's correct; they
    # only ever apply to event_type='automation_alert' rows. action_id
    # links back to the automation ActionProposal that produced this
    # notification (nullable FK, not enforced NOT NULL, so this table
    # stays fully usable by every existing non-automation notification
    # path unchanged). Known limitation, not worked around here: the
    # existing in-app inbox query (notifications/router.py) filters by
    # user_id, so a broadcast row (user_id=NULL, as NOTIFY_STAFF
    # produces) will not surface there without a future change to that
    # query — out of scope for this phase.
    action_id: Mapped[str | None] = mapped_column(String(200), ForeignKey("action_proposals.action_id"), nullable=True)
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
