"""Phase 15D — deterministic human approval / override workflow.

Implements the approval half of Phase 15A's lifecycle:

    PROPOSED -> APPROVAL_REQUIRED -> APPROVED/REJECTED/EXPIRED
             -> Policy Revalidation -> STOP (no executor exists yet)

Nothing in this module executes an action. There is no ActionExecutor
import, no database session, no HTTP client, no LLM call — every public
function here is a pure transform over its arguments (plus one small,
explicitly in-memory, non-persistent ApprovalLedger used only to
demonstrate genuine compare-and-swap concurrency protection without
introducing a database table).

Threshold discipline (Phase 15A/15D): this module never computes an
approval expiry duration. `expires_at` is always supplied by the caller
(or omitted, meaning "no expiry enforced"); nothing here defaults it to
30 minutes, 1 hour, 24 hours, or any other production value — Phase 15A
explicitly classified that duration as a business decision still
pending approval.

Why an in-memory ledger and not a database table: Phase 15D's own
instructions prefer no persistence unless unavoidable, and nothing here
needs to survive a process restart yet — there is no API endpoint, no
scheduler, and no executor consuming these decisions across requests in
this phase. `ApprovalLedger` exists purely so this module's compare-
and-swap guarantee (§ concurrent approval protection) can be
demonstrated with real concurrent threads in tests, not simulated with
sequential calls. A future phase's persistence layer would replace its
internals; the function contracts below would not need to change.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime

from app.modules.automation.schemas import (
    ActionProposal,
    Approval,
    ApprovalDecision,
    Policy,
    Role,
)

# Duplicated deliberately from policy_engine.py rather than imported —
# a 2-line mapping, small enough that duplication risk is negligible,
# and it keeps this phase's diff scoped to new files only, touching
# nothing from the already-shipped Phase 15B/15C modules. Mirrors the
# same "admin also satisfies a staff-level requirement" convention used
# everywhere else in this codebase (13B-13I's require_role, 15B/15C).
_ROLE_SATISFIES: dict[str, set[str]] = {
    "admin": {"admin"},
    "staff": {"admin", "staff"},
}


def compute_approval_id(action_id: str, proposal_hash: str, decision: str, approver: str) -> str:
    """Deterministic, never random — same inputs always produce the same
    id, so recording is reproducible and auditable, not a fresh
    coin-flip each call."""
    canonical = json.dumps(
        {"action_id": action_id, "proposal_hash": proposal_hash, "decision": decision, "approver": approver},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalResult:
    success: bool
    approval: Approval | None
    reason: str | None


@dataclass(frozen=True)
class RevalidationResult:
    eligible_for_execution: bool
    reason: str | None
    checks: dict[str, bool]


class ApprovalLedger:
    """In-memory only. Not a database, not persisted across process
    restarts, not shared across processes. See module docstring for why
    this is intentional for Phase 15D."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, Approval] = {}

    def record_decision(self, action_id: str, approval: Approval) -> bool:
        """First-ever resolution for this action_id. Returns True if
        this call created it, False if one already exists (duplicate or
        the losing side of a concurrent race) — a real compare-and-swap,
        not a read-then-write: the check and the write happen inside the
        same lock acquisition, so two threads can never both observe
        "not present" and both write."""
        with self._lock:
            if action_id in self._entries:
                return False
            self._entries[action_id] = approval
            return True

    def revoke(self, action_id: str, revocation: Approval) -> bool:
        """Transition an existing APPROVED entry to a terminal state
        (REVOKED or, via mark_expired, EXPIRED). Returns False if there
        is nothing to transition or it isn't currently APPROVED."""
        with self._lock:
            current = self._entries.get(action_id)
            if current is None or current.decision != "APPROVED":
                return False
            self._entries[action_id] = revocation
            return True

    def get(self, action_id: str) -> Approval | None:
        with self._lock:
            return self._entries.get(action_id)


