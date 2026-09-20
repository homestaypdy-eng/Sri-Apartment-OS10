"""
Phase 16C — deterministic signal -> NOTIFY_STAFF proposal generator tests.

Covers the 20-item test matrix from the Phase 16C spec. Item 20 ("full
backend regression") is not a function in this file — it is satisfied by
running the complete suite separately, in a disposable environment,
after this file passes on its own.

Run with: pytest tests/test_automation_signal_generator.py -v
"""
import json
import threading
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.main import app  # noqa: F401 -- full model registration, see test_automation_notify_staff.py
from app.modules.automation.action_registry import get_registry_entry
from app.modules.automation.models import ActionProposalRow
from app.modules.automation.persistence import load_audit_trail, load_proposal, persist_proposal
from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash, evaluate_policy
from app.modules.automation.schemas import ActionProposal
from app.modules.automation.schemas import Policy as AutomationPolicy
from app.modules.automation.signal_generator import generate_notify_staff_proposal
from app.modules.reports.decision_engine import run_decision_engine
from app.modules.reports.schemas import AIStrategistBundle, AIStrategistSignal

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_maintenance_signal(ticket_id=None, severity="WARNING", statement=None, **overrides):
    ticket_id = ticket_id or str(uuid.uuid4())
    defaults = dict(
        signal_id=f"attention:maintenance:{ticket_id}",
        category="maintenance",
        severity=severity,
        classification="FACT",
        statement=statement or "Urgent priority ticket, open 50h.",
        source_fields=["needs_attention[0].category", "needs_attention[0].severity", "needs_attention[0].reason"],
        evidence=[],
        confidence="HIGH",
    )
    defaults.update(overrides)
    return AIStrategistSignal(**defaults)


def _make_non_eligible_signal(category="gst", severity="INFO"):
    return AIStrategistSignal(
        signal_id=f"{category}:test:{uuid.uuid4().hex[:8]}",
        category=category, severity=severity, classification="FACT",
        statement="GST payable this period is zero.",
        source_fields=["gst.gst_payable"], evidence=[], confidence="HIGH",
    )


def _make_notify_policy(**overrides):
    defaults = dict(
        policy_id="notify_staff", action_type="NOTIFY_STAFF", automation_level="L2", risk_level="MEDIUM",
        conditions=[], required_role="staff", requires_approval=False, enabled=True, version=1,
    )
    defaults.update(overrides)
    return AutomationPolicy(**defaults)


