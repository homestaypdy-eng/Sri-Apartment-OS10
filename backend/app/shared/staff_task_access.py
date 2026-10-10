"""Server-side ownership rules for existing operational task records."""
from fastapi import HTTPException


def scoped_assignee(actor, requested_user_id):
    """Return the mandatory assignee filter for a Staff user's task view."""
    if actor.role.name != "staff":
        return requested_user_id
    if requested_user_id is not None and requested_user_id != actor.id:
        raise HTTPException(403, "Staff may only view tasks assigned to their own account")
    return actor.id


def require_assigned_task(actor, assigned_to) -> None:
    """Hide task existence from Staff unless it is assigned to that user."""
    if actor.role.name == "staff" and assigned_to != actor.id:
        raise HTTPException(404, "Task not found")


def forbid_staff_reassignment(actor, fields_set) -> None:
    if actor.role.name == "staff" and "assigned_to" in fields_set:
        raise HTTPException(403, "Staff cannot assign or reassign operational tasks")


def validate_staff_task_creation(actor, assigned_to) -> None:
    if actor.role.name == "staff" and assigned_to not in (None, actor.id):
        raise HTTPException(403, "Staff may only create a task assigned to their own account")
