"""Phase 15I — policy lifecycle governance.

Closes the known gap from Phase 15G/15H: `audit_entries` (Phase 15E-1)
has a NOT NULL `action_id` foreign key to `action_proposals`, so it
structurally cannot record an event that isn't about one specific
action — policy creation, policy updates, enable/disable, version
changes, retirement, or a global-automation-switch transition all fall
outside what it can represent. This module writes to a separate,
dedicated, append-only table (`policy_audit_entries`, Phase 15I) instead
of forcing those events into the action-scoped table.

This module is governance only. It does not change RELEASE_EXPIRED_HOLD
execution logic, the BookingHold TTL rule, or anything in
policy_engine.py/action_registry.py/approval_workflow.py/
action_executor.py — none of those files are imported here beyond
Phase 15G's own `persist_policy`/`load_policy` (reused, not
duplicated) and the `Policy`/`Condition` schema types (Phase 15B,
unmodified).

GLOBAL_AUTOMATION_ENABLED remains exactly what Phase 15B designed: a
hardcoded module constant in policy_engine.py with no settings/env-var
path, changeable only by editing that source file and redeploying (see
Phase 15G for the actual production activation). This module's
`record_global_automation_transition()` does not and cannot flip that
constant — it only records, after the fact, that a transition was
deliberately made (the same source-edit-and-redeploy step already used
in Phase 15G), so the transition has a durable, queryable history
alongside the policy history. Nothing here changes how the switch is
actually controlled.

Append-only: no update or delete function exists anywhere in this
module for PolicyAuditEntryRow, and a database-level trigger enforces
the same thing (see app/db/additive_migrations.py).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.automation.models import PolicyAuditEntryRow, PolicyRow
from app.modules.automation.persistence import load_policy, persist_policy
from app.modules.automation.schemas import Condition, Policy, PolicyLifecycleEventType, Role

# The exact six fields Phase 15I section 6 requires a version bump for.
# `enabled` and `policy_id`/`version` themselves are deliberately
# excluded — enable/disable has its own function and its own event
# types, and policy_id/version aren't "content."
_SEMANTIC_FIELDS = (
    "action_type", "automation_level", "risk_level", "conditions", "required_role", "requires_approval",
)


@dataclass(frozen=True)
class PersistenceResult:
    success: bool
    reason: str | None


def _policy_snapshot(policy: Policy) -> dict:
    """Exactly the fields Phase 15I section 9 requires the change_hash
    to cover — never guest data, never a credential, because none of
    those exist anywhere on a Policy object in the first place."""
    return {
        "policy_id": policy.policy_id,
        "action_type": policy.action_type,
        "automation_level": policy.automation_level,
        "risk_level": policy.risk_level,
        "conditions": [c.model_dump() for c in policy.conditions],
        "required_role": policy.required_role,
        "requires_approval": policy.requires_approval,
        "enabled": policy.enabled,
        "version": policy.version,
    }


def compute_policy_change_hash(policy: Policy) -> str:
    """Deterministic — same policy state always produces the same hash.
    Reuses the exact same hashlib.sha256 + json.dumps(sort_keys=True)
    discipline as policy_engine.py's compute_proposal_hash and
    approval_workflow.py's compute_approval_id, rather than inventing a
    second, unrelated hashing approach."""
    canonical = json.dumps(_policy_snapshot(policy), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_global_switch_hash(value: bool) -> str:
    canonical = json.dumps({"global_automation_enabled": value}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_policy_audit_id(
    policy_id: str | None, policy_version: int | None, event_type: str, actor: str, timestamp: datetime, change_hash: str
) -> str:
    """Includes policy_version and change_hash specifically so two
    genuinely different events (e.g. two consecutive updates to the same
    policy) can never collide on id even if they share an identical
    actor/timestamp — change_hash already captures the full state, so
    any real difference between two events is reflected here too."""
    canonical = json.dumps(
        {
            "policy_id": policy_id, "policy_version": policy_version, "event_type": event_type,
            "actor": actor, "timestamp": timestamp.isoformat(), "change_hash": change_hash,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_policy_audit(
    db: Session,
    policy_id: str | None,
    policy_version: int | None,
    event_type: PolicyLifecycleEventType,
    actor: str,
    timestamp: datetime,
    previous_state: Policy | None,
    new_state: Policy | None,
    reason: str | None,
) -> None:
    prev_snapshot = _policy_snapshot(previous_state) if previous_state is not None else None
    new_snapshot = _policy_snapshot(new_state) if new_state is not None else None
    change_hash = compute_policy_change_hash(new_state if new_state is not None else previous_state)
    row = PolicyAuditEntryRow(
        policy_audit_id=_compute_policy_audit_id(policy_id, policy_version, event_type, actor, timestamp, change_hash),
        policy_id=policy_id,
        policy_version=policy_version,
        event_type=event_type,
        actor=actor,
        timestamp=timestamp,
        previous_state=json.dumps(prev_snapshot, sort_keys=True, default=str) if prev_snapshot is not None else None,
        new_state=json.dumps(new_snapshot, sort_keys=True, default=str) if new_snapshot is not None else None,
        change_reason=reason,
        change_hash=change_hash,
    )
    db.add(row)


def _check_actor(actor: str, actor_role: str) -> str | None:
    """Returns a rejection reason, or None if authorized. actor is
    mandatory (a blank/whitespace-only identity is rejected, same as an
    unrecognized role); required_role values beyond admin/staff are
    rejected outright — no new role is ever accepted, matching the
    existing RBAC vocabulary exactly."""
    if not actor or not actor.strip():
        return "actor_required"
    if actor_role not in ("admin", "staff"):
        return "invalid_role"
    if actor_role != "admin":
        return "unauthorized_actor"
    return None


def create_policy(db: Session, policy: Policy, actor: str, actor_role: Role, reason: str | None, now: datetime) -> PersistenceResult:
    rejection = _check_actor(actor, actor_role)
    if rejection:
        return PersistenceResult(False, rejection)

    result = persist_policy(db, policy)  # Phase 15G, unmodified
    if not result.success:
        return PersistenceResult(False, result.reason)

    _write_policy_audit(db, policy.policy_id, policy.version, "POLICY_CREATED", actor, now, None, policy, reason)
    db.commit()
    return PersistenceResult(True, None)


def update_policy(
    db: Session,
    policy_id: str,
    actor: str,
    actor_role: Role,
    reason: str | None,
    now: datetime,
    action_type: str | None = None,
    automation_level: str | None = None,
    risk_level: str | None = None,
    conditions: list[Condition] | None = None,
    required_role: str | None = None,
    requires_approval: bool | None = None,
) -> PersistenceResult:
    """Version is never a caller-supplied parameter — the caller cannot
    set it directly at all, which is what makes a downgrade or a
    same-version-with-changed-semantics structurally impossible, not
    merely validated against. Every field parameter is optional and
    defaults to the current stored value when omitted."""
    rejection = _check_actor(actor, actor_role)
    if rejection:
        return PersistenceResult(False, rejection)

    current = load_policy(db, policy_id)
    if current is None:
        return PersistenceResult(False, "policy_not_found")

    updated = Policy(
        policy_id=current.policy_id,
        action_type=action_type if action_type is not None else current.action_type,
        automation_level=automation_level if automation_level is not None else current.automation_level,
        risk_level=risk_level if risk_level is not None else current.risk_level,
        conditions=conditions if conditions is not None else current.conditions,
        required_role=required_role if required_role is not None else current.required_role,
        requires_approval=requires_approval if requires_approval is not None else current.requires_approval,
        enabled=current.enabled,
        version=current.version,
    )

    semantic_changed = any(getattr(updated, f) != getattr(current, f) for f in _SEMANTIC_FIELDS)
    if not semantic_changed:
        return PersistenceResult(False, "no_semantic_change")

    updated = updated.model_copy(update={"version": current.version + 1})

    row = db.get(PolicyRow, policy_id)
    row.action_type = updated.action_type
    row.automation_level = updated.automation_level
    row.risk_level = updated.risk_level
    row.conditions = json.dumps([c.model_dump() for c in updated.conditions], default=str)
    row.required_role = updated.required_role
    row.requires_approval = updated.requires_approval
    row.version = updated.version
    db.flush()

    _write_policy_audit(db, policy_id, updated.version, "POLICY_UPDATED", actor, now, current, updated, reason)
    _write_policy_audit(db, policy_id, updated.version, "POLICY_VERSION_BUMPED", actor, now, current, updated, reason)
    db.commit()
    return PersistenceResult(True, None)


def set_policy_enabled(db: Session, policy_id: str, enabled: bool, actor: str, actor_role: Role, reason: str | None, now: datetime) -> PersistenceResult:
    rejection = _check_actor(actor, actor_role)
    if rejection:
        return PersistenceResult(False, rejection)

    current = load_policy(db, policy_id)
    if current is None:
        return PersistenceResult(False, "policy_not_found")
    if current.enabled == enabled:
        return PersistenceResult(False, "no_change")

    row = db.get(PolicyRow, policy_id)
    row.enabled = enabled
    db.flush()

    new_state = current.model_copy(update={"enabled": enabled})
    event_type = "POLICY_ENABLED" if enabled else "POLICY_DISABLED"
    _write_policy_audit(db, policy_id, current.version, event_type, actor, now, current, new_state, reason)
    db.commit()
    return PersistenceResult(True, None)


def retire_policy(db: Session, policy_id: str, actor: str, actor_role: Role, reason: str | None, now: datetime) -> PersistenceResult:
    """Never a physical delete — the row remains, enabled=False,
    status=RETIRED, permanently queryable."""
    rejection = _check_actor(actor, actor_role)
    if rejection:
        return PersistenceResult(False, rejection)

    current = load_policy(db, policy_id)
    if current is None:
        return PersistenceResult(False, "policy_not_found")
    row = db.get(PolicyRow, policy_id)
    if row.status == "RETIRED":
        return PersistenceResult(False, "already_retired")

    row.enabled = False
    row.status = "RETIRED"
    db.flush()

    new_state = current.model_copy(update={"enabled": False})
    _write_policy_audit(db, policy_id, current.version, "POLICY_RETIRED", actor, now, current, new_state, reason)
    db.commit()
    return PersistenceResult(True, None)


def record_global_automation_transition(
    db: Session, previous_value: bool, new_value: bool, actor: str, actor_role: Role, reason: str | None, now: datetime
) -> PersistenceResult:
    """Records, after the fact, a GLOBAL_AUTOMATION_ENABLED constant
    change already made by editing policy_engine.py and redeploying —
    this function has no ability to flip that constant itself, only to
    give the transition a durable, queryable history entry."""
    rejection = _check_actor(actor, actor_role)
    if rejection:
        return PersistenceResult(False, rejection)
    if previous_value == new_value:
        return PersistenceResult(False, "no_change")

    event_type = "GLOBAL_AUTOMATION_ENABLED" if new_value else "GLOBAL_AUTOMATION_DISABLED"
    global_switch_hash = compute_global_switch_hash(new_value)
    row = PolicyAuditEntryRow(
        policy_audit_id=_compute_policy_audit_id(None, None, event_type, actor, now, global_switch_hash),
        policy_id=None,
        policy_version=None,
        event_type=event_type,
        actor=actor,
        timestamp=now,
        previous_state=json.dumps({"global_automation_enabled": previous_value}),
        new_state=json.dumps({"global_automation_enabled": new_value}),
        change_reason=reason,
        change_hash=global_switch_hash,
    )
    db.add(row)
    db.commit()
    return PersistenceResult(True, None)


def establish_pre_audit_baseline(db: Session, policy_id: str, actor: str, actor_role: Role, reason: str | None, now: datetime) -> PersistenceResult:
    """Phase 15I section 15: the production release_expired_hold policy
    predates this audit mechanism (created in Phase 15G). This writes
    exactly one POLICY_AUDIT_BASELINE_ESTABLISHED event — never a
    fabricated POLICY_CREATED with a false timestamp — with
    previous_state=None (nothing is known/claimed about what came
    before) and new_state carrying the policy's own real, unaltered
    created_at inside the reason/detail, not a synthetic one."""
    rejection = _check_actor(actor, actor_role)
    if rejection:
        return PersistenceResult(False, rejection)

    current = load_policy(db, policy_id)
    if current is None:
        return PersistenceResult(False, "policy_not_found")

    row = db.get(PolicyRow, policy_id)
    baseline_reason = reason or f"pre-audit policy; actual created_at={row.created_at.isoformat()}"
    _write_policy_audit(db, policy_id, current.version, "POLICY_AUDIT_BASELINE_ESTABLISHED", actor, now, None, current, baseline_reason)
    db.commit()
    return PersistenceResult(True, None)


def load_policy_audit_history(db: Session, policy_id: str) -> list[dict]:
    from sqlalchemy import select
    rows = db.execute(
        select(PolicyAuditEntryRow)
        .where(PolicyAuditEntryRow.policy_id == policy_id)
        .order_by(PolicyAuditEntryRow.timestamp)
    ).scalars().all()
    return [_row_to_dict(r) for r in rows]


def load_global_automation_history(db: Session) -> list[dict]:
    from sqlalchemy import select
    rows = db.execute(
        select(PolicyAuditEntryRow)
        .where(PolicyAuditEntryRow.event_type.in_(["GLOBAL_AUTOMATION_ENABLED", "GLOBAL_AUTOMATION_DISABLED"]))
        .order_by(PolicyAuditEntryRow.timestamp)
    ).scalars().all()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: PolicyAuditEntryRow) -> dict:
    return {
        "policy_audit_id": row.policy_audit_id,
        "policy_id": row.policy_id,
        "policy_version": row.policy_version,
        "event_type": row.event_type,
        "actor": row.actor,
        "timestamp": row.timestamp,
        "previous_state": json.loads(row.previous_state) if row.previous_state else None,
        "new_state": json.loads(row.new_state) if row.new_state else None,
        "change_reason": row.change_reason,
        "change_hash": row.change_hash,
    }
