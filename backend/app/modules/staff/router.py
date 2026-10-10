import calendar
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import hash_password
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import UserCreate, UserOut
from app.modules.staff import hr_policy
from app.modules.staff.models import (
    Attendance,
    Holiday,
    LeaveRequest,
    Payroll,
    Shift,
    StaffAdvance,
    StaffApartmentAssignment,
    StaffDesignation,
    StaffPerformanceNote,
    StaffProfile,
)
from app.modules.staff.schemas import (
    AttendanceCreate,
    AttendanceMonthlySummaryOut,
    AttendanceOut,
    AttendanceUpdate,
    HolidayCreate,
    HolidayOut,
    LeaveRequestCreate,
    LeaveRequestOut,
    LeaveRequestUpdate,
    PayrollCreate,
    PayrollOut,
    ResolvedPolicyOut,
    ShiftCreate,
    ShiftOut,
    StaffAdvanceCreate,
    StaffAdvanceDeduct,
    StaffAdvanceOut,
    StaffAdvanceUpdate,
    StaffApartmentAssignmentCreate,
    StaffApartmentAssignmentOut,
    StaffDesignationCreate,
    StaffDesignationOut,
    StaffOperationalUserOut,
    StaffOnboardingCreate,
    StaffOnboardingOut,
    StaffDesignationUpdate,
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


@router.get("/operational-users", response_model=list[StaffOperationalUserOut], dependencies=[Depends(require_role("admin", "manager"))])
def list_operational_users(db: Session = Depends(get_db)):
    """Minimal active staff roster for daily scheduling; never exposes account credentials/contact details."""
    rows = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.name)).all()
    return [StaffOperationalUserOut(id=row.id, name=row.name, role=row.role.name) for row in rows if row.role.name == "staff"]


def _ensure_employee_id_available(db: Session, employee_id: str | None, *, excluding_profile_id: uuid.UUID | None = None) -> None:
    normalized = employee_id.strip() if employee_id else None
    if not normalized:
        return
    query = select(StaffProfile.id).where(func.lower(StaffProfile.employee_id) == normalized.lower())
    if excluding_profile_id is not None:
        query = query.where(StaffProfile.id != excluding_profile_id)
    if db.scalar(query) is not None:
        raise HTTPException(409, "Employee ID is already in use")


