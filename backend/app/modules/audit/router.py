import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.models import AuditLog, LoginEvent
from app.modules.audit.schemas import AuditLogOut, LoginEventOut
from app.shared.rbac import require_role

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("/logs", response_model=list[AuditLogOut], dependencies=[Depends(require_role("admin"))])
def list_audit_logs(
    user_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    db: Session = Depends(get_db),
):
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    return db.scalars(query).all()


@router.get("/logins", response_model=list[LoginEventOut], dependencies=[Depends(require_role("admin"))])
def list_login_events(user_id: uuid.UUID | None = None, success: bool | None = None, db: Session = Depends(get_db)):
    query = select(LoginEvent).order_by(LoginEvent.created_at.desc()).limit(500)
    if user_id:
        query = query.where(LoginEvent.user_id == user_id)
    if success is not None:
        query = query.where(LoginEvent.success == success)
    return db.scalars(query).all()