def _minimal_bundle_json() -> dict:
    """Same shape as test_ai_strategist_decisions.py's own helper — a
    structurally-valid, entirely empty AIStrategistBundle, extended here
    with one real maintenance needs_attention item so the full
    decision-engine -> generator integration path can be exercised."""
    today = date.today().isoformat()
    period = {"date_from": today, "date_to": today}
    return {
        "generated_at": "2026-01-01T00:00:00",
        "business_health": {
            "current_period": period, "previous_period": period,
            "revenue_current": 0.0, "revenue_previous": 0.0,
            "revenue_definition": "rate_applied x nights + extra_guest_charge",
            "occupancy": {"current": 0.0, "previous": 0.0, "change_percent": None, "change_percentage_points": None},
            "adr": {"current": 0.0, "previous": 0.0, "change_percent": None, "change_percentage_points": None},
            "revpar": {"current": 0.0, "previous": 0.0, "change_percent": None, "change_percentage_points": None},
        },
        "needs_attention": [
            {
                "category": "maintenance", "severity": "warning",
                "title": "Urgent maintenance — Unit 2A: AC not cooling",
                "reason": "Urgent priority ticket, open 50h.",
                "action": "Assign and resolve.",
                "reference_id": str(uuid.uuid4()),
                "link": "/maintenance",
            },
        ],
        "cancellations": {
            "current_period": period, "previous_period": period,
            "total_bookings_in_period": 0, "cancelled_count": 0, "cancellation_rate_percent": 0.0,
            "no_show_count": 0, "no_show_rate_percent": 0.0,
        },
        "outstanding_payments": {"total_pending_balance": 0.0, "pending_count": 0, "overdue_count": 0, "overdue_amount": 0.0},
        "unit_performance": {"current_period": period, "top_units": [], "bottom_units": []},
        "booking_sources": {"current_period": period, "revenue_by_source": {}, "revenue_definition": "rate_applied x nights + extra_guest_charge"},
        "top_guests": [],
        "maintenance": {"current_period": period, "maintenance_cost_total": 0.0},
        "gst": {"current_period": period, "gst_collected": 0.0, "gst_input_credit_eligible": 0.0, "gst_payable": 0.0},
        "weekly_facts": [],
        "finance": {
            "period": period,
            "source_note": "test fixture",
            "pnl": {
                "period_year": date.today().year, "period_month": date.today().month, "period_note": "test fixture",
                "revenue": 0.0, "operating_expenses": None, "central_expenses": None, "net_profit": None, "properties_included": 0,
            },
            "cash_flow": {
                "date_from": today, "date_to": today,
                "cash_in_payments": 0.0, "cash_in_deposits": 0.0, "cash_in_ota_payouts": 0.0, "cash_in_total": 0.0,
                "cash_out_expenses": 0.0, "cash_out_withdrawals": 0.0, "cash_out_deposit_refunds": 0.0, "cash_out_loan_emi": 0.0,
                "cash_out_total": 0.0, "net_cash_flow": 0.0, "cash_in_bank": 0.0, "cash_in_hand": 0.0,
            },
            "budget": {"period_year": date.today().year, "period_month": date.today().month, "total_budgeted": 0.0, "total_actual": 0.0, "lines": []},
            "loans": {
                "total_active_loans": 0, "total_monthly_emi_obligation": 0.0,
                "upcoming_emi_count": 0, "upcoming_emi_amount": 0.0,
                "overdue_emi_count": 0, "overdue_emi_amount": 0.0,
                "paid_this_month_count": 0, "paid_this_month_amount": 0.0,
                "outstanding_this_month_count": 0, "outstanding_this_month_amount": 0.0,
                "active_loans": [],
            },
            "ota": {
                "commission_total": 0.0, "gst_total": 0.0, "expected_payout_total": 0.0, "actual_received_total": 0.0,
                "reconciled_count": 0, "unreconciled_count": 0, "discrepancy_count": 0, "discrepancy_total": 0.0,
                "missing_commission_count": 0, "missing_commission_gross_total": 0.0,
            },
            "expenses": {"payroll_total": 0.0, "utilities_total": 0.0, "purchases_total": 0.0},
            "outstanding_obligations": {
                "emi_overdue_amount": 0.0, "emi_overdue_count": 0, "emi_due_this_month_amount": 0.0,
                "ota_unreconciled_expected_payout": 0.0, "guest_outstanding_balance": 0.0,
            },
        },
    }


# --- 1. Same input -> same proposal (deterministic hash/key) --------------------------


def test_same_input_produces_same_hash_and_idempotency_key():
    ticket_id = str(uuid.uuid4())
    sig_a = _make_maintenance_signal(ticket_id=ticket_id, severity="CRITICAL", statement="Open for 80h — past the 48h target for urgent priority.")
    sig_b = _make_maintenance_signal(ticket_id=ticket_id, severity="CRITICAL", statement="Open for 80h — past the 48h target for urgent priority.")

    target_a = {"entity_type": "staff_notification", "entity_id": sig_a.signal_id}
    params_a = {"message": sig_a.statement, "category": "maintenance", "severity": sig_a.severity, "source_id": sig_a.signal_id}
    target_b = {"entity_type": "staff_notification", "entity_id": sig_b.signal_id}
    params_b = {"message": sig_b.statement, "category": "maintenance", "severity": sig_b.severity, "source_id": sig_b.signal_id}

    h_a = compute_proposal_hash("NOTIFY_STAFF", target_a, params_a)
    h_b = compute_proposal_hash("NOTIFY_STAFF", target_b, params_b)
    assert h_a == h_b
    assert compute_idempotency_key("NOTIFY_STAFF", target_a, h_a) == compute_idempotency_key("NOTIFY_STAFF", target_b, h_b)


# --- 2. No eligible signal -> no proposal ----------------------------------------------


def test_non_eligible_signal_produces_no_proposal(db):
    before = db.execute(select(func.count()).select_from(ActionProposalRow)).scalar()
    signal = _make_non_eligible_signal(category="gst", severity="INFO")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is False
    assert result.reason == "signal_not_eligible"
    assert result.action_id is None
    after = db.execute(select(func.count()).select_from(ActionProposalRow)).scalar()
    assert after == before


