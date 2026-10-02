import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
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
    # Locked FOR UPDATE so two concurrent PATCHes on the SAME user can't
    # lost-update each other (same pattern established for HousekeepingTask/
    # MaintenanceTicket in Phases 6-7).
    user = db.get(User, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(404, "User not found")

    new_role = None
    if payload.role is not None:
        new_role = db.scalar(select(Role).where(Role.name == payload.role))
        if new_role is None:
            raise HTTPException(400, f"Unknown role '{payload.role}'")

    is_self = user_id == actor.id
    is_currently_admin = user.role.name == "admin"

    # An admin can never deactivate or demote their own account. This is
    # unconditional (not just a "you're the last admin" edge case) since the
    # point is to prevent an accidental self-lockout regardless of how many
    # other admins currently exist.
    if is_self:
        if payload.is_active is False:
            raise HTTPException(400, "You cannot deactivate your own account.")
        if new_role is not None and new_role.name != "admin":
            raise HTTPException(400, "You cannot remove your own admin role.")

    # The system must always retain at least one active admin. Block
    # demoting or deactivating the last one, whether or not the actor
    # performing the change is that admin (self-targeting is already,
    # unconditionally, covered by the block above).
    would_lose_admin_status = is_currently_admin and user.is_active and (
        (new_role is not None and new_role.name != "admin") or payload.is_active is False
    )
    if would_lose_admin_status:
        # Phase 8: lock the shared 'admin' Role row as a serialization gate.
        # Without it, two concurrent requests demoting/deactivating two
        # DIFFERENT admins (e.g. admin A demotes B while admin B
        # simultaneously demotes A) can each independently count the OTHER
        # as still active, both pass this check, and both commit — leaving
        # zero active admins. Locking this one shared row forces such
        # requests to serialize: the second one re-evaluates the count only
        # after the first has committed, so it correctly sees the reduced
        # count. (Postgres disallows FOR UPDATE on the aggregate COUNT
        # query itself, so the lock is taken on this row instead.)
        admin_role = db.scalar(select(Role).where(Role.name == "admin").with_for_update())
        other_active_admins = db.scalar(
            select(func.count()).select_from(User).where(
                User.role_id == admin_role.id, User.is_active.is_(True), User.id != user_id
            )
        )
        if not other_active_admins:
            raise HTTPException(400, "Cannot remove admin access from the last remaining active administrator.")

    changes = []
    if new_role is not None:
        changes.append(f"role -> {new_role.name}")
        user.role_id = new_role.id

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
