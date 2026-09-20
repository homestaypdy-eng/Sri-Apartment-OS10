"""
Phase 15I — policy lifecycle governance tests.

Uses a real Postgres connection so the append-only database trigger and
cross-session persistence are tested against real database behavior.

Run with: pytest tests/test_automation_governance.py -v
"""
import ast
import inspect
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.automation import governance
from app.modules.automation.governance import (
    compute_policy_change_hash,
    create_policy,
    establish_pre_audit_baseline,
    load_global_automation_history,
    load_policy_audit_history,
    record_global_automation_transition,
    retire_policy,
    set_policy_enabled,
    update_policy,
)
from app.modules.automation.persistence import load_policy
from app.modules.automation.schemas import Condition, Policy

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _unique_policy_id():
    return f"pol:governance-test-{uuid.uuid4().hex}"


def _make_policy(policy_id=None, **overrides):
    defaults = dict(
        policy_id=policy_id or _unique_policy_id(), action_type="RELEASE_EXPIRED_HOLD",
        automation_level="L2", risk_level="LOW", conditions=[], required_role="staff",
        requires_approval=False, enabled=True, version=1,
    )
    defaults.update(overrides)
    return Policy(**defaults)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- 1. Policy creation writes POLICY_CREATED --------------------------------


def test_policy_creation_writes_created_event(db):
    policy = _make_policy()
    result = create_policy(db, policy, "admin-1", "admin", "initial rollout", NOW)
    assert result.success is True

    history = load_policy_audit_history(db, policy.policy_id)
    assert len(history) == 1
    assert history[0]["event_type"] == "POLICY_CREATED"
    assert history[0]["previous_state"] is None
    assert history[0]["new_state"]["policy_id"] == policy.policy_id


# --- 2. Policy update writes POLICY_UPDATED -----------------------------------


