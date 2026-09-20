"""
Phase 15B — deterministic Policy Engine tests.

No database, no HTTP, no LLM — every test constructs Policy/ActionProposal
objects directly in memory and calls evaluate_policy() as a pure
function. Static source-scan tests (28/29/30) prove the module itself
never imports anything that could reach a database, an external HTTP
endpoint, or an LLM provider.

Run with: pytest tests/test_automation_policy_engine.py -v
"""
import inspect
from datetime import datetime, timezone

import pytest

from app.modules.automation import policy_engine
from app.modules.automation.policy_engine import (
    compute_idempotency_key,
    compute_proposal_hash,
    evaluate_policy,
)
from app.modules.automation.schemas import ActionProposal, Condition, Policy


def _make_policy(**overrides) -> Policy:
    defaults = dict(
        policy_id="pol:release-expired-hold",
        action_type="RELEASE_EXPIRED_HOLD",
        automation_level="L2",
        risk_level="LOW",
        conditions=[],
        required_role="staff",
        requires_approval=False,
        enabled=True,
        version=1,
    )
    defaults.update(overrides)
    return Policy(**defaults)


def _make_proposal(policy: Policy, target=None, parameters=None, **overrides) -> ActionProposal:
    target = target if target is not None else {"booking_hold_id": "hold-1"}
    parameters = parameters if parameters is not None else {}
    action_type = overrides.get("action_type", policy.action_type)
    proposal_hash = overrides.get("proposal_hash")
    if proposal_hash is None:
        proposal_hash = compute_proposal_hash(action_type, target, parameters)
    idem_key = compute_idempotency_key(action_type, target, proposal_hash)
    defaults = dict(
        action_id="action:1",
        action_type=policy.action_type,
        policy_id=policy.policy_id,
        risk_level=policy.risk_level,
        automation_level=policy.automation_level,
        actor="system",
        target=target,
        parameters=parameters,
        idempotency_key=idem_key,
        approval_requirement="none",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status="PROPOSED",
        proposal_hash=proposal_hash,
    )
    defaults.update(overrides)
    return ActionProposal(**defaults)


# --- 1. Determinism -----------------------------------------------------


def test_same_input_same_output():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    d1 = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    d2 = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert d1.outcome == d2.outcome == "ALLOW"
    assert d1.reason == d2.reason
    assert d1.decision_id == d2.decision_id
    assert d1.evaluated_conditions == d2.evaluated_conditions


# --- 2. Unknown policy ----------------------------------------------------


def test_unknown_policy_denied():
    policy = _make_policy()
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, None, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "policy_not_found"


# --- 3. Disabled policy -----------------------------------------------------


def test_disabled_policy_denied():
    policy = _make_policy(enabled=False)
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "policy_disabled"


# --- 4. Global automation disabled ------------------------------------------


def test_global_automation_disabled_denies_execution():
    """Tests the mechanism itself via an explicit False, rather than
    relying on the module-level GLOBAL_AUTOMATION_ENABLED constant's
    current value — that constant is a business-decision-driven setting
    (Phase 15G activated it in production for RELEASE_EXPIRED_HOLD only)
    and is expected to change over time; this test's safety guarantee
    (an explicitly-off switch always denies) must not depend on it."""
    policy = _make_policy()
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=False)
    assert decision.outcome == "DENY"
    assert decision.reason == "global_automation_disabled"


def test_global_disabled_overrides_policy_enabled_true():
    policy = _make_policy(enabled=True)
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=False)
    assert decision.outcome == "DENY"
    assert decision.reason == "global_automation_disabled"


# --- 5. Action type mismatch ------------------------------------------------


def test_action_type_mismatch_denied():
    policy = _make_policy()
    proposal = _make_proposal(policy, action_type="SOME_OTHER_ACTION")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "action_type_mismatch"


# --- 6. Policy ID mismatch --------------------------------------------------


def test_policy_id_mismatch_denied():
    policy = _make_policy()
    proposal = _make_proposal(policy, policy_id="pol:different")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "policy_id_mismatch"


# --- 7. Invalid risk level (mismatch with policy) ---------------------------


def test_risk_level_mismatch_denied():
    policy = _make_policy(risk_level="LOW")
    proposal = _make_proposal(policy, risk_level="HIGH")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "risk_level_mismatch"


# --- 8. Invalid automation level (mismatch with policy) ---------------------


