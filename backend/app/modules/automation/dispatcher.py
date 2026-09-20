"""Phase 16D — deterministic maintenance-signal dispatcher.

Orchestration only. This module contains no business logic of its own —
every step below is a call to an existing, unmodified function from an
earlier phase:

  signal
    -> signal_generator.generate_notify_staff_proposal()   (Phase 16C)
    -> persistence.load_proposal() / load_policy()          (Phase 15E-1/15G)
    -> policy_engine.evaluate_policy()                      (Phase 15B)
    -> persistence.persist_policy_decision()                (Phase 15E-1)
    -> persistence.update_proposal_status()                 (Phase 15E-1)
    -> action_executor.execute_notify_staff()                (Phase 16A)

No new hashing, no new registry check, no new authorization rule, and no
new stale-state or RBAC mechanism is introduced here — this module only
sequences existing ones in the existing, required order.

Never a scheduler: nothing in this file loops, polls, sleeps, listens for
an event, or registers a background job. dispatch_maintenance_signal()
runs exactly once per call, synchronously, and returns. Automatic,
repeated invocation is explicitly out of scope for this phase (see the
module's own Phase 16D instruction) — a future phase may wire this
function to a scheduler, but this file does not do that.

Authorization boundary: this module never fabricates an APPROVED status
without a genuine, freshly-computed ALLOW decision from evaluate_policy,
and it never calls execute_notify_staff() as a shortcut around that
decision — execute_notify_staff() always re-evaluates policy itself,
fresh, immediately before claiming EXECUTING, exactly as it does for
every other caller. The preliminary evaluation performed here is a
genuine use of the Policy Engine (required to decide whether to even
attempt the APPROVED transition), not a bypass of it — see policy_engine
module docstring's "never trust stale state" discipline, applied twice
in the successful path (once here, once inside execute_notify_staff)
exactly as it already is for RELEASE_EXPIRED_HOLD callers that go through
approval revalidation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.automation.action_executor import ExecutionResult, execute_notify_staff
from app.modules.automation.persistence import (
    load_policy,
    load_proposal,
    persist_policy_decision,
    update_proposal_status,
)
from app.modules.automation.policy_engine import evaluate_policy
from app.modules.automation.schemas import Role
from app.modules.automation.signal_generator import generate_notify_staff_proposal
from app.modules.reports.schemas import AIStrategistSignal

_POLICY_ID = "notify_staff"


@dataclass(frozen=True)
class DispatchResult:
    proposed: bool
    proposal_reason: str
    action_id: str | None
    decision_outcome: str | None
    executed: bool
    execution_reason: str | None


def dispatch_maintenance_signal(
    db: Session,
    signal: AIStrategistSignal,
    actor: str,
    actor_role: Role,
    global_automation_enabled: bool,
    now: datetime,
) -> DispatchResult:
    """The complete, deterministic pipeline for one signal. Safe to call
    repeatedly, including concurrently, with the same signal: every
    duplicate is caught by generate_notify_staff_proposal's own reuse of
    persist_proposal's real, database-level idempotency_key
    UniqueConstraint (Phase 16C) — nothing here adds a second dedup
    mechanism on top of it."""

    gen_result = generate_notify_staff_proposal(db, signal, actor, now)
    if not gen_result.created:
        return DispatchResult(
            proposed=False, proposal_reason=gen_result.reason, action_id=None,
            decision_outcome=None, executed=False, execution_reason=None,
        )

    proposal = load_proposal(db, gen_result.action_id)
    policy = load_policy(db, _POLICY_ID)
    decision = evaluate_policy(proposal, policy, actor_role, {}, global_automation_enabled=global_automation_enabled)
    persist_policy_decision(db, decision)

    if decision.outcome == "DENY":
        return DispatchResult(
            proposed=True, proposal_reason=gen_result.reason, action_id=proposal.action_id,
            decision_outcome=decision.outcome, executed=False, execution_reason=decision.reason,
        )

    if decision.outcome == "REQUIRE_APPROVAL":
        # No human approval step exists in this dispatcher, and none is
        # fabricated here — a REQUIRE_APPROVAL decision leaves the
        # proposal exactly as PROPOSED, unresolved, for a human/future
        # approval-workflow phase to handle. NOTIFY_STAFF's real,
        # deployed policy has requires_approval=False, so this branch
        # does not occur for the current production policy; it exists
        # for correctness if a policy were ever reconfigured otherwise.
        return DispatchResult(
            proposed=True, proposal_reason=gen_result.reason, action_id=proposal.action_id,
            decision_outcome=decision.outcome, executed=False,
            execution_reason="requires_approval_not_available_via_dispatcher",
        )

    # ALLOW: transition to APPROVED using the existing, unmodified
    # status-update function, then hand off entirely to the existing
    # executor, which performs its own complete, independent
    # revalidation before ever claiming EXECUTING.
    approve_result = update_proposal_status(db, proposal.action_id, "APPROVED", actor, now)
    if not approve_result.success:
        return DispatchResult(
            proposed=True, proposal_reason=gen_result.reason, action_id=proposal.action_id,
            decision_outcome=decision.outcome, executed=False, execution_reason=approve_result.reason,
        )

    execution: ExecutionResult = execute_notify_staff(db, proposal.action_id, policy, global_automation_enabled, actor_role, now)
    return DispatchResult(
        proposed=True, proposal_reason=gen_result.reason, action_id=proposal.action_id,
        decision_outcome=decision.outcome, executed=execution.success, execution_reason=execution.reason,
    )
