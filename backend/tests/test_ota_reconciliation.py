"""
Phase 19E: OTA payout & receipt reconciliation.

Covers:
- MMT added to both OTA channel vocabularies (ota/models.OTA_CHANNELS and
  finance/models.OTA_CHANNELS_FOR_COMMISSION), existing channels unchanged
- expected payout = gross booking amount - commission - OTA GST, computed
  live from Booking + OTACommission, never stored/invented
- manual actual-receipt recording (OTAPayoutReceipt), get-or-create +
  update via PATCH /finance/ota-commissions/{id}/receipt
- reconciliation status: unreconciled (no receipt row) / matched / partial
  / discrepancy (status is staff-declared on the receipt, not auto-derived)
- a booking with an OTA-looking source but no OTACommission is flagged via
  /finance/ota-missing-commissions, never given an invented commission
- idempotency + concurrency: repeated/simultaneous receipt submissions for
  the same commission never produce more than one effective receipt row
- audit trail for receipt create/update
- RBAC: recording a receipt is admin-only; reading is admin+staff
- financial isolation: Payments/Invoices/GST/Loan-EMI/Withdrawals/Deposits
  untouched by recording a receipt
- P&L isolation: OTA commission+GST still deducted exactly once in
  apartment_pnl regardless of receipt activity (no second expense)
- cash flow: expected payout is never cash; only a recorded actual receipt
  moves cash_in_ota_payouts, by exactly the recorded amount, once
- AI Strategist / Autonomous Actions unchanged

Run with: pytest tests/test_ota_reconciliation.py -v
"""
import threading
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def staff_headers(headers):
    email = f"otatest_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "OTA Test Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_property_and_unit(headers, rate=3000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"OTAProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"OTAUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return prop["id"], unit["id"]


def _make_booking(headers, unit_id, rate=3000, nights=2, days_offset=300, source="direct"):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "OTA Test Guest", "phone": f"97{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=nights)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
        "rate_applied": rate, "source": source,
    }).json()
    return booking, check_in


