import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.staff.models import Attendance, Holiday, LeaveRequest, Payroll, Shift, StaffApartmentAssignment, StaffPerformanceNote
from app.modules.staff.schemas import (
    AttendanceCreate,
    AttendanceOut,
    HolidayCreate,
    HolidayOut,
    LeaveRequestCreate,
    LeaveRequestOut,
    LeaveRequestUpdate,
    PayrollCreate,
    PayrollOut,
    ShiftCreate,
    ShiftOut,
    StaffApartmentAssignmentCreate,
    StaffApartmentAssignmentOut,
    StaffPerformanceNoteCreate,
    StaffPerformanceNoteOut,
)
from app.modules.audit.schemas import record_audit_log
from app.modules.finance.models import Expense
from app.modules.notifications.service import trigger_leave_reviewed, trigger_payroll_paid
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


# --- Attendance ---

@router.post("/attendance", response_model=AttendanceOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def mark_attendance(payload: AttendanceCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    record = Attendance(**payload.model_dump())
    db.add(record)
    try:
        db.flush()
        record_audit_log(db, user.id, "staff.attendance.mark", entity_type="attendance", entity_id=str(record.id), details=f"{payload.status} for user {payload.user_id} on {payload.date}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Attendance already recorded for this user on this date")
    db.refresh(record)
    return record


@router.get("/attendance", response_model=list[AttendanceOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_attendance(user_id: uuid.UUID | None = None, limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    query = select(Attendance).order_by(Attendance.date.desc(), Attendance.id)
    if user_id:
        query = query.where(Attendance.user_id == user_id)
    return db.scalars(query.offset(offset).limit(limit)).all()


# --- Leave ---

@router.post("/leave", response_model=LeaveRequestOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def apply_leave(payload: LeaveRequestCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.to_date < payload.from_date:
        raise HTTPException(400, "to_date must be on or after from_date")
    leave = LeaveRequest(**payload.model_dump(), status="pending")
    db.add(leave)
    db.flush()
    record_audit_log(db, user.id, "staff.leave.apply", entity_type="leave_request", entity_id=str(leave.id), details=f"{payload.from_date} to {payload.to_date}")
    db.commit()
    db.refresh(leave)
    return leave


@router.get("/leave", response_model=list[LeaveRequestOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_leave(user_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_db)):
    query = select(LeaveRequest).order_by(LeaveRequest.created_at.desc())
    if user_id:
        query = query.where(LeaveRequest.user_id == user_id)
    if status:
        query = query.where(LeaveRequest.status == status)
    return db.scalars(query).all()


@router.patch(
    "/leave/{leave_id}", response_model=LeaveRequestOut, dependencies=[Depends(require_role("admin"))]
)
def review_leave(leave_id: uuid.UUID, payload: LeaveRequestUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    leave = db.get(LeaveRequest, leave_id)
    if leave is None:
        raise HTTPException(404, "Leave request not found")
    if payload.status not in ("approved", "rejected"):
        raise HTTPException(400, "status must be 'approved' or 'rejected'")
    leave.status = payload.status
    leave.reviewed_by = user.id
    record_audit_log(db, user.id, "staff.leave.review", entity_type="leave_request", entity_id=str(leave.id), details=f"{payload.status}")
    trigger_leave_reviewed(db, leave)
    db.commit()
    db.refresh(leave)
    return leave


# --- Payroll ---

@router.post("/payroll", response_model=PayrollOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def generate_payroll(payload: PayrollCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not (1 <= payload.period_month <= 12):
        raise HTTPException(400, "period_month must be between 1 and 12")
    net_pay = round(payload.basic_salary - payload.deductions + payload.bonus, 2)
    record = Payroll(**payload.model_dump(), net_pay=net_pay, status="draft")
    db.add(record)
    try:
        db.flush()
        record_audit_log(db, user.id, "staff.payroll.generate", entity_type="payroll", entity_id=str(record.id), details=f"{payload.period_month}/{payload.period_year} for user {payload.user_id}, net Rs.{net_pay}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Payroll already generated for this user and period")
    db.refresh(record)
    return record


@router.get("/payroll", response_model=list[PayrollOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_payroll(user_id: uuid.UUID | None = None, db: Session = Depends(get_db)):
    query = select(Payroll).order_by(Payroll.period_year.desc(), Payroll.period_month.desc())
    if user_id:
        query = query.where(Payroll.user_id == user_id)
    return db.scalars(query).all()


@router.patch("/payroll/{payroll_id}/mark-paid", response_model=PayrollOut, dependencies=[Depends(require_role("admin"))])
def mark_payroll_paid(payroll_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    record = db.get(Payroll, payroll_id)
    if record is None:
        raise HTTPException(404, "Payroll record not found")
    record.status = "paid"
    record.paid_at = datetime.now(timezone.utc)
    record_audit_log(db, user.id, "staff.payroll.mark_paid", entity_type="payroll", entity_id=str(record.id), details=f"Rs.{record.net_pay} for user {record.user_id}")

    # Finance integration: a paid salary is a real Expense — the same
    # pattern as Maintenance ticket costs — so it actually flows into
    # Daily Cashbook and expense totals instead of only existing in the
    # Payroll table where nothing else in Finance ever looks.
    staff_member = db.get(User, record.user_id)
    expense = Expense(
        category="salary", amount=record.net_pay, expense_date=record.paid_at.date(),
        notes=f"Salary for {staff_member.name if staff_member else 'staff'} — {record.period_month}/{record.period_year}",
        created_by=user.id,
    )
    db.add(expense)
    db.flush()
    record.linked_expense_id = expense.id
    record_audit_log(db, user.id, "finance.expense.create", entity_type="expense", entity_id=str(expense.id), details=f"From payroll {record.id}: Rs.{record.net_pay}")

    trigger_payroll_paid(db, record)
    db.commit()
    db.refresh(record)
    return record


# --- Shifts ---

@router.post("/shifts", response_model=ShiftOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_shift(payload: ShiftCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    shift = Shift(**payload.model_dump())
    db.add(shift)
    db.flush()
    record_audit_log(db, user.id, "staff.shift.create", entity_type="shift", entity_id=str(shift.id), details=f"{payload.date} for user {payload.user_id}")
    db.commit()
    db.refresh(shift)
    return shift


@router.get("/shifts", response_model=list[ShiftOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_shifts(user_id: uuid.UUID | None = None, db: Session = Depends(get_db)):
    query = select(Shift).order_by(Shift.date.desc())
    if user_id:
        query = query.where(Shift.user_id == user_id)
    return db.scalars(query).all()


# --- Holidays ---

@router.post("/holidays", response_model=HolidayOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_holiday(payload: HolidayCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    holiday = Holiday(**payload.model_dump())
    db.add(holiday)
    try:
        db.flush()
        record_audit_log(db, user.id, "staff.holiday.create", entity_type="holiday", entity_id=str(holiday.id), details=str(payload.date))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A holiday is already recorded for this date")
    db.refresh(holiday)
    return holiday


@router.get("/holidays", response_model=list[HolidayOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_holidays(db: Session = Depends(get_db)):
    return db.scalars(select(Holiday).order_by(Holiday.date.asc())).all()


@router.post("/apartment-assignments", response_model=StaffApartmentAssignmentOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_apartment_assignment(payload: StaffApartmentAssignmentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assignment = StaffApartmentAssignment(**payload.model_dump())
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This staff member is already assigned to this apartment")
    record_audit_log(db, user.id, "staff.apartment_assignment.create", entity_type="staff_apartment_assignment", entity_id=str(assignment.id), details=f"user={payload.user_id} -> unit={payload.unit_id}")
    db.commit()
    db.refresh(assignment)
    return assignment


@router.get("/apartment-assignments", response_model=list[StaffApartmentAssignmentOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_apartment_assignments(user_id: uuid.UUID | None = None, unit_id: uuid.UUID | None = None, db: Session = Depends(get_db)):
    query = select(StaffApartmentAssignment)
    if user_id:
        query = query.where(StaffApartmentAssignment.user_id == user_id)
    if unit_id:
        query = query.where(StaffApartmentAssignment.unit_id == unit_id)
    return db.scalars(query).all()


@router.delete("/apartment-assignments/{assignment_id}", status_code=204, dependencies=[Depends(require_role("admin"))])
def delete_apartment_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assignment = db.get(StaffApartmentAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(404, "Assignment not found")
    record_audit_log(db, user.id, "staff.apartment_assignment.delete", entity_type="staff_apartment_assignment", entity_id=str(assignment.id), details=f"user={assignment.user_id}, unit={assignment.unit_id}")
    db.delete(assignment)
    db.commit()


@router.post("/performance-notes", response_model=StaffPerformanceNoteOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_performance_note(payload: StaffPerformanceNoteCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    note = StaffPerformanceNote(**payload.model_dump(), created_by=user.id)
    db.add(note)
    db.flush()
    record_audit_log(db, user.id, "staff.performance_note.create", entity_type="staff_performance_note", entity_id=str(note.id), details=f"About user {payload.user_id}: {payload.note[:100]}")
    db.commit()
    db.refresh(note)
    return note


@router.get("/performance-notes", response_model=list[StaffPerformanceNoteOut], dependencies=[Depends(require_role("admin"))])
def list_performance_notes(user_id: uuid.UUID | None = None, db: Session = Depends(get_db)):
    query = select(StaffPerformanceNote).order_by(StaffPerformanceNote.created_at.desc())
    if user_id:
        query = query.where(StaffPerformanceNote.user_id == user_id)
    return db.scalars(query).all()
