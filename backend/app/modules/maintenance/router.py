import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.finance.models import Expense
from app.modules.maintenance.models import PHOTO_TYPES, TICKET_CATEGORIES, TICKET_PRIORITIES, TICKET_STATUSES, MaintenancePhoto, MaintenanceTicket
from app.modules.maintenance.schemas import MaintenancePhotoOut, MaintenanceTicketCreate, MaintenanceTicketOut, MaintenanceTicketUpdate
from app.modules.notifications.service import trigger_maintenance_ticket_created, trigger_maintenance_ticket_resolved
from app.modules.properties.models import Unit
from app.shared.file_upload import read_and_validate_upload, IMAGE_CONTENT_TYPES
from app.shared.rbac import get_current_user, require_role

PHOTO_STORAGE_DIR = os.environ.get("MAINTENANCE_PHOTO_STORAGE_DIR", "/tmp/sri_apartment_maintenance_photos")
os.makedirs(PHOTO_STORAGE_DIR, exist_ok=True)

router = APIRouter(prefix="/api/v1/maintenance-tickets", tags=["maintenance"])


@router.post("", response_model=MaintenanceTicketOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def create_ticket(payload: MaintenanceTicketCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, payload.unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if payload.priority not in TICKET_PRIORITIES:
        raise HTTPException(400, f"Invalid priority '{payload.priority}'")
    if payload.category is not None and payload.category not in TICKET_CATEGORIES:
        raise HTTPException(400, f"Invalid category '{payload.category}'")
    if payload.assigned_to is not None and db.get(User, payload.assigned_to) is None:
        raise HTTPException(404, "Assigned user not found")

    ticket = MaintenanceTicket(
        unit_id=payload.unit_id,
        description=payload.description,
        priority=payload.priority,
        category=payload.category,
        assigned_to=payload.assigned_to,
        reported_by=user.id,
        status="open",
    )
    db.add(ticket)
    db.flush()
    record_audit_log(db, user.id, "maintenance.create", entity_type="maintenance_ticket", entity_id=str(ticket.id), details=f"{payload.priority} priority: {payload.description[:100]}")
    trigger_maintenance_ticket_created(db, ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.get("", response_model=list[MaintenanceTicketOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_tickets(
    unit_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(MaintenanceTicket)
    if unit_id:
        query = query.where(MaintenanceTicket.unit_id == unit_id)
    if status:
        query = query.where(MaintenanceTicket.status == status)
    return db.scalars(query.order_by(MaintenanceTicket.created_at.desc(), MaintenanceTicket.id).offset(offset).limit(limit)).all()


@router.patch("/{ticket_id}", response_model=MaintenanceTicketOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_ticket(ticket_id: uuid.UUID, payload: MaintenanceTicketUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # with_for_update locks the row for the rest of this transaction, so two
    # near-simultaneous PATCHes on the same ticket (e.g. a double-clicked
    # "Resolve") can't both read linked_expense_id as unset and each create
    # their own Expense.
    ticket = db.get(MaintenanceTicket, ticket_id, with_for_update=True)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")

    changes = []
    was_resolved_before = ticket.status in ("resolved", "closed")
    if payload.status is not None:
        if payload.status not in TICKET_STATUSES:
            raise HTTPException(400, f"Invalid status '{payload.status}'")
        changes.append(f"status: {ticket.status} -> {payload.status}")
        ticket.status = payload.status
        if payload.status in ("resolved", "closed") and ticket.resolved_at is None:
            ticket.resolved_at = datetime.now(timezone.utc)

    # model_fields_set (not `is not None`) distinguishes "field omitted"
    # from an explicit `assigned_to: null` — the latter now clears the
    # assignment. Every other field on this endpoint is untouched.
    if "assigned_to" in payload.model_fields_set:
        new_assignee = payload.assigned_to
        if new_assignee is not None and db.get(User, new_assignee) is None:
            raise HTTPException(404, "Assigned user not found")
        changes.append(f"assigned_to: {ticket.assigned_to} -> {new_assignee}")
        ticket.assigned_to = new_assignee
    if payload.priority is not None:
        if payload.priority not in TICKET_PRIORITIES:
            raise HTTPException(400, f"Invalid priority '{payload.priority}'")
        changes.append(f"priority: {ticket.priority} -> {payload.priority}")
        ticket.priority = payload.priority
    if payload.category is not None:
        if payload.category not in TICKET_CATEGORIES:
            raise HTTPException(400, f"Invalid category '{payload.category}'")
        changes.append(f"category: {ticket.category} -> {payload.category}")
        ticket.category = payload.category
    cost_changed_this_request = payload.cost is not None
    if payload.cost is not None:
        changes.append(f"cost: {ticket.cost} -> {payload.cost}")
        ticket.cost = payload.cost

    # Finance integration: a resolved/closed ticket with a real cost gets
    # a genuine Expense record (category="maintenance", tagged to this
    # ticket's unit) — this is what actually makes the cost flow into
    # Apartment P&L, Cash Flow, and Daily Cashbook, instead of sitting on
    # the ticket as a number nothing else in Finance ever sees.
    # linked_expense_id makes repeated saves update the same expense
    # rather than creating a new one every time.
    #
    # Only re-enter this sync on the request that actually resolves the
    # ticket for the first time, or one that explicitly changes cost — not
    # on every subsequent unrelated PATCH (e.g. just changing priority) —
    # otherwise an unrelated edit on an already-resolved ticket would
    # silently re-sync (and could overwrite) an Expense amount someone had
    # since corrected directly in Finance.
    is_resolved_now = ticket.status in ("resolved", "closed")
    just_resolved_this_request = is_resolved_now and not was_resolved_before
    if is_resolved_now and ticket.cost and float(ticket.cost) > 0 and (just_resolved_this_request or cost_changed_this_request):
        if ticket.linked_expense_id:
            expense = db.get(Expense, ticket.linked_expense_id)
            if expense and float(expense.amount) != float(ticket.cost):
                old_amount = expense.amount
                expense.amount = ticket.cost
                record_audit_log(db, user.id, "finance.expense.update", entity_type="expense", entity_id=str(expense.id), details=f"Maintenance ticket cost changed: Rs.{old_amount} -> Rs.{ticket.cost}")
        else:
            expense = Expense(
                category="maintenance", amount=ticket.cost, expense_date=(ticket.resolved_at or datetime.now(timezone.utc)).date(),
                unit_id=ticket.unit_id, notes=f"Auto-created from maintenance ticket: {ticket.description[:150]}", created_by=user.id,
            )
            db.add(expense)
            db.flush()
            ticket.linked_expense_id = expense.id
            record_audit_log(db, user.id, "finance.expense.create", entity_type="expense", entity_id=str(expense.id), details=f"From maintenance ticket {ticket.id}: Rs.{ticket.cost}")

    if changes:
        record_audit_log(db, user.id, "maintenance.update", entity_type="maintenance_ticket", entity_id=str(ticket.id), details="; ".join(changes))

    if is_resolved_now and not was_resolved_before:
        trigger_maintenance_ticket_resolved(db, ticket)

    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/photos", response_model=MaintenancePhotoOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
async def upload_maintenance_photo(ticket_id: uuid.UUID, photo_type: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ticket = db.get(MaintenanceTicket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    if photo_type not in PHOTO_TYPES:
        raise HTTPException(400, f"Invalid photo_type '{photo_type}'. Must be one of {PHOTO_TYPES}")

    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{ticket_id}_{photo_type}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(PHOTO_STORAGE_DIR, stored_name)
    contents = await read_and_validate_upload(file, allowed_content_types=IMAGE_CONTENT_TYPES)
    with open(stored_path, "wb") as f:
        f.write(contents)

    photo = MaintenancePhoto(
        ticket_id=ticket_id,
        photo_type=photo_type,
        filename=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream",
        file_path=stored_path,
        uploaded_by=user.id,
    )
    db.add(photo)
    record_audit_log(db, user.id, "maintenance.photo_upload", entity_type="maintenance_ticket", entity_id=str(ticket_id), details=f"{photo_type}: {photo.filename}")
    db.commit()
    db.refresh(photo)
    return photo


@router.get("/{ticket_id}/photos", response_model=list[MaintenancePhotoOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_maintenance_photos(ticket_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(MaintenancePhoto).where(MaintenancePhoto.ticket_id == ticket_id)).all()


@router.get("/{ticket_id}/photos/{photo_id}/download", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def download_maintenance_photo(ticket_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db)):
    photo = db.get(MaintenancePhoto, photo_id)
    if photo is None or photo.ticket_id != ticket_id:
        raise HTTPException(404, "Photo not found")
    if not os.path.exists(photo.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(photo.file_path, media_type=photo.content_type, filename=photo.filename)


@router.delete("/{ticket_id}/photos/{photo_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def delete_maintenance_photo(ticket_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    photo = db.get(MaintenancePhoto, photo_id)
    if photo is None or photo.ticket_id != ticket_id:
        raise HTTPException(404, "Photo not found")
    record_audit_log(db, user.id, "maintenance.photo_delete", entity_type="maintenance_ticket", entity_id=str(ticket_id), details=f"Deleted: {photo.filename}")
    if os.path.exists(photo.file_path):
        os.remove(photo.file_path)
    db.delete(photo)
    db.commit()


@router.get("/{ticket_id}/timeline", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_maintenance_timeline(ticket_id: uuid.UUID, db: Session = Depends(get_db)):
    """Every audit-logged event for this ticket, oldest first — same
    pattern as the booking/housekeeping timelines."""
    from app.modules.audit.models import AuditLog

    ticket = db.get(MaintenanceTicket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")

    logs = db.scalars(
        select(AuditLog).where(AuditLog.entity_type == "maintenance_ticket", AuditLog.entity_id == str(ticket_id)).order_by(AuditLog.created_at.asc())
    ).all()
    user_ids = {log.user_id for log in logs if log.user_id}
    users_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    events = []
    for log in logs:
        user = users_by_id.get(log.user_id) if log.user_id else None
        events.append({
            "action": log.action,
            "user_name": user.name if user else "System",
            "details": log.details,
            "previous_value": log.previous_value,
            "new_value": log.new_value,
            "created_at": log.created_at,
        })
    return {"ticket_id": ticket_id, "events": events}