def _make_commission(headers, booking_id, channel="booking_com", percent=15, commission=900, gst=162):
    resp = client.post("/api/v1/finance/ota-commissions", headers=headers, json={
        "booking_id": booking_id, "channel": channel,
        "commission_percent": percent, "commission_amount": commission, "ota_gst_amount": gst,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run_concurrently(*fns):
    threads = [threading.Thread(target=fn) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _receipt_row_count(commission_id: str) -> int:
    from sqlalchemy import text
    session = SessionLocal()
    try:
        return session.execute(
            text("SELECT COUNT(*) FROM ota_payout_receipts WHERE ota_commission_id = :cid"), {"cid": commission_id}
        ).scalar()
    finally:
        session.close()


def _action_proposals_count() -> int:
    from sqlalchemy import text
    session = SessionLocal()
    try:
        return session.execute(text("SELECT COUNT(*) FROM action_proposals")).scalar()
    finally:
        session.close()


# --- 1/2: channel vocabulary ---

def test_mmt_channel_accepted_for_commission(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"], channel="mmt", percent=18, commission=360, gst=64.8)
    assert commission["channel"] == "mmt"


def test_mmt_channel_present_in_ota_channel_config(headers):
    resp = client.get("/api/v1/ota/channels", headers=headers)
    assert resp.status_code == 200
    channels = {c["channel"] for c in resp.json()}
    assert "mmt" in channels


def test_existing_ota_channels_still_accepted(headers):
    _, unit_id = _make_property_and_unit(headers)
    for i, ch in enumerate(("booking_com", "airbnb", "agoda", "direct")):
        booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1, days_offset=310 + i)
        commission = _make_commission(headers, booking["id"], channel=ch, percent=10, commission=200, gst=36)
        assert commission["channel"] == ch


# --- 3: expected payout calculation ---

def test_expected_payout_calculation_matches_worked_example(headers):
    """Phase 19E worked example: commission=975, OTA GST=175.50 -> expected
    OTA cost = 1150.50. Gross is 5000 (2500/night x 2 nights), so expected
    payout = 5000 - 975 - 175.50 = 3849.50."""
    _, unit_id = _make_property_and_unit(headers, rate=2500)
    booking, check_in = _make_booking(headers, unit_id, rate=2500, nights=2)
    commission = _make_commission(headers, booking["id"], channel="booking_com", percent=19.5, commission=975, gst=175.50)

    lines = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()
    line = next(l for l in lines if l["ota_commission_id"] == commission["id"])
    assert line["gross_amount"] == 5000.0
    assert line["commission_amount"] == 975.0
    assert line["ota_gst_amount"] == 175.50
    assert line["expected_payout"] == 3849.50


# --- 5/10: missing commission not invented, unreconciled default ---

def test_missing_commission_booking_flagged_without_inventing_commission(headers):
    _, unit_id = _make_property_and_unit(headers, rate=1800)
    booking, check_in = _make_booking(headers, unit_id, rate=1800, nights=2, source="ota_booking_com")
    # Deliberately no OTACommission created.

    missing = client.get("/api/v1/finance/ota-missing-commissions", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()
    match = next(l for l in missing if l["booking_id"] == booking["id"])
    assert match["source"] == "ota_booking_com"
    assert match["gross_amount"] == 3600.0
    # And it must NOT appear in the reconciliation list (which only ever
    # reflects existing OTACommission rows).
    reconciliation = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()
    assert all(l["booking_id"] != booking["id"] for l in reconciliation)


def test_unreconciled_status_when_no_receipt_recorded(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"])

    lines = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()
    line = next(l for l in lines if l["ota_commission_id"] == commission["id"])
    assert line["reconciliation_status"] == "unreconciled"
    assert line["actual_received_amount"] is None
    assert line["difference"] is None


# --- 6/7/8/9: receipt recording + status classification ---

def test_record_receipt_exact_match_gives_matched_status(headers):
    _, unit_id = _make_property_and_unit(headers, rate=2000)
    booking, check_in = _make_booking(headers, unit_id, rate=2000, nights=2)
    commission = _make_commission(headers, booking["id"], percent=10, commission=400, gst=72)
    # gross = 4000, expected payout = 4000 - 400 - 72 = 3528
    resp = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3528, "receipt_date": date.today().isoformat(), "status": "matched", "reference_number": "REF-M1",
    })
    assert resp.status_code == 200, resp.text
    receipt = resp.json()
    assert receipt["actual_received_amount"] == 3528.0
    assert receipt["status"] == "matched"

    lines = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()
    line = next(l for l in lines if l["ota_commission_id"] == commission["id"])
    assert line["reconciliation_status"] == "matched"
    assert line["difference"] == 0.0


def test_record_receipt_partial_amount(headers):
    _, unit_id = _make_property_and_unit(headers, rate=2000)
    booking, check_in = _make_booking(headers, unit_id, rate=2000, nights=2)
    commission = _make_commission(headers, booking["id"], percent=10, commission=400, gst=72)
    # expected payout = 3528; only 2000 actually received so far
    resp = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 2000, "receipt_date": date.today().isoformat(), "status": "partial",
    })
    assert resp.status_code == 200, resp.text

    lines = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()
    line = next(l for l in lines if l["ota_commission_id"] == commission["id"])
    assert line["reconciliation_status"] == "partial"
    assert line["difference"] == 1528.0  # not silently rounded away


def test_record_receipt_discrepancy(headers):
    _, unit_id = _make_property_and_unit(headers, rate=2000)
    booking, check_in = _make_booking(headers, unit_id, rate=2000, nights=2)
    commission = _make_commission(headers, booking["id"], percent=10, commission=400, gst=72)
    # expected payout = 3528; OTA actually sent 3600 — overpaid/discrepancy
    resp = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3600, "receipt_date": date.today().isoformat(), "status": "discrepancy",
        "notes": "Received more than expected — investigate",
    })
    assert resp.status_code == 200, resp.text

    lines = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()
    line = next(l for l in lines if l["ota_commission_id"] == commission["id"])
    assert line["reconciliation_status"] == "discrepancy"
    assert line["difference"] == -72.0


