"""
Phase 15D — human approval / override workflow tests.

No database, no HTTP, no LLM, no execution anywhere — every test builds
Policy/ActionProposal/Approval objects directly in memory. Concurrent-
approval protection is tested with real threads against a shared
ApprovalLedger, not simulated with sequential calls.

Run with: pytest tests/test_automation_approval_workflow.py -v
"""
import ast
import inspect
import threading
from datetime import datetime, timedelta, timezone

from app.modules.automation import approval_workflow
from app.modules.automation.approval_workflow import (
    ApprovalLedger,
    compute_approval_id,
    mark_expired,
    record_approval,
    revalidate_approval,
    revoke_approval,
)
from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash
from app.modules.automation.schemas import ActionProposal, Approval
from app.modules.automation.schemas import Policy as AutomationPolicy

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_policy(**overrides):
    defaults = dict(
        policy_id="pol:release-expired-hold", action_type="RELEASE_EXPIRED_HOLD",
        automation_level="L2", risk_level="LOW", conditions=[], required_role="staff",
        requires_approval=True, enabled=True, version=1,
    )
    defaults.update(overrides)
    return AutomationPolicy(**defaults)


def _make_proposal(policy, target=None, parameters=None, **overrides):
    target = target if target is not None else {"entity_type": "booking_hold", "entity_id": "hold-1"}
    parameters = parameters if parameters is not None else {}
    action_type = overrides.get("action_type", policy.action_type)
    proposal_hash = overrides.get("proposal_hash")
    if proposal_hash is None:
        proposal_hash = compute_proposal_hash(action_type, target, parameters)
    idem_key = compute_idempotency_key(action_type, target, proposal_hash)
    defaults = dict(
        action_id="action:1", action_type=policy.action_type, policy_id=policy.policy_id,
        risk_level=policy.risk_level, automation_level=policy.automation_level,
        actor="staff-1", target=target, parameters=parameters,
        idempotency_key=idem_key, approval_requirement="single_admin",
        timestamp=NOW, status="APPROVAL_REQUIRED", proposal_hash=proposal_hash,
    )
    defaults.update(overrides)
    return ActionProposal(**defaults)


# --- 1. Valid admin approval ------------------------------------------------


def test_valid_admin_approval_succeeds():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", "looks fine", NOW)
    assert result.success is True
    assert result.approval.decision == "APPROVED"


# --- 2. Staff approval rejected -----------------------------------------------


def test_staff_approval_rejected():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "staff-2", "staff", "APPROVED", None, NOW)
    assert result.success is False
    assert result.reason == "unauthorized_approver"


# --- 3. Unauthenticated / non-role actor rejected -----------------------------


def test_non_role_actor_rejected():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "unknown", "", "APPROVED", None, NOW)
    assert result.success is False
    assert result.reason == "unauthorized_approver"


# --- 4/5. Approval binds to action_id and proposal_hash -----------------------


def test_approval_binds_to_action_id_and_proposal_hash():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    assert result.approval.action_id == proposal.action_id
    assert result.approval.proposal_hash == proposal.proposal_hash
    assert result.approval.policy_id == policy.policy_id
    assert result.approval.policy_version == policy.version


# --- 6/26. Parameter drift invalidates approval --------------------------------


