"""Phase 15B — deterministic Policy Engine.

Answers exactly one question: "is this proposed action permitted under
the supplied policy?" It never executes anything — there is no
ActionExecutor call anywhere in this module, no database session, no
HTTP client, no LLM call. Every function here is a pure transform over
its arguments: same ActionProposal + Policy + evaluation_data + kill-
switch state -> same PolicyDecision, always.

Threshold discipline (Phase 15A): this module hard-codes zero business
numbers (no price, no discount %, no refund limit, no approval TTL). A
Policy's `conditions` carry whatever thresholds a future business owner
approves; this engine only ever evaluates them, never invents or
substitutes one. If a policy is missing data it needs, the result is
DENY, never a guessed value.

GLOBAL_AUTOMATION_ENABLED defaults to False and there is no code path in
this module (or anywhere else in the current codebase) that flips it —
Phase 15B does not implement a settings UI or an endpoint for it, exactly
as instructed. Changing it means changing this source file.

Phase 15G: set to True following an explicit, documented business
decision ("Option B") to activate RELEASE_EXPIRED_HOLD only, after the
full pre-activation safety checklist passed against production. This is
still not an env var or settings field — reverting it means changing
this line back to False and redeploying, exactly as designed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.modules.automation.schemas import (
    ActionProposal,
    Condition,
    EvaluatedCondition,
    Policy,
    PolicyDecision,
)

GLOBAL_AUTOMATION_ENABLED = True

# Mirrors the existing app-wide RBAC convention observed throughout
# reports/router.py (Phase 13B-13I): an "admin+staff" requirement accepts
# either role, an "admin"-only requirement accepts only admin. No new
# role, no hierarchy beyond what the rest of the application already
# uses.
_ROLE_SATISFIES: dict[str, set[str]] = {
    "admin": {"admin"},
    "staff": {"admin", "staff"},
}


def compute_proposal_hash(action_type: str, target: dict[str, Any], parameters: dict[str, Any]) -> str:
    """Deterministic hash of the parts of a proposal that define WHAT it
    does — never the actor, timestamp, or id, so two proposals for the
    identical action are recognizably identical regardless of when or by
    whom they were raised. Dict key order never affects the result
    (json.dumps sort_keys=True)."""
    canonical = json.dumps(
        {"action_type": action_type, "target": target, "parameters": parameters},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_idempotency_key(action_type: str, target: dict[str, Any], proposal_hash: str) -> str:
    canonical = json.dumps(
        {"action_type": action_type, "target": target, "proposal_hash": proposal_hash},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_field(field: str, data: dict[str, Any]) -> tuple[bool, Any]:
    """Dotted-path lookup (e.g. "hold.status") into evaluation_data.
    Returns (present, value)."""
    current: Any = data
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _evaluate_condition(condition: Condition, evaluation_data: dict[str, Any]) -> EvaluatedCondition:
    present, actual = _resolve_field(condition.field, evaluation_data)
    if not present:
        return EvaluatedCondition(
            field=condition.field, operator=condition.operator, value=condition.value,
            actual=None, data_present=False, passed=False,
        )
    try:
        if condition.operator == "eq":
            passed = actual == condition.value
        elif condition.operator == "ne":
            passed = actual != condition.value
        elif condition.operator == "gt":
            passed = actual > condition.value
        elif condition.operator == "gte":
            passed = actual >= condition.value
        elif condition.operator == "lt":
            passed = actual < condition.value
        elif condition.operator == "lte":
            passed = actual <= condition.value
        elif condition.operator == "in":
            passed = actual in condition.value
        else:
            passed = False
    except TypeError:
        # Incomparable types (e.g. comparing a string to a number) is
        # always a failed condition, never an exception escaping the
        # engine and never a guessed pass.
        passed = False
    return EvaluatedCondition(
        field=condition.field, operator=condition.operator, value=condition.value,
        actual=actual, data_present=True, passed=passed,
    )


def _deny(action_id: str, policy_id: str, policy_version: int | None, reason: str,
          evaluated_conditions: list[EvaluatedCondition] | None = None) -> PolicyDecision:
    return PolicyDecision(
        decision_id=f"decision:{action_id}:{policy_id}:{policy_version if policy_version is not None else 'none'}",
        action_id=action_id,
        policy_id=policy_id,
        policy_version=policy_version,
        outcome="DENY",
        reason=reason,
        evaluated_conditions=evaluated_conditions or [],
        evaluated_at=datetime.now(timezone.utc),
    )


def evaluate_policy(
    proposal: ActionProposal,
    policy: Policy | None,
    actor_role: str,
    evaluation_data: dict[str, Any] | None = None,
    global_automation_enabled: bool = GLOBAL_AUTOMATION_ENABLED,
) -> PolicyDecision:
    """Pure function: (proposal, policy, actor_role, evaluation_data,
    kill-switch) -> PolicyDecision. Never mutates anything, never calls
    out to a database/HTTP/LLM. Decision rules are checked in a fixed
    order and short-circuit on the first failure, exactly mirroring
    Phase 15A's numbered DECISION RULES list."""
    evaluation_data = evaluation_data or {}

    # Rule: global kill switch overrides everything, including an
    # individually-enabled policy — checked first and unconditionally.
    if not global_automation_enabled:
        return _deny(proposal.action_id, proposal.policy_id, policy.version if policy else None,
                     "global_automation_disabled")

    # Rule 1: policy does not exist.
    if policy is None:
        return _deny(proposal.action_id, proposal.policy_id, None, "policy_not_found")

    # Rule 2: policy is disabled.
    if not policy.enabled:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "policy_disabled")

    # Rule 11: policy version is invalid.
    if policy.version < 1:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "invalid_policy_version")

    # Rule 4 (policy_id) / Rule 3 (action_type) / Rule 5 (automation
    # level) / Rule 6 (risk level): the proposal must be internally
    # consistent with the policy it claims to be evaluated under —
    # Pydantic's Literal typing already guarantees each field is one of
    # the supported values individually, so "invalid" here means
    # "doesn't match what this policy actually declares."
    if proposal.policy_id != policy.policy_id:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "policy_id_mismatch")
    if proposal.action_type != policy.action_type:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "action_type_mismatch")
    if proposal.automation_level != policy.automation_level:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "automation_level_mismatch")
    if proposal.risk_level != policy.risk_level:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "risk_level_mismatch")

    # Rule 10: proposal is structurally invalid — the strongest concrete
    # check available is that its own proposal_hash actually matches
    # what compute_proposal_hash() would derive from its own
    # action_type/target/parameters. A mismatch means the proposal was
    # tampered with, corrupted, or built incorrectly by the caller.
    if not proposal.action_id or not proposal.action_type:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "proposal_invalid")
    expected_hash = compute_proposal_hash(proposal.action_type, proposal.target, proposal.parameters)
    if proposal.proposal_hash != expected_hash:
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "proposal_hash_mismatch")

    # Rule 7: required role is not permitted.
    if actor_role not in _ROLE_SATISFIES.get(policy.required_role, set()):
        return _deny(proposal.action_id, proposal.policy_id, policy.version, "role_not_permitted")

    # Rules 8/9: every condition is evaluated (not short-circuited) so
    # evaluated_conditions is a complete audit trail even when only one
    # of several conditions fails.
    evaluated = [_evaluate_condition(c, evaluation_data) for c in policy.conditions]
    missing = next((e for e in evaluated if not e.data_present), None)
    if missing is not None:
        return _deny(proposal.action_id, proposal.policy_id, policy.version,
                     f"condition_missing_data:{missing.field}", evaluated)
    failed = next((e for e in evaluated if not e.passed), None)
    if failed is not None:
        return _deny(proposal.action_id, proposal.policy_id, policy.version,
                     f"condition_failed:{failed.field}", evaluated)

    # Every gate passed. L4 is a hard safety net: it may never resolve
    # to ALLOW, even if a policy were misconfigured with
    # requires_approval=False — Claude has zero authority here and
    # neither does a mis-set flag.
    decision_id = f"decision:{proposal.action_id}:{policy.policy_id}:{policy.version}"
    if policy.automation_level == "L4":
        return PolicyDecision(
            decision_id=decision_id, action_id=proposal.action_id, policy_id=policy.policy_id,
            policy_version=policy.version, outcome="REQUIRE_APPROVAL",
            reason="l4_always_requires_approval", evaluated_conditions=evaluated,
            evaluated_at=datetime.now(timezone.utc),
        )
    if policy.requires_approval:
        return PolicyDecision(
            decision_id=decision_id, action_id=proposal.action_id, policy_id=policy.policy_id,
            policy_version=policy.version, outcome="REQUIRE_APPROVAL",
            reason="policy_requires_approval", evaluated_conditions=evaluated,
            evaluated_at=datetime.now(timezone.utc),
        )
    return PolicyDecision(
        decision_id=decision_id, action_id=proposal.action_id, policy_id=policy.policy_id,
        policy_version=policy.version, outcome="ALLOW",
        reason="all_conditions_satisfied", evaluated_conditions=evaluated,
        evaluated_at=datetime.now(timezone.utc),
    )