def test_record_receipt_rejects_invalid_status(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"])
    resp = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 100, "receipt_date": date.today().isoformat(), "status": "unreconciled",
    })
    assert resp.status_code == 400


# --- 11: idempotency ---

def test_repeated_receipt_submission_does_not_duplicate(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"], percent=10, commission=180, gst=32.4)
    payload = {"actual_received_amount": 1787.6, "receipt_date": date.today().isoformat(), "status": "matched", "reference_number": "REF-DUP"}

    r1 = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json=payload)
    r2 = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json=payload)
    r3 = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json=payload)
    assert all(r.status_code == 200 for r in (r1, r2, r3))
    assert r1.json()["id"] == r2.json()["id"] == r3.json()["id"]
    assert _receipt_row_count(commission["id"]) == 1


# --- 12: concurrency ---

def test_concurrent_receipt_submission_produces_exactly_one_row(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"], percent=10, commission=180, gst=32.4)
    payload = {"actual_received_amount": 1787.6, "receipt_date": date.today().isoformat(), "status": "matched", "reference_number": "REF-CONC"}

    results = []
    def do_record():
        results.append(client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json=payload))

    _run_concurrently(do_record, do_record)

    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert _receipt_row_count(commission["id"]) == 1

    lines = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={
        "date_from": date.today().isoformat(), "date_to": (date.today() + timedelta(days=400)).isoformat(),
    }).json()
    line = next(l for l in lines if l["ota_commission_id"] == commission["id"])
    assert line["actual_received_amount"] == 1787.6
    assert line["reconciliation_status"] == "matched"


# --- 13: audit ---

def test_receipt_create_and_update_are_audited(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"], percent=10, commission=180, gst=32.4)

    created = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 1787.6, "receipt_date": date.today().isoformat(), "status": "matched",
    }).json()
    updated = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 1700, "receipt_date": date.today().isoformat(), "status": "partial",
    }).json()
    assert created["id"] == updated["id"]

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"entity_type": "ota_payout_receipt", "entity_id": updated["id"]}).json()
    actions = {l["action"] for l in logs}
    assert "finance.ota_payout_receipt.create" in actions
    assert "finance.ota_payout_receipt.update" in actions


# --- 14: RBAC ---

def test_staff_cannot_record_receipt(headers, staff_headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"])
    resp = client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=staff_headers, json={
        "actual_received_amount": 100, "receipt_date": date.today().isoformat(), "status": "matched",
    })
    assert resp.status_code == 403


def test_staff_can_read_reconciliation_and_missing_commissions(staff_headers):
    for path in ("/api/v1/finance/ota-commissions/reconciliation", "/api/v1/finance/ota-missing-commissions"):
        resp = client.get(path, headers=staff_headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


# --- 15: financial isolation ---

def test_recording_receipt_does_not_mutate_unrelated_financial_records(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"], percent=10, commission=180, gst=32.4)

    before_payments = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    before_deposits = client.get("/api/v1/finance/deposits", headers=headers).json()
    before_withdrawals = client.get("/api/v1/finance/owner-withdrawals", headers=headers).json()
    before_loans = client.get("/api/v1/finance/loans", headers=headers).json()

    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 1787.6, "receipt_date": date.today().isoformat(), "status": "matched",
    })

    after_payments = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    after_deposits = client.get("/api/v1/finance/deposits", headers=headers).json()
    after_withdrawals = client.get("/api/v1/finance/owner-withdrawals", headers=headers).json()
    after_loans = client.get("/api/v1/finance/loans", headers=headers).json()

    assert len(after_payments) == len(before_payments)
    assert len(after_deposits) == len(before_deposits)
    assert len(after_withdrawals) == len(before_withdrawals)
    assert len(after_loans) == len(before_loans)


# --- 16: P&L isolation (never double-counted) ---