def test_parameter_drift_invalidates_approval():
    policy = _make_policy()
    original_proposal = _make_proposal(policy, parameters={})
    ledger = ApprovalLedger()
    result = record_approval(ledger, original_proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    assert result.success is True

    # Same action_id, but a caller tries to reuse the approval against a
    # proposal whose parameters (and therefore proposal_hash) changed
    # after approval — this must never be silently accepted.
    drifted_proposal = _make_proposal(policy, parameters={"extra": "value"}, action_id=original_proposal.action_id)
    assert drifted_proposal.proposal_hash != original_proposal.proposal_hash

    revalidation = revalidate_approval(
        result.approval, drifted_proposal, policy, action_still_registered=True,
        global_automation_enabled=True, actor_role="staff", required_role=policy.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:proposal_hash_unchanged"


# --- 7. Policy version drift invalidates approval -------------------------------


def test_policy_version_drift_invalidates_approval():
    policy_v1 = _make_policy(version=1)
    proposal = _make_proposal(policy_v1)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy_v1, "admin-1", "admin", "APPROVED", None, NOW)
    assert result.success is True

    policy_v2 = _make_policy(version=2)
    revalidation = revalidate_approval(
        result.approval, proposal, policy_v2, action_still_registered=True,
        global_automation_enabled=True, actor_role="staff", required_role=policy_v2.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:policy_version_unchanged"


# --- 8. policy_id drift invalidates approval -------------------------------------


def test_policy_id_drift_invalidates_approval():
    policy = _make_policy(policy_id="pol:release-expired-hold")
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    assert result.success is True

    different_policy = _make_policy(policy_id="pol:different")
    revalidation = revalidate_approval(
        result.approval, proposal, different_policy, action_still_registered=True,
        global_automation_enabled=True, actor_role="staff", required_role=different_policy.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:policy_id_unchanged"


# --- 9. Rejected proposal cannot execute -----------------------------------------


def test_rejected_approval_not_eligible_for_execution():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "REJECTED", "not needed", NOW)
    assert result.success is True

    revalidation = revalidate_approval(
        result.approval, proposal, policy, action_still_registered=True,
        global_automation_enabled=True, actor_role="staff", required_role=policy.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:approval_is_approved"


# --- 10. Expired approval cannot execute -----------------------------------------


def test_expired_approval_not_eligible_for_execution():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    expires_at = NOW + timedelta(minutes=5)  # explicit test-only value, not a production default
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW, expires_at=expires_at)
    assert result.success is True

    later = expires_at + timedelta(seconds=1)
    revalidation = revalidate_approval(
        result.approval, proposal, policy, action_still_registered=True,
        global_automation_enabled=True, actor_role="staff", required_role=policy.required_role,
        target_state_valid=True, now=later,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:not_expired"


def test_mark_expired_transitions_ledger_entry():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    expires_at = NOW + timedelta(minutes=5)
    record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW, expires_at=expires_at)

    later = expires_at + timedelta(seconds=1)
    result = mark_expired(ledger, proposal.action_id, later)
    assert result.success is True
    assert result.approval.decision == "EXPIRED"
    assert ledger.get(proposal.action_id).decision == "EXPIRED"


# --- 11. Revoked approval cannot execute -----------------------------------------


def test_revoked_approval_not_eligible_for_execution():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    approve_result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    revoke_result = revoke_approval(ledger, proposal.action_id, "admin-2", "admin", "changed my mind", NOW)
    assert revoke_result.success is True
    assert revoke_result.approval.decision == "REVOKED"

    revalidation = revalidate_approval(
        revoke_result.approval, proposal, policy, action_still_registered=True,
        global_automation_enabled=True, actor_role="staff", required_role=policy.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:approval_is_approved"


def test_revoke_by_staff_rejected():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    result = revoke_approval(ledger, proposal.action_id, "staff-1", "staff", "trying to revoke", NOW)
    assert result.success is False
    assert result.reason == "unauthorized_revoker"


# --- 12. Concurrent approvals: exactly one succeeds -------------------------------


def test_concurrent_approvals_exactly_one_succeeds():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    results: list = []
    results_lock = threading.Lock()

    def _attempt(admin_id: str):
        r = record_approval(ledger, proposal, policy, admin_id, "admin", "APPROVED", None, NOW)
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_attempt, args=(f"admin-{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]
    assert len(successes) == 1
    assert len(failures) == 9
    assert all(f.reason == "already_resolved" for f in failures)


# --- 13. Duplicate approval rejected (sequential) ---------------------------------


def test_duplicate_approval_rejected_sequential():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    first = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    second = record_approval(ledger, proposal, policy, "admin-2", "admin", "APPROVED", None, NOW)
    assert first.success is True
    assert second.success is False
    assert second.reason == "already_resolved"


# --- 14. Self-approval protection (dual_admin, dormant today) ---------------------


def test_self_approval_protection_for_dual_admin_policy():
    policy = _make_policy(requires_approval=True)
    proposal = _make_proposal(policy, approval_requirement="dual_admin", actor="admin-1")
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    assert result.success is False
    assert result.reason == "self_approval_not_permitted"


def test_different_admin_can_approve_dual_admin_proposal():
    policy = _make_policy(requires_approval=True)
    proposal = _make_proposal(policy, approval_requirement="dual_admin", actor="admin-1")
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-2", "admin", "APPROVED", None, NOW)
    assert result.success is True


def test_self_approval_protection_not_activated_for_release_expired_hold():
    """RELEASE_EXPIRED_HOLD's registry entry sets approval_requirement
    'none' (Phase 15C) — this proves the dual_admin check simply never
    triggers for it, matching the "implemented but not activated"
    instruction."""
    policy = _make_policy()
    proposal = _make_proposal(policy, approval_requirement="single_admin", actor="admin-1")
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
    assert result.success is True  # same actor, but single_admin never checks self-approval


# --- 15/16. Deterministic decision, no random authorization -----------------------


def test_approval_id_deterministic():
    id1 = compute_approval_id("action:1", "hash-abc", "APPROVED", "admin-1")
    id2 = compute_approval_id("action:1", "hash-abc", "APPROVED", "admin-1")
    assert id1 == id2


def test_no_randomness_used_for_approval_id():
    imported_names = set()
    tree = ast.parse(inspect.getsource(approval_workflow))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
    assert "random" not in imported_names
    assert "uuid" not in imported_names


# --- 17/19/20/21/22. Static guardrails --------------------------------------------


def _imported_module_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module)
    return names


def test_no_llm_invocation():
    imported = _imported_module_names(approval_workflow)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in imported
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider",
        "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation",
    ):
        assert forbidden_submodule not in imported


def test_no_http_calls():
    imported = _imported_module_names(approval_workflow)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
        assert forbidden not in imported


def test_no_action_executor_import():
    imported = _imported_module_names(approval_workflow)
    assert not any("action_executor" in name for name in imported)
    module_attrs = {name.lower() for name in dir(approval_workflow)}
    assert {"execute", "run_action", "perform_action"}.isdisjoint(module_attrs)


def test_no_database_mutation_unless_justified():
    imported = _imported_module_names(approval_workflow)
    assert "sqlalchemy" not in imported
    source = inspect.getsource(approval_workflow)
    assert "app.db" not in source
    assert "Session" not in source
    # The only stateful object this module defines is ApprovalLedger,
    # which is explicitly in-memory (see its own docstring) — not a
    # database, no persistence across process restarts.


def test_no_booking_hold_model_touched():
    """RELEASE_EXPIRED_HOLD remains unexecuted: this module never even
    imports the BookingHold model, let alone reads or writes one."""
    imported = _imported_module_names(approval_workflow)
    assert not any("public_booking" in name for name in imported)


# --- 23/24. Global switch / disabled policy invalidate eligibility ----------------


def test_global_automation_off_invalidates_eligibility():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)

    revalidation = revalidate_approval(
        result.approval, proposal, policy, action_still_registered=True,
        global_automation_enabled=False, actor_role="staff", required_role=policy.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:global_automation_enabled"


def test_disabled_policy_invalidates_eligibility():
    policy = _make_policy(enabled=True)
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)

    disabled_policy = _make_policy(enabled=False)
    revalidation = revalidate_approval(
        result.approval, proposal, disabled_policy, action_still_registered=True,
        global_automation_enabled=True, actor_role="staff", required_role=disabled_policy.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:policy_still_enabled"


def test_unregistered_action_invalidates_eligibility():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)

    revalidation = revalidate_approval(
        result.approval, proposal, policy, action_still_registered=False,
        global_automation_enabled=True, actor_role="staff", required_role=policy.required_role,
        target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:action_still_registered"


# --- 25. Registry metadata cannot be overridden by approval -----------------------


def test_approval_schema_has_no_registry_override_fields():
    forbidden = {"risk_level", "automation_level", "required_role", "enabled", "action_type"}
    assert forbidden.isdisjoint(set(Approval.model_fields.keys()))


# --- 27. Policy version captured exactly -------------------------------------------


def test_policy_version_captured_exactly():
    for version in (1, 2, 42):
        policy = _make_policy(version=version)
        proposal = _make_proposal(policy)
        ledger = ApprovalLedger()
        result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW)
        assert result.approval.policy_version == version


# --- 28. Approval reason deterministic / human-entered only -----------------------


def test_reason_is_passed_through_verbatim_not_generated():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    result = record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", "manual review complete", NOW)
    assert result.approval.reason == "manual review complete"


def test_expiry_reason_is_a_fixed_constant_not_computed():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    ledger = ApprovalLedger()
    expires_at = NOW + timedelta(minutes=1)
    record_approval(ledger, proposal, policy, "admin-1", "admin", "APPROVED", None, NOW, expires_at=expires_at)
    result = mark_expired(ledger, proposal.action_id, expires_at + timedelta(seconds=1))
    assert result.approval.reason == "approval_window_elapsed"


# --- 29. Sensitive fields cannot enter approval metadata ---------------------------


def test_approval_schema_carries_no_pii_fields():
    forbidden = {"phone", "email", "guest_name", "guest_phone", "guest_email", "password", "token", "card"}
    assert forbidden.isdisjoint(set(Approval.model_fields.keys()))


def test_approval_has_no_target_or_parameters_field():
    """Approval can only ever reference a proposal by hash/id — it
    cannot carry its own copy of target/parameters, so it structurally
    cannot smuggle data beyond a free-text reason/approver string."""
    assert "target" not in Approval.model_fields
    assert "parameters" not in Approval.model_fields
    assert "evidence" not in Approval.model_fields
