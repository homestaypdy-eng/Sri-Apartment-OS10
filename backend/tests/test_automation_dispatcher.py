"""
Phase 16D — deterministic maintenance-signal dispatcher tests.

The dispatcher (dispatcher.py) hardcodes policy_id="notify_staff" to
match how it is actually wired in production, so every test here needs
a real, persisted "notify_staff" PolicyRow in the disposable database --
unlike Phase 16A/16C's tests, which pass an in-memory Policy object
directly and never call load_policy(). _ensure_notify_staff_policy()
below resets that one shared row to a known, canonical state at the
start of each test that needs it (INSERT on first use, UPDATE
thereafter), so tests remain independent of execution order and of
whatever an earlier test in this file (or, since all test files in one
pytest run share the same disposable database, an earlier file) left
behind. This is test-fixture setup, not part of dispatcher.py's own
behavior -- see dispatcher.py's own module docstring for why it never
does this kind of direct row manipulation itself.

Run with: pytest tests/test_automation_dispatcher.py -v
"""
import dataclasses
import json
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.session import SessionLocal
from app.main import app  # noqa: F401 -- full model registration
from app.modules.automation import action_registry as action_registry_module
from app.modules.automation.action_registry import get_registry_entry
from app.modules.automation.dispatcher import dispatch_maintenance_signal
from app.modules.automation.governance import set_policy_enabled
from app.modules.automation.models import ActionProposalRow, PolicyRow
from app.modules.automation.persistence import load_audit_trail, load_proposal, persist_proposal
from app.modules.automation.policy_engine import compute_idempotency_key, compute_proposal_hash
from app.modules.automation.schemas import ActionProposal
from app.modules.automation.signal_generator import generate_notify_staff_proposal
from app.modules.notifications.models import NotificationLog
from app.modules.reports.schemas import AIStrategistSignal

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def disabled_notify_staff_registry(monkeypatch):
    real_entry = action_registry_module._REGISTRY["NOTIFY_STAFF"]
    patched_entry = dataclasses.replace(real_entry, enabled=False)
    monkeypatch.setitem(action_registry_module._REGISTRY, "NOTIFY_STAFF", patched_entry)
    return patched_entry


def _ensure_notify_staff_policy(db, enabled=True, version=1, automation_level="L2", risk_level="MEDIUM",
                                 requires_approval=False, required_role="staff"):
    """Resets the one shared, hardcoded-by-dispatcher "notify_staff"
    policy row to an explicit, known state. INSERT on first use in this
    disposable database, UPDATE thereafter -- see module docstring."""
    row = db.get(PolicyRow, "notify_staff")
    if row is None:
        row = PolicyRow(
            policy_id="notify_staff", action_type="NOTIFY_STAFF",
            automation_level=automation_level, risk_level=risk_level,
            conditions="[]", required_role=required_role, requires_approval=requires_approval,
            enabled=enabled, version=version, status="ACTIVE",
        )
        db.add(row)
    else:
        row.automation_level = automation_level
        row.risk_level = risk_level
        row.required_role = required_role
        row.requires_approval = requires_approval
        row.enabled = enabled
        row.version = version
        row.status = "ACTIVE"
    db.commit()


def _make_maintenance_signal(ticket_id=None, severity="WARNING", statement=None):
    ticket_id = ticket_id or str(uuid.uuid4())
    return AIStrategistSignal(
        signal_id=f"attention:maintenance:{ticket_id}",
        category="maintenance", severity=severity, classification="FACT",
        statement=statement or "Urgent priority ticket, open 50h.",
        source_fields=["needs_attention[0].category"], evidence=[], confidence="HIGH",
    )


def _make_non_eligible_signal(category="gst", severity="INFO"):
    return AIStrategistSignal(
        signal_id=f"{category}:test:{uuid.uuid4().hex[:8]}",
        category=category, severity=severity, classification="FACT",
        statement="GST payable this period is zero.",
        source_fields=["gst.gst_payable"], evidence=[], confidence="HIGH",
    )


def _proposal_count_for_signal(db, signal):
    target = {"entity_type": "staff_notification", "entity_id": signal.signal_id}
    parameters = {"message": signal.statement, "category": "maintenance", "severity": signal.severity, "source_id": signal.signal_id}
    h = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    k = compute_idempotency_key("NOTIFY_STAFF", target, h)
    return db.execute(select(func.count()).select_from(ActionProposalRow).where(ActionProposalRow.idempotency_key == k)).scalar()


# --- 1. Eligible maintenance signal creates proposal ------------------------------------


