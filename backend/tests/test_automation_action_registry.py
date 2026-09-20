"""
Phase 15C — deterministic Action Registry tests.

No database, no HTTP, no LLM, no execution anywhere. Covers: the closed
registry (only RELEASE_EXPIRED_HOLD known, every other candidate action
type explicitly rejected), structural target/parameter validation,
determinism, RBAC reuse (no new role), and the separation from the
Policy Engine (registry validity is never authorization — Phase 15B's
evaluate_policy() remains the sole authority, unmodified).

Run with: pytest tests/test_automation_action_registry.py -v
"""
import ast
import inspect
from datetime import datetime, timezone

from app.modules.automation import action_registry
from app.modules.automation.action_registry import (
    ActionRegistryEntry,
    get_registry_entry,
    is_registered,
    list_registered_action_types,
    validate_proposal_structure,
)
from app.modules.automation.policy_engine import (
    compute_idempotency_key,
    compute_proposal_hash,
    evaluate_policy,
)
from app.modules.automation.schemas import ActionProposal
from app.modules.automation.schemas import Policy as AutomationPolicy


def _valid_hold_target(entity_id="11111111-1111-1111-1111-111111111111"):
    return {"entity_type": "booking_hold", "entity_id": entity_id}


def _make_release_hold_proposal(target=None, parameters=None, **overrides):
    target = target if target is not None else _valid_hold_target()
    parameters = parameters if parameters is not None else {}
    action_type = overrides.get("action_type", "RELEASE_EXPIRED_HOLD")
    proposal_hash = compute_proposal_hash(action_type, target, parameters)
    defaults = dict(
        action_id="action:1",
        action_type=action_type,
        policy_id="pol:release-expired-hold",
        risk_level="LOW",
        automation_level="L2",
        actor="system",
        target=target,
        parameters=parameters,
        idempotency_key=compute_idempotency_key(action_type, target, proposal_hash),
        approval_requirement="none",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status="PROPOSED",
        proposal_hash=proposal_hash,
    )
    defaults.update(overrides)
    return ActionProposal(**defaults)


# --- 1. RELEASE_EXPIRED_HOLD is registered ----------------------------------


def test_release_expired_hold_is_registered():
    assert is_registered("RELEASE_EXPIRED_HOLD") is True
    entry = get_registry_entry("RELEASE_EXPIRED_HOLD")
    assert entry is not None
    assert entry.action_type == "RELEASE_EXPIRED_HOLD"


# --- 2. Unknown action is rejected -------------------------------------------


def test_unknown_action_type_rejected():
    assert is_registered("SOME_MADE_UP_ACTION") is False
    assert get_registry_entry("SOME_MADE_UP_ACTION") is None


# --- 3-9. Closed registry: explicitly named high-risk actions are NOT registered


def test_price_change_not_registered():
    assert is_registered("PRICE_CHANGE") is False


def test_refund_not_registered():
    assert is_registered("REFUND") is False


def test_payment_capture_not_registered():
    assert is_registered("PAYMENT_CAPTURE") is False


def test_gst_adjustment_not_registered():
    assert is_registered("GST_ADJUSTMENT") is False


def test_booking_cancel_not_registered():
    assert is_registered("BOOKING_CANCEL") is False


def test_ota_actions_not_registered():
    assert is_registered("SYNC_RATE") is False
    assert is_registered("SYNC_AVAILABILITY") is False


def test_unit_block_not_registered():
    assert is_registered("UNIT_BLOCK") is False


def test_only_release_expired_hold_and_notify_staff_are_registered():
    """Phase 15C's original design closed the registry to exactly one
    entry, which is what this test asserted. Phase 16A deliberately,
    explicitly adds a second — NOTIFY_STAFF, shipped enabled=False (see
    action_registry.py's module docstring) — so the registry's closedness
    is still verified here, just against the now-current, intentionally
    widened set of two, rather than assuming no future phase ever adds
    another registered action type."""
    assert list_registered_action_types() == ["NOTIFY_STAFF", "RELEASE_EXPIRED_HOLD"]


# --- 10/11. Deterministic metadata --------------------------------------------


def test_registry_metadata_deterministic():
    e1 = get_registry_entry("RELEASE_EXPIRED_HOLD")
    e2 = get_registry_entry("RELEASE_EXPIRED_HOLD")
    assert e1 == e2


def test_same_action_lookup_returns_identical_metadata():
    e1 = get_registry_entry("RELEASE_EXPIRED_HOLD")
    e2 = get_registry_entry("RELEASE_EXPIRED_HOLD")
    assert e1.risk_level == e2.risk_level
    assert e1.automation_level == e2.automation_level
    assert e1.required_role == e2.required_role
    assert e1.policy_id == e2.policy_id


