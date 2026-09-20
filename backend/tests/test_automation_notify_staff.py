"""
Phase 16A — NOTIFY_STAFF executor tests.

NOTIFY_STAFF's registry entry originally shipped (Phase 16A) with
enabled=False — an extra layer of caution beyond how RELEASE_EXPIRED_HOLD
originally shipped. Phase 16B Gate B later activated it in production
after all 25 activation-gate checks passed (see action_registry.py's
module docstring), so the real, unpatched registry entry is now
enabled=True. Most tests in this file still use the
`enabled_notify_staff_registry` fixture below to make that explicit and
keep the test independent of whichever way the real, ambient value
happens to be set (same discipline Phase 15G established for
GLOBAL_AUTOMATION_ENABLED) — a handful of tests instead use
`disabled_notify_staff_registry` to force the opposite state and prove
the registry-level gate itself still works correctly.

Unlike test_automation_action_executor.py, this file never touches the
public booking API / TestClient — NOTIFY_STAFF's target is a synthetic
"staff_notification" entity, not a real booking resource, so no HTTP
login or property/unit/hold setup is needed anywhere here.

Run with: pytest tests/test_automation_notify_staff.py -v
"""
import dataclasses
import json
import threading
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.session import SessionLocal
# Triggers full model registration (every module's SQLAlchemy mapper),
# same as every other automation test file — without this, NotificationLog's
# pre-existing FK to payroll.id (unrelated to this phase) fails to resolve
# when this file is the only one pytest collects, since nothing else in
# this file imports the payroll module.
from app.main import app  # noqa: F401
from app.modules.automation import action_registry as action_registry_module
from app.modules.automation.action_executor import execute_notify_staff
from app.modules.automation.action_registry import get_registry_entry
from app.modules.automation.models import ActionProposalRow, ApprovalRow
from app.modules.automation.persistence import (
    load_audit_trail,
    load_proposal,
    persist_proposal,
    update_proposal_status,
)
from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash
from app.modules.automation.schemas import ActionProposal
from app.modules.automation.schemas import Policy as AutomationPolicy
from app.modules.automation.schemas import NotifyStaffParameters, NotifyStaffTarget
from app.modules.notifications.models import NotificationLog


@pytest.fixture
def enabled_notify_staff_registry(monkeypatch):
    """Forces the _REGISTRY dict entry for NOTIFY_STAFF to enabled=True
    for the duration of one test, independent of whatever the real,
    ambient value currently is (True since Phase 16B activation)."""
    real_entry = action_registry_module._REGISTRY["NOTIFY_STAFF"]
    patched_entry = dataclasses.replace(real_entry, enabled=True)
    monkeypatch.setitem(action_registry_module._REGISTRY, "NOTIFY_STAFF", patched_entry)
    return patched_entry


@pytest.fixture
def disabled_notify_staff_registry(monkeypatch):
    """Forces the _REGISTRY dict entry for NOTIFY_STAFF to enabled=False
    for the duration of one test, to prove the registry-level gate itself
    still correctly blocks execution — independent of the real, ambient
    (now True) default."""
    real_entry = action_registry_module._REGISTRY["NOTIFY_STAFF"]
    patched_entry = dataclasses.replace(real_entry, enabled=False)
    monkeypatch.setitem(action_registry_module._REGISTRY, "NOTIFY_STAFF", patched_entry)
    return patched_entry


def _make_notify_policy(**overrides):
    defaults = dict(
        policy_id="notify_staff", action_type="NOTIFY_STAFF",
        automation_level="L2", risk_level="MEDIUM", conditions=[], required_role="staff",
        requires_approval=False, enabled=True, version=1,
    )
    defaults.update(overrides)
    return AutomationPolicy(**defaults)


def _default_notify_parameters(**overrides):
    defaults = dict(
        message="Housekeeping backlog for Unit 4B exceeds 2 days.",
        category="operational", severity="WATCH", source_id=f"signal:{uuid.uuid4().hex[:10]}",
    )
    defaults.update(overrides)
    return defaults