def test_maintenance_info_severity_not_eligible(db):
    """Attention items only ever emit WARNING/CRITICAL (see
    decision_engine.py's _ATTENTION_SEVERITY_MAP) — INFO/WATCH maintenance
    signals should never occur in practice, but the generator must still
    reject them defensively rather than assume the caller's severity."""
    signal = _make_maintenance_signal(severity="INFO")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is False
    assert result.reason == "signal_not_eligible"


# --- 3. Valid eligible signal -> proposal created ---------------------------------------


def test_valid_maintenance_signal_creates_proposal(db):
    signal = _make_maintenance_signal(severity="WARNING")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is True
    assert result.reason == "created"
    assert result.action_id is not None

    proposal = load_proposal(db, result.action_id)
    assert proposal.action_type == "NOTIFY_STAFF"
    assert proposal.status == "PROPOSED"
    assert proposal.target == {"entity_type": "staff_notification", "entity_id": signal.signal_id}
    assert proposal.parameters == {
        "message": signal.statement, "category": "maintenance",
        "severity": "WARNING", "source_id": signal.signal_id,
    }


# --- 4. Duplicate signal -> no duplicate proposal ---------------------------------------


def test_duplicate_signal_does_not_create_second_proposal(db):
    signal = _make_maintenance_signal(severity="CRITICAL")
    first = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    second = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)

    assert first.created is True
    assert second.created is False
    assert second.reason == "duplicate_action_id_or_idempotency_key"

    count = db.execute(
        select(func.count()).select_from(ActionProposalRow).where(ActionProposalRow.idempotency_key == compute_idempotency_key(
            "NOTIFY_STAFF",
            {"entity_type": "staff_notification", "entity_id": signal.signal_id},
            compute_proposal_hash(
                "NOTIFY_STAFF",
                {"entity_type": "staff_notification", "entity_id": signal.signal_id},
                {"message": signal.statement, "category": "maintenance", "severity": signal.severity, "source_id": signal.signal_id},
            ),
        ))
    ).scalar()
    assert count == 1


# --- 5. Wrong target rejected via the shared, reused validation path -------------------


def test_wrong_target_rejected_via_shared_persist_proposal_path(db):
    """Proves the generator relies on the SAME structural validation
    every other action type goes through (persist_proposal ->
    validate_proposal_structure) rather than a weaker or duplicated
    check of its own -- constructed directly, bypassing the generator,
    to show the shared gate itself is what rejects a bad target."""
    bad_target = {"entity_type": "unit", "entity_id": str(uuid.uuid4())}
    parameters = {"message": "test", "category": "maintenance", "severity": "WARNING", "source_id": "x"}
    h = compute_proposal_hash("NOTIFY_STAFF", bad_target, parameters)
    k = compute_idempotency_key("NOTIFY_STAFF", bad_target, h)
    proposal = ActionProposal(
        action_id=f"action:{uuid.uuid4().hex}", action_type="NOTIFY_STAFF", policy_id="notify_staff",
        risk_level="MEDIUM", automation_level="L2", actor="system", target=bad_target, parameters=parameters,
        idempotency_key=k, approval_requirement="none", timestamp=NOW, status="PROPOSED", proposal_hash=h,
    )
    result = persist_proposal(db, proposal)
    assert result.success is False
    # entity_type is a closed Literal["staff_notification"] on
    # NotifyStaffTarget, so an invalid value fails schema validation
    # itself (target_invalid) -- it never reaches the separate
    # target_entity_type_mismatch check (which exists for a schema that
    # validates but whose entity_type field isn't itself a Literal).
    assert result.reason == "target_invalid"


# --- 6. Invalid parameters (oversized statement) rejected by the generator itself -------


def test_oversized_statement_rejected_by_generator(db):
    """AIStrategistSignal.statement has no length cap, but
    NotifyStaffParameters.message does (max 500). A pathological signal
    whose statement exceeds that must be caught by the generator's own
    defensive validation before ever reaching persist_proposal."""
    before = db.execute(select(func.count()).select_from(ActionProposalRow)).scalar()
    signal = _make_maintenance_signal(statement="x" * 501)
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is False
    assert result.reason == "invalid_shape"
    after = db.execute(select(func.count()).select_from(ActionProposalRow)).scalar()
    assert after == before


# --- 7. Policy disabled -> denied (downstream, on a generator-created proposal) --------


def test_generator_created_proposal_denied_when_policy_disabled(db):
    signal = _make_maintenance_signal(severity="WARNING")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is True

    proposal = load_proposal(db, result.action_id)
    policy = _make_notify_policy(enabled=False)
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "policy_disabled"


