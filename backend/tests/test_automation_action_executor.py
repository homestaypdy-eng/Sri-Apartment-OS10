"""
Phase 15E-2 — RELEASE_EXPIRED_HOLD executor tests.

Uses the real, existing public booking hold-creation flow (mocked
Razorpay only, same pattern as test_public_booking_payment_gate.py) so
every test operates on a genuine BookingHold row created the same way
production does, then manipulates `expires_at`/`status` directly the
same way that existing test file already does to simulate expiry.

Run with: pytest tests/test_automation_action_executor.py -v
"""
import json
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.main import app
from app.modules.automation.action_executor import execute_release_expired_hold
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
from app.modules.public_booking.models import BookingHold

client = TestClient(app)
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_available_unit(headers, base_rate=2000):
    prop = client.post(
        "/api/v1/properties", headers=headers,
        json={"name": f"ExecTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}
    ).json()
    unit = client.post(
        "/api/v1/units", headers=headers,
        json={"property_id": prop["id"], "name": f"ExecUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate}
    ).json()
    return prop["id"], unit["id"]


def _create_hold_mocked(unit_id, check_in, check_out, guest_phone=None):
    if guest_phone is None:
        guest_phone = f"77{uuid.uuid4().int % 10**8:08d}"
    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_name": "Exec Test Guest",
            "guest_phone": guest_phone,
            "guest_email": "exectest@example.com",
        })
    assert resp.status_code == 201, resp.text
    return resp.json()["hold_id"]


def _make_valid_expired_hold(headers, days_offset):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=2)
    hold_id = _create_hold_mocked(unit_id, check_in, check_out)
    _expire_hold(hold_id)
    return hold_id


def _expire_hold(hold_id, minutes_ago=1):
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        hold.expires_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        db.commit()
    finally:
        db.close()


def _set_hold_status(hold_id, status):
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        hold.status = status
        db.commit()
    finally:
        db.close()


def _make_policy(**overrides):
    defaults = dict(
        policy_id="pol:release-expired-hold", action_type="RELEASE_EXPIRED_HOLD",
        automation_level="L2", risk_level="LOW", conditions=[], required_role="staff",
        requires_approval=False, enabled=True, version=1,
    )
    defaults.update(overrides)
    return AutomationPolicy(**defaults)


def _make_and_persist_approved_proposal(db, hold_id, policy, status="APPROVED", target=None, action_type=None):
    action_type = action_type or policy.action_type
    target = target if target is not None else {"entity_type": "booking_hold", "entity_id": hold_id}
    parameters = {}
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