def test_eligible_maintenance_signal_creates_proposal(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is True
    assert result.action_id is not None


# --- 2/3. Non-eligible signals create nothing --------------------------------------------


def test_non_maintenance_signal_creates_nothing(db):
    _ensure_notify_staff_policy(db)
    signal = _make_non_eligible_signal(category="gst", severity="WARNING")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is False
    assert result.proposal_reason == "signal_not_eligible"
    assert result.action_id is None
    assert result.executed is False


def test_info_severity_maintenance_signal_creates_nothing(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="INFO")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is False
    assert result.proposal_reason == "signal_not_eligible"


# --- 4/5. WARNING and CRITICAL both create a proposal ------------------------------------


def test_warning_severity_creates_proposal_and_executes(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is True
    assert result.decision_outcome == "ALLOW"
    assert result.executed is True
    assert result.execution_reason == "notified"


def test_critical_severity_creates_proposal_and_executes(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="CRITICAL")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is True
    assert result.decision_outcome == "ALLOW"
    assert result.executed is True


# --- 6/7. Repeated dispatch is idempotent; duplicate proposal prevented ------------------


def test_repeated_dispatch_same_signal_is_idempotent(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")
    first = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    second = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)

    assert first.proposed is True and first.executed is True
    assert second.proposed is False
    assert second.proposal_reason == "duplicate_action_id_or_idempotency_key"
    assert second.action_id is None
    assert second.executed is False

    assert _proposal_count_for_signal(db, signal) == 1


# --- 8. Duplicate notification prevented --------------------------------------------------


def test_duplicate_notification_prevented(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="CRITICAL")
    dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)

    target = {"entity_type": "staff_notification", "entity_id": signal.signal_id}
    parameters = {"message": signal.statement, "category": "maintenance", "severity": signal.severity, "source_id": signal.signal_id}
    h = compute_proposal_hash("NOTIFY_STAFF", target, parameters)
    k = compute_idempotency_key("NOTIFY_STAFF", target, h)
    proposal_row = db.execute(select(ActionProposalRow).where(ActionProposalRow.idempotency_key == k)).scalar_one()
    count = db.execute(
        select(func.count()).select_from(NotificationLog).where(NotificationLog.action_id == proposal_row.action_id)
    ).scalar()
    assert count == 1


# --- 9. Fresh-session replay is safe --------------------------------------------------------


def test_fresh_session_replay_is_safe(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")
    first = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert first.executed is True

    fresh = SessionLocal()
    try:
        second = dispatch_maintenance_signal(fresh, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
        assert second.proposed is False
        assert second.proposal_reason == "duplicate_action_id_or_idempotency_key"

        loaded = load_proposal(fresh, first.action_id)
        assert loaded.status == "SUCCEEDED"
    finally:
        fresh.close()


# --- 10/11. Concurrent dispatch is safe; exactly one notification -----------------------


def test_concurrent_dispatch_same_signal_produces_exactly_one_notification():
    setup_db = SessionLocal()
    try:
        _ensure_notify_staff_policy(setup_db)
    finally:
        setup_db.close()

    signal = _make_maintenance_signal(severity="CRITICAL")
    results = []
    lock = threading.Lock()

    def _attempt():
        thread_db = SessionLocal()
        try:
            r = dispatch_maintenance_signal(thread_db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
        finally:
            thread_db.close()
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.proposed and r.executed]
    assert len(successes) == 1

    check_db = SessionLocal()
    try:
        action_id = successes[0].action_id
        count = check_db.execute(
            select(func.count()).select_from(NotificationLog).where(NotificationLog.action_id == action_id)
        ).scalar()
        assert count == 1
        assert _proposal_count_for_signal(check_db, signal) == 1
    finally:
        check_db.close()


# --- 12. Policy disabled blocks execution ---------------------------------------------------


def test_policy_disabled_blocks_execution(db):
    _ensure_notify_staff_policy(db, enabled=False)
    signal = _make_maintenance_signal(severity="WARNING")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is True
    assert result.decision_outcome == "DENY"
    assert result.execution_reason == "policy_disabled"
    assert result.executed is False

    proposal = load_proposal(db, result.action_id)
    assert proposal.status == "PROPOSED"  # never reached APPROVED


# --- 13. Global switch blocks execution -----------------------------------------------------


def test_global_switch_disabled_blocks_execution(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="CRITICAL")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=False, now=NOW)
    assert result.proposed is True
    assert result.decision_outcome == "DENY"
    assert result.execution_reason == "global_automation_disabled"
    assert result.executed is False

    trail = load_audit_trail(db, result.action_id)
    assert "KILL_SWITCH_BLOCKED" not in [e["event_type"] for e in trail]  # dispatcher's own evaluate_policy denies before any claim attempt


# --- 14. Registry disabled blocks execution ---------------------------------------------------


def test_registry_disabled_blocks_execution(db, disabled_notify_staff_registry):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    # The registry gate is checked inside generate_notify_staff_proposal's
    # own call to persist_proposal (validate_proposal_structure), before
    # the dispatcher ever gets a proposal to evaluate.
    assert result.proposed is False
    assert result.proposal_reason == "action_type_disabled"
    assert result.executed is False


# --- 15. Stale (tampered) proposal blocked -----------------------------------------------------


def test_tampered_dispatched_proposal_denied_on_hash_mismatch(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")
    gen_result = generate_notify_staff_proposal(db, signal, actor="system", now=NOW)
    assert gen_result.created is True

    proposal = load_proposal(db, gen_result.action_id)
    tampered = proposal.model_copy(update={"parameters": {**proposal.parameters, "message": "a different message"}})

    from app.modules.automation.policy_engine import evaluate_policy
    from app.modules.automation.persistence import load_policy
    policy = load_policy(db, "notify_staff")
    decision = evaluate_policy(tampered, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "proposal_hash_mismatch"


# --- 16. Policy-version drift blocked -----------------------------------------------------------


def test_policy_version_drift_blocks_retry(db):
    """A proposal generated (but not yet executed, e.g. because the
    global switch was off at the time) under one policy shape cannot
    later execute if the policy's semantic fields have since changed --
    reuses evaluate_policy's existing Rule 5 (automation_level_mismatch),
    the exact same protection Phase 16A/16C rely on, not a new one."""
    _ensure_notify_staff_policy(db, automation_level="L2")
    signal = _make_maintenance_signal(severity="CRITICAL")
    first = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=False, now=NOW)
    assert first.proposed is True
    assert first.executed is False  # blocked by kill switch, proposal remains PROPOSED

    # Policy is later reconfigured (a genuine semantic change).
    _ensure_notify_staff_policy(db, automation_level="L3", version=2)

    proposal = load_proposal(db, first.action_id)
    from app.modules.automation.policy_engine import evaluate_policy
    from app.modules.automation.persistence import load_policy
    policy = load_policy(db, "notify_staff")
    decision = evaluate_policy(proposal, policy, "staff", {}, global_automation_enabled=True)
    assert decision.outcome == "DENY"
    assert decision.reason == "automation_level_mismatch"


# --- 17. Wrong target rejected via the shared validation path ----------------------------------


def test_wrong_target_rejected_via_shared_persist_proposal_path(db):
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
    assert result.reason == "target_invalid"


# --- 18. Invalid parameters rejected -------------------------------------------------------------


def test_oversized_statement_rejected_before_any_proposal(db):
    _ensure_notify_staff_policy(db)
    before = db.execute(select(func.count()).select_from(ActionProposalRow)).scalar()
    signal = _make_maintenance_signal(statement="x" * 501)
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is False
    assert result.proposal_reason == "invalid_shape"
    after = db.execute(select(func.count()).select_from(ActionProposalRow)).scalar()
    assert after == before


# --- 19. Sensitive fields rejected/excluded --------------------------------------------------------


def test_no_sensitive_fields_in_dispatched_notification(db):
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="CRITICAL", statement="Open for 80h — past the 48h target for urgent priority.")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.executed is True

    row = db.execute(select(NotificationLog).where(NotificationLog.action_id == result.action_id)).scalar_one()
    serialized = json.dumps({
        "message": row.message, "category": row.category, "severity": row.severity, "source_id": row.source_id,
    }, default=str).lower()
    for forbidden in ("phone", "email", "password", "token", "api_key", "secret", "credit_card", "razorpay"):
        assert forbidden not in serialized


# --- 20. RBAC boundary ------------------------------------------------------------------------------


def test_rbac_boundary_wrong_role_denied(db):
    _ensure_notify_staff_policy(db, required_role="admin")
    signal = _make_maintenance_signal(severity="WARNING")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.proposed is True
    assert result.decision_outcome == "DENY"
    assert result.execution_reason == "role_not_permitted"
    assert result.executed is False


# --- 21. Audit trail correctness (success path) ----------------------------------------------------


def test_audit_trail_correctness_success_path(db):
    """PROPOSED, APPROVED, EXECUTION_STARTED, EXECUTION_SUCCEEDED, and
    execute_notify_staff's own revalidation POLICY_EVALUATED all reuse
    the SAME `now` value passed into dispatch_maintenance_signal
    (neither execute_notify_staff nor update_proposal_status ever calls
    datetime.now() internally -- they use their `now` parameter
    verbatim, possibly more than once). Only the dispatcher's own
    pre-check POLICY_EVALUATED (via persist_policy_decision, which uses
    evaluate_policy's independently-generated evaluated_at) gets a
    genuinely different timestamp. This means several events are
    genuinely tied, and load_audit_trail's `ORDER BY timestamp` provides
    no tiebreaker for ties -- a real, pre-existing characteristic of
    Phase 15E-1, not a Phase 16D defect. This test therefore checks
    event-type membership/counts and compares actual timestamp values
    directly, never list position, which is the only way to make this
    assertion reliable regardless of tie-breaking behavior."""
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.executed is True

    trail = load_audit_trail(db, result.action_id)
    event_types = [e["event_type"] for e in trail]
    assert set(event_types) == {"PROPOSED", "APPROVED", "POLICY_EVALUATED", "EXECUTION_STARTED", "EXECUTION_SUCCEEDED"}
    assert len(trail) == 6  # PROPOSED, APPROVED, 2x POLICY_EVALUATED, EXECUTION_STARTED, EXECUTION_SUCCEEDED

    # Two POLICY_EVALUATED entries are expected and correct: the
    # dispatcher's own pre-check evaluation, and execute_notify_staff's
    # own independent, mandatory revalidation immediately before
    # claiming EXECUTING -- the same "never trust stale state"
    # discipline already used everywhere else in this package.
    policy_evaluated_events = [e for e in trail if e["event_type"] == "POLICY_EVALUATED"]
    assert len(policy_evaluated_events) == 2
    revalidation_events = [e for e in policy_evaluated_events if e["detail"].get("context") == "pre_execution_revalidation"]
    precheck_events = [e for e in policy_evaluated_events if e["detail"].get("context") != "pre_execution_revalidation"]
    assert len(revalidation_events) == 1
    assert len(precheck_events) == 1
    # Not asserting a timestamp ordering between precheck and
    # revalidation: the precheck's timestamp comes from evaluate_policy's
    # own fresh datetime.now(timezone.utc) call, while the revalidation's
    # (like every other event here) reuses the fixed `now` this test
    # passed in -- two different clock sources that are not meaningfully
    # comparable for ordering, even though the precheck genuinely
    # executes first. Real production calls pass a real, single
    # timestamp per invocation, at which point this distinction and
    # load_audit_trail's own tie-breaking limitations (see the class
    # docstring above) both stop applying.


def test_audit_trail_correctness_denied_path(db):
    _ensure_notify_staff_policy(db, enabled=False)
    signal = _make_maintenance_signal(severity="CRITICAL")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.executed is False

    trail = load_audit_trail(db, result.action_id)
    event_types = [e["event_type"] for e in trail]
    assert event_types[0] == "PROPOSED"
    assert "POLICY_EVALUATED" in event_types
    assert "EXECUTION_STARTED" not in event_types


# --- 22. Failed execution audit -----------------------------------------------------------------------


def test_failed_execution_audit(db):
    """Forces a real (mocked) transient failure inside the executor's
    notification-creation step, after EXECUTING has already been
    claimed, to exercise the genuine EXECUTION_FAILED path -- no audit
    event is fabricated; every event written here is one the real code
    path actually emits given this forced condition."""
    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="WARNING")

    with patch(
        "app.modules.automation.action_executor._create_staff_notification_with_retry",
        return_value=(False, "transient_database_error", {}),
    ):
        result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)

    assert result.proposed is True
    assert result.executed is False
    assert result.execution_reason == "transient_database_error"

    trail = load_audit_trail(db, result.action_id)
    event_types = [e["event_type"] for e in trail]
    assert "EXECUTION_STARTED" in event_types
    assert "EXECUTION_FAILED" in event_types
    assert "EXECUTION_SUCCEEDED" not in event_types