# --- 8. Global automation disabled -> denied (downstream) ------------------------------


def test_generator_created_proposal_denied_when_global_automation_disabled(db):
    signal = _make_maintenance_signal(severity="CRITICAL")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is True

    proposal = load_proposal(db, result.action_id)
    policy = _make_notify_policy()
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=False)
    assert decision.outcome == "DENY"
    assert decision.reason == "global_automation_disabled"


# --- 9. Policy version/id drift -> denied (downstream) ----------------------------------


def test_generator_created_proposal_denied_on_policy_id_mismatch(db):
    signal = _make_maintenance_signal(severity="WARNING")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    proposal = load_proposal(db, result.action_id)

    different_policy = _make_notify_policy(policy_id="pol:different")
    decision = evaluate_policy(proposal, different_policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "policy_id_mismatch"


# --- 10. Stale (tampered) proposal -> denied ---------------------------------------------


def test_tampered_generator_created_proposal_denied_on_hash_mismatch(db):
    """Simulates staleness: the persisted proposal's target/parameters no
    longer match its own proposal_hash (e.g. if a row were ever mutated
    out from under the pipeline). evaluate_policy's existing Rule 10
    catches this on any proposal, generator-created or not."""
    signal = _make_maintenance_signal(severity="CRITICAL")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    proposal = load_proposal(db, result.action_id)

    tampered = proposal.model_copy(update={"parameters": {**proposal.parameters, "message": "a different message entirely"}})
    policy = _make_notify_policy()
    decision = evaluate_policy(tampered, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "proposal_hash_mismatch"


# --- 11. Idempotency replay -> exactly once ----------------------------------------------


def test_repeated_generation_attempts_never_exceed_one_row(db):
    signal = _make_maintenance_signal(severity="WARNING")
    results = [generate_notify_staff_proposal(db, signal, actor="system", now=NOW) for _ in range(5)]
    successes = [r for r in results if r.created]
    assert len(successes) == 1

    target = {"entity_type": "staff_notification", "entity_id": signal.signal_id}
    parameters = {"message": signal.statement, "category": "maintenance", "severity": signal.severity, "source_id": signal.signal_id}
    h = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    k = compute_idempotency_key("NOTIFY_STAFF", target, h)
    count = db.execute(select(func.count()).select_from(ActionProposalRow).where(ActionProposalRow.idempotency_key == k)).scalar()
    assert count == 1


# --- 12. Concurrent generation -> no duplicates (real threads) ---------------------------


def test_concurrent_generation_same_signal_no_duplicates():
    signal = _make_maintenance_signal(severity="CRITICAL")
    results = []
    results_lock = threading.Lock()

    def _attempt():
        thread_db = SessionLocal()
        try:
            r = generate_notify_staff_proposal(thread_db, signal, actor="system", now=NOW)
        finally:
            thread_db.close()
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.created]
    assert len(successes) == 1

    check_db = SessionLocal()
    try:
        target = {"entity_type": "staff_notification", "entity_id": signal.signal_id}
        parameters = {"message": signal.statement, "category": "maintenance", "severity": signal.severity, "source_id": signal.signal_id}
        h = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
        k = compute_idempotency_key("NOTIFY_STAFF", target, h)
        count = check_db.execute(select(func.count()).select_from(ActionProposalRow).where(ActionProposalRow.idempotency_key == k)).scalar()
        assert count == 1
    finally:
        check_db.close()


# --- 13. RBAC boundary: generation is unauthenticated by design; role is enforced downstream --


def test_rbac_enforced_downstream_not_at_generation(db):
    """The generator has no actor_role parameter and does not gate on
    role -- by design, matching Phase 15A: proposal creation is
    structural, not an authorization decision. required_role is still
    enforced later, at policy evaluation (evaluate_policy Rule 7), on
    the exact proposal the generator created."""
    signal = _make_maintenance_signal(severity="WARNING")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is True

    proposal = load_proposal(db, result.action_id)
    policy = _make_notify_policy(required_role="admin")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "role_not_permitted"


# --- 14. Sensitive-field exclusion --------------------------------------------------------


def test_no_sensitive_fields_in_generated_proposal(db):
    signal = _make_maintenance_signal(severity="CRITICAL", statement="Open for 80h — past the 48h target for urgent priority.")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    proposal = load_proposal(db, result.action_id)

    serialized = json.dumps({"target": proposal.target, "parameters": proposal.parameters}, default=str).lower()
    for forbidden in ("phone", "email", "password", "token", "api_key", "secret", "credit_card", "razorpay"):
        assert forbidden not in serialized


def test_signal_generator_source_has_no_guest_or_payment_field_references():
    import inspect
    import app.modules.automation.signal_generator as gen_module
    source = inspect.getsource(gen_module)
    for forbidden in ("guest_phone", "guest_email", "guest_name", "card_number", "razorpay", "payment_id"):
        assert forbidden not in source.lower()


# --- 15. Claude isolation ------------------------------------------------------------------


def test_no_claude_or_llm_invocation_in_signal_generator():
    import ast
    import inspect
    import app.modules.automation.signal_generator as gen_module
    tree = ast.parse(inspect.getsource(gen_module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in names
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider", "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation", "app.modules.reports.decision_engine",
    ):
        assert forbidden_submodule not in names


# --- 16. External HTTP = zero ---------------------------------------------------------------


def test_no_external_http_in_signal_generator():
    import ast
    import inspect
    import app.modules.automation.signal_generator as gen_module
    tree = ast.parse(inspect.getsource(gen_module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
        assert forbidden not in names


# --- 17. Financial-table mutation = zero -----------------------------------------------------


def test_signal_generator_source_imports_no_financial_models():
    import inspect
    import app.modules.automation.signal_generator as gen_module
    source = inspect.getsource(gen_module)
    for forbidden_model in (
        "app.modules.bookings.models", "app.modules.payments.models",
        "app.modules.finance.models", "app.modules.ota.models", "app.modules.public_booking.models",
    ):
        assert forbidden_model not in source


def test_no_side_effects_on_financial_tables(db):
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment
    from app.modules.public_booking.models import BookingHold

    bookings_before = db.execute(select(func.count()).select_from(Booking)).scalar()
    payments_before = db.execute(select(func.count()).select_from(Payment)).scalar()
    holds_before = db.execute(select(func.count()).select_from(BookingHold)).scalar()

    signal = _make_maintenance_signal(severity="WARNING")
    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is True

    bookings_after = db.execute(select(func.count()).select_from(Booking)).scalar()
    payments_after = db.execute(select(func.count()).select_from(Payment)).scalar()
    holds_after = db.execute(select(func.count()).select_from(BookingHold)).scalar()
    assert bookings_after == bookings_before
    assert payments_after == payments_before
    assert holds_after == holds_before


# --- 18. AI Strategist regression: decision_engine.py unmodified, integrates correctly -----


def test_decision_engine_maintenance_signal_feeds_generator_correctly(db):
    """End-to-end: a real AIStrategistBundle (via the unmodified Phase
    13E decision engine) produces a maintenance AIStrategistSignal that
    the generator accepts and turns into a proposal -- proving the two
    modules connect correctly without decision_engine.py having been
    changed by this phase."""
    bundle = AIStrategistBundle.model_validate(_minimal_bundle_json())
    decision = run_decision_engine(bundle)

    maintenance_signals = [s for s in decision.signals if s.category == "maintenance"]
    assert len(maintenance_signals) == 1
    signal = maintenance_signals[0]
    assert signal.severity == "WARNING"

    result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert result.created is True
    proposal = load_proposal(db, result.action_id)
    assert proposal.parameters["message"] == signal.statement


def test_reports_package_has_no_automation_import():
    """No reverse dependency from reports/ to automation/ -- unchanged
    by this phase, verified directly against the source files."""
    import pathlib
    reports_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules" / "reports"
    for py_file in reports_dir.glob("*.py"):
        source = py_file.read_text()
        assert "automation" not in source, f"unexpected automation reference in {py_file.name}"


# --- 19. RELEASE_EXPIRED_HOLD regression: registry entry exactly unchanged -----------------


def test_release_expired_hold_registry_entry_unchanged():
    entry = get_registry_entry("RELEASE_EXPIRED_HOLD")
    assert entry is not None
    assert entry.enabled is True
    assert entry.risk_level == "LOW"
    assert entry.automation_level == "L2"
    assert entry.required_role == "staff"
    assert entry.approval_requirement == "none"
    assert entry.target_entity_type == "booking_hold"
    assert entry.policy_id == "pol:release-expired-hold"
