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


class AttendanceOut(AttendanceCreate):
    id: uuid.UUID
    created_at: datetime

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

    class Config:
        from_attributes = True


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