def record_approval(
    ledger: ApprovalLedger,
    proposal: ActionProposal,
    policy: Policy,
    approver_actor: str,
    approver_role: str,
    decision: ApprovalDecision,
    reason: str | None,
    now: datetime,
    expires_at: datetime | None = None,
) -> ApprovalResult:
    """Records an admin's APPROVED/REJECTED decision. Only ever admin —
    staff (or any other role string) is rejected outright; approving an
    autonomous action is never a staff capability in this design."""
    if decision not in ("APPROVED", "REJECTED"):
        return ApprovalResult(False, None, "invalid_decision_for_record_approval")
    if approver_role != "admin":
        return ApprovalResult(False, None, "unauthorized_approver")
    if proposal.policy_id != policy.policy_id:
        return ApprovalResult(False, None, "policy_id_mismatch")
    # Self-approval separation: only meaningful for dual_admin policies
    # (Phase 15A's four-eyes note was scoped to L4/dual-admin). No
    # currently-registered action sets approval_requirement="dual_admin"
    # (RELEASE_EXPIRED_HOLD is "none"), so this branch exists as a
    # supported future capability and is not reachable today — exactly
    # as instructed, implemented but not activated.
    if proposal.approval_requirement == "dual_admin" and approver_actor == proposal.actor:
        return ApprovalResult(False, None, "self_approval_not_permitted")

    approval = Approval(
        approval_id=compute_approval_id(proposal.action_id, proposal.proposal_hash, decision, approver_actor),
        action_id=proposal.action_id,
        proposal_hash=proposal.proposal_hash,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        approver=approver_actor,
        decision=decision,
        created_at=now,
        expires_at=expires_at,
        reason=reason,
    )
    won = ledger.record_decision(proposal.action_id, approval)
    if not won:
        return ApprovalResult(False, None, "already_resolved")
    return ApprovalResult(True, approval, None)


def revoke_approval(
    ledger: ApprovalLedger,
    action_id: str,
    revoking_actor: str,
    revoking_role: str,
    reason: str | None,
    now: datetime,
) -> ApprovalResult:
    if revoking_role != "admin":
        return ApprovalResult(False, None, "unauthorized_revoker")
    current = ledger.get(action_id)
    if current is None:
        return ApprovalResult(False, None, "no_approval_to_revoke")
    revocation = Approval(
        approval_id=compute_approval_id(action_id, current.proposal_hash, "REVOKED", revoking_actor),
        action_id=action_id,
        proposal_hash=current.proposal_hash,
        policy_id=current.policy_id,
        policy_version=current.policy_version,
        approver=revoking_actor,
        decision="REVOKED",
        created_at=now,
        expires_at=current.expires_at,
        reason=reason,
    )
    won = ledger.revoke(action_id, revocation)
    if not won:
        return ApprovalResult(False, None, "not_currently_approved")
    return ApprovalResult(True, revocation, None)


_EXPIRY_REASON = "approval_window_elapsed"


def mark_expired(ledger: ApprovalLedger, action_id: str, now: datetime) -> ApprovalResult:
    """Transitions an APPROVED entry whose caller-supplied expires_at
    has passed into an explicit EXPIRED record. Never invents the
    expiry duration itself — only ever compares against whatever
    expires_at the original approval was given."""
    current = ledger.get(action_id)
    if current is None or current.decision != "APPROVED":
        return ApprovalResult(False, None, "not_currently_approved")
    if current.expires_at is None or now <= current.expires_at:
        return ApprovalResult(False, None, "not_yet_expired")
    expiry = Approval(
        approval_id=compute_approval_id(action_id, current.proposal_hash, "EXPIRED", "system"),
        action_id=action_id,
        proposal_hash=current.proposal_hash,
        policy_id=current.policy_id,
        policy_version=current.policy_version,
        approver="system",
        decision="EXPIRED",
        created_at=now,
        expires_at=current.expires_at,
        reason=_EXPIRY_REASON,
    )
    won = ledger.revoke(action_id, expiry)
    if not won:
        return ApprovalResult(False, None, "not_currently_approved")
    return ApprovalResult(True, expiry, None)


def revalidate_approval(
    approval: Approval,
    current_proposal: ActionProposal,
    current_policy: Policy | None,
    action_still_registered: bool,
    global_automation_enabled: bool,
    actor_role: Role,
    required_role: Role,
    target_state_valid: bool,
    now: datetime,
) -> RevalidationResult:
    """Phase 15A's 8-point revalidation, checked in full (not short-
    circuited) so `checks` is always a complete audit trail. This never
    calls an executor — it only ever answers "would this approval still
    be usable right now," for a future phase to consume."""
    checks: dict[str, bool] = {}
    checks["approval_is_approved"] = approval.decision == "APPROVED"
    checks["not_expired"] = approval.expires_at is None or now <= approval.expires_at
    checks["proposal_hash_unchanged"] = approval.proposal_hash == current_proposal.proposal_hash
    checks["policy_id_unchanged"] = current_policy is not None and approval.policy_id == current_policy.policy_id
    checks["policy_version_unchanged"] = (
        current_policy is not None and approval.policy_version == current_policy.version
    )
    checks["action_still_registered"] = action_still_registered
    checks["policy_still_enabled"] = current_policy is not None and current_policy.enabled
    checks["global_automation_enabled"] = global_automation_enabled
    checks["actor_permissions_valid"] = actor_role in _ROLE_SATISFIES.get(required_role, set())
    checks["target_state_valid"] = target_state_valid

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return RevalidationResult(False, f"revalidation_failed:{failed[0]}", checks)
    return RevalidationResult(True, None, checks)