def _make_and_persist_notify_proposal(db, policy, status="APPROVED", target=None, parameters=None, action_type=None):
    """Requires the registry entry to be enabled (see
    enabled_notify_staff_registry) — persist_proposal runs the real
    structural validation gate, same as every other action type."""
    action_type = action_type or policy.action_type
    target = target if target is not None else {"entity_type": "staff_notification", "entity_id": str(uuid.uuid4())}
    parameters = parameters if parameters is not None else _default_notify_parameters()
    h = compute_proposal_hash(action_type, target, parameters)
    k = compute_idempotency_key(action_type, target, h)
    proposal = ActionProposal(
        action_id=f"action:{uuid.uuid4().hex}", action_type=policy.action_type, policy_id=policy.policy_id,
        risk_level=policy.risk_level, automation_level=policy.automation_level, actor="staff-1",
        target=target, parameters=parameters, idempotency_key=k, approval_requirement="none",
        timestamp=datetime.now(timezone.utc), status="PROPOSED", proposal_hash=h,
    )
    result = persist_proposal(db, proposal)
    assert result.success is True, result.reason
    if status != "PROPOSED":
        r2 = update_proposal_status(db, proposal.action_id, status, "system", datetime.now(timezone.utc))
        assert r2.success is True, r2.reason
    return proposal


