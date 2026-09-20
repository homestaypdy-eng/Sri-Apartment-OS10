"""
Phase 16B — NOTIFY_STAFF production-readiness / controlled-activation
tests.

Phase 16A already covers NOTIFY_STAFF's executor behavior exhaustively
(35 tests in test_automation_notify_staff.py) using in-memory Policy
objects constructed directly by the test. This file covers what's new
for 16B specifically: creating and enabling the real NOTIFY_STAFF policy
through the existing Policy Governance mechanism (governance.py, Phase
15I, unmodified), and reconstructing the full signal -> notification
audit chain end to end using that governance-created, database-persisted
policy rather than a test-constructed one — the same shape production
will actually use.

Run with: pytest tests/test_automation_notify_staff_activation.py -v
"""
import dataclasses
import uuid
from datetime import datetime, timezone

import pytest

from app.db.session import SessionLocal
from app.main import app  # noqa: F401 -- full model registration, see test_automation_notify_staff.py
from app.modules.automation import action_registry as action_registry_module
from app.modules.automation.action_executor import execute_notify_staff
from app.modules.automation.governance import (
    create_policy,
    load_policy_audit_history,
    set_policy_enabled,
)
from app.modules.automation.persistence import (
    load_audit_trail,
    load_policy,
    load_proposal,
    persist_proposal,
)
from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash
from app.modules.automation.schemas import ActionProposal, Policy
from app.modules.notifications.models import NotificationLog

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def enabled_notify_staff_registry(monkeypatch):
    real_entry = action_registry_module._REGISTRY["NOTIFY_STAFF"]
    patched_entry = dataclasses.replace(real_entry, enabled=True)
    monkeypatch.setitem(action_registry_module._REGISTRY, "NOTIFY_STAFF", patched_entry)
    return patched_entry


def _unique_policy_id():
    return f"pol:notify-staff-activation-test-{uuid.uuid4().hex}"


def _make_notify_staff_policy(policy_id, **overrides):
    """Exactly the shape Phase 16B section 2 requires for the real
    production notify_staff policy, aside from a unique policy_id per
    test to avoid PK collisions in the shared disposable database."""
    defaults = dict(
        policy_id=policy_id, action_type="NOTIFY_STAFF", automation_level="L2", risk_level="MEDIUM",
        conditions=[], required_role="staff", requires_approval=False, enabled=False, version=1,
    )
    defaults.update(overrides)
    return Policy(**defaults)


# --- 1. governance.create_policy persists the required NOTIFY_STAFF shape, disabled --


def test_governance_creates_notify_staff_policy_disabled(db):
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)
    result = create_policy(db, policy, actor="admin-1", actor_role="admin", reason="Phase 16B readiness", now=NOW)
    assert result.success is True, result.reason

    loaded = load_policy(db, policy_id)
    assert loaded is not None
    assert loaded.action_type == "NOTIFY_STAFF"
    assert loaded.automation_level == "L2"
    assert loaded.risk_level == "MEDIUM"
    assert loaded.required_role == "staff"
    assert loaded.requires_approval is False
    assert loaded.enabled is False
    assert loaded.version == 1

    from app.modules.automation.models import PolicyRow
    row = db.get(PolicyRow, policy_id)
    assert row.status == "ACTIVE"


# --- 2. POLICY_CREATED audit event recorded with a deterministic hash ----------------


def test_governance_policy_created_audit_recorded_with_deterministic_hash(db):
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)
    result = create_policy(db, policy, actor="admin-1", actor_role="admin", reason="Phase 16B readiness", now=NOW)
    assert result.success is True

    history = load_policy_audit_history(db, policy_id)
    created_events = [e for e in history if e["event_type"] == "POLICY_CREATED"]
    assert len(created_events) == 1
    event = created_events[0]
    assert event["policy_id"] == policy_id
    assert event["policy_version"] == 1
    assert event["actor"] == "admin-1"
    assert event["timestamp"] is not None
    assert event["change_hash"]

    from app.modules.automation.governance import compute_policy_change_hash
    assert event["change_hash"] == compute_policy_change_hash(policy)