def test_pnl_ota_expense_deducted_once_regardless_of_receipt_activity(headers):
    _, unit_id = _make_property_and_unit(headers, rate=2500)
    booking, check_in = _make_booking(headers, unit_id, rate=2500, nights=2)
    commission = _make_commission(headers, booking["id"], percent=19.5, commission=975, gst=175.50)

    pnl_before = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    ota_line_before = next(l for l in pnl_before["expense_lines"] if l["category"] == "ota_commission")
    assert ota_line_before["amount"] == 1150.50

    # Record + repeat a receipt — P&L's OTA expense must remain exactly the
    # same commission+GST figure, never becoming 2301.00 (double-counted).
    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3849.50, "receipt_date": date.today().isoformat(), "status": "matched",
    })
    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3849.50, "receipt_date": date.today().isoformat(), "status": "matched",
    })

    pnl_after = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    ota_line_after = next(l for l in pnl_after["expense_lines"] if l["category"] == "ota_commission")
    assert ota_line_after["amount"] == 1150.50
    assert pnl_after["net_profit"] == pnl_before["net_profit"]


# --- 17/18: cash flow — expected payout is never cash, only actual receipt is ---

def test_expected_payout_never_appears_as_cash(headers):
    _, unit_id = _make_property_and_unit(headers, rate=2500)
    booking, check_in = _make_booking(headers, unit_id, rate=2500, nights=2)
    d_from = check_in.isoformat()
    d_to = (check_in + timedelta(days=1)).isoformat()

    before = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": d_from, "date_to": d_to}).json()
    _make_commission(headers, booking["id"], percent=19.5, commission=975, gst=175.50)
    after_commission_only = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": d_from, "date_to": d_to}).json()

    # A commission record alone (no receipt) must not move cash at all.
    assert after_commission_only["cash_in_ota_payouts"] == before["cash_in_ota_payouts"]
    assert after_commission_only["net_cash_flow"] == before["net_cash_flow"]


def test_actual_receipt_increases_cash_in_by_exact_amount_once(headers):
    _, unit_id = _make_property_and_unit(headers, rate=2500)
    booking, check_in = _make_booking(headers, unit_id, rate=2500, nights=2)
    commission = _make_commission(headers, booking["id"], percent=19.5, commission=975, gst=175.50)
    receipt_date = date.today()
    d_from = receipt_date.isoformat()
    d_to = (receipt_date + timedelta(days=1)).isoformat()

    before = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": d_from, "date_to": d_to}).json()

    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3849.50, "receipt_date": receipt_date.isoformat(), "status": "matched",
    })
    after = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": d_from, "date_to": d_to}).json()
    assert round(after["cash_in_ota_payouts"] - before["cash_in_ota_payouts"], 2) == 3849.50
    assert round(after["net_cash_flow"] - before["net_cash_flow"], 2) == 3849.50

    # Repeating (correcting) the same receipt must not double-count cash —
    # the row is updated in place, not re-added.
    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3849.50, "receipt_date": receipt_date.isoformat(), "status": "matched",
    })
    after_repeat = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": d_from, "date_to": d_to}).json()
    assert after_repeat["cash_in_ota_payouts"] == after["cash_in_ota_payouts"]


# --- 19/20: AI Strategist + Autonomous Actions unchanged ---

def test_ai_strategist_healthy_after_ota_reconciliation_activity(headers):
    for path in ("/api/v1/reports/ai-strategist", "/api/v1/reports/ai-strategist/decisions", "/api/v1/reports/ai-strategist/narrative"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_autonomous_actions_unchanged_after_ota_reconciliation_activity(headers):
    before = _action_proposals_count()
    _, unit_id = _make_property_and_unit(headers)
    booking, _ = _make_booking(headers, unit_id, rate=2000, nights=1)
    commission = _make_commission(headers, booking["id"], percent=10, commission=180, gst=32.4)
    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 1787.6, "receipt_date": date.today().isoformat(), "status": "matched",
    })
    after = _action_proposals_count()
    assert after == before