def _insert_raw_proposal_row(db, action_id, action_type, target, status="APPROVED", idempotency_key=None):
    """Bypasses persist_proposal's registry gate on purpose, to test the
    executor's OWN independent defenses against a proposal that should
    never have existed in the first place."""
    parameters = {}
    h = compute_proposal_hash(action_type, target, parameters)
    row = ActionProposalRow(
        action_id=action_id, action_type=action_type, policy_id="pol:release-expired-hold",
        risk_level="LOW", automation_level="L2", actor="staff-1",
        target=json.dumps(target), parameters=json.dumps(parameters),
        idempotency_key=idempotency_key or compute_idempotency_key(action_type, target, h),
        approval_requirement="none", proposal_hash=h, status=status,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()


# --- 1. Valid expired hold releases successfully -------------------------------


def test_valid_expired_hold_releases_successfully(headers):
    hold_id = _make_valid_expired_hold(headers, 900)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True
        assert result.status == "SUCCEEDED"

        hold = db.get(BookingHold, uuid.UUID(hold_id))
        assert hold.status == "expired"
        assert load_proposal(db, proposal.action_id).status == "SUCCEEDED"
    finally:
        db.close()


# --- 2. Non-expired hold does not release ---------------------------------------


def test_non_expired_hold_does_not_release(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=901)
    check_out = check_in + timedelta(days=2)
    hold_id = _create_hold_mocked(unit_id, check_in, check_out)  # not expired

    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "hold_not_yet_expired"

        hold = db.get(BookingHold, uuid.UUID(hold_id))
        assert hold.status == "pending_payment"
    finally:
        db.close()


# --- 3. Already released hold does not execute -----------------------------------


def test_already_released_hold_does_not_execute(headers):
    hold_id = _make_valid_expired_hold(headers, 902)
    _set_hold_status(hold_id, "expired")  # already released by some other process

    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "hold_already_released"
    finally:
        db.close()


# --- 4. Hold converted to booking does not execute -------------------------------


def test_converted_hold_does_not_execute(headers):
    hold_id = _make_valid_expired_hold(headers, 903)
    _set_hold_status(hold_id, "converted")

    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "hold_already_converted"
    finally:
        db.close()


# --- 5. Missing hold does not execute ---------------------------------------------


def test_missing_hold_does_not_execute():
    fake_hold_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, fake_hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "hold_not_found"
    finally:
        db.close()


# --- 6. Wrong target entity type rejected -----------------------------------------


def test_wrong_target_entity_type_rejected(headers):
    _, unit_id = _make_available_unit(headers)
    db = SessionLocal()
    try:
        action_id = f"action:{uuid.uuid4().hex}"
        bad_target = {"entity_type": "unit", "entity_id": str(unit_id)}
        _insert_raw_proposal_row(db, action_id, "RELEASE_EXPIRED_HOLD", bad_target)

        policy = _make_policy()
        result = execute_release_expired_hold(db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "target_invalid"
    finally:
        db.close()


# --- 7. Global automation OFF blocks execution ------------------------------------


def test_global_automation_off_blocks_execution(headers):
    hold_id = _make_valid_expired_hold(headers, 904)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, False, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "global_automation_disabled"

        hold = db.get(BookingHold, uuid.UUID(hold_id))
        assert hold.status == "pending_payment"

        trail = load_audit_trail(db, proposal.action_id)
        assert "KILL_SWITCH_BLOCKED" in [e["event_type"] for e in trail]
    finally:
        db.close()


# --- 8. Per-action policy disabled blocks execution -------------------------------


def test_disabled_policy_blocks_execution(headers):
    hold_id = _make_valid_expired_hold(headers, 905)
    db = SessionLocal()
    try:
        policy = _make_policy(enabled=False)
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "policy_disabled"
    finally:
        db.close()


# --- 9. Stale proposal hash blocks execution --------------------------------------


def test_stale_proposal_hash_blocks_execution(headers):
    hold_id = _make_valid_expired_hold(headers, 906)
    db = SessionLocal()
    try:
        policy = _make_policy(requires_approval=True)
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)

        stale_approval = ApprovalRow(
            approval_id=f"approval:{uuid.uuid4().hex}", action_id=proposal.action_id,
            proposal_hash="stale-hash-does-not-match-current-proposal", policy_id=policy.policy_id,
            policy_version=policy.version, approver="admin-1", decision="APPROVED", status="APPROVED",
            reason=None, created_at=datetime.now(timezone.utc), expires_at=None,
        )
        db.add(stale_approval)
        db.commit()

        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert "proposal_hash" in result.reason
    finally:
        db.close()


# --- 10. Policy version drift blocks execution ------------------------------------


def test_policy_version_drift_blocks_execution(headers):
    hold_id = _make_valid_expired_hold(headers, 907)
    db = SessionLocal()
    try:
        policy_v1 = _make_policy(requires_approval=True, version=1)
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy_v1)

        approval = ApprovalRow(
            approval_id=f"approval:{uuid.uuid4().hex}", action_id=proposal.action_id,
            proposal_hash=proposal.proposal_hash, policy_id=policy_v1.policy_id,
            policy_version=1, approver="admin-1", decision="APPROVED", status="APPROVED",
            reason=None, created_at=datetime.now(timezone.utc), expires_at=None,
        )
        db.add(approval)
        db.commit()

        policy_v2 = _make_policy(requires_approval=True, version=2)
        result = execute_release_expired_hold(db, proposal.action_id, policy_v2, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert "policy_version" in result.reason
    finally:
        db.close()


# --- 11. Policy ID drift blocks execution -----------------------------------------


def test_policy_id_drift_blocks_execution(headers):
    hold_id = _make_valid_expired_hold(headers, 908)
    db = SessionLocal()
    try:
        policy = _make_policy(requires_approval=True)
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)

        approval = ApprovalRow(
            approval_id=f"approval:{uuid.uuid4().hex}", action_id=proposal.action_id,
            proposal_hash=proposal.proposal_hash, policy_id=policy.policy_id,
            policy_version=policy.version, approver="admin-1", decision="APPROVED", status="APPROVED",
            reason=None, created_at=datetime.now(timezone.utc), expires_at=None,
        )
        db.add(approval)
        db.commit()

        different_policy = _make_policy(requires_approval=True, policy_id="pol:different")
        result = execute_release_expired_hold(db, proposal.action_id, different_policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert "policy_id" in result.reason
    finally:
        db.close()


# --- 12/13. Unknown / unsupported action type rejected -----------------------------


def test_unknown_action_type_rejected():
    db = SessionLocal()
    try:
        action_id = f"action:{uuid.uuid4().hex}"
        _insert_raw_proposal_row(db, action_id, "SOME_MADE_UP_ACTION", {"entity_type": "booking_hold", "entity_id": str(uuid.uuid4())})
        policy = _make_policy(action_type="SOME_MADE_UP_ACTION")
        result = execute_release_expired_hold(db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "unsupported_action_type"
    finally:
        db.close()


def test_unsupported_action_type_rejected():
    db = SessionLocal()
    try:
        action_id = f"action:{uuid.uuid4().hex}"
        _insert_raw_proposal_row(db, action_id, "PRICE_CHANGE", {"entity_type": "unit", "entity_id": str(uuid.uuid4())})
        policy = _make_policy(action_type="PRICE_CHANGE")
        result = execute_release_expired_hold(db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "unsupported_action_type"
    finally:
        db.close()


# --- 14. Duplicate idempotency key does not execute twice --------------------------


def test_duplicate_execution_attempt_does_not_execute_twice(headers):
    hold_id = _make_valid_expired_hold(headers, 909)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)

        first = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        second = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))

        assert first.success is True and first.reason == "released"
        assert second.success is True and second.reason == "already_succeeded"
    finally:
        db.close()