# --- 3/4. RBAC: only admin may create or enable a policy -----------------------------


def test_governance_staff_cannot_create_notify_staff_policy(db):
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)
    result = create_policy(db, policy, actor="staff-1", actor_role="staff", reason=None, now=NOW)
    assert result.success is False
    assert result.reason == "unauthorized_actor"
    assert load_policy(db, policy_id) is None


def test_governance_staff_cannot_enable_notify_staff_policy(db):
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)
    assert create_policy(db, policy, actor="admin-1", actor_role="admin", reason=None, now=NOW).success is True

    result = set_policy_enabled(db, policy_id, True, actor="staff-1", actor_role="staff", reason=None, now=NOW)
    assert result.success is False
    assert result.reason == "unauthorized_actor"
    assert load_policy(db, policy_id).enabled is False


# --- 5. set_policy_enabled flips the policy and records POLICY_ENABLED ---------------


def test_set_policy_enabled_flips_and_records_policy_enabled_audit(db):
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)
    assert create_policy(db, policy, actor="admin-1", actor_role="admin", reason=None, now=NOW).success is True

    result = set_policy_enabled(db, policy_id, True, actor="admin-1", actor_role="admin",
                                 reason="Phase 16B activation gate passed", now=NOW)
    assert result.success is True

    loaded = load_policy(db, policy_id)
    assert loaded.enabled is True

    history = load_policy_audit_history(db, policy_id)
    enabled_events = [e for e in history if e["event_type"] == "POLICY_ENABLED"]
    assert len(enabled_events) == 1
    event = enabled_events[0]
    assert event["previous_state"]["enabled"] is False
    assert event["new_state"]["enabled"] is True
    assert event["actor"] == "admin-1"
    assert event["change_hash"]


# --- 6. A persisted, disabled governance policy blocks execution ---------------------


def test_persisted_disabled_policy_blocks_execution(db, enabled_notify_staff_registry):
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)  # enabled=False
    assert create_policy(db, policy, actor="admin-1", actor_role="admin", reason=None, now=NOW).success is True

    current_policy = load_policy(db, policy_id)
    target = {"entity_type": "staff_notification", "entity_id": str(uuid.uuid4())}
    parameters = {"message": "Test signal.", "category": "operational", "severity": "INFO", "source_id": f"signal:{uuid.uuid4().hex[:8]}"}
    h = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    k = compute_idempotency_key("NOTIFY_STAFF", target, h)
    proposal = ActionProposal(
        action_id=f"action:{uuid.uuid4().hex}", action_type="NOTIFY_STAFF", policy_id=policy_id,
        risk_level="MEDIUM", automation_level="L2", actor="staff-1", target=target, parameters=parameters,
        idempotency_key=k, approval_requirement="none", timestamp=NOW, status="PROPOSED", proposal_hash=h,
    )
    persist_result = persist_proposal(db, proposal)
    assert persist_result.success is True, persist_result.reason

    result = execute_notify_staff(db, proposal.action_id, current_policy, True, "staff", datetime.now(timezone.utc))
    assert result.success is False
    assert result.reason == "policy_disabled"


# --- 7. A persisted, governance-enabled policy allows a valid proposal to execute ----


