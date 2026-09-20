"""
Phase 15F — production-readiness / activation-gate audit tests.

These are genuinely NEW checks not already covered by Phase 15B-15E-2's
test suites: structural proof that GLOBAL_AUTOMATION_ENABLED has no
environment-variable/config-parsing path at all (so no env var can ever
flip it), proof that no live HTTP/scheduler entry point into the
automation system exists anywhere in the app, that L2 execution's audit
actor is always exactly "system" (never a Claude/AI identity), and a
consolidated secret/Claude-isolation sweep across the whole automation
package as one unit rather than per-module.

Sections 6-13 of the Phase 15F brief (kill-switch, per-action-policy,
expired-hold, stale-state, conversion-race, concurrency, restart,
audit-completeness) are already comprehensively covered by
test_automation_action_executor.py's 30 tests — this file does not
duplicate them; it is re-run fresh as part of the same verification
pass instead (see the final report for the explicit mapping).

Run with: pytest tests/test_automation_production_readiness.py -v
"""
import ast
import inspect
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.modules.automation import action_executor, action_registry, approval_workflow, persistence, policy_engine, schemas


def _module_source(module) -> str:
    return inspect.getsource(module)


def _imported_names(module) -> set[str]:
    tree = ast.parse(_module_source(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module)
    return names


# --- 1. GLOBAL_AUTOMATION_ENABLED has no environment/config parsing path -----------


def test_global_switch_is_a_hardcoded_constant_not_env_backed():
    """The substantive claim under test is the absence of any
    environment/config-parsing path — not a specific boolean value.
    GLOBAL_AUTOMATION_ENABLED's current value is a business-decision
    setting (see Phase 15G, which activated it in production for
    RELEASE_EXPIRED_HOLD only) and is expected to change over time; this
    test must not hardcode an expectation of False."""
    source = _module_source(policy_engine)
    for forbidden in ("os.environ", "getenv", "Settings", "settings.", "pydantic_settings", "BaseSettings"):
        assert forbidden not in source, (
            f"policy_engine.py references '{forbidden}' — the global switch "
            "must have no environment-variable or config-fallback path at all"
        )
    assert isinstance(policy_engine.GLOBAL_AUTOMATION_ENABLED, bool)


def test_global_switch_default_parameter_matches_constant():
    import inspect as _inspect
    sig = _inspect.signature(policy_engine.evaluate_policy)
    assert sig.parameters["global_automation_enabled"].default is policy_engine.GLOBAL_AUTOMATION_ENABLED


# --- 2. No live entry point (router/scheduler) into the automation system ---------


def test_no_automation_router_module_exists():
    import importlib.util
    assert importlib.util.find_spec("app.modules.automation.router") is None


def test_main_app_never_references_automation():
    import app.main as main_module
    source = _module_source(main_module)
    assert "automation" not in source.lower()


def test_no_scheduler_or_cron_wiring_in_automation_package():
    for module in (action_executor, action_registry, approval_workflow, persistence, policy_engine):
        source = _module_source(module)
        for forbidden in ("apscheduler", "BackgroundScheduler", "celery", "@app.on_event", "cron"):
            assert forbidden not in source, f"{module.__name__} references '{forbidden}'"


# --- 19. RBAC: L2 execution actor is always exactly "system" -----------------------


def test_execution_audit_actor_is_always_system(tmp_path=None):
    """Re-derives the same real release scenario as Phase 15E-2 and
    checks every execution-related audit entry's actor value, proving no
    Claude/AI/LLM/assistant identity can ever appear as an actor for
    autonomous execution."""
    import uuid
    from datetime import timedelta
    from unittest.mock import patch

    from fastapi.testclient import TestClient
    from app.main import app
    from app.modules.automation.action_executor import execute_release_expired_hold
    from app.modules.automation.persistence import load_audit_trail, persist_proposal, update_proposal_status
    from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash
    from app.modules.automation.schemas import ActionProposal
    from app.modules.automation.schemas import Policy as AutomationPolicy
    from app.modules.public_booking.models import BookingHold

    from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"ROTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"ROUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()

    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    from datetime import date
    check_in = date.today() + timedelta(days=950)
    check_out = check_in + timedelta(days=2)
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": unit["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "guest_name": "RO Guest", "guest_phone": f"77{uuid.uuid4().int % 10**8:08d}", "guest_email": "ro@example.com",
        })
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]

    db = SessionLocal()
    try:
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        hold.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

        target = {"entity_type": "booking_hold", "entity_id": hold_id}
        h = compute_proposal_hash("RELEASE_EXPIRED_HOLD", target, {})
        k = compute_idempotency_key("RELEASE_EXPIRED_HOLD", target, h)
        proposal = ActionProposal(
            action_id=f"action:{uuid.uuid4().hex}", action_type="RELEASE_EXPIRED_HOLD",
            policy_id="pol:release-expired-hold", risk_level="LOW", automation_level="L2",
            actor="system", target=target, parameters={}, idempotency_key=k,
            approval_requirement="none", timestamp=datetime.now(timezone.utc),
            status="PROPOSED", proposal_hash=h,
        )
        persist_proposal(db, proposal)
        update_proposal_status(db, proposal.action_id, "APPROVED", "system", datetime.now(timezone.utc))

        policy = AutomationPolicy(
            policy_id="pol:release-expired-hold", action_type="RELEASE_EXPIRED_HOLD",
            automation_level="L2", risk_level="LOW", conditions=[], required_role="staff",
            requires_approval=False, enabled=True, version=1,
        )
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        trail = load_audit_trail(db, proposal.action_id)
        execution_events = [e for e in trail if e["event_type"] in
                             ("POLICY_EVALUATED", "EXECUTION_STARTED", "EXECUTION_SUCCEEDED")]
        assert len(execution_events) >= 3
        for event in execution_events:
            assert event["actor"] == "system"
            assert event["actor"].lower() not in ("claude", "ai", "llm", "assistant", "anthropic")
    finally:
        db.close()