# --- 23-27. No side effects on any business table -----------------------------------------------------


def test_no_business_table_mutations(db):
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment
    from app.modules.finance.models import Expense
    from app.modules.ota.models import OTASyncLog
    from app.modules.public_booking.models import BookingHold

    before = {
        "bookings": db.execute(select(func.count()).select_from(Booking)).scalar(),
        "payments": db.execute(select(func.count()).select_from(Payment)).scalar(),
        "booking_holds": db.execute(select(func.count()).select_from(BookingHold)).scalar(),
        "expenses": db.execute(select(func.count()).select_from(Expense)).scalar(),
        "ota_sync_logs": db.execute(select(func.count()).select_from(OTASyncLog)).scalar(),
    }

    _ensure_notify_staff_policy(db)
    signal = _make_maintenance_signal(severity="CRITICAL")
    result = dispatch_maintenance_signal(db, signal, actor="system", actor_role="staff", global_automation_enabled=True, now=NOW)
    assert result.executed is True

    after = {
        "bookings": db.execute(select(func.count()).select_from(Booking)).scalar(),
        "payments": db.execute(select(func.count()).select_from(Payment)).scalar(),
        "booking_holds": db.execute(select(func.count()).select_from(BookingHold)).scalar(),
        "expenses": db.execute(select(func.count()).select_from(Expense)).scalar(),
        "ota_sync_logs": db.execute(select(func.count()).select_from(OTASyncLog)).scalar(),
    }
    assert before == after


