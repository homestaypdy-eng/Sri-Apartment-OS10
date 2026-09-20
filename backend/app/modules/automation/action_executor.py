"""Phase 15E-2 — the first, and only, real autonomous action executor:
RELEASE_EXPIRED_HOLD.

This is the ONE place in the entire codebase where the automation system
is allowed to write to a real business table (`booking_holds`) — every
prior automation phase (15B/15C/15D/15E-1) was explicitly forbidden from
touching it. That authorization is scoped as narrowly as possible: this
module recognizes exactly one action_type, and every other candidate
action named in the Phase 15A taxonomy (PRICE_CHANGE, REFUND,
PAYMENT_CAPTURE, GST_ADJUSTMENT, BOOKING_CANCEL, SYNC_RATE,
SYNC_AVAILABILITY, UNIT_BLOCK) is explicitly rejected before anything
else happens.

Existing TTL rule reuse: `_release_expired_hold_row()` below applies the
exact same condition (`status='pending_payment' AND expires_at < now()`)
that public_booking/router.py already uses in two places — its
pre-creation bulk cleanup query ("P0-4: Expire ghost holds...") and its
lazy single-hold expiry check on the confirm endpoint ("TTL check —
lazy expiry"). No new TTL value, no new expiry formula, nothing
recomputed — the same columns (`status`, `expires_at`), the same
comparison, the same HOLD_TTL_MINUTES concept already baked into
`expires_at` at hold-creation time. There is no existing standalone,
importable function to call instead (the two existing call sites are
both inline in router.py, not factored out) — since "DO NOT modify
existing booking logic" is an explicit, repeated hard rule across every
phase of this whole automation effort, this module reuses the same
condition/columns via its own statement, scoped to one specific hold by
id, rather than refactoring public_booking/router.py to extract a
shared function. This tradeoff is deliberate and disclosed, not silent.

Never trust stale state: every one of Phase 15A's numbered safety checks
is re-verified fresh, immediately before the one atomic write — nothing
about the proposal, the policy, the registry, the kill switch, or the
booking hold itself is trusted from an earlier point in time.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.modules.automation.action_registry import get_registry_entry, validate_proposal_structure
from app.modules.automation.approval_workflow import revalidate_approval
from app.modules.automation.models import ActionProposalRow
from app.modules.automation.persistence import load_approval, load_proposal, write_audit
from app.modules.automation.policy_engine import evaluate_policy
from app.modules.automation.schemas import Policy, Role

_SUPPORTED_ACTION_TYPES = frozenset({"RELEASE_EXPIRED_HOLD"})

# Phase 16A: NOTIFY_STAFF is deliberately gated by its own separate
# constant, checked inside its own separate execute_notify_staff()
# function below — not folded into _SUPPORTED_ACTION_TYPES /
# execute_release_expired_hold(). RELEASE_EXPIRED_HOLD is live in
# production; refactoring it to share a code path with a brand-new,
# unactivated action type would risk regressing something that
# currently works, for no real benefit. The two functions duplicate
# the revalidation preamble shape on purpose — see module docstring.
_NOTIFY_STAFF_SUPPORTED_ACTION_TYPES = frozenset({"NOTIFY_STAFF"})

_MAX_ATTEMPTS = 2  # 1 initial + 1 retry, transient infrastructure failures only


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    status: str  # "SUCCEEDED" or "FAILED" — never anything else, matching the normal/failure paths defined for this phase
    reason: str
    detail: dict


def _looks_like_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _release_expired_hold_row(db: Session, hold_id: str, now: datetime) -> tuple[bool, str, dict]:
    """The one and only write this whole module is allowed to make to a
    business table. Reuses public_booking's own expiry condition — see
    module docstring. A pre-flight read classifies *why* a release can't
    happen (not found / already converted / already released / not yet
    expired) before the actual atomic conditional UPDATE runs; the
    UPDATE's own WHERE clause — not the earlier read — is what
    determines success, so a race between the two is caught safely
    (rowcount 0 = state changed, never assumed to have succeeded)."""
    from app.modules.public_booking.models import BookingHold  # only import site in this module

    hold_uuid = _looks_like_uuid(hold_id)
    if hold_uuid is None:
        return False, "invalid_target_id", {}

    hold = db.get(BookingHold, hold_uuid)
    if hold is None:
        return False, "hold_not_found", {}
    if hold.status == "converted":
        return False, "hold_already_converted", {"hold_status": hold.status}
    if hold.status in ("expired", "cancelled"):
        return False, "hold_already_released", {"hold_status": hold.status}
    if hold.status != "pending_payment":
        return False, "hold_unexpected_status", {"hold_status": hold.status}
    if now <= hold.expires_at:
        return False, "hold_not_yet_expired", {"expires_at": hold.expires_at.isoformat()}

    unit_id = str(hold.unit_id)
    result = db.execute(
        text(
            "UPDATE booking_holds SET status = 'expired', updated_at = :now "
            "WHERE id = :id AND status = 'pending_payment' AND expires_at < :now"
        ),
        {"id": hold_uuid, "now": now},
    )
    if result.rowcount != 1:
        return False, "hold_state_changed_concurrently", {}
    return True, "released", {"unit_id": unit_id}


def _release_expired_hold_row_with_retry(db: Session, hold_id: str, now: datetime) -> tuple[bool, str, dict]:
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return _release_expired_hold_row(db, hold_id, now)
        except OperationalError:
            db.rollback()
            if attempt == _MAX_ATTEMPTS:
                return False, "transient_database_error", {}
            continue
    return False, "transient_database_error", {}


def execute_release_expired_hold(
    db: Session,
    action_id: str,
    current_policy: Policy | None,
    global_automation_enabled: bool,
    actor_role: Role,
    now: datetime,
) -> ExecutionResult:
    """The single entry point for this executor. Every check below runs
    fresh against the database, right now — nothing is trusted from
    proposal time, approval time, or any earlier evaluation."""

    proposal = load_proposal(db, action_id)
    if proposal is None:
        return ExecutionResult(False, "FAILED", "proposal_not_found", {})

    # Executor-level capability boundary: this function implements
    # exactly one action_type, full stop, regardless of what the
    # registry might say about any other type.
    if proposal.action_type not in _SUPPORTED_ACTION_TYPES:
        return ExecutionResult(False, "FAILED", "unsupported_action_type", {})

    # Idempotent replay: a proposal that already succeeded is never
    # re-executed — the same idempotency_key can never produce a second
    # real release.
    if proposal.status == "SUCCEEDED":
        return ExecutionResult(True, "SUCCEEDED", "already_succeeded", {})

    entry = get_registry_entry(proposal.action_type)
    if entry is None or not entry.enabled:
        return ExecutionResult(False, "FAILED", "action_not_registered_or_disabled", {})

    structural = validate_proposal_structure(proposal)
    if not structural.valid:
        return ExecutionResult(False, "FAILED", structural.reason, {})

    if not global_automation_enabled:
        write_audit(db, action_id, "KILL_SWITCH_BLOCKED", "system", now, {"reason": "global_automation_disabled"})
        db.commit()
        return ExecutionResult(False, "FAILED", "global_automation_disabled", {})

    # Fresh policy re-evaluation (Phase 15B, unmodified) — the absence
    # of a human-approval requirement for this L2 action does NOT skip
    # this step. This also re-checks: policy exists, policy enabled,
    # action_type/policy_id/automation_level/risk_level consistency,
    # role permitted, and every declared condition.
    decision = evaluate_policy(proposal, current_policy, actor_role, {}, global_automation_enabled=global_automation_enabled)
    write_audit(db, action_id, "POLICY_EVALUATED", "system", now,
                {"outcome": decision.outcome, "reason": decision.reason, "context": "pre_execution_revalidation"})
    db.commit()

    if decision.outcome == "DENY":
        if decision.reason == "global_automation_disabled":
            write_audit(db, action_id, "KILL_SWITCH_BLOCKED", "system", now, {"reason": decision.reason})
            db.commit()
        return ExecutionResult(False, "FAILED", decision.reason, {})

    if decision.outcome == "REQUIRE_APPROVAL":
        approval = load_approval(db, action_id)
        if approval is None:
            return ExecutionResult(False, "FAILED", "approval_required_but_missing", {})
        revalidation = revalidate_approval(
            approval, proposal, current_policy, action_still_registered=True,
            global_automation_enabled=global_automation_enabled, actor_role=actor_role,
            required_role=current_policy.required_role, target_state_valid=True, now=now,
        )
        if not revalidation.eligible_for_execution:
            return ExecutionResult(False, "FAILED", revalidation.reason or "approval_not_eligible", {})

    # Claim exclusive right to execute: a conditional UPDATE, only from
    # APPROVED. Two concurrent callers for the same action_id can never
    # both win this — the loser's rowcount is 0 and it stops here,
    # before ever touching booking_holds.
    claim = db.execute(
        update(ActionProposalRow)
        .where(ActionProposalRow.action_id == action_id, ActionProposalRow.status == "APPROVED")
        .values(status="EXECUTING")
    )
    if claim.rowcount == 0:
        db.rollback()
        return ExecutionResult(False, "FAILED", "not_in_approved_state", {})

    write_audit(db, action_id, "EXECUTION_STARTED", "system", now,
                {"action_type": proposal.action_type, "target": proposal.target})
    db.commit()

    hold_id = proposal.target.get("entity_id")
    success, reason, detail = _release_expired_hold_row_with_retry(db, hold_id, now)

    if success:
        db.execute(update(ActionProposalRow).where(ActionProposalRow.action_id == action_id).values(status="SUCCEEDED"))
        write_audit(db, action_id, "EXECUTION_SUCCEEDED", "system", now, {"hold_id": hold_id, **detail})
        db.commit()
        return ExecutionResult(True, "SUCCEEDED", reason, detail)

    db.execute(update(ActionProposalRow).where(ActionProposalRow.action_id == action_id).values(status="FAILED"))
    write_audit(db, action_id, "EXECUTION_FAILED", "system", now, {"hold_id": hold_id, "reason": reason})
    db.commit()
    return ExecutionResult(False, "FAILED", reason, detail)


# --- Phase 16A: NOTIFY_STAFF -------------------------------------------------
#
# The second, and so far only other, real autonomous action executor. Its
# registry entry ships with enabled=False (see action_registry.py) and its
# policy is not activated in production this phase — see the module-level
# instruction this phase was built under. Nothing below writes to any
# booking/payment/pricing/GST/OTA/guest-record table; the one write this
# function is allowed to make is one new row added to notification_logs,
# an existing, pre-Phase-16A table (see notifications/models.py) reused
# rather than inventing a new one, per this phase's own instruction to
# check for and reuse an existing mechanism first.


def _create_staff_notification(
    db: Session, action_id: str, category: str, severity: str, message: str, source_id: str,
) -> tuple[bool, str, dict]:
    """The one and only write this function is allowed to make. Always
    a broadcast row (user_id=None) — see notifications/models.py's own
    comment on the resulting, disclosed in-app-inbox limitation."""
    from app.modules.notifications.models import NotificationLog  # only import site in this function

    row = NotificationLog(
        event_type="automation_alert",
        channel="in_app",
        recipient="staff",
        user_id=None,
        action_id=action_id,
        category=category,
        severity=severity,
        message=message,
        source_id=source_id,
        success=True,
    )
    db.add(row)
    db.flush()
    return True, "notified", {"notification_id": str(row.id)}


def _create_staff_notification_with_retry(
    db: Session, action_id: str, category: str, severity: str, message: str, source_id: str,
) -> tuple[bool, str, dict]:
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return _create_staff_notification(db, action_id, category, severity, message, source_id)
        except OperationalError:
            db.rollback()
            if attempt == _MAX_ATTEMPTS:
                return False, "transient_database_error", {}
            continue
    return False, "transient_database_error", {}


def execute_notify_staff(
    db: Session,
    action_id: str,
    current_policy: Policy | None,
    global_automation_enabled: bool,
    actor_role: Role,
    now: datetime,
) -> ExecutionResult:
    """Mirrors execute_release_expired_hold()'s revalidation preamble
    exactly, on purpose (see module docstring for why this is a separate
    function rather than a shared helper). Every check below runs fresh
    against the database, right now — nothing is trusted from proposal
    time, approval time, or any earlier evaluation."""

    proposal = load_proposal(db, action_id)
    if proposal is None:
        return ExecutionResult(False, "FAILED", "proposal_not_found", {})

    if proposal.action_type not in _NOTIFY_STAFF_SUPPORTED_ACTION_TYPES:
        return ExecutionResult(False, "FAILED", "unsupported_action_type", {})

    # Idempotent replay: a proposal that already succeeded is never
    # re-executed — the same idempotency_key can never produce a second
    # real notification.
    if proposal.status == "SUCCEEDED":
        return ExecutionResult(True, "SUCCEEDED", "already_succeeded", {})

    entry = get_registry_entry(proposal.action_type)
    if entry is None or not entry.enabled:
        return ExecutionResult(False, "FAILED", "action_not_registered_or_disabled", {})

    structural = validate_proposal_structure(proposal)
    if not structural.valid:
        return ExecutionResult(False, "FAILED", structural.reason, {})

    if not global_automation_enabled:
        write_audit(db, action_id, "KILL_SWITCH_BLOCKED", "system", now, {"reason": "global_automation_disabled"})
        db.commit()
        return ExecutionResult(False, "FAILED", "global_automation_disabled", {})

    # Fresh policy re-evaluation (Phase 15B, unmodified) — same as
    # execute_release_expired_hold(): re-checks policy existence,
    # enabled-ness, action_type/policy_id/automation_level/risk_level
    # consistency, role permitted, and every declared condition.
    decision = evaluate_policy(proposal, current_policy, actor_role, {}, global_automation_enabled=global_automation_enabled)
    write_audit(db, action_id, "POLICY_EVALUATED", "system", now,
                {"outcome": decision.outcome, "reason": decision.reason, "context": "pre_execution_revalidation"})
    db.commit()

    if decision.outcome == "DENY":
        if decision.reason == "global_automation_disabled":
            write_audit(db, action_id, "KILL_SWITCH_BLOCKED", "system", now, {"reason": decision.reason})
            db.commit()
        return ExecutionResult(False, "FAILED", decision.reason, {})

    if decision.outcome == "REQUIRE_APPROVAL":
        approval = load_approval(db, action_id)
        if approval is None:
            return ExecutionResult(False, "FAILED", "approval_required_but_missing", {})
        revalidation = revalidate_approval(
            approval, proposal, current_policy, action_still_registered=True,
            global_automation_enabled=global_automation_enabled, actor_role=actor_role,
            required_role=current_policy.required_role, target_state_valid=True, now=now,
        )
        if not revalidation.eligible_for_execution:
            return ExecutionResult(False, "FAILED", revalidation.reason or "approval_not_eligible", {})

    # Claim exclusive right to execute: a conditional UPDATE, only from
    # APPROVED. Two concurrent callers for the same action_id can never
    # both win this — the loser's rowcount is 0 and it stops here,
    # before ever touching notification_logs.
    claim = db.execute(
        update(ActionProposalRow)
        .where(ActionProposalRow.action_id == action_id, ActionProposalRow.status == "APPROVED")
        .values(status="EXECUTING")
    )
    if claim.rowcount == 0:
        db.rollback()
        return ExecutionResult(False, "FAILED", "not_in_approved_state", {})

    write_audit(db, action_id, "EXECUTION_STARTED", "system", now,
                {"action_type": proposal.action_type, "target": proposal.target})
    db.commit()

    category = proposal.parameters.get("category")
    severity = proposal.parameters.get("severity")
    message = proposal.parameters.get("message")
    source_id = proposal.parameters.get("source_id")
    success, reason, detail = _create_staff_notification_with_retry(db, action_id, category, severity, message, source_id)

    if success:
        db.execute(update(ActionProposalRow).where(ActionProposalRow.action_id == action_id).values(status="SUCCEEDED"))
        # Deliberately excludes the raw message text from the audit
        # detail — the notification_id is enough to look the full row
        # up in notification_logs if ever needed; the audit trail is
        # not a second place for the message content to live.
        write_audit(db, action_id, "EXECUTION_SUCCEEDED", "system", now, {**detail})
        db.commit()
        return ExecutionResult(True, "SUCCEEDED", reason, detail)

    db.execute(update(ActionProposalRow).where(ActionProposalRow.action_id == action_id).values(status="FAILED"))
    write_audit(db, action_id, "EXECUTION_FAILED", "system", now, {"reason": reason})
    db.commit()
    return ExecutionResult(False, "FAILED", reason, detail)