# --- 15. Concurrent execution of same action → exactly one successful transition --


def test_concurrent_execution_same_action_exactly_one_succeeds(headers):
    hold_id = _make_valid_expired_hold(headers, 910)
    setup_db = SessionLocal()
    policy = _make_policy()
    try:
        proposal = _make_and_persist_approved_proposal(setup_db, hold_id, policy)
    finally:
        setup_db.close()

    results = []
    results_lock = threading.Lock()

    def _attempt():
        thread_db = SessionLocal()
        try:
            r = execute_release_expired_hold(thread_db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        finally:
            thread_db.close()
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_attempt) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success and r.reason == "released"]
    assert len(successes) == 1

    check_db = SessionLocal()
    try:
        hold = check_db.get(BookingHold, uuid.UUID(hold_id))
        assert hold.status == "expired"
    finally:
        check_db.close()


# --- 16. Concurrent execution of same hold through different proposals -------------


def test_concurrent_execution_same_hold_different_proposals(headers):
    """Two distinct ActionProposal rows (bypassing the normal
    idempotency-key uniqueness gate on purpose, via direct insert) both
    targeting the SAME hold — proves the booking_hold-level atomic
    UPDATE, not proposal identity, is what actually guarantees
    exactly-once release."""
    hold_id = _make_valid_expired_hold(headers, 911)
    policy = _make_policy()
    action_ids = [f"action:{uuid.uuid4().hex}" for _ in range(4)]

    setup_db = SessionLocal()
    try:
        # idempotency_key would naturally collide across these (same
        # action_type/target/hash) — 15E-1's UNIQUE constraint would
        # correctly prevent this in the normal creation path; give each
        # row a distinct fake key purely so this artificial scenario can
        # be constructed at all, since what's actually under test here
        # is the booking_hold-level atomicity, not proposal identity.
        for action_id in action_ids:
            _insert_raw_proposal_row(setup_db, action_id, "RELEASE_EXPIRED_HOLD",
                                      {"entity_type": "booking_hold", "entity_id": hold_id}, status="APPROVED",
                                      idempotency_key=f"artificial-{action_id}")
    finally:
        setup_db.close()

    results = []
    results_lock = threading.Lock()

    def _attempt(action_id):
        thread_db = SessionLocal()
        try:
            r = execute_release_expired_hold(thread_db, action_id, policy, True, "staff", datetime.now(timezone.utc))
        finally:
            thread_db.close()
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_attempt, args=(aid,)) for aid in action_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success and r.reason == "released"]
    assert len(successes) == 1

    check_db = SessionLocal()
    try:
        hold = check_db.get(BookingHold, uuid.UUID(hold_id))
        assert hold.status == "expired"
    finally:
        check_db.close()