# --- 12/13. Risk / automation level -------------------------------------------


def test_risk_level_is_low():
    assert get_registry_entry("RELEASE_EXPIRED_HOLD").risk_level == "LOW"


def test_automation_level_is_l2():
    assert get_registry_entry("RELEASE_EXPIRED_HOLD").automation_level == "L2"


# --- 14/15. RBAC reuse, no new role --------------------------------------------


def test_required_role_uses_existing_rbac_role():
    entry = get_registry_entry("RELEASE_EXPIRED_HOLD")
    assert entry.required_role in ("admin", "staff")


def test_no_new_role_anywhere_in_registry():
    entry = get_registry_entry("RELEASE_EXPIRED_HOLD")
    forbidden_roles = {"automation_admin", "automation_service", "superadmin", "ai_agent", "claude"}
    assert entry.required_role.lower() not in forbidden_roles
    source = inspect.getsource(action_registry).lower()
    for forbidden in forbidden_roles:
        assert forbidden not in source, f"action_registry.py references invented role '{forbidden}'"


# --- 16. Idempotency required ---------------------------------------------------


def test_idempotency_required_true():
    assert get_registry_entry("RELEASE_EXPIRED_HOLD").idempotency_required is True


# --- 17/20. No arbitrary parameters accepted ------------------------------------


def test_arbitrary_parameters_rejected():
    proposal = _make_release_hold_proposal(parameters={"discount_percent": 10})
    result = validate_proposal_structure(proposal)
    assert result.valid is False
    assert result.reason == "parameters_invalid"


def test_valid_empty_parameters_accepted():
    proposal = _make_release_hold_proposal(parameters={})
    result = validate_proposal_structure(proposal)
    assert result.valid is True


# --- 18. Missing required target rejected ----------------------------------------


def test_missing_target_fields_rejected():
    proposal = _make_release_hold_proposal(target={})
    result = validate_proposal_structure(proposal)
    assert result.valid is False
    assert result.reason == "target_invalid"


# --- 19. Wrong target entity type rejected ----------------------------------------


def test_wrong_target_entity_type_rejected():
    proposal = _make_release_hold_proposal(target={"entity_type": "booking", "entity_id": "abc"})
    result = validate_proposal_structure(proposal)
    assert result.valid is False
    assert result.reason == "target_invalid"  # rejected by the Literal type itself


def test_arbitrary_table_name_not_permitted_as_target():
    proposal = _make_release_hold_proposal(target={"entity_type": "users", "entity_id": "abc"})
    result = validate_proposal_structure(proposal)
    assert result.valid is False


# --- 21. Wrong parameter types rejected --------------------------------------------


def test_wrong_target_field_type_rejected():
    # entity_id must be a str; an int must be rejected, not coerced.
    proposal_dict_target = {"entity_type": "booking_hold", "entity_id": 12345}
    proposal = _make_release_hold_proposal(target=proposal_dict_target)
    result = validate_proposal_structure(proposal)
    assert result.valid is False
    assert result.reason == "target_invalid"


# --- 22. Sensitive fields rejected / not represented --------------------------------


def test_sensitive_fields_rejected_if_supplied():
    proposal = _make_release_hold_proposal(
        target={"entity_type": "booking_hold", "entity_id": "abc", "guest_phone": "9999999999"}
    )
    result = validate_proposal_structure(proposal)
    assert result.valid is False
    assert result.reason == "target_invalid"


def test_sensitive_fields_not_represented_in_schemas():
    from app.modules.automation.schemas import ReleaseExpiredHoldParameters, ReleaseExpiredHoldTarget
    forbidden = {"phone", "email", "guest_name", "guest_phone", "guest_email", "password", "token", "card", "amount", "price"}
    target_fields = set(ReleaseExpiredHoldTarget.model_fields.keys())
    param_fields = set(ReleaseExpiredHoldParameters.model_fields.keys())
    assert forbidden.isdisjoint(target_fields)
    assert forbidden.isdisjoint(param_fields)


# --- 23-27. Static guardrails: registry does nothing but describe ------------------


def _imported_module_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module)  # also keep the full dotted path for submodule checks
    return names


def test_registry_does_not_execute_anything():
    forbidden_names = {"execute", "run_action", "perform_action", "action_executor"}
    module_attrs = {name.lower() for name in dir(action_registry)}
    assert forbidden_names.isdisjoint(module_attrs)


def test_registry_does_not_import_action_executor():
    imported = _imported_module_names(action_registry)
    assert not any("action_executor" in name for name in imported)