def test_automation_level_mismatch_denied():
    policy = _make_policy(automation_level="L2")
    proposal = _make_proposal(policy, automation_level="L3")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "automation_level_mismatch"


# --- 9. Unauthorized role ----------------------------------------------------


def test_unauthorized_role_denied():
    policy = _make_policy(required_role="admin")
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "role_not_permitted"


def test_admin_satisfies_staff_required_role():
    policy = _make_policy(required_role="staff")
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "admin", {}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


# --- 10-17. Condition operators ----------------------------------------------


def test_condition_eq_pass():
    policy = _make_policy(conditions=[Condition(field="status", operator="eq", value="expired")])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"status": "expired"}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


def test_condition_eq_fail():
    policy = _make_policy(conditions=[Condition(field="status", operator="eq", value="expired")])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"status": "active"}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "condition_failed:status"


def test_condition_ne():
    policy = _make_policy(conditions=[Condition(field="status", operator="ne", value="paid")])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"status": "expired"}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


def test_condition_gt():
    policy = _make_policy(conditions=[Condition(field="hours_open", operator="gt", value=24)])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"hours_open": 25}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


def test_condition_gte():
    policy = _make_policy(conditions=[Condition(field="hours_open", operator="gte", value=24)])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"hours_open": 24}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


def test_condition_lt():
    policy = _make_policy(conditions=[Condition(field="hours_remaining", operator="lt", value=1)])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"hours_remaining": 0}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


def test_condition_lte():
    policy = _make_policy(conditions=[Condition(field="hours_remaining", operator="lte", value=0)])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"hours_remaining": 0}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


def test_condition_in():
    policy = _make_policy(conditions=[Condition(field="status", operator="in", value=["expired", "cancelled"])])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"status": "expired"}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"


# --- 18. Missing condition field ---------------------------------------------


def test_missing_condition_field_denied():
    policy = _make_policy(conditions=[Condition(field="status", operator="eq", value="expired")])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "condition_missing_data:status"
    assert decision.evaluated_conditions[0].data_present is False


# --- 19/20. Multiple conditions ----------------------------------------------


def test_multiple_conditions_all_pass():
    policy = _make_policy(conditions=[
        Condition(field="status", operator="eq", value="expired"),
        Condition(field="hours_open", operator="gt", value=1),
    ])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"status": "expired", "hours_open": 5}, global_automation_enabled=True)
    assert decision.outcome == "ALLOW"
    assert len(decision.evaluated_conditions) == 2
    assert all(c.passed for c in decision.evaluated_conditions)


def test_multiple_conditions_one_fails():
    policy = _make_policy(conditions=[
        Condition(field="status", operator="eq", value="expired"),
        Condition(field="hours_open", operator="gt", value=100),
    ])
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {"status": "expired", "hours_open": 5}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "condition_failed:hours_open"
    # Full audit trail: both conditions are present, not just the failing one.
    assert len(decision.evaluated_conditions) == 2
    assert decision.evaluated_conditions[0].passed is True
    assert decision.evaluated_conditions[1].passed is False


# --- 21. requires_approval=true -----------------------------------------------


def test_requires_approval_true_returns_require_approval():
    policy = _make_policy(requires_approval=True, automation_level="L3")
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "REQUIRE_APPROVAL"
    assert decision.reason == "policy_requires_approval"


# --- 22. L4 can never silently become ALLOW -----------------------------------


def test_l4_never_allows_even_if_requires_approval_false():
    policy = _make_policy(automation_level="L4", requires_approval=False)
    proposal = _make_proposal(policy, automation_level="L4")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "REQUIRE_APPROVAL"
    assert decision.reason == "l4_always_requires_approval"


def test_l4_with_requires_approval_true_also_never_allows():
    policy = _make_policy(automation_level="L4", requires_approval=True)
    proposal = _make_proposal(policy, automation_level="L4")
    decision = evaluate_policy(proposal, policy, "admin", {}, global_automation_enabled=True)
    assert decision.outcome == "REQUIRE_APPROVAL"
    assert decision.outcome != "ALLOW"


# --- 23/24/25. proposal_hash determinism --------------------------------------


def test_proposal_hash_deterministic():
    h1 = compute_proposal_hash("RELEASE_EXPIRED_HOLD", {"a": 1}, {"x": "y"})
    h2 = compute_proposal_hash("RELEASE_EXPIRED_HOLD", {"a": 1}, {"x": "y"})
    assert h1 == h2