# --- 17. booking_hold row changes exactly once on success ---------------------------


def test_booking_hold_changes_exactly_once(headers):
    hold_id = _make_valid_expired_hold(headers, 912)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        before = db.get(BookingHold, uuid.UUID(hold_id))
        before_updated_at = before.updated_at

        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        db.expire_all()
        after = db.get(BookingHold, uuid.UUID(hold_id))
        assert after.status == "expired"
        assert after.updated_at != before_updated_at
    finally:
        db.close()


# --- 18-23. No side effects on unrelated data ---------------------------------------


def test_no_side_effects_on_unrelated_data(headers):
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment

    db = SessionLocal()
    try:
        bookings_before = db.execute(select(func.count()).select_from(Booking)).scalar()
        payments_before = db.execute(select(func.count()).select_from(Payment)).scalar()
    finally:
        db.close()

    hold_id = _make_valid_expired_hold(headers, 913)
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        guest_name_before, guest_phone_before, guest_email_before = hold.guest_name, hold.guest_phone, hold.guest_email
        total_before = hold.total

        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        db.expire_all()
        hold_after = db.get(BookingHold, uuid.UUID(hold_id))
        assert hold_after.guest_name == guest_name_before
        assert hold_after.guest_phone == guest_phone_before
        assert hold_after.guest_email == guest_email_before
        assert float(hold_after.total) == float(total_before)

        bookings_after = db.execute(select(func.count()).select_from(Booking)).scalar()
        payments_after = db.execute(select(func.count()).select_from(Payment)).scalar()
        assert bookings_after == bookings_before
        assert payments_after == payments_before
    finally:
        db.close()


# --- 24/25/26. Audit events emitted only when they actually occurred ---------------


def test_audit_started_and_succeeded_emitted_on_success(headers):
    hold_id = _make_valid_expired_hold(headers, 914)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True

        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "EXECUTION_STARTED" in event_types
        assert "EXECUTION_SUCCEEDED" in event_types
        assert "EXECUTION_FAILED" not in event_types
    finally:
        db.close()


def test_audit_failed_emitted_on_failed_execution(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=915)
    check_out = check_in + timedelta(days=2)
    hold_id = _create_hold_mocked(unit_id, check_in, check_out)  # not expired -> will fail after EXECUTING starts

    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False

        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "EXECUTION_STARTED" in event_types
        assert "EXECUTION_FAILED" in event_types
        assert "EXECUTION_SUCCEEDED" not in event_types
    finally:
        db.close()


def test_no_execution_started_when_blocked_before_claim(headers):
    """A kill-switch or disabled-policy block happens before the
    EXECUTING claim — EXECUTION_STARTED must never appear."""
    hold_id = _make_valid_expired_hold(headers, 916)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        result = execute_release_expired_hold(db, proposal.action_id, policy, False, "staff", datetime.now(timezone.utc))
        assert result.success is False

        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "EXECUTION_STARTED" not in event_types
    finally:
        db.close()


# --- 27. Kill switch produces KILL_SWITCH_BLOCKED ------------------------------------


def test_kill_switch_produces_blocked_audit_event(headers):
    hold_id = _make_valid_expired_hold(headers, 917)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        execute_release_expired_hold(db, proposal.action_id, policy, False, "staff", datetime.now(timezone.utc))
        event_types = [e["event_type"] for e in load_audit_trail(db, proposal.action_id)]
        assert "KILL_SWITCH_BLOCKED" in event_types
    finally:
        db.close()


# --- 28/29/30. Static guardrails -----------------------------------------------------


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


def test_no_claude_invocation():
    import app.modules.automation.action_executor as executor_module
    imported = _imported_module_names(executor_module)
    for forbidden in ("anthropic", "openai", "google"):
        assert forbidden not in imported
    for forbidden_submodule in (
        "app.modules.reports.narrative_provider", "app.modules.reports.narrative_service",
        "app.modules.reports.narrative_validation",
    ):
        assert forbidden_submodule not in imported


def test_no_external_http():
    import app.modules.automation.action_executor as executor_module
    imported = _imported_module_names(executor_module)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket"):
        assert forbidden not in imported


