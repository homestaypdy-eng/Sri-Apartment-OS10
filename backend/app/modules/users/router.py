import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import UserOut
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None


@router.get("", response_model=list[UserOut], dependencies=[Depends(require_role("admin"))])
def list_users(db: Session = Depends(get_db)):
    users = db.scalars(select(User)).all()
    return [UserOut(id=u.id, name=u.name, email=u.email, role=u.role.name, is_active=u.is_active) for u in users]


@router.patch("/{user_id}", response_model=UserOut, dependencies=[Depends(require_role("admin"))])
def update_user(user_id: uuid.UUID, payload: UserUpdate, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")

    changes = []
    if payload.role is not None:
        role = db.scalar(select(Role).where(Role.name == payload.role))
        if role is None:
            raise HTTPException(400, f"Unknown role '{payload.role}'")
        changes.append(f"role -> {payload.role}")
        user.role_id = role.id

    if payload.is_active is not None:
        changes.append(f"is_active -> {payload.is_active}")
        user.is_active = payload.is_active

    if changes:
        record_audit_log(
            db, actor.id, "user.update", entity_type="user", entity_id=str(user.id),
            details="; ".join(changes),
        )

    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, name=user.name, email=user.email, role=user.role.name, is_active=user.is_active)