def test_proposal_hash_ignores_key_order():
    h1 = compute_proposal_hash("RELEASE_EXPIRED_HOLD", {"a": 1, "b": 2}, {"x": "y", "z": "w"})
    h2 = compute_proposal_hash("RELEASE_EXPIRED_HOLD", {"b": 2, "a": 1}, {"z": "w", "x": "y"})
    assert h1 == h2


def test_proposal_hash_changes_with_parameters():
    h1 = compute_proposal_hash("RELEASE_EXPIRED_HOLD", {"a": 1}, {"x": "y"})
    h2 = compute_proposal_hash("RELEASE_EXPIRED_HOLD", {"a": 1}, {"x": "different"})
    assert h1 != h2


# --- 26. Idempotency key determinism -------------------------------------------


def test_idempotency_key_deterministic():
    h = compute_proposal_hash("RELEASE_EXPIRED_HOLD", {"a": 1}, {})
    k1 = compute_idempotency_key("RELEASE_EXPIRED_HOLD", {"a": 1}, h)
    k2 = compute_idempotency_key("RELEASE_EXPIRED_HOLD", {"a": 1}, h)
    assert k1 == k2


# --- 27. Policy.version copied exactly ------------------------------------------


@pytest.mark.parametrize("version", [1, 2, 42])
def test_policy_version_copied_exactly(version):
    policy = _make_policy(version=version)
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.policy_version == version


def test_invalid_policy_version_denied():
    policy = _make_policy(version=0)
    proposal = _make_proposal(policy)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "invalid_policy_version"


# --- proposal structural validity (hash tamper detection) ----------------------


def test_tampered_proposal_hash_denied():
    policy = _make_policy()
    proposal = _make_proposal(policy, proposal_hash="not-the-real-hash")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "proposal_hash_mismatch"


# --- 28/29/30. Static source-scan: no LLM, no DB, no HTTP ----------------------
#
# Scans only the module's actual `import` statements (via ast), not
# prose/docstrings/comments — the module's own docstrings intentionally
# explain what it does NOT do (e.g. "no database session, no LLM call"),
# which would trip a naive substring search on the raw source text.


def _imported_module_names(module) -> set[str]:
    import ast as _ast

    tree = _ast.parse(inspect.getsource(module))
    names = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_llm_invocation_in_source():
    imported = _imported_module_names(policy_engine)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in imported, f"policy_engine.py imports '{forbidden}'"
    # Never imports anything from the narrative layer either — Claude has
    # zero wiring into this module, not even indirectly.
    full_source = inspect.getsource(policy_engine)
    assert "narrative_provider" not in full_source
    assert "narrative_service" not in full_source


def test_no_database_mutation_in_source():
    imported = _imported_module_names(policy_engine)
    for forbidden in ("sqlalchemy",):
        assert forbidden not in imported, f"policy_engine.py imports '{forbidden}'"
    full_source = inspect.getsource(policy_engine)
    assert "app.db" not in full_source
    assert "Session" not in full_source


def test_no_external_http_calls_in_source():
    imported = _imported_module_names(policy_engine)
    for forbidden in ("httpx", "requests", "urllib", "socket", "aiohttp"):
        assert forbidden not in imported, f"policy_engine.py imports '{forbidden}'"


# --- 31. Sensitive fields never leak into the decision --------------------------


def test_sensitive_evaluation_data_does_not_leak_into_decision():
    policy = _make_policy(conditions=[Condition(field="status", operator="eq", value="expired")])
    proposal = _make_proposal(policy)
    evaluation_data = {
        "status": "expired",
        "guest_phone": "9999999999",
        "guest_email": "guest@example.com",
        "password": "hunter2",
        "api_key": "sk-fake-not-real",
    }
    decision = evaluate_policy(proposal, policy, "staff", evaluation_data, global_automation_enabled=True)
    serialized = decision.model_dump_json().lower()
    for forbidden in ("9999999999", "guest@example.com", "hunter2", "sk-fake-not-real", "guest_phone", "guest_email", "password", "api_key"):
        assert forbidden not in serialized


def test_policy_engine_signature_has_no_credential_parameter():
    import inspect as _inspect
    sig = _inspect.signature(evaluate_policy)
    forbidden_names = {"api_key", "token", "secret", "credential", "password"}
    assert forbidden_names.isdisjoint(set(sig.parameters.keys()))