# --- 28/29. No Claude calls, no external HTTP (static scans) -------------------------------------------


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


def test_no_claude_or_llm_invocation_in_dispatcher():
    import app.modules.automation.dispatcher as dispatcher_module
    imported = _imported_module_names(dispatcher_module)
    for forbidden in ("anthropic", "openai", "google", "gemini"):
        assert forbidden not in imported
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider", "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation", "app.modules.reports.decision_engine",
    ):
        assert forbidden_submodule not in imported


def test_no_external_http_in_dispatcher():
    import app.modules.automation.dispatcher as dispatcher_module
    imported = _imported_module_names(dispatcher_module)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
        assert forbidden not in imported


def test_no_scheduler_or_background_worker_imports_in_dispatcher():
    """Phase 16D explicitly forbids adding automatic scheduling in this
    phase -- static proof dispatcher.py never imports one."""
    import inspect
    import app.modules.automation.dispatcher as dispatcher_module
    source = inspect.getsource(dispatcher_module).lower()
    for forbidden in ("apscheduler", "celery", "cron", "webhook", "while true", "asyncio.sleep", "time.sleep"):
        assert forbidden not in source


def test_dispatcher_source_has_no_direct_db_manipulation():
    """Orchestration only: dispatcher.py must never call db.add / db.commit
    / db.execute / raw SQL text() itself -- every persistence operation
    is delegated to an existing function from an earlier phase."""
    import inspect
    import app.modules.automation.dispatcher as dispatcher_module
    source = inspect.getsource(dispatcher_module)
    for forbidden in ("db.add(", "db.commit(", "db.execute(", "db.flush(", "text("):
        assert forbidden not in source


