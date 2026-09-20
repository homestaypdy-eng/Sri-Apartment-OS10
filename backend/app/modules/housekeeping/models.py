import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

TASK_TYPES = ("checkout_clean", "routine", "deep_clean", "inspection")
TASK_STATUSES = ("pending", "in_progress", "completed")


class HousekeepingTask(Base):
    __tablename__ = "housekeeping_tasks"
    __table_args__ = (
        CheckConstraint(f"task_type IN {TASK_TYPES}", name="ck_housekeeping_task_type"),
        CheckConstraint(f"status IN {TASK_STATUSES}", name="ck_housekeeping_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    task_type: Mapped[str] = mapped_column(String(20), default="routine")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # set when status first moves to in_progress
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    unit = relationship("Unit")


class HousekeepingPhoto(Base):
    """A photo attached to a housekeeping task — e.g. an inspection photo
    or an issue found while cleaning. Same local-disk-storage pattern as
    BookingAttachment/ExpenseAttachment."""

    __tablename__ = "housekeeping_photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("housekeeping_tasks.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
