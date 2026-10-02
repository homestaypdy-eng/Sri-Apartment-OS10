"""
Finance remediation: Payment.mode="zotel" was silently uncategorised in
finance/service.py::cash_flow() — counted in cash_in_payments (an
unconditional sum over every Payment row in range) but excluded from both
cash_in_bank and cash_in_hand (neither bucket's mode filter recognised
it), breaking the cash_in_payments == cash_in_bank + cash_in_hand
invariant whenever a Zotel-sourced payment existed in the queried range.
Discovered during Phase 2B's own testing (a real Rs.6300 zotel payment
created by that phase's test data reproduced the gap exactly).

Fix: cash_in_payments/cash_in_bank/cash_in_hand/cash_in_total are now
computed from PAYMENT_MODES-scoped payments only (upi/card/bank_transfer/
cash/razorpay) — the actual bank/hand instrument set, per
payments/models.py. mode="zotel" is deliberately excluded from that cash
figure (folding it in would double-count against cash_in_ota_payouts once
an OTAPayoutReceipt eventually reconciles the same revenue) but is not
silently dropped from the report either: a new, explicitly-separate
cash_in_zotel_pending field surfaces it.

Every test below uses its own unique, explicit paid_at date/time (not
"today") and queries cash_flow() for that exact date — this is the
correct isolation technique against a shared test database (relying on a
booking's check-in date does NOT isolate Payment.paid_at, which always
defaults to "now" regardless — the exact mistake that let this defect go
undetected in the pre-existing test suite).

SAFETY: no external call anywhere in this file — every payment is
inserted directly via the ORM (matching test_razorpay_payment_mode.py's
own established pattern for razorpay-mode rows) or via the local
/finance/cash-flow and /payments endpoints, neither of which reaches any
external service.

Run with: pytest tests/test_finance_cash_flow_zotel_mode.py -v
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.session import SessionLocal
from app.modules.payments.models import Payment
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_sends(monkeypatch):
    # POST /payments (used by one test below) calls trigger_payment_received,
    # which best-effort-emails BusinessSettings.contact_email if that row
    # already exists (created by any earlier test in this session) — mocked
    # here regardless, so this file never depends on session ordering.
    monkeypatch.setattr("app.modules.notifications.service.send_email", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_whatsapp", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_sms", lambda *a, **kw: None)


# Each test gets its own isolated calendar date, far from any other test
# file's range, so paid_at-based cash_flow() queries can never see another
# test's payments.
_BASE_DATE = date(2031, 6, 1)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_booking(headers, day_offset):
    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"CFZProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"CFZUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers,
                         json={"name": "CFZ Test Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = (_BASE_DATE + timedelta(days=day_offset)).isoformat()
    check_out = (_BASE_DATE + timedelta(days=day_offset + 2)).isoformat()
    booking = client.post("/api/v1/bookings", headers=headers,
                           json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in, "check_out": check_out}).json()
    assert "id" in booking, f"Booking creation failed: {booking}"
    return booking


def _insert_payment(booking_id: str, amount: float, mode: str, paid_on: date) -> Payment:
    """Directly inserts a Payment with an explicit paid_at, so this test's
    cash-flow query window (paid_on..paid_on) can never see any other
    test's data regardless of run order."""
    db = SessionLocal()
    try:
        payment = Payment(
            booking_id=uuid.UUID(booking_id), amount=amount, mode=mode, status="completed",
            reference_no=f"TEST-{mode}-{uuid.uuid4().hex[:8]}",
            paid_at=datetime.combine(paid_on, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12),
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        return payment
    finally:
        db.close()


def _cash_flow(headers, on_date: date) -> dict:
    resp = client.get("/api/v1/finance/cash-flow", headers=headers,
                       params={"date_from": on_date.isoformat(), "date_to": on_date.isoformat()})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1-6: each individual payment mode, in isolation
# ---------------------------------------------------------------------------

def test_zotel_payment_excluded_from_bank_and_hand_but_visible_separately(headers):
    on = _BASE_DATE + timedelta(days=1)
    booking = _make_booking(headers, 1)
    _insert_payment(booking["id"], 6300.0, "zotel", on)

    flow = _cash_flow(headers, on)
    assert flow["cash_in_bank"] == 0.0
    assert flow["cash_in_hand"] == 0.0
    assert flow["cash_in_payments"] == 0.0  # not a bank/hand instrument
    assert flow["cash_in_zotel_pending"] == 6300.0  # but not silently dropped either
    assert flow["cash_in_total"] == 0.0  # not double-counted into the cash total


def test_cash_payment_counted_in_hand(headers):
    on = _BASE_DATE + timedelta(days=2)
    booking = _make_booking(headers, 2)
    client.post("/api/v1/payments", headers=headers,
                json={"booking_id": booking["id"], "amount": 1500, "mode": "cash", "reference_no": "CASHTEST"})
    # Backfill paid_at to our isolated date (the endpoint always uses now()).
    db = SessionLocal()
    try:
        p = db.scalar(select(Payment).where(Payment.booking_id == uuid.UUID(booking["id"])))
        p.paid_at = datetime.combine(on, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
        db.commit()
    finally:
        db.close()

    flow = _cash_flow(headers, on)
    assert flow["cash_in_hand"] == 1500.0
    assert flow["cash_in_bank"] == 0.0
    assert flow["cash_in_payments"] == 1500.0
    assert flow["cash_in_zotel_pending"] == 0.0


def test_upi_payment_counted_in_bank(headers):
    on = _BASE_DATE + timedelta(days=3)
    booking = _make_booking(headers, 3)
    _insert_payment(booking["id"], 2200.0, "upi", on)
    flow = _cash_flow(headers, on)
    assert flow["cash_in_bank"] == 2200.0
    assert flow["cash_in_hand"] == 0.0
    assert flow["cash_in_payments"] == 2200.0


def test_card_payment_counted_in_bank(headers):
    on = _BASE_DATE + timedelta(days=4)
    booking = _make_booking(headers, 4)
    _insert_payment(booking["id"], 3300.0, "card", on)
    flow = _cash_flow(headers, on)
    assert flow["cash_in_bank"] == 3300.0
    assert flow["cash_in_payments"] == 3300.0


def test_bank_transfer_payment_counted_in_bank(headers):
    on = _BASE_DATE + timedelta(days=5)
    booking = _make_booking(headers, 5)
    _insert_payment(booking["id"], 4400.0, "bank_transfer", on)
    flow = _cash_flow(headers, on)
    assert flow["cash_in_bank"] == 4400.0
    assert flow["cash_in_payments"] == 4400.0


def test_razorpay_payment_counted_in_bank(headers):
    on = _BASE_DATE + timedelta(days=6)
    booking = _make_booking(headers, 6)
    _insert_payment(booking["id"], 5500.0, "razorpay", on)
    flow = _cash_flow(headers, on)
    assert flow["cash_in_bank"] == 5500.0
    assert flow["cash_in_payments"] == 5500.0


# ---------------------------------------------------------------------------
# 7: mixed payment modes in one date range
# ---------------------------------------------------------------------------

def test_mixed_payment_modes_all_bucketed_correctly(headers):
    on = _BASE_DATE + timedelta(days=7)
    booking = _make_booking(headers, 7)
    _insert_payment(booking["id"], 1000.0, "cash", on)
    _insert_payment(booking["id"], 2000.0, "upi", on)
    _insert_payment(booking["id"], 3000.0, "card", on)
    _insert_payment(booking["id"], 4000.0, "bank_transfer", on)
    _insert_payment(booking["id"], 5000.0, "razorpay", on)
    _insert_payment(booking["id"], 6300.0, "zotel", on)

    flow = _cash_flow(headers, on)
    assert flow["cash_in_hand"] == 1000.0
    assert flow["cash_in_bank"] == 2000.0 + 3000.0 + 4000.0 + 5000.0
    assert flow["cash_in_payments"] == 1000.0 + 2000.0 + 3000.0 + 4000.0 + 5000.0  # zotel excluded
    assert flow["cash_in_zotel_pending"] == 6300.0
    # The core invariant this whole fix exists to restore:
    assert round(flow["cash_in_payments"] - flow["cash_in_bank"] - flow["cash_in_hand"], 2) == 0.0
    assert flow["cash_in_total"] == round(flow["cash_in_payments"] + flow["cash_in_deposits"] + flow["cash_in_ota_payouts"], 2)


# ---------------------------------------------------------------------------
# 8: same-day Zotel payment alongside known-mode payments (the exact
# real-world shape of the originally-discovered defect)
# ---------------------------------------------------------------------------

def test_same_day_zotel_payment_does_not_break_invariant_for_other_modes(headers):
    on = _BASE_DATE + timedelta(days=8)
    booking_a = _make_booking(headers, 8)
    booking_b = _make_booking(headers, 8)
    _insert_payment(booking_a["id"], 6300.0, "zotel", on)
    _insert_payment(booking_b["id"], 7000.0, "razorpay", on)

    flow = _cash_flow(headers, on)
    assert flow["cash_in_bank"] == 7000.0
    assert flow["cash_in_payments"] == 7000.0  # zotel's 6300 correctly excluded
    assert flow["cash_in_zotel_pending"] == 6300.0
    assert round(flow["cash_in_payments"] - flow["cash_in_bank"] - flow["cash_in_hand"], 2) == 0.0


# ---------------------------------------------------------------------------
# 9: Zotel payment does not disappear from the report entirely
# ---------------------------------------------------------------------------

def test_zotel_payment_remains_visible_not_silently_dropped(headers):
    on = _BASE_DATE + timedelta(days=9)
    booking = _make_booking(headers, 9)
    _insert_payment(booking["id"], 1234.56, "zotel", on)

    flow = _cash_flow(headers, on)
    # Not in the cash figures (correct — see module docstring)...
    assert flow["cash_in_payments"] == 0.0
    # ...but genuinely present and readable, not vanished.
    assert flow["cash_in_zotel_pending"] == 1234.56


# ---------------------------------------------------------------------------
# 10: existing cash-flow invariants remain correct with only known modes
# (regression guard — this is exactly what test_razorpay_payment_mode.py's
# own tests check, reproduced here with fully isolated dates)
# ---------------------------------------------------------------------------

def test_existing_invariant_holds_with_only_known_modes_no_zotel(headers):
    on = _BASE_DATE + timedelta(days=10)
    booking = _make_booking(headers, 10)
    _insert_payment(booking["id"], 1111.0, "upi", on)
    _insert_payment(booking["id"], 2222.0, "cash", on)

    flow = _cash_flow(headers, on)
    assert flow["cash_in_zotel_pending"] == 0.0
    assert round(flow["cash_in_payments"] - flow["cash_in_bank"] - flow["cash_in_hand"], 2) == 0.0
    assert flow["cash_in_payments"] == 1111.0 + 2222.0
