"""
Phase 15E-1 — automation persistence tests.

Uses a real Postgres connection (via the app's own SessionLocal/engine)
so that uniqueness constraints and conditional-UPDATE concurrency
protection are tested against real database behavior, not simulated.
Concurrency is tested with genuinely separate DB connections in real OS
threads, not a single shared session.

Run with: pytest tests/test_automation_persistence.py -v
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import SessionLocal
from app.modules.automation.models import ActionProposalRow, ApprovalRow, AuditEntryRow, PolicyDecisionRow
from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash, evaluate_policy
from app.modules.automation.persistence import (
    load_approval,
    load_audit_trail,
    load_proposal,
    mark_expired,
    persist_approval,
    persist_policy_decision,
    persist_proposal,
    revoke_approval,
    update_proposal_status,
)
from app.modules.automation.schemas import ActionProposal
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


def _make_proposal(policy, action_id=None, target=None, parameters=None, **overrides):
    action_id = action_id or f"action:{uuid.uuid4().hex}"
    target = target if target is not None else {"entity_type": "booking_hold", "entity_id": uuid.uuid4().hex}
    parameters = parameters if parameters is not None else {}
    action_type = overrides.get("action_type", policy.action_type)
    proposal_hash = overrides.get("proposal_hash")
    if proposal_hash is None:
        proposal_hash = compute_proposal_hash(action_type, target, parameters)
    idem_key = overrides.pop("idempotency_key", None) or compute_idempotency_key(action_type, target, proposal_hash)
    defaults = dict(
        action_id=action_id, action_type=policy.action_type, policy_id=policy.policy_id,
        risk_level=policy.risk_level, automation_level=policy.automation_level,
        actor="staff-1", target=target, parameters=parameters,
        idempotency_key=idem_key, approval_requirement="single_admin",
        timestamp=NOW, status="APPROVAL_REQUIRED", proposal_hash=proposal_hash,
    )
    defaults.update(overrides)
    return ActionProposal(**defaults)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- 1. Action proposal persists --------------------------------------------


def test_action_proposal_persists(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    result = persist_proposal(db, proposal)
    assert result.success is True

    loaded = load_proposal(db, proposal.action_id)
    assert loaded is not None
    assert loaded.action_id == proposal.action_id
    assert loaded.proposal_hash == proposal.proposal_hash
    assert loaded.target == proposal.target


# --- 2. action_id uniqueness ---------------------------------------------------


def test_action_id_uniqueness_enforced(db):
    policy = _make_policy()
    action_id = f"action:{uuid.uuid4().hex}"
    proposal_a = _make_proposal(policy, action_id=action_id)
    result_a = persist_proposal(db, proposal_a)
    assert result_a.success is True

    proposal_b = _make_proposal(policy, action_id=action_id)  # same action_id, different target
    result_b = persist_proposal(db, proposal_b)
    assert result_b.success is False
    assert result_b.reason == "duplicate_action_id_or_idempotency_key"


# --- 3. idempotency_key uniqueness ----------------------------------------------


def test_idempotency_key_uniqueness_enforced(db):
    policy = _make_policy()
    target = {"entity_type": "booking_hold", "entity_id": uuid.uuid4().hex}
    proposal_a = _make_proposal(policy, target=target, parameters={})
    result_a = persist_proposal(db, proposal_a)
    assert result_a.success is True

    # Different action_id, but identical action_type/target/proposal_hash
    # -> identical idempotency_key -> must be rejected.
    proposal_b = _make_proposal(
        policy, target=target, parameters={},
        proposal_hash=proposal_a.proposal_hash, idempotency_key=proposal_a.idempotency_key,
    )
    result_b = persist_proposal(db, proposal_b)
    assert result_b.success is False
    assert result_b.reason == "duplicate_action_id_or_idempotency_key"


# --- 4. Policy decision persists -------------------------------------------------


def test_policy_decision_persists(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)

    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    result = persist_policy_decision(db, decision)
    assert result.success is True

    row = db.get(PolicyDecisionRow, decision.decision_id)
    assert row is not None
    assert row.outcome == decision.outcome
    assert row.policy_version == policy.version


# --- 5. Approval persists ---------------------------------------------------------


def test_approval_persists(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)

    result = persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", "fine", NOW)
    assert result.success is True

    loaded = load_approval(db, proposal.action_id)
    assert loaded is not None
    assert loaded.decision == "APPROVED"
    assert loaded.approver == "admin-1"


# --- 6. Audit entry persists -------------------------------------------------------


def test_audit_entry_persists(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW)

    trail = load_audit_trail(db, proposal.action_id)
    event_types = [e["event_type"] for e in trail]
    assert "PROPOSED" in event_types
    assert "APPROVED" in event_types


# --- 7/8. Audit entry cannot be edited/deleted through the service ---------------


def test_audit_entry_has_no_update_function():
    import app.modules.automation.persistence as persistence_module
    module_attrs = {name.lower() for name in dir(persistence_module)}
    assert "update_audit_entry" not in module_attrs
    assert "edit_audit_entry" not in module_attrs


def test_audit_entry_has_no_delete_function():
    import app.modules.automation.persistence as persistence_module
    module_attrs = {name.lower() for name in dir(persistence_module)}
    assert "delete_audit_entry" not in module_attrs
    assert "remove_audit_entry" not in module_attrs


# --- 9/10. Approval binds to action_id and proposal_hash -------------------------


def test_approval_binds_to_action_id_and_proposal_hash(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    result = persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW)
    assert result.success is True

    approval = load_approval(db, proposal.action_id)
    assert approval.action_id == proposal.action_id
    assert approval.proposal_hash == proposal.proposal_hash


# --- 11. Proposal hash drift invalidates approval (via persisted records) --------


def test_proposal_hash_drift_invalidates_approval(db):
    policy = _make_policy()
    proposal = _make_proposal(policy, parameters={})
    persist_proposal(db, proposal)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW)

    approval = load_approval(db, proposal.action_id)
    drifted = _make_proposal(policy, action_id=proposal.action_id, parameters={"changed": True})
    assert drifted.proposal_hash != proposal.proposal_hash

    from app.modules.automation.approval_workflow import revalidate_approval
    revalidation = revalidate_approval(
        approval, drifted, policy, action_still_registered=True, global_automation_enabled=True,
        actor_role="staff", required_role=policy.required_role, target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:proposal_hash_unchanged"


# --- 12. Policy version drift invalidates approval --------------------------------


def test_policy_version_drift_invalidates_approval(db):
    policy_v1 = _make_policy(version=1)
    proposal = _make_proposal(policy_v1)
    persist_proposal(db, proposal)
    persist_approval(db, proposal, policy_v1.version, "admin-1", "admin", "APPROVED", None, NOW)
    approval = load_approval(db, proposal.action_id)

    policy_v2 = _make_policy(version=2)
    from app.modules.automation.approval_workflow import revalidate_approval
    revalidation = revalidate_approval(
        approval, proposal, policy_v2, action_still_registered=True, global_automation_enabled=True,
        actor_role="staff", required_role=policy_v2.required_role, target_state_valid=True, now=NOW,
    )
    assert revalidation.eligible_for_execution is False
    assert revalidation.reason == "revalidation_failed:policy_version_unchanged"


# --- 13. Concurrent approval: exactly one succeeds (real DB connections) ---------


def test_concurrent_approval_exactly_one_succeeds_real_db():
    setup_db = SessionLocal()
    policy = _make_policy()
    proposal = _make_proposal(policy)
    try:
        persist_proposal(setup_db, proposal)
    finally:
        setup_db.close()

    results = []
    results_lock = threading.Lock()

    def _attempt(admin_id: str):
        thread_db = SessionLocal()
        try:
            r = persist_approval(thread_db, proposal, policy.version, admin_id, "admin", "APPROVED", None, NOW)
        finally:
            thread_db.close()
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_attempt, args=(f"admin-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]
    assert len(successes) == 1
    assert len(failures) == 7
    assert all(f.reason == "already_resolved" for f in failures)


# --- 14. Duplicate approval rejected (sequential) ---------------------------------


def test_duplicate_approval_rejected_sequential(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    first = persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW)
    second = persist_approval(db, proposal, policy.version, "admin-2", "admin", "APPROVED", None, NOW)
    assert first.success is True
    assert second.success is False
    assert second.reason == "already_resolved"


# --- 15/16/17. Rejected/expired/revoked approvals remain persisted ---------------


def test_rejected_approval_remains_persisted(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "REJECTED", "not needed", NOW)

    row = db.get(ApprovalRow, load_approval(db, proposal.action_id).approval_id)
    assert row is not None
    assert row.decision == "REJECTED"


def test_expired_approval_remains_persisted(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    expires_at = NOW + timedelta(minutes=5)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW, expires_at=expires_at)

    later = expires_at + timedelta(seconds=1)
    result = mark_expired(db, proposal.action_id, later)
    assert result.success is True

    approval = load_approval(db, proposal.action_id)
    assert approval is not None
    assert approval.decision == "EXPIRED"


def test_revoked_approval_remains_persisted(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW)

    result = revoke_approval(db, proposal.action_id, "admin-2", "admin", "changed mind", NOW)
    assert result.success is True

    approval = load_approval(db, proposal.action_id)
    assert approval is not None
    assert approval.decision == "REVOKED"


# --- 18. Sensitive fields cannot be persisted -------------------------------------


def test_sensitive_target_field_rejected_before_persistence(db):
    policy = _make_policy()
    proposal = _make_proposal(
        policy, target={"entity_type": "booking_hold", "entity_id": "x", "guest_phone": "9999999999"}
    )
    result = persist_proposal(db, proposal)
    assert result.success is False
    assert result.reason == "target_invalid"

    assert load_proposal(db, proposal.action_id) is None  # nothing was written


# --- 19. Unknown action type rejected before persistence -------------------------


def test_unknown_action_type_rejected_before_persistence(db):
    policy = _make_policy(action_type="PRICE_CHANGE")
    proposal = _make_proposal(policy, action_type="PRICE_CHANGE", target={"entity_type": "unit", "entity_id": "x"})
    result = persist_proposal(db, proposal)
    assert result.success is False
    assert result.reason == "action_type_not_registered"
    assert load_proposal(db, proposal.action_id) is None


# --- 20. Invalid target rejected --------------------------------------------------


def test_invalid_target_rejected(db):
    policy = _make_policy()
    proposal = _make_proposal(policy, target={"entity_type": "wrong_type", "entity_id": "x"})
    result = persist_proposal(db, proposal)
    assert result.success is False
    assert result.reason == "target_invalid"
    assert load_proposal(db, proposal.action_id) is None


# --- 21. Invalid parameters rejected ----------------------------------------------


def test_invalid_parameters_rejected(db):
    policy = _make_policy()
    proposal = _make_proposal(policy, parameters={"discount": 10})
    result = persist_proposal(db, proposal)
    assert result.success is False
    assert result.reason == "parameters_invalid"
    assert load_proposal(db, proposal.action_id) is None


# --- 22. No ActionExecutor exists --------------------------------------------------


def test_persistence_module_itself_has_no_execute_function():
    """Phase 15E-1's own boundary: this module (persistence.py) never
    executes anything, regardless of whether an executor exists
    elsewhere in the package. Phase 15E-2 later adds
    app/modules/automation/action_executor.py as its own, explicitly
    authorized, narrowly-scoped module — that addition doesn't weaken
    this file's own guarantee, which is what's actually under test
    here."""
    import app.modules.automation.persistence as persistence_module
    module_attrs = {name.lower() for name in dir(persistence_module)}
    assert {"execute", "run_action", "perform_action"}.isdisjoint(module_attrs)


# --- 23/24/25/26/27. No business-table side effects --------------------------------


def _imported_module_names(module) -> set[str]:
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module)
    return names


def test_persistence_never_imports_business_modules():
    import app.modules.automation.persistence as persistence_module
    imported = _imported_module_names(persistence_module)
    for forbidden_submodule in (
        "app.modules.public_booking.models", "app.modules.bookings.models",
        "app.modules.payments.models", "app.modules.finance.models",
        "app.modules.ota.models", "app.modules.properties.models",
    ):
        assert forbidden_submodule not in imported, f"persistence.py imports '{forbidden_submodule}'"


def test_models_never_import_business_modules():
    import app.modules.automation.models as models_module
    imported = _imported_module_names(models_module)
    for forbidden_submodule in (
        "app.modules.public_booking.models", "app.modules.bookings.models",
        "app.modules.payments.models", "app.modules.finance.models", "app.modules.ota.models",
    ):
        assert forbidden_submodule not in imported


def test_no_booking_hold_row_count_changes(db):
    from sqlalchemy import func, select as _select
    from app.modules.public_booking.models import BookingHold

    before = db.execute(_select(func.count()).select_from(BookingHold)).scalar()

    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW)

    after = db.execute(_select(func.count()).select_from(BookingHold)).scalar()
    assert after == before


# --- 28. No Claude calls -----------------------------------------------------------


def test_no_claude_or_narrative_imports():
    import app.modules.automation.persistence as persistence_module
    imported = _imported_module_names(persistence_module)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in imported
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider", "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation",
    ):
        assert forbidden_submodule not in imported


# --- 29/30. No external HTTP calls --------------------------------------------------


def test_no_http_imports():
    import app.modules.automation.persistence as persistence_module
    imported = _imported_module_names(persistence_module)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
        assert forbidden not in imported


# --- 31. Execution states cannot be created by persistence service -----------------


@pytest.mark.parametrize("bad_status", ["EXECUTING", "SUCCEEDED", "FAILED", "ROLLED_BACK"])
def test_execution_status_rejected_on_create(db, bad_status):
    policy = _make_policy()
    proposal = _make_proposal(policy, status=bad_status)
    result = persist_proposal(db, proposal)
    assert result.success is False
    assert result.reason == "execution_status_not_permitted_in_15e1"
    assert load_proposal(db, proposal.action_id) is None


@pytest.mark.parametrize("bad_status", ["EXECUTING", "SUCCEEDED", "FAILED", "ROLLED_BACK"])
def test_execution_status_rejected_on_update(db, bad_status):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    result = update_proposal_status(db, proposal.action_id, bad_status, "admin-1", NOW)
    assert result.success is False
    assert result.reason == "execution_status_not_permitted_in_15e1"
    assert load_proposal(db, proposal.action_id).status != bad_status


# --- 32. Same proposal cannot be persisted twice via idempotency key --------------


def test_same_proposal_cannot_be_persisted_twice(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    result_a = persist_proposal(db, proposal)
    result_b = persist_proposal(db, proposal)  # exact same object, same action_id and idempotency_key
    assert result_a.success is True
    assert result_b.success is False


# --- 33. Audit trail records deterministic state transitions -----------------------


def test_audit_trail_records_deterministic_transitions(db):
    # Distinct, increasing timestamps: load_audit_trail orders by
    # timestamp only, so identical timestamps across calls (as a single
    # shared NOW constant would produce) leave the returned order
    # undefined by Postgres for tied rows — using strictly increasing
    # times matches how these events genuinely happen in sequence and
    # makes the ordering assertion below meaningful rather than flaky.
    policy = _make_policy()
    proposal = _make_proposal(policy)
    t1, t2, t3 = NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)
    persist_proposal(db, proposal)
    update_proposal_status(db, proposal.action_id, "APPROVAL_REQUIRED", "system", t2)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, t3)

    trail = load_audit_trail(db, proposal.action_id)
    event_types = [e["event_type"] for e in trail]
    assert event_types == ["PROPOSED", "APPROVAL_REQUESTED", "APPROVED"]


# --- 34/35/36. Durability across a fresh connection (proxy for restart) -----------


def test_proposal_survives_new_session(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)

    fresh = SessionLocal()
    try:
        loaded = load_proposal(fresh, proposal.action_id)
    finally:
        fresh.close()
    assert loaded is not None
    assert loaded.action_id == proposal.action_id


def test_approval_survives_new_session(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)
    persist_approval(db, proposal, policy.version, "admin-1", "admin", "APPROVED", None, NOW)

    fresh = SessionLocal()
    try:
        loaded = load_approval(fresh, proposal.action_id)
    finally:
        fresh.close()
    assert loaded is not None
    assert loaded.decision == "APPROVED"


def test_audit_entries_survive_new_session(db):
    policy = _make_policy()
    proposal = _make_proposal(policy)
    persist_proposal(db, proposal)

    fresh = SessionLocal()
    try:
        trail = load_audit_trail(fresh, proposal.action_id)
    finally:
        fresh.close()
    assert len(trail) >= 1
    assert trail[0]["event_type"] == "PROPOSED"
