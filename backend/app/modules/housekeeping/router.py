import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.housekeeping.models import TASK_TYPES, HousekeepingPhoto, HousekeepingTask
from app.modules.housekeeping.schemas import HousekeepingPhotoOut, HousekeepingTaskCreate, HousekeepingTaskOut, HousekeepingTaskUpdate
from app.modules.housekeeping.service import update_housekeeping_task
from app.modules.notifications.service import trigger_housekeeping_task_created
from app.modules.properties.models import Unit
from app.shared.file_upload import read_and_validate_upload, IMAGE_CONTENT_TYPES
from app.shared.rbac import get_current_user, require_role

PHOTO_STORAGE_DIR = os.environ.get("HOUSEKEEPING_PHOTO_STORAGE_DIR", "/tmp/sri_apartment_housekeeping_photos")
os.makedirs(PHOTO_STORAGE_DIR, exist_ok=True)

router = APIRouter(prefix="/api/v1/housekeeping-tasks", tags=["housekeeping"])


@router.post("", response_model=HousekeepingTaskOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def create_task(payload: HousekeepingTaskCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, payload.unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if payload.task_type not in TASK_TYPES:
        raise HTTPException(400, f"Invalid task_type '{payload.task_type}'")
    if payload.assigned_to is not None and db.get(User, payload.assigned_to) is None:
        raise HTTPException(404, "Assigned user not found")

    task = HousekeepingTask(
        unit_id=payload.unit_id,
        assigned_to=payload.assigned_to,
        task_type=payload.task_type,
        notes=payload.notes,
        status="pending",
    )
    db.add(task)
    db.flush()
    record_audit_log(db, user.id, "housekeeping.create", entity_type="housekeeping_task", entity_id=str(task.id), details=f"{payload.task_type} task for unit {payload.unit_id}")
    trigger_housekeeping_task_created(db, task)
    db.commit()
    db.refresh(task)
    return task


@router.get("", response_model=list[HousekeepingTaskOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_tasks(
    unit_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(HousekeepingTask)
    if unit_id:
        query = query.where(HousekeepingTask.unit_id == unit_id)
    if status:
        query = query.where(HousekeepingTask.status == status)
    return db.scalars(query.order_by(HousekeepingTask.created_at.desc(), HousekeepingTask.id).offset(offset).limit(limit)).all()


@router.patch("/{task_id}", response_model=HousekeepingTaskOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_task(task_id: uuid.UUID, payload: HousekeepingTaskUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Phase 6: locked FOR UPDATE so two concurrent PATCHes for the same task
    # (e.g. two staff both trying to claim/start/complete it) serialize
    # rather than lost-update-racing — matching the same pattern this
    # router's staff_ops/router.py sibling already uses for MaintenanceTicket.
    task = db.get(HousekeepingTask, task_id, with_for_update=True)
    if task is None:
        raise HTTPException(404, "Task not found")

    # Canonical mutation workflow (app/modules/housekeeping/service.py) —
    # also called by staff_ops/router.py's unified Staff Operations
    # endpoint, so a status change from either surface behaves identically
    # (validation, timestamps, audit logging, notification).
    update_housekeeping_task(
        db, task,
        status=payload.status,
        assigned_to=payload.assigned_to,
        # model_fields_set (not `is not None`) distinguishes "field omitted"
        # from an explicit `assigned_to: null`, which clears the assignment.
        assigned_to_set="assigned_to" in payload.model_fields_set,
        notes=payload.notes,
        actor_user_id=user.id,
    )
    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/photos", response_model=HousekeepingPhotoOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
async def upload_housekeeping_photo(task_id: uuid.UUID, caption: str | None = None, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(HousekeepingTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")

    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{task_id}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(PHOTO_STORAGE_DIR, stored_name)
    contents = await read_and_validate_upload(file, allowed_content_types=IMAGE_CONTENT_TYPES)
    with open(stored_path, "wb") as f:
        f.write(contents)

    photo = HousekeepingPhoto(
        task_id=task_id,
        filename=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream",
        file_path=stored_path,
        caption=caption,
        uploaded_by=user.id,
    )
    db.add(photo)
    record_audit_log(db, user.id, "housekeeping.photo_upload", entity_type="housekeeping_task", entity_id=str(task_id), details=f"Uploaded: {photo.filename}")
    db.commit()
    db.refresh(photo)
    return photo


@router.get("/{task_id}/photos", response_model=list[HousekeepingPhotoOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_housekeeping_photos(task_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(HousekeepingPhoto).where(HousekeepingPhoto.task_id == task_id)).all()


@router.get("/{task_id}/photos/{photo_id}/download", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def download_housekeeping_photo(task_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db)):
    photo = db.get(HousekeepingPhoto, photo_id)
    if photo is None or photo.task_id != task_id:
        raise HTTPException(404, "Photo not found")
    if not os.path.exists(photo.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(photo.file_path, media_type=photo.content_type, filename=photo.filename)


@router.delete("/{task_id}/photos/{photo_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def delete_housekeeping_photo(task_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    photo = db.get(HousekeepingPhoto, photo_id)
    if photo is None or photo.task_id != task_id:
        raise HTTPException(404, "Photo not found")
    record_audit_log(db, user.id, "housekeeping.photo_delete", entity_type="housekeeping_task", entity_id=str(task_id), details=f"Deleted: {photo.filename}")
    if os.path.exists(photo.file_path):
        os.remove(photo.file_path)
    db.delete(photo)
    db.commit()


@router.get("/{task_id}/timeline", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_housekeeping_timeline(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """Every audit-logged event for this task, oldest first — same pattern
    as the booking timeline (a thin read over the shared AuditLog table,
    filtered to this one entity)."""
    from app.modules.audit.models import AuditLog

    task = db.get(HousekeepingTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")

    logs = db.scalars(
        select(AuditLog).where(AuditLog.entity_type == "housekeeping_task", AuditLog.entity_id == str(task_id)).order_by(AuditLog.created_at.asc())
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
    return {"task_id": task_id, "events": events}