def test_policy_update_writes_updated_event(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    result = update_policy(db, policy.policy_id, "admin-1", "admin", "raise risk", NOW, risk_level="MEDIUM")
    assert result.success is True

    history = load_policy_audit_history(db, policy.policy_id)
    event_types = [e["event_type"] for e in history]
    assert "POLICY_UPDATED" in event_types


# --- 3/4. Enable/disable transitions -------------------------------------------


def test_disable_then_enable_writes_correct_events(db):
    policy = _make_policy(enabled=True)
    create_policy(db, policy, "admin-1", "admin", None, NOW)

    disable_result = set_policy_enabled(db, policy.policy_id, False, "admin-1", "admin", "pause", NOW)
    assert disable_result.success is True
    enable_result = set_policy_enabled(db, policy.policy_id, True, "admin-1", "admin", "resume", NOW)
    assert enable_result.success is True

    history = load_policy_audit_history(db, policy.policy_id)
    event_types = [e["event_type"] for e in history]
    assert "POLICY_DISABLED" in event_types
    assert "POLICY_ENABLED" in event_types


# --- 5. Unchanged enabled state creates no duplicate event ---------------------


def test_unchanged_enabled_state_creates_no_event(db):
    policy = _make_policy(enabled=True)
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    result = set_policy_enabled(db, policy.policy_id, True, "admin-1", "admin", None, NOW)  # already True
    assert result.success is False
    assert result.reason == "no_change"

    history = load_policy_audit_history(db, policy.policy_id)
    assert len([e for e in history if e["event_type"] in ("POLICY_ENABLED", "POLICY_DISABLED")]) == 0


# --- 6/7/8/9. Versioning -------------------------------------------------------


def test_semantic_change_increments_version(db):
    policy = _make_policy(version=1)
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    update_policy(db, policy.policy_id, "admin-1", "admin", "widen role", NOW, required_role="admin")
    updated = load_policy(db, policy.policy_id)
    assert updated.version == 2


def test_unchanged_policy_does_not_increment_version(db):
    policy = _make_policy(version=1)
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    # Identical values to what's already stored.
    result = update_policy(db, policy.policy_id, "admin-1", "admin", None, NOW, risk_level="LOW")
    assert result.success is False
    assert result.reason == "no_semantic_change"
    assert load_policy(db, policy.policy_id).version == 1


def test_version_is_monotonic_across_multiple_updates(db):
    policy = _make_policy(version=1)
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    update_policy(db, policy.policy_id, "admin-1", "admin", None, NOW, risk_level="MEDIUM")
    update_policy(db, policy.policy_id, "admin-1", "admin", None, NOW, risk_level="HIGH")
    update_policy(db, policy.policy_id, "admin-1", "admin", None, NOW, risk_level="CRITICAL")
    assert load_policy(db, policy.policy_id).version == 4


def test_version_downgrade_is_structurally_impossible(db):
    """update_policy() has no `version` parameter at all — there is no
    call shape that could ever request a downgrade; the function always
    computes it as current+1 or leaves it unchanged."""
    sig = inspect.signature(update_policy)
    assert "version" not in sig.parameters


# --- 10/11. Persistence across sessions / restart -------------------------------


def test_policy_audit_persists_across_new_session(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", None, NOW)

    fresh = SessionLocal()
    try:
        history = load_policy_audit_history(fresh, policy.policy_id)
    finally:
        fresh.close()
    assert len(history) == 1
    assert history[0]["event_type"] == "POLICY_CREATED"


# --- 12/13. Append-only: no update/delete path -------------------------------


def test_governance_module_has_no_update_or_delete_function():
    module_attrs = {name.lower() for name in dir(governance)}
    forbidden = {"update_policy_audit_entry", "delete_policy_audit_entry", "edit_policy_audit_entry", "remove_policy_audit_entry"}
    assert forbidden.isdisjoint(module_attrs)


def test_database_trigger_rejects_update_and_delete(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    history = load_policy_audit_history(db, policy.policy_id)
    audit_id = history[0]["policy_audit_id"]

    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE policy_audit_entries SET change_reason = 'tampered' WHERE policy_audit_id = :id"), {"id": audit_id})
        db.flush()
    db.rollback()

    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM policy_audit_entries WHERE policy_audit_id = :id"), {"id": audit_id})
        db.flush()
    db.rollback()

    # The row must still be exactly as written.
    fresh = SessionLocal()
    try:
        history_after = load_policy_audit_history(fresh, policy.policy_id)
    finally:
        fresh.close()
    assert history_after[0]["change_reason"] is None


# --- 14/15. Global automation switch audit --------------------------------------


def test_global_automation_enable_and_disable_events(db):
    r1 = record_global_automation_transition(db, False, True, "admin-1", "admin", "activate for testing", NOW)
    assert r1.success is True
    r2 = record_global_automation_transition(db, True, False, "admin-1", "admin", "deactivate", NOW)
    assert r2.success is True

    history = load_global_automation_history(db)
    event_types = [e["event_type"] for e in history if e["actor"] == "admin-1" and e["change_reason"] in ("activate for testing", "deactivate")]
    assert "GLOBAL_AUTOMATION_ENABLED" in event_types
    assert "GLOBAL_AUTOMATION_DISABLED" in event_types


def test_global_automation_no_change_rejected(db):
    result = record_global_automation_transition(db, True, True, "admin-1", "admin", None, NOW)
    assert result.success is False
    assert result.reason == "no_change"


# --- 16. Actor mandatory --------------------------------------------------------


def test_actor_is_mandatory(db):
    policy = _make_policy()
    result = create_policy(db, policy, "", "admin", None, NOW)
    assert result.success is False
    assert result.reason == "actor_required"


# --- 17. Arbitrary role strings rejected ----------------------------------------


def test_arbitrary_role_string_rejected(db):
    policy = _make_policy()
    result = create_policy(db, policy, "someone", "automation_admin", None, NOW)
    assert result.success is False
    assert result.reason == "invalid_role"


# --- 18/19. RBAC: staff cannot mutate, admin can --------------------------------


def test_staff_cannot_create_policy(db):
    policy = _make_policy()
    result = create_policy(db, policy, "staff-1", "staff", None, NOW)
    assert result.success is False
    assert result.reason == "unauthorized_actor"


def test_staff_cannot_update_enable_disable_retire(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", None, NOW)

    assert update_policy(db, policy.policy_id, "staff-1", "staff", None, NOW, risk_level="HIGH").reason == "unauthorized_actor"
    assert set_policy_enabled(db, policy.policy_id, False, "staff-1", "staff", None, NOW).reason == "unauthorized_actor"
    assert retire_policy(db, policy.policy_id, "staff-1", "staff", None, NOW).reason == "unauthorized_actor"


def test_admin_can_mutate_policy(db):
    policy = _make_policy()
    assert create_policy(db, policy, "admin-1", "admin", None, NOW).success is True
    assert update_policy(db, policy.policy_id, "admin-1", "admin", None, NOW, risk_level="HIGH").success is True
    assert set_policy_enabled(db, policy.policy_id, False, "admin-1", "admin", None, NOW).success is True
    assert retire_policy(db, policy.policy_id, "admin-1", "admin", None, NOW).success is True


# --- 20/21/22. change_hash determinism ------------------------------------------


def test_change_hash_deterministic():
    policy = _make_policy(policy_id="pol:hash-test")
    h1 = compute_policy_change_hash(policy)
    h2 = compute_policy_change_hash(policy)
    assert h1 == h2


def test_identical_policy_state_produces_identical_hash():
    policy_a = _make_policy(policy_id="pol:hash-a")
    policy_b = policy_a.model_copy()
    assert compute_policy_change_hash(policy_a) == compute_policy_change_hash(policy_b)


def test_semantic_change_produces_different_hash():
    policy_a = _make_policy(policy_id="pol:hash-c", risk_level="LOW")
    policy_b = policy_a.model_copy(update={"risk_level": "HIGH"})
    assert compute_policy_change_hash(policy_a) != compute_policy_change_hash(policy_b)


# --- 23. Sensitive fields cannot enter audit detail -----------------------------


def test_no_sensitive_fields_in_policy_audit(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", "no PII here", NOW)
    history = load_policy_audit_history(db, policy.policy_id)
    import json as _json
    serialized = _json.dumps(history, default=str).lower()
    for forbidden in ("phone", "email", "guest_name", "password", "token", "api_key", "secret", "credential"):
        assert forbidden not in serialized


def test_policy_audit_entry_schema_has_no_pii_fields():
    from app.modules.automation.schemas import PolicyAuditEntry
    forbidden = {"phone", "email", "guest_name", "guest_id", "password", "token", "api_key", "secret", "credential"}
    assert forbidden.isdisjoint(set(PolicyAuditEntry.model_fields.keys()))


# --- 24/25. Static guardrails ---------------------------------------------------


def _imported_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module)
    return names


def test_no_claude_dependency_in_governance():
    imported = _imported_names(governance)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in imported
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider", "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation", "app.modules.reports.narrative_provider_anthropic",
    ):
        assert forbidden_submodule not in imported


def test_no_http_client_in_governance():
    imported = _imported_names(governance)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
        assert forbidden not in imported


# --- 26-30. No business-table access --------------------------------------------


def test_governance_never_imports_business_modules():
    imported = _imported_names(governance)
    for forbidden in (
        "app.modules.bookings.models", "app.modules.public_booking.models",
        "app.modules.payments.models", "app.modules.finance.models", "app.modules.ota.models",
    ):
        assert forbidden not in imported


def test_governance_only_touches_its_own_tables():
    source = inspect.getsource(governance)
    for forbidden_table in ("booking_holds", "bookings", "payments", "gst", "ota_"):
        assert forbidden_table not in source.lower()


# --- 31/32/33. Execution/evaluation logic unchanged (structural proof) ----------


def test_policy_engine_module_untouched_by_governance():
    """Governance never imports policy_engine.py or action_executor.py —
    it cannot have changed their behavior since it doesn't even load
    them."""
    imported = _imported_names(governance)
    assert not any("policy_engine" in name for name in imported)
    assert not any("action_executor" in name for name in imported)
    assert not any("action_registry" in name for name in imported)


# --- Retirement (section 16) -----------------------------------------------------


def test_retired_policy_remains_queryable_not_deleted(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    result = retire_policy(db, policy.policy_id, "admin-1", "admin", "no longer needed", NOW)
    assert result.success is True

    still_there = load_policy(db, policy.policy_id)
    assert still_there is not None
    assert still_there.enabled is False

    history = load_policy_audit_history(db, policy.policy_id)
    assert "POLICY_RETIRED" in [e["event_type"] for e in history]


def test_retiring_twice_rejected(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", None, NOW)
    retire_policy(db, policy.policy_id, "admin-1", "admin", None, NOW)
    second = retire_policy(db, policy.policy_id, "admin-1", "admin", None, NOW)
    assert second.success is False
    assert second.reason == "already_retired"


# --- Pre-audit baseline (section 15) ---------------------------------------------


def test_establish_pre_audit_baseline_does_not_fabricate_created_event(db):
    policy = _make_policy()
    create_policy(db, policy, "admin-1", "admin", None, NOW)  # simulate a pre-existing policy
    result = establish_pre_audit_baseline(db, policy.policy_id, "admin-1", "admin", None, NOW)
    assert result.success is True

    history = load_policy_audit_history(db, policy.policy_id)
    event_types = [e["event_type"] for e in history]
    assert "POLICY_AUDIT_BASELINE_ESTABLISHED" in event_types
    # Never a second, fabricated POLICY_CREATED.
    assert event_types.count("POLICY_CREATED") == 1
