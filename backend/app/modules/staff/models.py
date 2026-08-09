import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

ATTENDANCE_STATUSES = ("present", "absent", "half_day", "on_leave")
LEAVE_STATUSES = ("pending", "approved", "rejected")
PAYROLL_STATUSES = ("draft", "paid")


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_attendance_user_date"),
        CheckConstraint(f"status IN {ATTENDANCE_STATUSES}", name="ck_attendance_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    check_out_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="present")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (CheckConstraint(f"status IN {LEAVE_STATUSES}", name="ck_leave_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", foreign_keys=[user_id])


class Payroll(Base):
    __tablename__ = "payroll"
    __table_args__ = (
        UniqueConstraint("user_id", "period_year", "period_month", name="uq_payroll_user_period"),
        CheckConstraint(f"status IN {PAYROLL_STATUSES}", name="ck_payroll_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    period_year: Mapped[int] = mapped_column(nullable=False)
    period_month: Mapped[int] = mapped_column(nullable=False)  # 1-12
    basic_salary: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    deductions: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    bonus: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    net_pay: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Finance integration: marking payroll paid creates a real Expense
    # (category="salary") so it actually flows into Daily Cashbook,
    # Apartment-adjacent cost totals, and the GST/expense dashboards —
    # the same pattern used for Maintenance ticket costs.
    linked_expense_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("expenses.id"), nullable=True)

    user = relationship("User")


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_start: Mapped[time] = mapped_column(Time, nullable=False)
    shift_end: Mapped[time] = mapped_column(Time, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user = relationship("User")


class Holiday(Base):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("date", name="uq_holiday_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)


class StaffApartmentAssignment(Base):
    """
    Which unit(s) a staff member is responsible for. Lives entirely in the
    Staff module and only references users.id as a foreign key — it does
    NOT add any column to the users table itself, so Auth/Users stays
    untouched per the explicit constraint. A staff member can have more
    than one assignment (multiple apartments), and a unit can have more
    than one assigned staff member.
    """

    __tablename__ = "staff_apartment_assignments"
    __table_args__ = (UniqueConstraint("user_id", "unit_id", name="uq_staff_unit_assignment"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User")
    unit = relationship("Unit")


class StaffPerformanceNote(Base):
    """A dated performance note about a staff member — also only
    references users.id, doesn't touch the users table."""

    __tablename__ = "staff_performance_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", foreign_keys=[user_id])
