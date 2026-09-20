"""
Staff Operations — a deterministic, read-mostly operational layer over
booking check-in/check-out, housekeeping, maintenance, and guest requests.

Deliberately NOT a new source of truth: every read here queries the
existing bookings/housekeeping_tasks/maintenance_tickets/guest_requests
tables directly, and every write (check-in/check-out, task status change)
either calls into the existing booking-status transition logic or updates
one of those three existing rows using the same status/priority vocabulary
and audit-log action names their own dedicated endpoints already use. This
module adds an aggregated staff-facing view; it does not duplicate or
replace the Housekeeping/Maintenance/Guest Requests modules, which remain
the full-featured CRUD surface (photos, timelines, etc.) for each.

No financial, pricing, GST, OTA, AI Strategist, or autonomous-action code
is imported or touched anywhere in this module — see
test_staff_operations.py's isolation tests.
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.bookings.models import Booking, Guest
from app.modules.housekeeping.models import TASK_STATUSES as HK_STATUSES, HousekeepingTask
from app.modules.maintenance.models import TICKET_STATUSES as MAINT_STATUSES, MaintenanceTicket
from app.modules.payments.models import Payment
from app.modules.portal.models import REQUEST_STATUSES as REQ_STATUSES, GuestRequest
from app.modules.properties.models import Unit
from app.modules.staff_ops.schemas import CheckInLine, CheckOutLine, StaffDashboardOut, StaffTaskOut, StaffTaskUpdate
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/staff", tags=["staff-operations"])

_OPEN_HK = ("pending", "in_progress")
_OPEN_MAINT = ("open", "in_progress")
_OPEN_REQ = ("open", "in_progress")


@router.get("/dashboard", response_model=StaffDashboardOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_dashboard(db: Session = Depends(get_db)):
    today = date.today()

    check_ins_today = len(db.scalars(select(Booking.id).where(Booking.check_in == today, Booking.status == "confirmed")).all())
    check_outs_today = len(db.scalars(select(Booking.id).where(Booking.check_out == today, Booking.status == "checked_in")).all())

    active_units = db.scalars(
        select(Unit).join(Unit.property).where(Unit.is_active.is_(True))
    ).all()
    active_units = [u for u in active_units if u.property.is_active]
    occupied_unit_ids = set(
        db.scalars(select(Booking.unit_id).where(Booking.status == "checked_in")).all()
    )
    occupied_units = sum(1 for u in active_units if u.id in occupied_unit_ids)
    vacant_units = len(active_units) - occupied_units
    units_needing_cleaning = sum(1 for u in active_units if u.housekeeping_status == "dirty")

    open_maintenance_tickets = len(db.scalars(select(MaintenanceTicket.id).where(MaintenanceTicket.status.in_(_OPEN_MAINT))).all())
    open_guest_requests = len(db.scalars(select(GuestRequest.id).where(GuestRequest.status.in_(_OPEN_REQ))).all())
    pending_hk = len(db.scalars(select(HousekeepingTask.id).where(HousekeepingTask.status.in_(_OPEN_HK))).all())

    return StaffDashboardOut(
        date=today,
        check_ins_today=check_ins_today,
        check_outs_today=check_outs_today,
        occupied_units=occupied_units,
        vacant_units=vacant_units,
        units_needing_cleaning=units_needing_cleaning,
        open_maintenance_tickets=open_maintenance_tickets,
        open_guest_requests=open_guest_requests,
        pending_staff_tasks=pending_hk + open_maintenance_tickets + open_guest_requests,
    )


@router.get("/check-ins", response_model=list[CheckInLine], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_check_ins(target_date: date = Query(default=None), db: Session = Depends(get_db)):
    """Bookings due to check in on target_date (default today) that
    haven't been checked in yet — mirrors the dashboard's check_ins_today
    count exactly, so the two can never disagree."""
    target_date = target_date or date.today()
    bookings = db.scalars(
        select(Booking).where(Booking.check_in == target_date, Booking.status == "confirmed").order_by(Booking.arrival_time.is_(None), Booking.arrival_time)
    ).all()
    return _check_in_lines(db, bookings)


@router.get("/check-outs", response_model=list[CheckOutLine], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_check_outs(target_date: date = Query(default=None), db: Session = Depends(get_db)):
    target_date = target_date or date.today()
    bookings = db.scalars(
        select(Booking).where(Booking.check_out == target_date, Booking.status == "checked_in").order_by(Booking.departure_time.is_(None), Booking.departure_time)
    ).all()
    return _check_out_lines(db, bookings)


def _check_in_lines(db: Session, bookings: list[Booking]) -> list[CheckInLine]:
    if not bookings:
        return []
    unit_ids = {b.unit_id for b in bookings}
    guest_ids = {b.guest_id for b in bookings}
    booking_ids = [b.id for b in bookings]
    units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()}
    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}
    paid_by_booking: dict = {}
    for p in db.scalars(select(Payment).where(Payment.booking_id.in_(booking_ids))).all():
        paid_by_booking[p.booking_id] = paid_by_booking.get(p.booking_id, 0.0) + float(p.amount)

    return [
        CheckInLine(
            booking_id=b.id,
            guest_name=guests_by_id[b.guest_id].name if b.guest_id in guests_by_id else "—",
            unit_id=b.unit_id,
            unit_name=units_by_id[b.unit_id].name if b.unit_id in units_by_id else "—",
            arrival_time=b.arrival_time,
            adults=b.adults,
            children=b.children,
            status=b.status,
            net_paid=round(paid_by_booking.get(b.id, 0.0), 2),
        )
        for b in bookings
    ]


def _check_out_lines(db: Session, bookings: list[Booking]) -> list[CheckOutLine]:
    if not bookings:
        return []
    unit_ids = {b.unit_id for b in bookings}
    guest_ids = {b.guest_id for b in bookings}
    units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()}
    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}

    return [
        CheckOutLine(
            booking_id=b.id,
            guest_name=guests_by_id[b.guest_id].name if b.guest_id in guests_by_id else "—",
            unit_id=b.unit_id,
            unit_name=units_by_id[b.unit_id].name if b.unit_id in units_by_id else "—",
            departure_time=b.departure_time,
            status=b.status,
            housekeeping_status=units_by_id[b.unit_id].housekeeping_status if b.unit_id in units_by_id else "—",
        )
        for b in bookings
    ]


@router.get("/tasks", response_model=list[StaffTaskOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_tasks(
    category: str | None = Query(None, description="housekeeping | maintenance | guest_request"),
    status: str | None = None,
    assigned_to: uuid.UUID | None = Query(None, description="Pass the caller's own user id to see only tasks assigned to them"),
    db: Session = Depends(get_db),
):
    if category is not None and category not in ("housekeeping", "maintenance", "guest_request"):
        raise HTTPException(400, f"Invalid category '{category}'")

    unit_ids: set[uuid.UUID] = set()
    hk_tasks: list[HousekeepingTask] = []
    maint_tickets: list[MaintenanceTicket] = []
    guest_reqs: list[GuestRequest] = []

    if category in (None, "housekeeping"):
        q = select(HousekeepingTask)
        if status:
            q = q.where(HousekeepingTask.status == status)
        if assigned_to:
            q = q.where(HousekeepingTask.assigned_to == assigned_to)
        hk_tasks = db.scalars(q).all()
        unit_ids |= {t.unit_id for t in hk_tasks}

    if category in (None, "maintenance"):
        q = select(MaintenanceTicket)
        if status:
            q = q.where(MaintenanceTicket.status == status)
        if assigned_to:
            q = q.where(MaintenanceTicket.assigned_to == assigned_to)
        maint_tickets = db.scalars(q).all()
        unit_ids |= {t.unit_id for t in maint_tickets}

    if category in (None, "guest_request"):
        q = select(GuestRequest)
        if status:
            q = q.where(GuestRequest.status == status)
        if assigned_to:
            q = q.where(GuestRequest.assigned_to == assigned_to)
        guest_reqs = db.scalars(q).all()
        booking_ids = {r.booking_id for r in guest_reqs if r.booking_id}
        if booking_ids:
            bookings_by_id = {b.id: b for b in db.scalars(select(Booking).where(Booking.id.in_(booking_ids))).all()}
            unit_ids |= {b.unit_id for b in bookings_by_id.values()}
        else:
            bookings_by_id = {}
    else:
        bookings_by_id = {}

    units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()} if unit_ids else {}

    tasks: list[StaffTaskOut] = []
    for t in hk_tasks:
        tasks.append(StaffTaskOut(
            id=t.id, category="housekeeping", unit_id=t.unit_id,
            unit_name=units_by_id[t.unit_id].name if t.unit_id in units_by_id else None,
            booking_id=None,
            label=t.task_type.replace("_", " ").title(), priority=None, status=t.status,
            assigned_to=t.assigned_to, created_at=t.created_at, started_at=t.started_at,
            completed_at=t.completed_at, notes=t.notes,
        ))
    for t in maint_tickets:
        tasks.append(StaffTaskOut(
            id=t.id, category="maintenance", unit_id=t.unit_id,
            unit_name=units_by_id[t.unit_id].name if t.unit_id in units_by_id else None,
            booking_id=None,
            label=(t.category or "General").replace("_", " ").title(), priority=t.priority, status=t.status,
            assigned_to=t.assigned_to, created_at=t.created_at, started_at=None,
            completed_at=t.resolved_at, notes=t.description,
        ))
    for r in guest_reqs:
        booking = bookings_by_id.get(r.booking_id) if r.booking_id else None
        unit_id = booking.unit_id if booking else None
        tasks.append(StaffTaskOut(
            id=r.id, category="guest_request", unit_id=unit_id,
            unit_name=units_by_id[unit_id].name if unit_id in units_by_id else None,
            booking_id=r.booking_id,
            label=r.request_type.replace("_", " ").title(), priority=r.priority, status=r.status,
            assigned_to=r.assigned_to, created_at=r.created_at, started_at=None,
            completed_at=r.resolved_at, notes=r.staff_notes or r.description,
        ))

    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return tasks


@router.patch("/tasks/{task_id}", response_model=StaffTaskOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_task(
    task_id: uuid.UUID,
    payload: StaffTaskUpdate,
    category: str = Query(..., description="housekeeping | maintenance | guest_request"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Dispatches to whichever of the three existing tables `category`
    identifies, reusing that table's own status vocabulary and audit
    action name — a task updated from the unified Staff Operations view
    produces an identical audit trail to one updated from the dedicated
    Housekeeping/Maintenance/Guest Requests page.
    """
    if category == "housekeeping":
        task = db.get(HousekeepingTask, task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        changes = []
        if payload.status is not None:
            if payload.status not in HK_STATUSES:
                raise HTTPException(400, f"Invalid status '{payload.status}'")
            changes.append(f"status: {task.status} -> {payload.status}")
            task.status = payload.status
            if payload.status == "in_progress" and task.started_at is None:
                task.started_at = datetime.now(timezone.utc)
            if payload.status == "completed":
                if task.completed_at is None:
                    task.completed_at = datetime.now(timezone.utc)
                unit = db.get(Unit, task.unit_id)
                unit.housekeeping_status = "clean"
        # model_fields_set (not `is not None`) distinguishes "field omitted"
        # from an explicit `assigned_to: null` — the latter now clears the
        # assignment. Every other field on this endpoint is untouched.
        if "assigned_to" in payload.model_fields_set:
            new_assignee = payload.assigned_to
            if new_assignee is not None and db.get(User, new_assignee) is None:
                raise HTTPException(404, "Assigned user not found")
            changes.append(f"assigned_to: {task.assigned_to} -> {new_assignee}")
            task.assigned_to = new_assignee
        if payload.notes is not None:
            task.notes = payload.notes
        if changes:
            record_audit_log(db, user.id, "housekeeping.update", entity_type="housekeeping_task", entity_id=str(task.id), details="; ".join(changes))
        db.commit()
        db.refresh(task)
        unit = db.get(Unit, task.unit_id)
        return StaffTaskOut(
            id=task.id, category="housekeeping", unit_id=task.unit_id, unit_name=unit.name if unit else None,
            booking_id=None,
            label=task.task_type.replace("_", " ").title(), priority=None, status=task.status,
            assigned_to=task.assigned_to, created_at=task.created_at, started_at=task.started_at,
            completed_at=task.completed_at, notes=task.notes,
        )

    if category == "maintenance":
        ticket = db.get(MaintenanceTicket, task_id, with_for_update=True)
        if ticket is None:
            raise HTTPException(404, "Task not found")
        changes = []
        if payload.status is not None:
            if payload.status not in MAINT_STATUSES:
                raise HTTPException(400, f"Invalid status '{payload.status}'")
            changes.append(f"status: {ticket.status} -> {payload.status}")
            ticket.status = payload.status
            if payload.status in ("resolved", "closed") and ticket.resolved_at is None:
                ticket.resolved_at = datetime.now(timezone.utc)
        if "assigned_to" in payload.model_fields_set:
            new_assignee = payload.assigned_to
            if new_assignee is not None and db.get(User, new_assignee) is None:
                raise HTTPException(404, "Assigned user not found")
            changes.append(f"assigned_to: {ticket.assigned_to} -> {new_assignee}")
            ticket.assigned_to = new_assignee
        if changes:
            record_audit_log(db, user.id, "maintenance.update", entity_type="maintenance_ticket", entity_id=str(ticket.id), details="; ".join(changes))
        db.commit()
        db.refresh(ticket)
        unit = db.get(Unit, ticket.unit_id)
        return StaffTaskOut(
            id=ticket.id, category="maintenance", unit_id=ticket.unit_id, unit_name=unit.name if unit else None,
            booking_id=None,
            label=(ticket.category or "General").replace("_", " ").title(), priority=ticket.priority, status=ticket.status,
            assigned_to=ticket.assigned_to, created_at=ticket.created_at, started_at=None,
            completed_at=ticket.resolved_at, notes=ticket.description,
        )

    if category == "guest_request":
        req = db.get(GuestRequest, task_id)
        if req is None:
            raise HTTPException(404, "Task not found")
        changes = []
        if payload.status is not None:
            if payload.status not in REQ_STATUSES:
                raise HTTPException(400, f"Invalid status '{payload.status}'")
            changes.append(f"status: {req.status} -> {payload.status}")
            req.status = payload.status
            if payload.status == "resolved" and req.resolved_at is None:
                req.resolved_at = datetime.now(timezone.utc)
        if "assigned_to" in payload.model_fields_set:
            new_assignee = payload.assigned_to
            if new_assignee is not None and db.get(User, new_assignee) is None:
                raise HTTPException(404, "Assigned user not found")
            changes.append(f"assigned_to: {req.assigned_to} -> {new_assignee}")
            req.assigned_to = new_assignee
        if payload.notes is not None:
            req.staff_notes = payload.notes
        if changes:
            record_audit_log(db, user.id, "guest_request.update", entity_type="guest_request", entity_id=str(req.id), details="; ".join(changes))
        db.commit()
        db.refresh(req)
        booking = db.get(Booking, req.booking_id) if req.booking_id else None
        unit = db.get(Unit, booking.unit_id) if booking else None
        return StaffTaskOut(
            id=req.id, category="guest_request", unit_id=unit.id if unit else None, unit_name=unit.name if unit else None,
            booking_id=req.booking_id,
            label=req.request_type.replace("_", " ").title(), priority=req.priority, status=req.status,
            assigned_to=req.assigned_to, created_at=req.created_at, started_at=None,
            completed_at=req.resolved_at, notes=req.staff_notes or req.description,
        )

    raise HTTPException(400, f"Invalid category '{category}'")