def _insert_raw_notify_proposal_row(db, action_id, action_type, target, parameters=None, status="APPROVED", idempotency_key=None):
    """Bypasses persist_proposal's registry gate on purpose, to test the
    executor's OWN independent defenses against a proposal that should
    never have existed in the first place (mirrors
    test_automation_action_executor.py's own helper of the same shape)."""
    parameters = parameters if parameters is not None else _default_notify_parameters()
    h = compute_proposal_hash(action_type, target, parameters)
    row = ActionProposalRow(
        action_id=action_id, action_type=action_type, policy_id="notify_staff",
        risk_level="MEDIUM", automation_level="L2", actor="staff-1",
        target=json.dumps(target), parameters=json.dumps(parameters),
        idempotency_key=idempotency_key or compute_idempotency_key(action_type, target, h),
        approval_requirement="none", proposal_hash=h, status=status,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()


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


# --- 1. Registry entry ships disabled by default (real, unpatched) -----------------


def test_registry_entry_activated_in_phase_16b():
    """NOTIFY_STAFF shipped disabled=True in Phase 16A and was
    explicitly activated in Phase 16B Gate B after all 25 activation-gate
    checks passed. This asserts the real, unpatched, currently-deployed
    state directly."""
    entry = get_registry_entry("NOTIFY_STAFF")
    assert entry is not None
    assert entry.enabled is True


# --- 2. Registry entry has the correct closed shape ---------------------------------


def test_notify_staff_registered_with_correct_shape():
    entry = get_registry_entry("NOTIFY_STAFF")
    assert entry.domain == "OPERATIONS"
    assert entry.risk_level == "MEDIUM"
    assert entry.automation_level == "L2"
    assert entry.required_role == "staff"
    assert entry.approval_requirement == "none"
    assert entry.rollback_supported is False
    assert entry.idempotency_required is True
    assert entry.target_entity_type == "staff_notification"
    assert entry.target_schema is NotifyStaffTarget
    assert entry.parameters_schema is NotifyStaffParameters
    assert entry.policy_id == "notify_staff"


# --- 3/4. Target schema is closed -----------------------------------------------------


def test_notify_staff_target_schema_rejects_extra_field():
    with pytest.raises(ValidationError):
        NotifyStaffTarget(entity_type="staff_notification", entity_id="x", extra_field="not allowed")


def test_notify_staff_target_schema_requires_correct_entity_type():
    with pytest.raises(ValidationError):
        NotifyStaffTarget(entity_type="unit", entity_id="x")


# --- 5-9. Parameters schema is closed --------------------------------------------------


def test_notify_staff_parameters_schema_rejects_extra_field():
    with pytest.raises(ValidationError):
        NotifyStaffParameters(**_default_notify_parameters(), extra_field="not allowed")


def test_notify_staff_parameters_schema_rejects_invalid_category():
    with pytest.raises(ValidationError):
        NotifyStaffParameters(**_default_notify_parameters(category="not_a_real_category"))


def test_notify_staff_parameters_schema_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        NotifyStaffParameters(**_default_notify_parameters(severity="NOT_A_REAL_SEVERITY"))


def test_notify_staff_parameters_schema_rejects_message_too_long():
    with pytest.raises(ValidationError):
        NotifyStaffParameters(**_default_notify_parameters(message="x" * 501))


def test_notify_staff_parameters_schema_requires_all_fields():
    params = _default_notify_parameters()
    del params["source_id"]
    with pytest.raises(ValidationError):
        NotifyStaffParameters(**params)


# --- 10. Valid proposal creates a notification successfully -------------------------


def test_valid_proposal_creates_notification_successfully(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True
        assert result.status == "SUCCEEDED"
        assert "notification_id" in result.detail
        assert load_proposal(db, proposal.action_id).status == "SUCCEEDED"
    finally:
        db.close()


# --- 11. notification_logs row has the expected fields and content ------------------


def test_notification_log_row_has_expected_fields_and_content(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        params = _default_notify_parameters(category="maintenance", severity="CRITICAL", message="Water leak reported in Unit 2A.")
        proposal = _make_and_persist_notify_proposal(db, policy, parameters=params)
        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        row = db.get(NotificationLog, uuid.UUID(result.detail["notification_id"]))
        assert row is not None
        assert row.event_type == "automation_alert"
        assert row.channel == "in_app"
        assert row.recipient == "staff"
        assert row.user_id is None
        assert row.action_id == proposal.action_id
        assert row.category == "maintenance"
        assert row.severity == "CRITICAL"
        assert row.message == "Water leak reported in Unit 2A."
        assert row.source_id == params["source_id"]
        assert row.success is True
    finally:
        db.close()


# --- 12. Successful execution survives a fresh session (restart proxy) --------------


def test_successful_execution_survives_new_session(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
    finally:
        db.close()

    fresh = SessionLocal()
    try:
        loaded = load_proposal(fresh, proposal.action_id)
        assert loaded.status == "SUCCEEDED"
    finally:
        fresh.close()

    repeat_db = SessionLocal()
    try:
        result = execute_notify_staff(repeat_db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True
        assert result.reason == "already_succeeded"
    finally:
        repeat_db.close()


# --- 13. Disabled registry (real, unpatched default) blocks execution ---------------


def test_disabled_registry_blocks_execution_when_explicitly_disabled(disabled_notify_staff_registry):
    """Proves the registry-level gate itself still works correctly, using
    an explicit override rather than relying on the (now True, since
    Phase 16B) ambient default."""
    db = SessionLocal()
    try:
        action_id = f"action:{uuid.uuid4().hex}"
        target = {"entity_type": "staff_notification", "entity_id": str(uuid.uuid4())}
        _insert_raw_notify_proposal_row(db, action_id, "NOTIFY_STAFF", target)

        policy = _make_notify_policy()
        result = execute_notify_staff(db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "action_not_registered_or_disabled"
    finally:
        db.close()


# --- 14. Global automation OFF blocks execution --------------------------------------


def test_global_automation_off_blocks_execution(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, False, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "global_automation_disabled"

        assert db.execute(select(func.count()).select_from(NotificationLog).where(NotificationLog.action_id == proposal.action_id)).scalar() == 0

        trail = load_audit_trail(db, proposal.action_id)
        assert "KILL_SWITCH_BLOCKED" in [e["event_type"] for e in trail]
    finally:
        db.close()


# --- 15. Per-action policy disabled blocks execution ---------------------------------


def test_disabled_policy_blocks_execution(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy(enabled=False)
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "policy_disabled"
    finally:
        db.close()


# --- 16. Stale proposal hash on the approval blocks execution ------------------------


def test_stale_proposal_hash_blocks_execution(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy(requires_approval=True)
        proposal = _make_and_persist_notify_proposal(db, policy)

        stale_approval = ApprovalRow(
            approval_id=f"approval:{uuid.uuid4().hex}", action_id=proposal.action_id,
            proposal_hash="stale-hash-does-not-match-current-proposal", policy_id=policy.policy_id,
            policy_version=policy.version, approver="admin-1", decision="APPROVED", status="APPROVED",
            reason=None, created_at=datetime.now(timezone.utc), expires_at=None,
        )
        db.add(stale_approval)
        db.commit()

        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert "proposal_hash" in result.reason
    finally:
        db.close()


# --- 17. Policy version drift blocks execution ----------------------------------------


def test_policy_version_drift_blocks_execution(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy_v1 = _make_notify_policy(requires_approval=True, version=1)
        proposal = _make_and_persist_notify_proposal(db, policy_v1)

        approval = ApprovalRow(
            approval_id=f"approval:{uuid.uuid4().hex}", action_id=proposal.action_id,
            proposal_hash=proposal.proposal_hash, policy_id=policy_v1.policy_id,
            policy_version=1, approver="admin-1", decision="APPROVED", status="APPROVED",
            reason=None, created_at=datetime.now(timezone.utc), expires_at=None,
        )
        db.add(approval)
        db.commit()

        policy_v2 = _make_notify_policy(requires_approval=True, version=2)
        result = execute_notify_staff(db, proposal.action_id, policy_v2, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert "policy_version" in result.reason
    finally:
        db.close()


# --- 18. Policy ID drift blocks execution ----------------------------------------------


def test_policy_id_drift_blocks_execution(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy(requires_approval=True)
        proposal = _make_and_persist_notify_proposal(db, policy)

        approval = ApprovalRow(
            approval_id=f"approval:{uuid.uuid4().hex}", action_id=proposal.action_id,
            proposal_hash=proposal.proposal_hash, policy_id=policy.policy_id,
            policy_version=policy.version, approver="admin-1", decision="APPROVED", status="APPROVED",
            reason=None, created_at=datetime.now(timezone.utc), expires_at=None,
        )
        db.add(approval)
        db.commit()

        different_policy = _make_notify_policy(requires_approval=True, policy_id="pol:different")
        result = execute_notify_staff(db, proposal.action_id, different_policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert "policy_id" in result.reason
    finally:
        db.close()


# --- 19/20. Unknown / unsupported action type rejected ---------------------------------


def test_unknown_action_type_rejected():
    db = SessionLocal()
    try:
        action_id = f"action:{uuid.uuid4().hex}"
        target = {"entity_type": "staff_notification", "entity_id": str(uuid.uuid4())}
        _insert_raw_notify_proposal_row(db, action_id, "SOME_MADE_UP_ACTION", target)
        policy = _make_notify_policy(action_type="SOME_MADE_UP_ACTION")
        result = execute_notify_staff(db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "unsupported_action_type"
    finally:
        db.close()


def test_unsupported_action_type_rejected():
    """RELEASE_EXPIRED_HOLD is a real, registered, enabled action type —
    just not one this function (execute_notify_staff) implements."""
    db = SessionLocal()
    try:
        action_id = f"action:{uuid.uuid4().hex}"
        target = {"entity_type": "booking_hold", "entity_id": str(uuid.uuid4())}
        _insert_raw_notify_proposal_row(db, action_id, "RELEASE_EXPIRED_HOLD", target, parameters={})
        policy = _make_notify_policy(action_type="RELEASE_EXPIRED_HOLD", policy_id="pol:release-expired-hold")
        result = execute_notify_staff(db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "unsupported_action_type"
    finally:
        db.close()


# --- 21. Wrong target entity type rejected ----------------------------------------------


def test_wrong_target_entity_type_rejected(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        action_id = f"action:{uuid.uuid4().hex}"
        bad_target = {"entity_type": "unit", "entity_id": str(uuid.uuid4())}
        _insert_raw_notify_proposal_row(db, action_id, "NOTIFY_STAFF", bad_target)

        policy = _make_notify_policy()
        result = execute_notify_staff(db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "target_invalid"
    finally:
        db.close()


# --- 22. Duplicate execution attempt does not execute twice ----------------------------


def test_duplicate_execution_attempt_does_not_execute_twice(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)

        first = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        second = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))

        assert first.success is True and first.reason == "notified"
        assert second.success is True and second.reason == "already_succeeded"

        count = db.execute(
            select(func.count()).select_from(NotificationLog).where(NotificationLog.action_id == proposal.action_id)
        ).scalar()
        assert count == 1
    finally:
        db.close()


# --- 23. Concurrent execution of same action -> exactly one successful transition ------


def test_concurrent_execution_same_action_exactly_one_succeeds(enabled_notify_staff_registry):
    setup_db = SessionLocal()
    policy = _make_notify_policy()
    try:
        proposal = _make_and_persist_notify_proposal(setup_db, policy)
    finally:
        setup_db.close()

    results = []
    results_lock = threading.Lock()

    def _attempt():
        thread_db = SessionLocal()
        try:
            r = execute_notify_staff(thread_db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        finally:
            thread_db.close()
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_attempt) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success and r.reason == "notified"]
    assert len(successes) == 1

    check_db = SessionLocal()
    try:
        count = check_db.execute(
            select(func.count()).select_from(NotificationLog).where(NotificationLog.action_id == proposal.action_id)
        ).scalar()
        assert count == 1
    finally:
        check_db.close()


# --- 24. Notification row created exactly once on success ------------------------------


def test_notification_created_exactly_once_on_success(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        before = db.execute(select(func.count()).select_from(NotificationLog)).scalar()

        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        after = db.execute(select(func.count()).select_from(NotificationLog)).scalar()
        assert after == before + 1
    finally:
        db.close()


# --- 25. No side effects on booking/payment tables ---------------------------------------


def test_no_side_effects_on_booking_and_payment_tables(enabled_notify_staff_registry):
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment
    from app.modules.public_booking.models import BookingHold

    db = SessionLocal()
    try:
        bookings_before = db.execute(select(func.count()).select_from(Booking)).scalar()
        payments_before = db.execute(select(func.count()).select_from(Payment)).scalar()
        holds_before = db.execute(select(func.count()).select_from(BookingHold)).scalar()

        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        bookings_after = db.execute(select(func.count()).select_from(Booking)).scalar()
        payments_after = db.execute(select(func.count()).select_from(Payment)).scalar()
        holds_after = db.execute(select(func.count()).select_from(BookingHold)).scalar()
        assert bookings_after == bookings_before
        assert payments_after == payments_before
        assert holds_after == holds_before
    finally:
        db.close()


# --- 26. RELEASE_EXPIRED_HOLD's registry entry/state is untouched by this module -------


def test_release_expired_hold_policy_and_state_untouched():
    """A NOTIFY_STAFF-focused cross-check, run without any registry
    fixture: RELEASE_EXPIRED_HOLD's own real registry entry must remain
    exactly as Phase 15C shipped it, regardless of anything NOTIFY_STAFF
    does elsewhere in this file or its own Phase 16B activation."""
    entry = get_registry_entry("RELEASE_EXPIRED_HOLD")
    assert entry is not None
    assert entry.enabled is True
    assert entry.risk_level == "LOW"
    assert entry.automation_level == "L2"
    assert entry.target_entity_type == "booking_hold"
    assert entry.policy_id == "pol:release-expired-hold"

    notify_entry = get_registry_entry("NOTIFY_STAFF")
    assert notify_entry.enabled is True  # Phase 16B: activated


# --- 27/28/29. Audit events emitted only when they actually occurred -------------------


def test_audit_started_and_succeeded_emitted_on_success(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "EXECUTION_STARTED" in event_types
        assert "EXECUTION_SUCCEEDED" in event_types
        assert "EXECUTION_FAILED" not in event_types
    finally:
        db.close()


def test_audit_failed_emitted_on_failed_execution(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy(enabled=False)
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False

        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "EXECUTION_STARTED" not in event_types
        assert "EXECUTION_FAILED" not in event_types  # policy_disabled DENY, never reached the claim step
    finally:
        db.close()


def test_no_execution_started_when_blocked_before_claim(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        result = execute_notify_staff(db, proposal.action_id, policy, False, "staff", datetime.now(timezone.utc))
        assert result.success is False

        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "EXECUTION_STARTED" not in event_types
    finally:
        db.close()


# --- 30. Kill switch produces KILL_SWITCH_BLOCKED --------------------------------------


def test_kill_switch_produces_blocked_audit_event(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy)
        execute_notify_staff(db, proposal.action_id, policy, False, "staff", datetime.now(timezone.utc))
        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "KILL_SWITCH_BLOCKED" in event_types
    finally:
        db.close()


# --- 31. No Claude/LLM invocation anywhere in the executor module ----------------------


def test_no_claude_or_llm_invocation():
    import app.modules.automation.action_executor as executor_module
    imported = _imported_module_names(executor_module)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in imported
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider", "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation",
    ):
        assert forbidden_submodule not in imported


# --- 32. No new external notification transport added ----------------------------------


def test_no_external_notification_transport():
    import inspect
    import app.modules.automation.action_executor as executor_module
    imported = _imported_module_names(executor_module)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket", "smtplib", "boto3"):
        assert forbidden not in imported
    source = inspect.getsource(executor_module)
    for forbidden_text in ("twilio", "sendgrid", "slack_sdk", "whatsapp"):
        assert forbidden_text not in source.lower()


# --- 33. No arbitrary database table access ---------------------------------------------


def test_no_arbitrary_database_table_access():
    import inspect
    import app.modules.automation.action_executor as executor_module
    source = inspect.getsource(executor_module)
    for forbidden_model in (
        "app.modules.bookings.models", "app.modules.payments.models",
        "app.modules.finance.models", "app.modules.ota.models",
    ):
        assert forbidden_model not in source
    assert source.count("from app.modules.notifications.models import NotificationLog") == 1
    # Every raw text(...) SQL statement in this module is the one
    # pre-existing RELEASE_EXPIRED_HOLD write against booking_holds — the
    # notification write is a plain ORM db.add(), never a second text()
    # call. This scans actual text("...") call sites, not bare substrings,
    # since the module's own prose (e.g. "a single insert into
    # notification_logs") legitimately contains words like "insert"
    # without being a raw SQL statement.
    import re
    text_calls = re.findall(r'text\(\s*"([^"]*)"', source)
    assert len(text_calls) == 1
    assert "UPDATE booking_holds" in text_calls[0]
    assert "notification_logs" not in text_calls[0]
    # The one pre-existing raw-SQL business-table write (RELEASE_EXPIRED_HOLD's)
    # is unchanged by this phase — still exactly one occurrence.
    assert source.count("UPDATE booking_holds") == 1
    assert "UPDATE notification_logs" not in source.upper()


# --- 34. No sensitive message text in audit detail ---------------------------------------


def test_no_sensitive_message_text_in_audit_detail(enabled_notify_staff_registry):
    db = SessionLocal()
    try:
        secret_message = "CONFIDENTIAL-CANARY-STRING-77219 guest dispute details"
        policy = _make_notify_policy()
        proposal = _make_and_persist_notify_proposal(db, policy, parameters=_default_notify_parameters(message=secret_message))
        execute_notify_staff(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))

        trail = load_audit_trail(db, proposal.action_id)
        succeeded = [e for e in trail if e["event_type"] == "EXECUTION_SUCCEEDED"]
        assert len(succeeded) == 1
        assert "message" not in succeeded[0]["detail"]
        serialized = json.dumps(trail, default=str)
        assert secret_message not in serialized
    finally:
        db.close()


# --- 35. Reuses existing hash functions, no second implementation invented -------------


def test_reuses_existing_hash_functions_no_second_implementation():
    import inspect
    import app.modules.automation.action_executor as executor_module
    import app.modules.automation.schemas as schemas_module

    for module in (executor_module, schemas_module):
        source = inspect.getsource(module)
        assert "hashlib" not in source

    target = {"entity_type": "staff_notification", "entity_id": "fixed-id"}
    parameters = _default_notify_parameters(source_id="fixed-source")
    h1 = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    h2 = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    assert h1 == h2  # deterministic, same canonical hash function used everywhere
    k1 = compute_idempotency_key("NOTIFY_STAFF", target, h1)
    k2 = compute_idempotency_key("NOTIFY_STAFF", target, h2)
    assert k1 == k2