# --- 15/16/17. Consolidated financial / Claude / secret isolation sweep -----------


def test_no_financial_module_imported_anywhere_in_automation_package():
    for module in (action_executor, action_registry, approval_workflow, persistence, policy_engine, schemas):
        imported = _imported_names(module)
        for forbidden in (
            "app.modules.payments.models", "app.modules.finance.models",
            "app.modules.payment_gateway.models", "app.modules.ota.models",
        ):
            assert forbidden not in imported, f"{module.__name__} imports '{forbidden}'"


def test_no_claude_dependency_anywhere_in_automation_package():
    for module in (action_executor, action_registry, approval_workflow, persistence, policy_engine, schemas):
        imported = _imported_names(module)
        for forbidden in ("anthropic", "openai", "google"):
            assert forbidden not in imported, f"{module.__name__} imports '{forbidden}'"
        for forbidden_submodule in (
            "app.modules.reports.narrative_provider", "app.modules.reports.narrative_service",
            "app.modules.reports.narrative_validation", "app.modules.reports.narrative_provider_anthropic",
        ):
            assert forbidden_submodule not in imported, f"{module.__name__} imports '{forbidden_submodule}'"


def test_no_credential_field_anywhere_in_automation_schemas():
    forbidden_field_names = {"api_key", "apikey", "authorization", "token", "secret", "credential", "password"}
    for name, cls in vars(schemas).items():
        if isinstance(cls, type) and hasattr(cls, "model_fields"):
            fields = set(cls.model_fields.keys())
            assert forbidden_field_names.isdisjoint(fields), f"schemas.{name} has a credential-shaped field"


def test_no_http_client_anywhere_in_automation_package():
    for module in (action_executor, action_registry, approval_workflow, persistence, policy_engine, schemas):
        imported = _imported_names(module)
        for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
            assert forbidden not in imported, f"{module.__name__} imports '{forbidden}'"
