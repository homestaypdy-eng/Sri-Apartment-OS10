import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog, LoginEvent


def record_audit_log(
    db: Session,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: str | None = None,
    previous_value: str | None = None,
    new_value: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Adds an audit log row to the current session WITHOUT committing —
    callers commit as part of their own transaction, so a failed audit
    write can't silently succeed while the real action fails, or vice versa.
    previous_value/new_value are optional structured before/after state —
    existing callers that only pass a human-readable `details` string keep
    working unchanged; new callers can populate these two directly instead
    of (or in addition to) folding everything into `details` text."""
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            details=details,
            previous_value=previous_value,
            new_value=new_value,
            ip_address=ip_address,
        )
    )


def record_login_event(db: Session, email_attempted: str, success: bool, user_id: uuid.UUID | None, ip_address: str | None, user_agent: str | None) -> None:
    db.add(
        LoginEvent(
            user_id=user_id,
            email_attempted=email_attempted,
            success=success,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )


class AuditLogOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    details: str | None
    previous_value: str | None
    new_value: str | None
    ip_address: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class LoginEventOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    email_attempted: str
    success: bool
    ip_address: str | None
    user_agent: str | None
    created_at: datetime

    class Config:
        from_attributes = True
