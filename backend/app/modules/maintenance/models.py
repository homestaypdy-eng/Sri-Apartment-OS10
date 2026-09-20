import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

TICKET_PRIORITIES = ("low", "medium", "high", "urgent")
TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
# App-layer validated (like priority/status) rather than a DB CHECK
# constraint, matching the pattern of other free-choice classification
# fields in this codebase (e.g. Booking.source) — nullable so existing
# tickets created before this field existed are simply uncategorized.
TICKET_CATEGORIES = ("ac", "electrical", "plumbing", "water", "appliance", "furniture", "internet", "other")


class MaintenanceTicket(Base):
    __tablename__ = "maintenance_tickets"
    __table_args__ = (
        CheckConstraint(f"priority IN {TICKET_PRIORITIES}", name="ck_maintenance_priority"),
        CheckConstraint(f"status IN {TICKET_STATUSES}", name="ck_maintenance_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    reported_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="open")
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a ticket with a cost is resolved/closed, a real Expense record
    # is created automatically (category="maintenance", tagged to this
    # ticket's unit) so the cost actually flows into Apartment P&L, Cash
    # Flow, and Daily Cashbook — not just a number sitting on the ticket
    # itself. This FK tracks that link so re-saving the same resolved
    # ticket updates the existing expense's amount instead of creating a
    # second one every time.
    linked_expense_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("expenses.id"), nullable=True)

    unit = relationship("Unit")


PHOTO_TYPES = ("before", "after")


class MaintenancePhoto(Base):
    """A before/after photo attached to a maintenance ticket. photo_type
    distinguishes the two explicitly rather than relying on upload order,
    since tickets can get multiple photos of each kind over time."""

    __tablename__ = "maintenance_photos"
    __table_args__ = (CheckConstraint(f"photo_type IN {PHOTO_TYPES}", name="ck_maintenance_photo_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("maintenance_tickets.id"), nullable=False)
    photo_type: Mapped[str] = mapped_column(String(10), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
