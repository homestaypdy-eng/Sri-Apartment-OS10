"""
The single authoritative housekeeping-task status/mutation workflow.

Consolidates two independent, near-identical implementations that used to
exist side by side:
  - housekeeping/router.py::update_task (PATCH /housekeeping-tasks/{id}) —
    the dedicated Housekeeping page's own endpoint.
  - staff_ops/router.py::update_task's category="housekeeping" branch
    (PATCH /staff/tasks/{id}?category=housekeeping) — the unified Staff
    Operations dashboard's endpoint.

Both endpoints are real, independently used by different frontend pages
(HousekeepingPage.tsx and StaffOperationsPage.tsx respectively) with
different response shapes (HousekeepingTaskOut vs. StaffTaskOut), so
neither could simply be deleted. Both now call this one function; each
router keeps its own request/response schema and just adapts this
function's result to its own Out model.

BEHAVIORAL RECONCILIATION (the two prior copies disagreed on two points —
resolved here using the current code itself as evidence, not invented):
  1. completed_at: the housekeeping/router.py copy unconditionally
     overwrote completed_at to "now" every time status was (re-)set to
     "completed", even on an already-completed task — meaning a
     redundant "mark completed" call kept advancing the recorded
     completion time. The staff_ops copy already set it only if it was
     still None. The staff_ops behavior is adopted here: it is the one
     that satisfies "repeated identical status mutation must be
     idempotent" (an already-completed task's completed_at must not
     drift forward on a resend), and it was already present and working
     in this exact codebase, not a new invention.
  2. trigger_housekeeping_task_completed: only the housekeeping/router.py
     copy ever called this notification trigger; the staff_ops copy
     silently never did, despite staff_ops's own module docstring
     claiming an update from either surface "produces an identical audit
     trail" (true for AuditLog, but the notification was a real,
     undocumented gap). Both entry points now get it, guarded by the
     same was_completed_before check that already made the original
     housekeeping/router.py call site fire at most once per completion.
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.housekeeping.models import TASK_STATUSES, HousekeepingTask
from app.modules.notifications.service import trigger_housekeeping_task_completed


def update_housekeeping_task(
    db: Session,
    task: HousekeepingTask,
    *,
    status: str | None,
    assigned_to: uuid.UUID | None,
    assigned_to_set: bool,
    notes: str | None,
    actor_user_id: uuid.UUID | None,
) -> HousekeepingTask:
    """Mutates `task` in place and stages its audit-log entry/notification
    trigger on `db`. Does NOT commit — callers commit as part of their
    own transaction (same convention as record_audit_log itself), so a
    failure anywhere in the caller's request rolls back the task change,
    the audit row, and the notification trigger's own DB writes together.

    status/assigned_to/notes mirror HousekeepingTaskUpdate/StaffTaskUpdate,
    which are already structurally identical — assigned_to_set distinguishes
    "field omitted" from an explicit `assigned_to: null` (which clears the
    assignment), matching both schemas' own exclude-unset convention.
    """
    changes: list[str] = []
    was_completed_before = task.status == "completed"

    if status is not None:
        if status not in TASK_STATUSES:
            raise HTTPException(400, f"Invalid status '{status}'")
        changes.append(f"status: {task.status} -> {status}")
        task.status = status
        if status == "in_progress" and task.started_at is None:
            task.started_at = datetime.now(timezone.utc)
        if status == "completed":
            if task.completed_at is None:
                task.completed_at = datetime.now(timezone.utc)
            # Completing a cleaning task is what actually clears the
            # room's dirty flag — this is the one place
            # housekeeping_status flips back to clean, so it can't be
            # marked clean without a task.
            if task.unit is not None:
                task.unit.housekeeping_status = "clean"

    if assigned_to_set:
        if assigned_to is not None and db.get(User, assigned_to) is None:
            raise HTTPException(404, "Assigned user not found")
        changes.append(f"assigned_to: {task.assigned_to} -> {assigned_to}")
        task.assigned_to = assigned_to

    if notes is not None:
        task.notes = notes

    if changes:
        record_audit_log(
            db, actor_user_id, "housekeeping.update", entity_type="housekeeping_task", entity_id=str(task.id),
            details="; ".join(changes),
        )

    if task.status == "completed" and not was_completed_before:
        trigger_housekeeping_task_completed(db, task)

    return task