@router.post("/onboard", response_model=StaffOnboardingOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def onboard_staff(payload: StaffOnboardingCreate, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    """Create the existing canonical User + StaffProfile in one transaction.

    This phase intentionally creates a Staff login: true loginless employee
    identities require a separate approved architecture decision.
    """
    account = UserCreate(name=payload.name, email=payload.email, password=payload.password, role="staff")
    if db.scalar(select(User.id).where(func.lower(User.email) == str(account.email).lower())) is not None:
        raise HTTPException(409, "Email already registered")

    role = db.scalar(select(Role).where(Role.name == "staff"))
    if role is None:
        raise HTTPException(409, "Staff role is not provisioned")

    user = User(name=account.name.strip(), email=str(account.email).lower(), password_hash=hash_password(account.password), role_id=role.id)
    db.add(user)
    try:
        db.flush()
        profile_payload = StaffProfileCreate.model_validate({**payload.profile, "user_id": user.id})
        _ensure_employee_id_available(db, profile_payload.employee_id)
        if profile_payload.employee_id:
            profile_payload.employee_id = profile_payload.employee_id.strip()
        if profile_payload.designation_id is not None:
            designation = db.get(StaffDesignation, profile_payload.designation_id)
            if designation is None or not designation.is_active:
                raise HTTPException(404, "Active designation not found")
        if profile_payload.supervisor_user_id is not None:
            if profile_payload.supervisor_user_id == user.id:
                raise HTTPException(400, "A staff member cannot be their own supervisor")
            if db.get(User, profile_payload.supervisor_user_id) is None:
                raise HTTPException(404, "Supervisor user not found")
        profile = StaffProfile(**profile_payload.model_dump())
        db.add(profile)
        for unit_id in dict.fromkeys(payload.assigned_unit_ids):
            unit = db.get(Unit, unit_id)
            if unit is None:
                raise HTTPException(404, "Assigned apartment not found")
            if not unit.is_active or not unit.property.is_active:
                raise HTTPException(409, "An assigned apartment or property is inactive")
            db.add(StaffApartmentAssignment(user_id=user.id, unit_id=unit_id))
        db.flush()
        record_audit_log(db, actor.id, "staff.onboard", entity_type="user", entity_id=str(user.id), details=f"Staff account and profile created for {user.email}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Staff account, employee ID, or apartment assignment conflicts with an existing record")
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    db.refresh(profile)
    return StaffOnboardingOut(user=UserOut(id=user.id, name=user.name, email=user.email, role="staff", is_active=user.is_active), profile=profile)


def _resolved_policy_for_user(db: Session, user_id: uuid.UUID) -> tuple[StaffProfile | None, StaffDesignation | None, hr_policy.ResolvedPolicy]:
    """Fetches the rows and calls the pure resolver — DB access stays here,
    same convention as properties/pricing.py callers."""
    profile = db.scalar(select(StaffProfile).where(StaffProfile.user_id == user_id))
    designation = None
    if profile and profile.designation_id:
        designation = db.get(StaffDesignation, profile.designation_id)
    if profile is None:
        # No profile at all: resolver still needs a profile-shaped object
        # with every override field None, so it correctly reports
        # "unconfigured" rather than erroring.
        class _EmptyProfile:
            salary_override = shift_start_override = shift_end_override = None
            lunch_break_override_minutes = standard_working_hours_override = None
            leave_entitlement_override = None
        profile_for_resolver = _EmptyProfile()
    else:
        profile_for_resolver = profile
    policy = hr_policy.resolve_employee_policy(profile_for_resolver, designation)
    return profile, designation, policy


def _maybe_compute_attendance_hours(record: Attendance, db: Session) -> None:
    """If both check_in_time and check_out_time are now present, resolve
    this user's current policy and snapshot the four computed columns.
    Called after any create/update that could have just completed the
    pair — a no-op (all four stay None) if either time is still missing."""
    if record.check_in_time is None or record.check_out_time is None:
        return
    _, _, policy = _resolved_policy_for_user(db, record.user_id)
    computation = hr_policy.compute_attendance_hours(
        attendance_date=record.date,
        check_in_time=record.check_in_time,
        check_out_time=record.check_out_time,
        lunch_break_minutes=policy.lunch_break_minutes,
        standard_working_hours=policy.standard_working_hours,
    )
    record.total_presence_minutes = computation.total_presence_minutes
    record.lunch_break_minutes_applied = computation.lunch_break_minutes_applied
    record.working_minutes = computation.working_minutes
    record.overtime_minutes = computation.overtime_minutes


# --- Attendance ---

@router.post("/attendance", response_model=AttendanceOut, status_code=201, dependencies=[Depends(require_role("admin", "manager", "staff"))])
def mark_attendance(payload: AttendanceCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role.name == "staff" and payload.user_id != user.id:
        raise HTTPException(403, "You can only record your own attendance")
    if user.role.name == "manager" and payload.user_id != user.id:
        target = db.get(User, payload.user_id)
        if target is None or target.role.name != "staff":
            raise HTTPException(403, "Managers may record attendance only for operational staff")
    record = Attendance(**payload.model_dump())
    db.add(record)
    try:
        db.flush()
        _maybe_compute_attendance_hours(record, db)  # both times may already be known (after-the-fact daily entry)
        record_audit_log(db, user.id, "staff.attendance.mark", entity_type="attendance", entity_id=str(record.id), details=f"{payload.status} for user {payload.user_id} on {payload.date}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Attendance already recorded for this user on this date")
    db.refresh(record)
    return record


@router.get("/attendance", response_model=list[AttendanceOut], dependencies=[Depends(require_role("admin", "manager", "staff"))])
def list_attendance(user_id: uuid.UUID | None = None, limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    if actor.role.name == "staff":
        if user_id is not None and user_id != actor.id:
            raise HTTPException(403, "You can only view your own attendance")
        user_id = actor.id
    query = select(Attendance).order_by(Attendance.date.desc(), Attendance.id)
    if actor.role.name == "manager":
        query = query.join(User, Attendance.user_id == User.id).where((User.role.has(name="staff")) | (User.id == actor.id))
    if user_id:
        query = query.where(Attendance.user_id == user_id)
    return db.scalars(query.offset(offset).limit(limit)).all()


@router.patch("/attendance/{attendance_id}", response_model=AttendanceOut, dependencies=[Depends(require_role("admin", "manager", "staff"))])
def update_attendance(attendance_id: uuid.UUID, payload: AttendanceUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Records a check-out (or corrects/backfills a missed one) on an
    existing attendance row created earlier the same day by mark_attendance.
    A "staff" caller may only update their own attendance; admin may
    update anyone's. Whenever this leaves both check_in_time and
    check_out_time set, the four computed hours columns are (re)snapshotted
    from the resolved policy at the moment of this call.
    """
    record = db.get(Attendance, attendance_id)
    if record is None:
        raise HTTPException(404, "Attendance record not found")
    if user.role.name == "staff" and record.user_id != user.id:
        raise HTTPException(403, "You can only update your own attendance")
    if user.role.name == "manager" and record.user_id != user.id:
        target = db.get(User, record.user_id)
        if target is None or target.role.name != "staff":
            raise HTTPException(403, "Managers may update attendance only for operational staff")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update were provided")
    for field, value in changes.items():
        setattr(record, field, value)
    _maybe_compute_attendance_hours(record, db)
    record_audit_log(db, user.id, "staff.attendance.update", entity_type="attendance", entity_id=str(record.id), details=f"Updated: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(record)
    return record


@router.get("/attendance/monthly-summary", response_model=AttendanceMonthlySummaryOut, dependencies=[Depends(require_role("admin", "manager", "staff"))])
def attendance_monthly_summary(
    user_id: uuid.UUID = Query(...),
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """
    Read-only reference numbers for a payroll review screen: total presence/
    working/overtime minutes recorded this month, plus approved-leave days
    split into free (within entitlement) vs. excess. This never writes
    anything and never computes a salary deduction — excess leave days are
    surfaced as a count only (see hr_policy.split_free_and_excess_leave).
    """
    if actor.role.name == "staff" and actor.id != user_id:
        raise HTTPException(403, "You can only view your own attendance summary")
    if actor.role.name == "manager" and actor.id != user_id:
        target = db.get(User, user_id)
        if target is None or target.role.name != "staff":
            raise HTTPException(403, "Managers may view attendance summaries only for operational staff")

    attendance_rows = db.scalars(
        select(Attendance).where(
            Attendance.user_id == user_id,
            Attendance.date >= date(year, month, 1),
            Attendance.date <= date(year, month, calendar.monthrange(year, month)[1]),
        )
    ).all()
    leave_rows = db.scalars(select(LeaveRequest).where(LeaveRequest.user_id == user_id)).all()

    approved_days = hr_policy.count_approved_leave_days_in_month(leave_rows, year, month)
    _, _, policy = _resolved_policy_for_user(db, user_id)
    free_days, excess_days = hr_policy.split_free_and_excess_leave(approved_days, policy.leave_entitlement_per_month)

    return AttendanceMonthlySummaryOut(
        user_id=user_id,
        year=year,
        month=month,
        days_recorded=len(attendance_rows),
        total_presence_minutes=sum(r.total_presence_minutes or 0 for r in attendance_rows),
        total_working_minutes=sum(r.working_minutes or 0 for r in attendance_rows),
        total_overtime_minutes=sum(r.overtime_minutes or 0 for r in attendance_rows),
        approved_leave_days_in_month=approved_days,
        leave_entitlement_per_month=policy.leave_entitlement_per_month,
        free_leave_days=free_days,
        excess_leave_days=excess_days,
    )


# --- Leave ---

@router.post("/leave", response_model=LeaveRequestOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def apply_leave(payload: LeaveRequestCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role.name == "staff" and payload.user_id != user.id:
        raise HTTPException(403, "You can only submit your own leave request")
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
def list_leave(user_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    query = select(LeaveRequest).order_by(LeaveRequest.created_at.desc())
    if actor.role.name == "staff":
        if user_id is not None and user_id != actor.id:
            raise HTTPException(403, "You can only view your own leave requests")
        user_id = actor.id
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
def list_payroll(user_id: uuid.UUID | None = None, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    query = select(Payroll).order_by(Payroll.period_year.desc(), Payroll.period_month.desc())
    if actor.role.name == "staff":
        if user_id is not None and user_id != actor.id:
            raise HTTPException(403, "You can only view your own payroll records")
        user_id = actor.id
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


# --- Staff Designations (post/designation master) ---

@router.post("/designations", response_model=StaffDesignationOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_designation(payload: StaffDesignationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.overtime_mode not in ("untracked", "tracked_no_pay", "tracked_with_rate"):
        raise HTTPException(400, "overtime_mode must be one of: untracked, tracked_no_pay, tracked_with_rate")
    designation = StaffDesignation(**payload.model_dump())
    db.add(designation)
    try:
        db.flush()
        record_audit_log(db, user.id, "staff.designation.create", entity_type="staff_designation", entity_id=str(designation.id), details=f"{payload.name}, Rs.{payload.default_basic_salary}/month")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"A designation named '{payload.name}' already exists")
    db.refresh(designation)
    return designation


@router.get("/designations", response_model=list[StaffDesignationOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_designations(include_inactive: bool = Query(False), db: Session = Depends(get_db)):
    query = select(StaffDesignation).order_by(StaffDesignation.name)
    if not include_inactive:
        query = query.where(StaffDesignation.is_active.is_(True))
    return db.scalars(query).all()


@router.get("/designations/{designation_id}", response_model=StaffDesignationOut, dependencies=[Depends(require_role("admin", "staff"))])
def get_designation(designation_id: uuid.UUID, db: Session = Depends(get_db)):
    designation = db.get(StaffDesignation, designation_id)
    if designation is None:
        raise HTTPException(404, "Designation not found")
    return designation


@router.patch("/designations/{designation_id}", response_model=StaffDesignationOut, dependencies=[Depends(require_role("admin"))])
def update_designation(designation_id: uuid.UUID, payload: StaffDesignationUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    designation = db.get(StaffDesignation, designation_id)
    if designation is None:
        raise HTTPException(404, "Designation not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update were provided")
    if "overtime_mode" in changes and changes["overtime_mode"] not in ("untracked", "tracked_no_pay", "tracked_with_rate"):
        raise HTTPException(400, "overtime_mode must be one of: untracked, tracked_no_pay, tracked_with_rate")
    for field, value in changes.items():
        setattr(designation, field, value)
    # Note: this only changes what future default-resolution and future
    # attendance snapshots see. Already-generated Payroll rows and
    # already-recorded Attendance hour snapshots are untouched — see
    # StaffDesignationUpdate's docstring.
    record_audit_log(db, user.id, "staff.designation.update", entity_type="staff_designation", entity_id=str(designation.id), details=f"Updated: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(designation)
    return designation


@router.patch("/designations/{designation_id}/deactivate", response_model=StaffDesignationOut, dependencies=[Depends(require_role("admin"))])
def deactivate_designation(designation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    designation = db.get(StaffDesignation, designation_id)
    if designation is None:
        raise HTTPException(404, "Designation not found")
    if not designation.is_active:
        raise HTTPException(409, "Designation is already deactivated")
    designation.is_active = False
    record_audit_log(db, user.id, "staff.designation.deactivate", entity_type="staff_designation", entity_id=str(designation.id), details=designation.name)
    db.commit()
    db.refresh(designation)
    return designation


@router.patch("/designations/{designation_id}/reactivate", response_model=StaffDesignationOut, dependencies=[Depends(require_role("admin"))])
def reactivate_designation(designation_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    designation = db.get(StaffDesignation, designation_id)
    if designation is None:
        raise HTTPException(404, "Designation not found")
    if designation.is_active:
        raise HTTPException(409, "Designation is already active")
    designation.is_active = True
    record_audit_log(db, user.id, "staff.designation.reactivate", entity_type="staff_designation", entity_id=str(designation.id), details=designation.name)
    db.commit()
    db.refresh(designation)
    return designation


@router.get("/policy/{user_id}", response_model=ResolvedPolicyOut, dependencies=[Depends(require_role("admin", "staff"))])
def get_resolved_policy(user_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    """
    The policy that actually applies to this employee right now — post
    default with any employee-specific override already applied, per
    field, plus which source ("override"/"designation"/"unconfigured")
    each field came from. Read-only; changes nothing.
    """
    if actor.role.name != "admin" and actor.id != user_id:
        raise HTTPException(403, "You can only view your own resolved policy")
    if db.get(User, user_id) is None:
        raise HTTPException(404, "User not found")
    _, designation, policy = _resolved_policy_for_user(db, user_id)
    return ResolvedPolicyOut(
        user_id=user_id,
        designation_id=designation.id if designation else None,
        designation_name=designation.name if designation else None,
        basic_salary=policy.basic_salary,
        shift_start=policy.shift_start,
        shift_end=policy.shift_end,
        lunch_break_minutes=policy.lunch_break_minutes,
        standard_working_hours=policy.standard_working_hours,
        leave_entitlement_per_month=policy.leave_entitlement_per_month,
        overtime_mode=policy.overtime_mode,
        overtime_rate_multiplier=policy.overtime_rate_multiplier,
        source=policy.source,
    )


# --- Shifts ---

@router.post("/shifts", response_model=ShiftOut, status_code=201, dependencies=[Depends(require_role("admin", "manager"))])
def create_shift(payload: ShiftCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role.name == "manager":
        target = db.get(User, payload.user_id)
        if target is None or target.role.name != "staff":
            raise HTTPException(403, "Managers may schedule only operational staff")
    shift = Shift(**payload.model_dump())
    db.add(shift)
    db.flush()
    record_audit_log(db, user.id, "staff.shift.create", entity_type="shift", entity_id=str(shift.id), details=f"{payload.date} for user {payload.user_id}")
    db.commit()
    db.refresh(shift)
    return shift


@router.get("/shifts", response_model=list[ShiftOut], dependencies=[Depends(require_role("admin", "manager", "staff"))])
def list_shifts(user_id: uuid.UUID | None = None, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    query = select(Shift).order_by(Shift.date.desc())
    if actor.role.name == "staff":
        if user_id is not None and user_id != actor.id:
            raise HTTPException(403, "You can only view your own shifts")
        user_id = actor.id
    elif actor.role.name == "manager":
        query = query.join(User, Shift.user_id == User.id).where((User.role.has(name="staff")) | (User.id == actor.id))
    if user_id:
        query = query.where(Shift.user_id == user_id)
    return db.scalars(query).all()


# --- Holidays ---

@router.post("/holidays", response_model=HolidayOut, status_code=201, dependencies=[Depends(require_role("admin", "manager"))])
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


@router.get("/holidays", response_model=list[HolidayOut], dependencies=[Depends(require_role("admin", "manager", "staff"))])
def list_holidays(db: Session = Depends(get_db)):
    return db.scalars(select(Holiday).order_by(Holiday.date.asc())).all()


@router.post("/apartment-assignments", response_model=StaffApartmentAssignmentOut, status_code=201, dependencies=[Depends(require_role("admin", "manager"))])
def create_apartment_assignment(payload: StaffApartmentAssignmentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target_user = db.get(User, payload.user_id)
    if target_user is None:
        raise HTTPException(404, "Staff user not found")
    if user.role.name == "manager" and target_user.role.name != "staff":
        raise HTTPException(403, "Managers may assign apartments only to operational staff")
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


@router.get("/apartment-assignments", response_model=list[StaffApartmentAssignmentOut], dependencies=[Depends(require_role("admin", "manager", "staff"))])
def list_apartment_assignments(user_id: uuid.UUID | None = None, unit_id: uuid.UUID | None = None, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    query = select(StaffApartmentAssignment)
    if actor.role.name == "staff":
        if user_id is not None and user_id != actor.id:
            raise HTTPException(403, "You can only view your own apartment assignments")
        user_id = actor.id
    elif actor.role.name == "manager":
        query = query.join(User, StaffApartmentAssignment.user_id == User.id).where((User.role.has(name="staff")) | (User.id == actor.id))
    if user_id:
        query = query.where(StaffApartmentAssignment.user_id == user_id)
    if unit_id:
        query = query.where(StaffApartmentAssignment.unit_id == unit_id)
    return db.scalars(query).all()


@router.delete("/apartment-assignments/{assignment_id}", status_code=204, dependencies=[Depends(require_role("admin", "manager"))])
def delete_apartment_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assignment = db.get(StaffApartmentAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(404, "Assignment not found")
    if user.role.name == "manager":
        target_user = db.get(User, assignment.user_id)
        if target_user is None or target_user.role.name != "staff":
            raise HTTPException(403, "Managers may remove apartment assignments only for operational staff")
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
    _ensure_employee_id_available(db, payload.employee_id)
    if payload.employee_id:
        payload.employee_id = payload.employee_id.strip()
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
    if "employee_id" in changes:
        _ensure_employee_id_available(db, changes["employee_id"], excluding_profile_id=profile.id)
        changes["employee_id"] = changes["employee_id"].strip() if changes["employee_id"] else None
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