def test_dispatcher_source_imports_no_financial_models():
    import inspect
    import app.modules.automation.dispatcher as dispatcher_module
    source = inspect.getsource(dispatcher_module)
    for forbidden_model in (
        "app.modules.bookings.models", "app.modules.payments.models",
        "app.modules.finance.models", "app.modules.ota.models", "app.modules.public_booking.models",
    ):
        assert forbidden_model not in source


# --- 30. AI Strategist regression -----------------------------------------------------------------------


def test_reports_package_has_no_automation_import():
    import pathlib
    reports_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules" / "reports"
    for py_file in reports_dir.glob("*.py"):
        source = py_file.read_text()
        assert "automation" not in source, f"unexpected automation reference in {py_file.name}"


# --- 31. RELEASE_EXPIRED_HOLD regression ------------------------------------------------------------------


def test_release_expired_hold_registry_entry_unchanged():
    entry = get_registry_entry("RELEASE_EXPIRED_HOLD")
    assert entry is not None
    assert entry.enabled is True
    assert entry.risk_level == "LOW"
    assert entry.automation_level == "L2"
    assert entry.required_role == "staff"
    assert entry.target_entity_type == "booking_hold"
    assert entry.policy_id == "pol:release-expired-hold"


# --- 32/33. 16A/16B registry+policy shape unaffected by the dispatcher's addition --------------------


def test_notify_staff_registry_entry_unaffected_by_dispatcher():
    entry = get_registry_entry("NOTIFY_STAFF")
    assert entry is not None
    assert entry.enabled is True  # activated in Phase 16B, unchanged by 16D
    assert entry.risk_level == "MEDIUM"
    assert entry.automation_level == "L2"
    assert entry.target_entity_type == "staff_notification"
    assert entry.policy_id == "notify_staff"


def test_governance_set_policy_enabled_still_works_on_dispatcher_managed_row(db):
    """Confirms Phase 16B's governance mechanism is untouched and still
    correctly operates on the same "notify_staff" row the dispatcher
    reads -- proving 16D added a consumer, not a second writer."""
    _ensure_notify_staff_policy(db, enabled=True)
    result = set_policy_enabled(db, "notify_staff", False, actor="admin-1", actor_role="admin", reason="test", now=NOW)
    assert result.success is True
    row = db.get(PolicyRow, "notify_staff")
    assert row.enabled is False