def test_registry_does_not_import_database_modules():
    imported = _imported_module_names(action_registry)
    for forbidden in ("sqlalchemy",):
        assert forbidden not in imported
    source = inspect.getsource(action_registry)
    assert "app.db" not in source
    assert "Session" not in source


def test_registry_does_not_import_http_clients():
    imported = _imported_module_names(action_registry)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
        assert forbidden not in imported


def test_registry_does_not_import_claude_or_narrative_modules():
    imported = _imported_module_names(action_registry)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in imported
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider",
        "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation",
    ):
        assert forbidden_submodule not in imported


# --- 28. No API router exists for this phase ----------------------------------------


def test_no_router_module_in_automation_package():
    import importlib
    import importlib.util
    spec = importlib.util.find_spec("app.modules.automation.router")
    assert spec is None, "an automation router module must not exist in Phase 15C"


def test_action_registry_has_no_fastapi_dependency():
    imported = _imported_module_names(action_registry)
    assert "fastapi" not in imported


# --- 29. No database mutation (covered above, restated explicitly) -----------------


def test_no_database_writes_possible():
    source = inspect.getsource(action_registry).lower()
    for forbidden in (".commit(", ".add(", ".execute(", "insert into", "update ", "delete from"):
        assert forbidden not in source


# --- 30-33. Policy Engine remains the sole authority --------------------------------


def test_structural_validity_alone_never_authorizes():
    """A structurally perfect proposal for a registered, enabled action
    must still be denied by the Policy Engine when global automation is
    explicitly off — registry validity is not authorization. Passes
    global_automation_enabled=False explicitly rather than relying on
    the module-level GLOBAL_AUTOMATION_ENABLED constant's current value,
    which is a business-decision-driven setting (Phase 15G) expected to
    change over time — this test's guarantee must not depend on it."""
    proposal = _make_release_hold_proposal()
    result = validate_proposal_structure(proposal)
    assert result.valid is True  # registry says: well-formed

    policy = AutomationPolicy(
        policy_id="pol:release-expired-hold", action_type="RELEASE_EXPIRED_HOLD",
        automation_level="L2", risk_level="LOW", conditions=[], required_role="staff",
        requires_approval=False, enabled=True, version=1,
    )
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=False)
    assert decision.outcome == "DENY"
    assert decision.reason == "global_automation_disabled"


def test_policy_disabled_overrides_valid_registry_entry():
    proposal = _make_release_hold_proposal()
    assert validate_proposal_structure(proposal).valid is True

    policy = AutomationPolicy(
        policy_id="pol:release-expired-hold", action_type="RELEASE_EXPIRED_HOLD",
        automation_level="L2", risk_level="LOW", conditions=[], required_role="staff",
        requires_approval=False, enabled=False, version=1,
    )
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "policy_disabled"


def test_structural_validation_result_has_no_authorization_field():
    """The registry's own result type cannot even express an ALLOW —
    proving, structurally, that it has no path to override a Policy
    Engine denial."""
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(action_registry.StructuralValidationResult)}
    forbidden = {"outcome", "allow", "allowed", "authorized", "permitted"}
    assert forbidden.isdisjoint(field_names)


def test_registry_module_never_imports_policy_engine():
    """The registry and the Policy Engine stay decoupled siblings — the
    registry cannot call evaluate_policy() and therefore cannot
    duplicate or shortcut its decision logic."""
    imported = _imported_module_names(action_registry)
    assert not any("policy_engine" in name for name in imported)


# --- 34/35/36. No invented financial/pricing/confirmation rule ----------------------


def test_no_financial_amount_in_registry_entry():
    import dataclasses
    entry = get_registry_entry("RELEASE_EXPIRED_HOLD")
    for f in dataclasses.fields(entry):
        value = getattr(entry, f.name)
        assert not isinstance(value, float), f"unexpected float field '{f.name}' on the registry entry"
    forbidden_field_names = {"amount", "price", "discount", "refund_limit", "payment_limit"}
    field_names = {f.name for f in dataclasses.fields(entry)}
    assert forbidden_field_names.isdisjoint(field_names)


def test_no_pricing_threshold_anywhere_in_module():
    source = inspect.getsource(action_registry).lower()
    for forbidden in ("minimum_price", "maximum_price", "min_price", "max_price", "discount_percent", "elasticity"):
        assert forbidden not in source


def test_no_invented_booking_confirmation_rule():
    source = inspect.getsource(action_registry).lower()
    for forbidden in ("confirm_booking", "confirmation_threshold", "auto_confirm"):
        assert forbidden not in source