def test_no_arbitrary_database_table_access():
    """The only business-table model imported is BookingHold, and the
    only raw SQL statement touches booking_holds specifically. (Other
    "UPDATE " occurrences in the source are SQLAlchemy ORM `update(...)`
    calls against the automation module's own ActionProposalRow, or
    prose in comments/docstrings — neither is a raw-SQL business-table
    write, so this checks the raw-SQL text specifically rather than
    counting the substring across the whole file.)"""
    import inspect
    import app.modules.automation.action_executor as executor_module
    source = inspect.getsource(executor_module)
    for forbidden_model in (
        "app.modules.bookings.models", "app.modules.payments.models",
        "app.modules.finance.models", "app.modules.ota.models",
    ):
        assert forbidden_model not in source
    assert source.count("UPDATE booking_holds") == 1
    assert "DELETE FROM" not in source.upper()
    assert "INSERT INTO" not in source.upper()


# --- 31. No sensitive data in audit detail --------------------------------------------


def test_no_sensitive_data_in_audit_detail(headers):
    hold_id = _make_valid_expired_hold(headers, 918)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))

        trail = load_audit_trail(db, proposal.action_id)
        serialized = json.dumps(trail, default=str).lower()
        for forbidden in ("guest_phone", "guest_email", "guest_name", "exectest@example.com", "password", "token", "api_key", "secret"):
            assert forbidden not in serialized
    finally:
        db.close()


# --- 32/33. Durability across a fresh session (restart proxy) -------------------------


def test_successful_execution_survives_new_session(headers):
    hold_id = _make_valid_expired_hold(headers, 919)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
    finally:
        db.close()

    fresh = SessionLocal()
    try:
        loaded = load_proposal(fresh, proposal.action_id)
        assert loaded.status == "SUCCEEDED"
        hold = fresh.get(BookingHold, uuid.UUID(hold_id))
        assert hold.status == "expired"
    finally:
        fresh.close()

    # Cannot repeat after "restart" — persisted idempotency (status == SUCCEEDED) blocks it.
    repeat_db = SessionLocal()
    try:
        result = execute_release_expired_hold(repeat_db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is True
        assert result.reason == "already_succeeded"
    finally:
        repeat_db.close()


# --- 34. Policy revalidation occurs immediately before execution ----------------------


def test_policy_revalidation_audit_recorded(headers):
    hold_id = _make_valid_expired_hold(headers, 920)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)
        execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))

        trail = load_audit_trail(db, proposal.action_id)
        policy_events = [e for e in trail if e["event_type"] == "POLICY_EVALUATED"]
        assert len(policy_events) == 1
        assert policy_events[0]["detail"]["context"] == "pre_execution_revalidation"
    finally:
        db.close()


# --- 35. Stale proposal cannot release a newly valid hold ------------------------------


def test_stale_proposal_cannot_release_a_newly_valid_hold(headers):
    hold_id = _make_valid_expired_hold(headers, 921)
    db = SessionLocal()
    try:
        policy = _make_policy()
        proposal = _make_and_persist_approved_proposal(db, hold_id, policy)

        hold = db.get(BookingHold, uuid.UUID(hold_id))
        hold.expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)  # valid again
        db.commit()

        result = execute_release_expired_hold(db, proposal.action_id, policy, True, "staff", datetime.now(timezone.utc))
        assert result.success is False
        assert result.reason == "hold_not_yet_expired"

        db.expire_all()
        hold_after = db.get(BookingHold, uuid.UUID(hold_id))
        assert hold_after.status == "pending_payment"
    finally:
        db.close()


# --- 36/37. Existing TTL rule reused, no new TTL introduced -----------------------------


def test_no_new_ttl_or_timedelta_in_executor():
    """No new duration is ever computed — the module never imports or
    uses `timedelta` (which would be needed to derive a new expiry
    window) and instead only ever compares against the hold's own
    already-stored `expires_at` value. (The existing HOLD_TTL_MINUTES
    constant's *name* appears only in an explanatory docstring, never as
    a redefined or reimplemented value — that's a prose reference, not a
    behavioral one, so it isn't checked here.)"""
    import inspect
    import app.modules.automation.action_executor as executor_module
    source = inspect.getsource(executor_module)
    assert "timedelta" not in source
    assert "HOLD_TTL_MINUTES =" not in source  # never redefines the existing constant
    assert "expires_at" in source  # confirms it reuses the existing stored field
