import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

ATTENDANCE_STATUSES = ("present", "absent", "half_day", "on_leave")
LEAVE_STATUSES = ("pending", "approved", "rejected")
PAYROLL_STATUSES = ("draft", "paid")
STAFF_ADVANCE_STATUSES = ("pending", "partially_deducted", "fully_deducted")
# "tracked_no_pay": overtime minutes are computed and shown, never auto-paid.
# "tracked_with_rate": same, plus overtime_rate_multiplier is set, as a data
# hook for a future auto-bonus calculation — nothing in this phase reads it
# to compute a bonus automatically (no rate rule has been defined yet).
# "untracked": overtime is not computed at all for this designation.
DESIGNATION_OVERTIME_MODES = ("untracked", "tracked_no_pay", "tracked_with_rate")


class StaffDesignation(Base):
    """
    Post/designation master — the level at which default salary, shift
    timing, lunch break, standard working hours, overtime handling, and
    leave entitlement are configured by an Admin. An individual
    StaffProfile's *_override fields take precedence over these defaults
    when set (see StaffProfile below and staff/hr_policy.py for the
    resolution order) — this table only holds what applies when nothing
    is overridden. Soft-deactivation only, same as Property/Unit — a
    designation already referenced by staff profiles is never hard-deleted.
    """

    __tablename__ = "staff_designations"
    __table_args__ = (
        UniqueConstraint("name", name="uq_staff_designation_name"),
        CheckConstraint(f"overtime_mode IN {DESIGNATION_OVERTIME_MODES}", name="ck_designation_overtime_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "Manager", "Housekeeping" — admin-defined, not a fixed enum
    default_basic_salary: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    default_shift_start: Mapped[time] = mapped_column(Time, nullable=False)
    default_shift_end: Mapped[time] = mapped_column(Time, nullable=False)
    default_lunch_break_minutes: Mapped[int] = mapped_column(default=60)
    standard_working_hours: Mapped[float] = mapped_column(Numeric(4, 2), default=8.00)
    leave_entitlement_per_month: Mapped[int] = mapped_column(default=4)
    overtime_mode: Mapped[str] = mapped_column(String(20), default="tracked_no_pay")
    overtime_rate_multiplier: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)  # soft-deactivation — never hard-deleted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
    # Computed once, at the moment both check_in_time and check_out_time
    # are known (see staff/hr_policy.py compute_attendance_hours), using
    # whatever policy (designation default / employee override) resolved
    # for this user AT THAT TIME. Deliberately a snapshot, not a live
    # computed value — if the designation's standard hours change next
    # month, this month's already-recorded attendance must not silently
    # change. All four stay NULL until both times are present; a missing
    # check-out is never guessed at.
    total_presence_minutes: Mapped[int | None] = mapped_column(nullable=True)
    lunch_break_minutes_applied: Mapped[int | None] = mapped_column(nullable=True)
    working_minutes: Mapped[int | None] = mapped_column(nullable=True)
    overtime_minutes: Mapped[int | None] = mapped_column(nullable=True)

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


class StaffProfile(Base):
    """
    A 1:1 HR profile attached to an existing User — deliberately NOT a
    replacement identity. Attendance/LeaveRequest/Payroll/Shift/
    StaffPerformanceNote/StaffApartmentAssignment all keep referencing
    users.id exactly as before; this table only adds employment/personal/
    bank data that has nowhere else to live, without touching any of them
    or the users table itself. A User can exist with no StaffProfile row
    (every existing sub-module keeps working either way) — this table is
    purely additive.
    """

    __tablename__ = "staff_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    # Identity / employment
    employee_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(100), nullable=True)  # free text, e.g. "Housekeeping Supervisor" — not an enum
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # e.g. full_time/part_time/contract
    join_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)  # employment status — independent of User.is_active (login access)

    # Contact
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    alternate_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Personal
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Emergency contact
    emergency_contact_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    emergency_contact_relationship: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ID / documents (single-field, same pattern as Guest.id_proof_* — no upload subsystem in this phase)
    id_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    id_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Bank / payroll preference — NOT a salary field; actual pay history lives in Payroll
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    account_holder_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ifsc: Mapped[str | None] = mapped_column(String(15), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Work
    weekly_off_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    supervisor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Post/designation link + employee-specific overrides. designation
    # (free-text, above) is kept exactly as-is for display/back-compat;
    # designation_id is the authoritative link to StaffDesignation once
    # set. Every *_override field below is nullable and, when NULL, falls
    # back to the linked designation's default — same "unset = inherit
    # parent default" convention as Unit.weekend_rate. Resolution is done
    # by staff/hr_policy.py:resolve_employee_policy, never inline here.
    designation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_designations.id"), nullable=True)
    salary_override: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    shift_start_override: Mapped[time | None] = mapped_column(Time, nullable=True)
    shift_end_override: Mapped[time | None] = mapped_column(Time, nullable=True)
    lunch_break_override_minutes: Mapped[int | None] = mapped_column(nullable=True)
    standard_working_hours_override: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    leave_entitlement_override: Mapped[int | None] = mapped_column(nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", foreign_keys=[user_id])
    supervisor = relationship("User", foreign_keys=[supervisor_user_id])
    # Named distinctly from the pre-existing free-text `designation` column
    # above (String) to avoid a Python attribute collision on this class.
    designation_ref = relationship("StaffDesignation", foreign_keys=[designation_id])


class StaffAdvance(Base):
    """
    A salary advance/bonus/reimbursement/loan given to a staff member,
    with partial-deduction tracking against future payroll. References
    user_id directly (same convention as every other staff-domain table)
    rather than staff_profiles.id, so it works even before a StaffProfile
    exists for that user. deducted_amount is a running total updated only
    via the /advances/{id}/deduct endpoint — outstanding balance is always
    amount - deducted_amount, computed on read, never stored separately.
    """

    __tablename__ = "staff_advances"
    __table_args__ = (CheckConstraint(f"status IN {STAFF_ADVANCE_STATUSES}", name="ck_staff_advance_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="advance")  # advance/bonus/reimbursement/loan
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    deducted_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    linked_payroll_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payroll.id"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", foreign_keys=[user_id])

    @property
    def outstanding_amount(self) -> float:
        return float(self.amount) - float(self.deducted_amount)
