from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.notifications.models import NotificationLog
from app.modules.notifications.schemas import NotificationLogOut, ReminderScanResult
from app.modules.notifications.service import scan_and_send_due_reminders, scan_and_send_escalations
from app.shared.rbac import get_current_user, require_role
import uuid
from datetime import datetime, timezone

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

# RBAC Phase B security fix (finding I-2): GET /logs is the system-wide,
# unscoped notification log — unlike /inbox (self-scoped to the caller),
# it was never meant to expose HR-adjacent events about OTHER staff members
# to Manager. These are the only two event_type values that reveal
# HR/payroll activity (see trigger_leave_reviewed / trigger_payroll_paid in
# notifications/service.py) — every other event_type is operational and
# stays visible to Manager unchanged.
MANAGER_EXCLUDED_EVENT_TYPES = {"leave_reviewed", "payroll_paid"}


@router.get("/logs", response_model=list[NotificationLogOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_notification_logs(event_type: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = select(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(500)
    if event_type:
        query = query.where(NotificationLog.event_type == event_type)
    # Applied unconditionally, after the optional event_type filter above —
    # a Manager passing ?event_type=payroll_paid must still get nothing back,
    # not have this exclusion narrowed away by their own query param.
    if user.role.name == "manager":
        query = query.where(NotificationLog.event_type.notin_(MANAGER_EXCLUDED_EVENT_TYPES))
    return db.scalars(query).all()


@router.get("/inbox", response_model=list[NotificationLogOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_my_inbox(unread_only: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The current logged-in user's in-app notifications — things
    addressed to THEM specifically (their leave decision, their payslip),
    not every notification in the whole system."""
    query = select(NotificationLog).where(NotificationLog.user_id == user.id).order_by(NotificationLog.created_at.desc()).limit(100)
    if unread_only:
        query = query.where(NotificationLog.read_at.is_(None))
    return db.scalars(query).all()


@router.post("/inbox/{notification_id}/mark-read", response_model=NotificationLogOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def mark_notification_read(notification_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    notification = db.get(NotificationLog, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification


@router.post("/scan-reminders", response_model=ReminderScanResult, dependencies=[Depends(require_role("admin"))])
def manual_reminder_scan(db: Session = Depends(get_db)):
    """
    Manually triggers the same reminder scan the background scheduler runs
    automatically (when enabled) — useful for the 'Manual Sync Button'-style
    on-demand trigger, and for verifying the reminder logic without waiting
    for the schedule.
    """
    results = scan_and_send_due_reminders(db)
    return ReminderScanResult(**results)


@router.post("/scan-escalations", dependencies=[Depends(require_role("admin"))])
def manual_escalation_scan(db: Session = Depends(get_db)):
    """Manually triggers the escalation scan — same on-demand pattern as
    scan-reminders, kept as its own endpoint so it has its own response
    shape and doesn't change scan-reminders' existing behavior."""
    return scan_and_send_escalations(db)
