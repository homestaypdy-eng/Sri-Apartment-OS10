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
from app.modules.housekeeping.models import TASK_STATUSES, TASK_TYPES, HousekeepingPhoto, HousekeepingTask
from app.modules.housekeeping.schemas import HousekeepingPhotoOut, HousekeepingTaskCreate, HousekeepingTaskOut, HousekeepingTaskUpdate
from app.modules.notifications.service import trigger_housekeeping_task_created, trigger_housekeeping_task_completed
from app.modules.properties.models import Unit
from app.shared.file_upload import read_and_validate_upload, IMAGE_CONTENT_TYPES
from app.shared.rbac import get_current_user, require_role

PHOTO_STORAGE_DIR = os.environ.get("HOUSEKEEPING_PHOTO_STORAGE_DIR", "/tmp/sri_apartment_housekeeping_photos")
os.makedirs(PHOTO_STORAGE_DIR, exist_ok=True)

router = APIRouter(prefix="/api/v1/housekeeping-tasks", tags=["housekeeping"])


@router.post("", response_model=HousekeepingTaskOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def create_task(payload: HousekeepingTaskCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, payload.unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if payload.task_type not in TASK_TYPES:
        raise HTTPException(400, f"Invalid task_type '{payload.task_type}'")

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


@router.get("", response_model=list[HousekeepingTaskOut], dependencies=[Depends(require_role("admin", "staff"))])
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


@router.patch("/{task_id}", response_model=HousekeepingTaskOut, dependencies=[Depends(require_role("admin", "staff"))])
def update_task(task_id: uuid.UUID, payload: HousekeepingTaskUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(HousekeepingTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")

    changes = []
    was_completed_before = task.status == "completed"
    if payload.status is not None:
        if payload.status not in TASK_STATUSES:
            raise HTTPException(400, f"Invalid status '{payload.status}'")
        changes.append(f"status: {task.status} -> {payload.status}")
        task.status = payload.status
        if payload.status == "completed":
            task.completed_at = datetime.now(timezone.utc)
            # Completing a cleaning task is what actually clears the room's
            # dirty flag — this is the one place housekeeping_status flips
            # back to clean, so it can't be marked clean without a task.
            unit = db.get(Unit, task.unit_id)
            unit.housekeeping_status = "clean"

    if payload.assigned_to is not None:
        changes.append(f"assigned_to: {task.assigned_to} -> {payload.assigned_to}")
        task.assigned_to = payload.assigned_to
    if payload.notes is not None:
        task.notes = payload.notes

    if changes:
        record_audit_log(db, user.id, "housekeeping.update", entity_type="housekeeping_task", entity_id=str(task.id), details="; ".join(changes))
    if task.status == "completed" and not was_completed_before:
        trigger_housekeeping_task_completed(db, task)
    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/photos", response_model=HousekeepingPhotoOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
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


@router.get("/{task_id}/photos", response_model=list[HousekeepingPhotoOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_housekeeping_photos(task_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(HousekeepingPhoto).where(HousekeepingPhoto.task_id == task_id)).all()


@router.get("/{task_id}/photos/{photo_id}/download", dependencies=[Depends(require_role("admin", "staff"))])
def download_housekeeping_photo(task_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db)):
    photo = db.get(HousekeepingPhoto, photo_id)
    if photo is None or photo.task_id != task_id:
        raise HTTPException(404, "Photo not found")
    if not os.path.exists(photo.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(photo.file_path, media_type=photo.content_type, filename=photo.filename)


@router.delete("/{task_id}/photos/{photo_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff"))])
def delete_housekeeping_photo(task_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    photo = db.get(HousekeepingPhoto, photo_id)
    if photo is None or photo.task_id != task_id:
        raise HTTPException(404, "Photo not found")
    record_audit_log(db, user.id, "housekeeping.photo_delete", entity_type="housekeeping_task", entity_id=str(task_id), details=f"Deleted: {photo.filename}")
    if os.path.exists(photo.file_path):
        os.remove(photo.file_path)
    db.delete(photo)
    db.commit()


@router.get("/{task_id}/timeline", dependencies=[Depends(require_role("admin", "staff"))])
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
