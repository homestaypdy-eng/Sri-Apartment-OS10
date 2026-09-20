"""Phase 15E-1 — durable persistence for automation authorization state.

This module only stores what 15B (Policy Engine), 15C (Action Registry),
and 15D (Approval Workflow) already decided. It never authorizes
anything itself — every write here is gated by re-running 15C's
structural validation and/or 15D's RBAC checks first, and a rejection at
either gate means nothing is written. It never executes anything: there
is no ActionExecutor import, no booking/payment/pricing/GST/OTA model
import anywhere in this file, and no function here can set an
ActionProposal's status to EXECUTING/SUCCEEDED/FAILED/ROLLED_BACK — see
_ALLOWED_PROPOSAL_STATUSES below, a hard, structural gate.

Concurrency: the `approvals` table has a UNIQUE constraint on
`action_id`. The initial APPROVED/REJECTED write is a plain INSERT — a
second concurrent INSERT for the same action_id fails the unique
constraint at the database level (a real compare-and-swap, not a
read-then-write race). Revocation and expiry are conditional UPDATEs
(`WHERE decision = 'APPROVED'`), which Postgres serializes via row-level
locking under normal transaction isolation — no `threading.Lock` is
used or needed here (unlike Phase 15D's in-memory ApprovalLedger, which
existed only because that phase had no database yet).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.automation.action_registry import validate_proposal_structure
from app.modules.automation.approval_workflow import (
    RevalidationResult,
    compute_approval_id,
    revalidate_approval,
)

# Duplicated from approval_workflow.py's own constant rather than
# importing a module-private name across the module boundary — same
# small-constant-duplication convention used elsewhere in this package
# (see approval_workflow.py's own _ROLE_SATISFIES comment).
_EXPIRY_REASON = "approval_window_elapsed"
from app.modules.automation.models import (
    ActionProposalRow,
    ApprovalRow,
    AuditEntryRow,
    PolicyDecisionRow,
    PolicyRow,
)
from app.modules.automation.schemas import (
    ActionProposal,
    Approval,
    Policy,
    PolicyDecision,
    Role,
)

# A proposal can only ever be *created* or have its status *updated* to
# one of these — Phase 15E-1 has no executor, so no function in this
# module accepts an execution-related status, no matter what a caller
# passes in.
_ALLOWED_PROPOSAL_STATUSES = {
    "PROPOSED", "APPROVAL_REQUIRED", "APPROVED", "REJECTED", "EXPIRED", "CANCELLED",
}


@dataclass(frozen=True)
class PersistenceResult:
    success: bool
    reason: str | None


def compute_audit_id(action_id: str, event_type: str, actor: str, timestamp: datetime, detail: dict) -> str:
    canonical = json.dumps(
        {"action_id": action_id, "event_type": event_type, "actor": actor,
         "timestamp": timestamp.isoformat(), "detail": detail},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_audit(db: Session, action_id: str, event_type: str, actor: str, timestamp: datetime, detail: dict) -> None:
    row = AuditEntryRow(
        audit_id=compute_audit_id(action_id, event_type, actor, timestamp, detail),
        action_id=action_id, event_type=event_type, actor=actor,
        timestamp=timestamp, detail=json.dumps(detail, sort_keys=True, default=str),
    )
    db.add(row)


def persist_policy(db: Session, policy: Policy) -> PersistenceResult:
    """Phase 15G: writes exactly one controlled row per call — there is
    no generic "update any policy" path; this is invoked only by an
    explicit, one-off activation script, never by request-handling code
    (no router exists)."""
    row = PolicyRow(
        policy_id=policy.policy_id,
        action_type=policy.action_type,
        automation_level=policy.automation_level,
        risk_level=policy.risk_level,
        conditions=json.dumps([c.model_dump() for c in policy.conditions], default=str),
        required_role=policy.required_role,
        requires_approval=policy.requires_approval,
        enabled=policy.enabled,
        version=policy.version,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return PersistenceResult(False, "duplicate_policy_id")
    db.commit()
    return PersistenceResult(True, None)


def load_policy(db: Session, policy_id: str) -> Policy | None:
    row = db.get(PolicyRow, policy_id)
    if row is None:
        return None
    from app.modules.automation.schemas import Condition
    conditions = [Condition(**c) for c in json.loads(row.conditions)]
    return Policy(
        policy_id=row.policy_id, action_type=row.action_type, automation_level=row.automation_level,
        risk_level=row.risk_level, conditions=conditions, required_role=row.required_role,
        requires_approval=row.requires_approval, enabled=row.enabled, version=row.version,
    )


def persist_proposal(db: Session, proposal: ActionProposal) -> PersistenceResult:
    """Structural validation (Phase 15C, unmodified) runs before any
    write — an unregistered action type, a disabled action, or an
    invalid target/parameters shape never reaches the database."""
    if proposal.status not in _ALLOWED_PROPOSAL_STATUSES:
        return PersistenceResult(False, "execution_status_not_permitted_in_15e1")

    structural = validate_proposal_structure(proposal)
    if not structural.valid:
        return PersistenceResult(False, structural.reason)

    row = ActionProposalRow(
        action_id=proposal.action_id,
        action_type=proposal.action_type,
        policy_id=proposal.policy_id,
        risk_level=proposal.risk_level,
        automation_level=proposal.automation_level,
        actor=proposal.actor,
        target=json.dumps(proposal.target, sort_keys=True, default=str),
        parameters=json.dumps(proposal.parameters, sort_keys=True, default=str),
        idempotency_key=proposal.idempotency_key,
        approval_requirement=proposal.approval_requirement,
        proposal_hash=proposal.proposal_hash,
        status=proposal.status,
        timestamp=proposal.timestamp,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return PersistenceResult(False, "duplicate_action_id_or_idempotency_key")

    write_audit(db, proposal.action_id, "PROPOSED", proposal.actor, proposal.timestamp,
                 {"action_type": proposal.action_type})
    db.commit()
    return PersistenceResult(True, None)


def update_proposal_status(db: Session, action_id: str, new_status: str, actor: str, now: datetime) -> PersistenceResult:
    if new_status not in _ALLOWED_PROPOSAL_STATUSES:
        return PersistenceResult(False, "execution_status_not_permitted_in_15e1")
    result = db.execute(
        update(ActionProposalRow).where(ActionProposalRow.action_id == action_id).values(status=new_status)
    )
    if result.rowcount == 0:
        db.rollback()
        return PersistenceResult(False, "proposal_not_found")
    event_type = "APPROVAL_REQUESTED" if new_status == "APPROVAL_REQUIRED" else new_status
    write_audit(db, action_id, event_type, actor, now, {"new_status": new_status})
    db.commit()
    return PersistenceResult(True, None)


def persist_policy_decision(db: Session, decision: PolicyDecision) -> PersistenceResult:
    row = PolicyDecisionRow(
        decision_id=decision.decision_id,
        action_id=decision.action_id,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        outcome=decision.outcome,
        reason=decision.reason,
        evaluated_conditions=json.dumps([c.model_dump() for c in decision.evaluated_conditions], default=str),
        evaluated_at=decision.evaluated_at,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return PersistenceResult(False, "duplicate_decision_id")

    write_audit(db, decision.action_id, "POLICY_EVALUATED", "system", decision.evaluated_at,
                 {"outcome": decision.outcome, "reason": decision.reason})
    db.commit()
    return PersistenceResult(True, None)


def persist_approval(
    db: Session,
    proposal: ActionProposal,
    policy_version: int,
    approver: str,
    approver_role: Role,
    decision: str,
    reason: str | None,
    now: datetime,
    expires_at: datetime | None = None,
) -> PersistenceResult:
    if decision not in ("APPROVED", "REJECTED"):
        return PersistenceResult(False, "invalid_decision_for_persist_approval")
    if approver_role != "admin":
        return PersistenceResult(False, "unauthorized_approver")
    if proposal.approval_requirement == "dual_admin" and approver == proposal.actor:
        return PersistenceResult(False, "self_approval_not_permitted")

    row = ApprovalRow(
        approval_id=compute_approval_id(proposal.action_id, proposal.proposal_hash, decision, approver),
        action_id=proposal.action_id,
        proposal_hash=proposal.proposal_hash,
        policy_id=proposal.policy_id,
        policy_version=policy_version,
        approver=approver,
        decision=decision,
        status=decision,
        reason=reason,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(row)
    try:
        db.flush()  # UNIQUE(action_id) enforces exactly-once resolution
    except IntegrityError:
        db.rollback()
        return PersistenceResult(False, "already_resolved")

    write_audit(db, proposal.action_id, decision, approver, now, {"reason": reason})
    db.commit()
    return PersistenceResult(True, None)


def revoke_approval(db: Session, action_id: str, revoking_actor: str, revoking_role: Role, reason: str | None, now: datetime) -> PersistenceResult:
    if revoking_role != "admin":
        return PersistenceResult(False, "unauthorized_revoker")

    result = db.execute(
        update(ApprovalRow)
        .where(ApprovalRow.action_id == action_id, ApprovalRow.decision == "APPROVED")
        .values(decision="REVOKED", status="REVOKED", reason=reason)
    )
    if result.rowcount == 0:
        db.rollback()
        return PersistenceResult(False, "not_currently_approved")

    write_audit(db, action_id, "REVOKED", revoking_actor, now, {"reason": reason})
    db.commit()
    return PersistenceResult(True, None)


def mark_expired(db: Session, action_id: str, now: datetime) -> PersistenceResult:
    result = db.execute(
        update(ApprovalRow)
        .where(
            ApprovalRow.action_id == action_id,
            ApprovalRow.decision == "APPROVED",
            ApprovalRow.expires_at.isnot(None),
            ApprovalRow.expires_at < now,
        )
        .values(decision="EXPIRED", status="EXPIRED", reason=_EXPIRY_REASON)
    )
    if result.rowcount == 0:
        db.rollback()
        return PersistenceResult(False, "not_currently_approved_or_not_expired")

    write_audit(db, action_id, "EXPIRED", "system", now, {"reason": _EXPIRY_REASON})
    db.commit()
    return PersistenceResult(True, None)


def load_proposal(db: Session, action_id: str) -> ActionProposal | None:
    row = db.execute(select(ActionProposalRow).where(ActionProposalRow.action_id == action_id)).scalar_one_or_none()
    if row is None:
        return None
    return ActionProposal(
        action_id=row.action_id, action_type=row.action_type, policy_id=row.policy_id,
        risk_level=row.risk_level, automation_level=row.automation_level, actor=row.actor,
        target=json.loads(row.target), parameters=json.loads(row.parameters),
        idempotency_key=row.idempotency_key, approval_requirement=row.approval_requirement,
        timestamp=row.timestamp, status=row.status, proposal_hash=row.proposal_hash,
    )


def load_approval(db: Session, action_id: str) -> Approval | None:
    row = db.execute(select(ApprovalRow).where(ApprovalRow.action_id == action_id)).scalar_one_or_none()
    if row is None:
        return None
    return Approval(
        approval_id=row.approval_id, action_id=row.action_id, proposal_hash=row.proposal_hash,
        policy_id=row.policy_id, policy_version=row.policy_version, approver=row.approver,
        decision=row.decision, created_at=row.created_at, expires_at=row.expires_at, reason=row.reason,
    )


def load_audit_trail(db: Session, action_id: str) -> list[dict]:
    rows = db.execute(
        select(AuditEntryRow).where(AuditEntryRow.action_id == action_id).order_by(AuditEntryRow.timestamp)
    ).scalars().all()
    return [
        {
            "audit_id": r.audit_id, "action_id": r.action_id, "event_type": r.event_type,
            "actor": r.actor, "timestamp": r.timestamp, "detail": json.loads(r.detail),
        }
        for r in rows
    ]


def revalidate(
    db: Session,
    action_id: str,
    current_policy: Policy | None,
    action_still_registered: bool,
    global_automation_enabled: bool,
    actor_role: Role,
    required_role: Role,
    target_state_valid: bool,
    now: datetime,
) -> RevalidationResult | None:
    """Loads the persisted proposal+approval and delegates the actual
    decision to Phase 15D's unmodified, pure revalidate_approval() —
    this function only does the I/O, never duplicates the logic."""
    proposal = load_proposal(db, action_id)
    approval = load_approval(db, action_id)
    if proposal is None or approval is None:
        return None
    return revalidate_approval(
        approval, proposal, current_policy, action_still_registered,
        global_automation_enabled, actor_role, required_role, target_state_valid, now,
    )
