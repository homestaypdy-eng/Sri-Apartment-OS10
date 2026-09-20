import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

REQUEST_TYPES = (
    "service", "complaint", "extend_stay",
    # Staff-operational request types (Phase 18A: Staff Operations) — logged
    # by staff on a guest's behalf (phone call, in person), distinct from
    # the guest-self-service types above but sharing the same table/workflow
    # so there's one request queue, not two.
    "towels", "linens", "cleaning", "water", "maintenance", "check_in_help", "check_out_help", "other",
)
REQUEST_STATUSES = ("open", "in_progress", "resolved")
REQUEST_PRIORITIES = ("low", "medium", "high", "urgent")


class GuestRequest(Base):
    __tablename__ = "guest_requests"
    __table_args__ = (
        CheckConstraint(f"request_type IN {REQUEST_TYPES}", name="ck_guest_request_type"),
        CheckConstraint(f"status IN {REQUEST_STATUSES}", name="ck_guest_request_status"),
        CheckConstraint(f"priority IN {REQUEST_PRIORITIES}", name="ck_guest_request_priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guests.id"), nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    request_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requested_check_out_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # for extend_stay requests
    status: Mapped[str] = mapped_column(String(20), default="open")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    staff_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    guest_feedback_rating: Mapped[int | None] = mapped_column(nullable=True)  # 1-5, given by the guest after resolution
    guest_feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    guest = relationship("Guest")
    booking = relationship("Booking")