def test_persisted_enabled_policy_allows_execution(db, enabled_notify_staff_registry):
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)
    assert create_policy(db, policy, actor="admin-1", actor_role="admin", reason=None, now=NOW).success is True
    assert set_policy_enabled(db, policy_id, True, actor="admin-1", actor_role="admin",
                               reason="readiness test", now=NOW).success is True

    current_policy = load_policy(db, policy_id)
    assert current_policy.enabled is True

    target = {"entity_type": "staff_notification", "entity_id": str(uuid.uuid4())}
    parameters = {"message": "Housekeeping backlog detected.", "category": "operational", "severity": "WATCH", "source_id": f"signal:{uuid.uuid4().hex[:8]}"}
    h = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    k = compute_idempotency_key("NOTIFY_STAFF", target, h)
    proposal = ActionProposal(
        action_id=f"action:{uuid.uuid4().hex}", action_type="NOTIFY_STAFF", policy_id=policy_id,
        risk_level="MEDIUM", automation_level="L2", actor="staff-1", target=target, parameters=parameters,
        idempotency_key=k, approval_requirement="none", timestamp=NOW, status="PROPOSED", proposal_hash=h,
    )
    assert persist_proposal(db, proposal).success is True

    from app.modules.automation.persistence import update_proposal_status
    assert update_proposal_status(db, proposal.action_id, "APPROVED", "system", NOW).success is True

    result = execute_notify_staff(db, proposal.action_id, current_policy, True, "staff", datetime.now(timezone.utc))
    assert result.success is True
    assert result.status == "SUCCEEDED"


# --- 8. Full audit reconstruction: signal -> proposal -> decision -> execution -> notification --


def test_full_audit_reconstruction_signal_to_notification(db, enabled_notify_staff_registry):
    """Mirrors Phase 16B section 20's required reconstruction chain,
    using a real governance-created, database-persisted policy end to
    end -- the same shape a real 13E signal -> NOTIFY_STAFF proposal
    pipeline would use in production."""
    policy_id = _unique_policy_id()
    policy = _make_notify_staff_policy(policy_id)
    assert create_policy(db, policy, actor="admin-1", actor_role="admin", reason=None, now=NOW).success is True
    assert set_policy_enabled(db, policy_id, True, actor="admin-1", actor_role="admin", reason=None, now=NOW).success is True
    current_policy = load_policy(db, policy_id)

    # Signal: a deterministic fact, exactly as 13E would produce -- no
    # invented content, no Claude call anywhere in this chain.
    signal = {
        "source_id": f"signal:housekeeping-backlog-{uuid.uuid4().hex[:8]}",
        "category": "operational",
        "severity": "WATCH",
        "message": "3 housekeeping tasks overdue by more than 24 hours.",
    }
    target = {"entity_type": "staff_notification", "entity_id": str(uuid.uuid4())}
    parameters = {"message": signal["message"], "category": signal["category"], "severity": signal["severity"], "source_id": signal["source_id"]}
    h = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    k = compute_idempotency_key("NOTIFY_STAFF", target, h)
    proposal = ActionProposal(
        action_id=f"action:{uuid.uuid4().hex}", action_type="NOTIFY_STAFF", policy_id=policy_id,
        risk_level="MEDIUM", automation_level="L2", actor="staff-1", target=target, parameters=parameters,
        idempotency_key=k, approval_requirement="none", timestamp=NOW, status="PROPOSED", proposal_hash=h,
    )
    assert persist_proposal(db, proposal).success is True

    from app.modules.automation.persistence import update_proposal_status
    assert update_proposal_status(db, proposal.action_id, "APPROVED", "system", NOW).success is True

    result = execute_notify_staff(db, proposal.action_id, current_policy, True, "staff", datetime.now(timezone.utc))
    assert result.success is True

    # Reconstruct the chain from durable state alone.
    final_proposal = load_proposal(db, proposal.action_id)
    assert final_proposal.status == "SUCCEEDED"
    assert final_proposal.policy_id == policy_id

    trail = load_audit_trail(db, proposal.action_id)
    event_types = [e["event_type"] for e in trail]
    assert "POLICY_EVALUATED" in event_types
    assert "EXECUTION_STARTED" in event_types
    assert "EXECUTION_SUCCEEDED" in event_types

    policy_history = load_policy_audit_history(db, policy_id)
    assert any(e["event_type"] == "POLICY_CREATED" for e in policy_history)
    assert any(e["event_type"] == "POLICY_ENABLED" for e in policy_history)
    assert any(e["policy_version"] == 1 for e in policy_history)

    notification_id = result.detail["notification_id"]
    notification = db.get(NotificationLog, uuid.UUID(notification_id))
    assert notification is not None
    assert notification.action_id == proposal.action_id
    assert notification.source_id == signal["source_id"]
