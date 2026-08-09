import uuid
from datetime import date, datetime, time

from pydantic import BaseModel


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
