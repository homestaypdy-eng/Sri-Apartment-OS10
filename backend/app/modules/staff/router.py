import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.staff.models import (
    Attendance,
    Holiday,
    LeaveRequest,
    Payroll,
    Shift,
    StaffAdvance,
    StaffApartmentAssignment,
    StaffPerformanceNote,
    StaffProfile,
)
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
    StaffAdvanceCreate,
    StaffAdvanceDeduct,
    StaffAdvanceOut,
    StaffAdvanceUpdate,
    StaffApartmentAssignmentCreate,
    StaffApartmentAssignmentOut,
    StaffPerformanceNoteCreate,
    StaffPerformanceNoteOut,
    StaffProfileCreate,
    StaffProfileOut,
    StaffProfileUpdate,
)
from app.modules.audit.schemas import record_audit_log
from app.modules.finance.models import Expense
from app.modules.notifications.service import trigger_leave_reviewed, trigger_payroll_paid
from app.modules.properties.models import Unit
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
    if db.get(User, payload.user_id) is None:
        raise HTTPException(404, "Staff user not found")
    unit = db.get(Unit, payload.unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if not unit.is_active or not unit.property.is_active:
        raise HTTPException(409, "This unit (or its property) is deactivated and cannot accept new staff assignments")
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


# --- Staff Profiles (1:1 HR profile attached to an existing User) ---

@router.post("/profiles", response_model=StaffProfileOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_staff_profile(payload: StaffProfileCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    if db.scalar(select(StaffProfile).where(StaffProfile.user_id == payload.user_id)) is not None:
        raise HTTPException(409, "This user already has a staff profile")
    if payload.supervisor_user_id is not None:
        if payload.supervisor_user_id == payload.user_id:
            raise HTTPException(400, "A staff member cannot be their own supervisor")
        if db.get(User, payload.supervisor_user_id) is None:
            raise HTTPException(404, "Supervisor user not found")

    profile = StaffProfile(**payload.model_dump())
    db.add(profile)
    db.flush()
    record_audit_log(db, user.id, "staff.create", entity_type="staff_profile", entity_id=str(profile.id), details=f"Profile created for user {payload.user_id}")
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/profiles", response_model=list[StaffProfileOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_staff_profiles(db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    query = select(StaffProfile)
    if actor.role.name != "admin":
        query = query.where(StaffProfile.user_id == actor.id)
    return db.scalars(query).all()


@router.get("/profiles/{profile_id}", response_model=StaffProfileOut, dependencies=[Depends(require_role("admin", "staff"))])
def get_staff_profile(profile_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    profile = db.get(StaffProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Staff profile not found")
    if actor.role.name != "admin" and profile.user_id != actor.id:
        raise HTTPException(403, "You can only view your own staff profile")
    return profile


@router.patch("/profiles/{profile_id}", response_model=StaffProfileOut, dependencies=[Depends(require_role("admin"))])
def update_staff_profile(profile_id: uuid.UUID, payload: StaffProfileUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    profile = db.get(StaffProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Staff profile not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update were provided")
    if "supervisor_user_id" in changes and changes["supervisor_user_id"] is not None:
        if changes["supervisor_user_id"] == profile.user_id:
            raise HTTPException(400, "A staff member cannot be their own supervisor")
        if db.get(User, changes["supervisor_user_id"]) is None:
            raise HTTPException(404, "Supervisor user not found")
    for field, value in changes.items():
        setattr(profile, field, value)
    record_audit_log(db, user.id, "staff.update", entity_type="staff_profile", entity_id=str(profile.id), details=f"Updated: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(profile)
    return profile


@router.patch("/profiles/{profile_id}/deactivate", response_model=StaffProfileOut, dependencies=[Depends(require_role("admin"))])
def deactivate_staff_profile(profile_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Soft-deactivation only — the profile row and every attendance/leave/
    payroll/shift/performance/advance record tied to its user_id are
    untouched. Deliberately does NOT touch User.is_active (login access)
    — employment status and login access are independent concerns.
    """
    profile = db.get(StaffProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Staff profile not found")
    if not profile.is_active:
        raise HTTPException(409, "Staff profile is already deactivated")
    profile.is_active = False
    record_audit_log(db, user.id, "staff.deactivate", entity_type="staff_profile", entity_id=str(profile.id), details=f"Deactivated user {profile.user_id}")
    db.commit()
    db.refresh(profile)
    return profile


@router.patch("/profiles/{profile_id}/reactivate", response_model=StaffProfileOut, dependencies=[Depends(require_role("admin"))])
def reactivate_staff_profile(profile_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    profile = db.get(StaffProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Staff profile not found")
    if profile.is_active:
        raise HTTPException(409, "Staff profile is already active")
    profile.is_active = True
    record_audit_log(db, user.id, "staff.reactivate", entity_type="staff_profile", entity_id=str(profile.id), details=f"Reactivated user {profile.user_id}")
    db.commit()
    db.refresh(profile)
    return profile


# --- Staff Advances ---
# No existing advance/deduction architecture was found to reuse — confirmed
# by exhaustive search during the architecture audit; Payroll.deductions is
# a single flat manual number with no category/reason/status/partial-
# deduction tracking, and finance/advances is guest-booking-deposit
# reporting, structurally unrelated. This is a new, minimal ledger.

@router.post("/advances", response_model=StaffAdvanceOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_staff_advance(payload: StaffAdvanceCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.get(User, payload.user_id) is None:
        raise HTTPException(404, "User not found")
    advance = StaffAdvance(**payload.model_dump(), created_by=user.id)
    db.add(advance)
    db.flush()
    record_audit_log(db, user.id, "staff.advance.create", entity_type="staff_advance", entity_id=str(advance.id), details=f"Rs.{payload.amount} ({payload.category}) for user {payload.user_id}")
    db.commit()
    db.refresh(advance)
    return advance


@router.get("/advances", response_model=list[StaffAdvanceOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_staff_advances(user_id: uuid.UUID | None = None, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    query = select(StaffAdvance).order_by(StaffAdvance.date.desc())
    if actor.role.name != "admin":
        query = query.where(StaffAdvance.user_id == actor.id)
    elif user_id:
        query = query.where(StaffAdvance.user_id == user_id)
    return db.scalars(query).all()


@router.get("/advances/{advance_id}", response_model=StaffAdvanceOut, dependencies=[Depends(require_role("admin", "staff"))])
def get_staff_advance(advance_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    advance = db.get(StaffAdvance, advance_id)
    if advance is None:
        raise HTTPException(404, "Staff advance not found")
    if actor.role.name != "admin" and advance.user_id != actor.id:
        raise HTTPException(403, "You can only view your own advances")
    return advance


@router.patch("/advances/{advance_id}", response_model=StaffAdvanceOut, dependencies=[Depends(require_role("admin"))])
def update_staff_advance(advance_id: uuid.UUID, payload: StaffAdvanceUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    advance = db.get(StaffAdvance, advance_id)
    if advance is None:
        raise HTTPException(404, "Staff advance not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update were provided")
    for field, value in changes.items():
        setattr(advance, field, value)
    record_audit_log(db, user.id, "staff.advance.update", entity_type="staff_advance", entity_id=str(advance.id), details=f"Updated: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(advance)
    return advance


@router.post("/advances/{advance_id}/deduct", response_model=StaffAdvanceOut, dependencies=[Depends(require_role("admin"))])
def deduct_staff_advance(advance_id: uuid.UUID, payload: StaffAdvanceDeduct, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    The only path that ever changes deducted_amount/status — keeps the
    outstanding-balance math (amount - deducted_amount) impossible to
    desync from a raw PATCH. Does not silently alter Payroll: it only
    records which payroll run (if any) the deduction was applied against,
    via linked_payroll_id — payroll's own net_pay calculation is untouched.
    """
    advance = db.get(StaffAdvance, advance_id)
    if advance is None:
        raise HTTPException(404, "Staff advance not found")
    if advance.status == "fully_deducted":
        raise HTTPException(409, "This advance has already been fully deducted")
    outstanding = float(advance.amount) - float(advance.deducted_amount)
    if payload.amount > outstanding:
        raise HTTPException(400, f"Deduction amount (Rs.{payload.amount}) exceeds the outstanding balance (Rs.{outstanding})")
    if payload.payroll_id is not None and db.get(Payroll, payload.payroll_id) is None:
        raise HTTPException(404, "Payroll record not found")

    advance.deducted_amount = float(advance.deducted_amount) + payload.amount
    advance.status = "fully_deducted" if advance.deducted_amount >= float(advance.amount) else "partially_deducted"
    if payload.payroll_id is not None:
        advance.linked_payroll_id = payload.payroll_id

    record_audit_log(
        db, user.id, "staff.advance.deduction", entity_type="staff_advance", entity_id=str(advance.id),
        details=f"Deducted Rs.{payload.amount}, status now {advance.status}, outstanding Rs.{float(advance.amount) - float(advance.deducted_amount)}",
    )
    db.commit()
    db.refresh(advance)
    return advance
