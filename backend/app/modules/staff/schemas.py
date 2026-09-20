import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field


class AttendanceCreate(BaseModel):
    user_id: uuid.UUID
    date: date
    check_in_time: time | None = None
    check_out_time: time | None = None
    status: str = "present"
    notes: str | None = None


class AttendanceUpdate(BaseModel):
    """All fields optional — used to record a check-out later than the
    initial mark_attendance call, or to correct/backfill a missed one."""
    check_in_time: time | None = None
    check_out_time: time | None = None
    status: str | None = None
    notes: str | None = None


class AttendanceOut(AttendanceCreate):
    id: uuid.UUID
    created_at: datetime
    total_presence_minutes: int | None = None
    lunch_break_minutes_applied: int | None = None
    working_minutes: int | None = None
    overtime_minutes: int | None = None

    class Config:
        from_attributes = True


class LeaveRequestCreate(BaseModel):
    user_id: uuid.UUID
    from_date: date
    to_date: date
    reason: str | None = None


class LeaveRequestUpdate(BaseModel):
    status: str  # approved / rejected


class LeaveRequestOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    from_date: date
    to_date: date
    reason: str | None
    status: str
    reviewed_by: uuid.UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class PayrollCreate(BaseModel):
    user_id: uuid.UUID
    period_year: int
    period_month: int
    basic_salary: float
    deductions: float = 0
    bonus: float = 0


class PayrollOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    period_year: int
    period_month: int
    basic_salary: float
    deductions: float
    bonus: float
    net_pay: float
    status: str
    paid_at: datetime | None
    created_at: datetime
    linked_expense_id: uuid.UUID | None = None

    class Config:
        from_attributes = True


class ShiftCreate(BaseModel):
    user_id: uuid.UUID
    date: date
    shift_start: time
    shift_end: time
    notes: str | None = None


class ShiftOut(ShiftCreate):
    id: uuid.UUID

    class Config:
        from_attributes = True


class HolidayCreate(BaseModel):
    date: date
    name: str


class HolidayOut(HolidayCreate):
    id: uuid.UUID

    class Config:
        from_attributes = True


class StaffApartmentAssignmentCreate(BaseModel):
    user_id: uuid.UUID
    unit_id: uuid.UUID


class StaffApartmentAssignmentOut(StaffApartmentAssignmentCreate):
    id: uuid.UUID
    assigned_at: datetime

    class Config:
        from_attributes = True


class StaffPerformanceNoteCreate(BaseModel):
    user_id: uuid.UUID
    note: str


class StaffPerformanceNoteOut(StaffPerformanceNoteCreate):
    id: uuid.UUID
    created_by: uuid.UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class StaffProfileCreate(BaseModel):
    user_id: uuid.UUID
    employee_id: str | None = None
    designation: str | None = None
    department: str | None = None
    employment_type: str | None = None
    join_date: date | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    address: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    emergency_contact_relationship: str | None = None
    id_type: str | None = None
    id_number: str | None = None
    bank_name: str | None = None
    account_holder_name: str | None = None
    account_number: str | None = None
    ifsc: str | None = None
    payment_method: str | None = None
    weekly_off_day: str | None = None
    supervisor_user_id: uuid.UUID | None = None
    notes: str | None = None
    designation_id: uuid.UUID | None = None
    salary_override: float | None = Field(default=None, ge=0)
    shift_start_override: time | None = None
    shift_end_override: time | None = None
    lunch_break_override_minutes: int | None = Field(default=None, ge=0)
    standard_working_hours_override: float | None = Field(default=None, gt=0)
    leave_entitlement_override: int | None = Field(default=None, ge=0)


class StaffProfileUpdate(BaseModel):
    """All fields optional — user_id is intentionally excluded, the 1:1
    link to a User is set once at creation and never repointed."""
    employee_id: str | None = None
    designation: str | None = None
    department: str | None = None
    employment_type: str | None = None
    join_date: date | None = None
    exit_date: date | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    address: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    emergency_contact_relationship: str | None = None
    id_type: str | None = None
    id_number: str | None = None
    bank_name: str | None = None
    account_holder_name: str | None = None
    account_number: str | None = None
    ifsc: str | None = None
    payment_method: str | None = None
    weekly_off_day: str | None = None
    supervisor_user_id: uuid.UUID | None = None
    notes: str | None = None
    designation_id: uuid.UUID | None = None
    salary_override: float | None = Field(default=None, ge=0)
    shift_start_override: time | None = None
    shift_end_override: time | None = None
    lunch_break_override_minutes: int | None = Field(default=None, ge=0)
    standard_working_hours_override: float | None = Field(default=None, gt=0)
    leave_entitlement_override: int | None = Field(default=None, ge=0)


class StaffProfileOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    employee_id: str | None
    designation: str | None
    department: str | None
    employment_type: str | None
    join_date: date | None
    exit_date: date | None
    is_active: bool
    phone: str | None
    alternate_phone: str | None
    date_of_birth: date | None
    address: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    emergency_contact_relationship: str | None
    id_type: str | None
    id_number: str | None
    bank_name: str | None
    account_holder_name: str | None
    account_number: str | None
    ifsc: str | None
    payment_method: str | None
    weekly_off_day: str | None
    supervisor_user_id: uuid.UUID | None
    notes: str | None
    created_at: datetime
    designation_id: uuid.UUID | None = None
    salary_override: float | None = None
    shift_start_override: time | None = None
    shift_end_override: time | None = None
    lunch_break_override_minutes: int | None = None
    standard_working_hours_override: float | None = None
    leave_entitlement_override: int | None = None

    class Config:
        from_attributes = True


# --- Staff Designations (post/designation master: defaults an employee's
# overrides fall back to — see StaffProfile.*_override and hr_policy.py) ---

class StaffDesignationCreate(BaseModel):
    name: str
    default_basic_salary: float = Field(gt=0)
    default_shift_start: time
    default_shift_end: time
    default_lunch_break_minutes: int = Field(default=60, ge=0)
    standard_working_hours: float = Field(default=8.0, gt=0)
    leave_entitlement_per_month: int = Field(default=4, ge=0)
    overtime_mode: str = "tracked_no_pay"  # untracked / tracked_no_pay / tracked_with_rate
    overtime_rate_multiplier: float | None = None


class StaffDesignationUpdate(BaseModel):
    """All fields optional. Changing a default here only affects staff
    profiles that don't have their own override for that field, and only
    from this point forward — already-recorded Attendance snapshots and
    already-generated Payroll rows are never retroactively changed."""
    name: str | None = None
    default_basic_salary: float | None = Field(default=None, gt=0)
    default_shift_start: time | None = None
    default_shift_end: time | None = None
    default_lunch_break_minutes: int | None = Field(default=None, ge=0)
    standard_working_hours: float | None = Field(default=None, gt=0)
    leave_entitlement_per_month: int | None = Field(default=None, ge=0)
    overtime_mode: str | None = None
    overtime_rate_multiplier: float | None = None


class StaffDesignationOut(BaseModel):
    id: uuid.UUID
    name: str
    default_basic_salary: float
    default_shift_start: time
    default_shift_end: time
    default_lunch_break_minutes: int
    standard_working_hours: float
    leave_entitlement_per_month: int
    overtime_mode: str
    overtime_rate_multiplier: float | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# --- Resolved policy / monthly summary (read-only, computed) ---

class ResolvedPolicyOut(BaseModel):
    user_id: uuid.UUID
    designation_id: uuid.UUID | None
    designation_name: str | None
    basic_salary: float | None
    shift_start: time | None
    shift_end: time | None
    lunch_break_minutes: int | None
    standard_working_hours: float | None
    leave_entitlement_per_month: int | None
    overtime_mode: str | None
    overtime_rate_multiplier: float | None
    source: dict[str, str]  # per-field: "override" | "designation" | "unconfigured"


class AttendanceMonthlySummaryOut(BaseModel):
    user_id: uuid.UUID
    year: int
    month: int
    days_recorded: int
    total_presence_minutes: int
    total_working_minutes: int
    total_overtime_minutes: int
    approved_leave_days_in_month: int
    leave_entitlement_per_month: int | None
    free_leave_days: int
    excess_leave_days: int


class StaffAdvanceCreate(BaseModel):
    user_id: uuid.UUID
    date: date
    amount: float = Field(gt=0)
    category: str = "advance"  # advance/bonus/reimbursement/loan
    reason: str | None = None
    payment_method: str | None = None
    reference_number: str | None = None


class StaffAdvanceUpdate(BaseModel):
    """Deliberately excludes deducted_amount/status transitions driven by
    deduction — those only happen through POST /advances/{id}/deduct so the
    outstanding-balance math can never be bypassed by a raw field edit."""
    reason: str | None = None
    payment_method: str | None = None
    reference_number: str | None = None


class StaffAdvanceDeduct(BaseModel):
    amount: float = Field(gt=0)
    payroll_id: uuid.UUID | None = None


class StaffAdvanceOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    date: date
    amount: float
    category: str
    reason: str | None
    payment_method: str | None
    reference_number: str | None
    status: str
    deducted_amount: float
    outstanding_amount: float
    linked_payroll_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime

    class Config:
        from_attributes = True
