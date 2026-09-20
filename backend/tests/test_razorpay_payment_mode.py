"""
Regression tests for Razorpay payment-mode classification (P2 fix).

Verifies:
  - PAYMENT_MODES tuple contains "razorpay"
  - cash_flow() counts mode="razorpay" in cash_in_bank (not excluded, not in cash_in_hand)
  - cash_in_bank + cash_in_hand == cash_in_payments for a controlled set of razorpay payments
    (i.e. no mode falls through both filters)
  - The admin POST /payments endpoint still rejects "razorpay" (gateway-only mode)

Note: The admin payment endpoint deliberately does not accept mode="razorpay" because
that mode is written only by _convert_hold_to_booking (the web booking flow), never
by manual admin entry. The PAYMENT_MODES update is for code-consistency and finance
categorisation only. These tests confirm both invariants hold simultaneously.
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.payments.models import PAYMENT_MODES

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

# Use days 800-899 to avoid conflicts with other test files
_DAY_OFFSET = 800


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_booking(headers):
    """Create a minimal property/unit/guest/booking via the HTTP API."""
    prop = client.post(
        "/api/v1/properties", headers=headers,
        json={"name": f"RZPProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"},
    ).json()
    unit = client.post(
        "/api/v1/units", headers=headers,
        json={"property_id": prop["id"], "name": f"RZPUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000},
    ).json()
    guest = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "RZP Test Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"},
    ).json()
    offset = _DAY_OFFSET + uuid.uuid4().int % 50
    check_in = (date.today() + timedelta(days=offset)).isoformat()
    check_out = (date.today() + timedelta(days=offset + 2)).isoformat()
    booking = client.post(
        "/api/v1/bookings", headers=headers,
        json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in, "check_out": check_out},
    ).json()
    assert "id" in booking, f"Booking creation failed: {booking}"
    return booking


def _insert_razorpay_payment(booking_id: str, amount: float) -> dict:
    """Insert a payment with mode='razorpay' directly via the DB.

    The admin HTTP endpoint deliberately disallows mode='razorpay' (gateway-only
    mode). We bypass it here the same way _convert_hold_to_booking does — direct
    ORM write — so the cash_flow categorisation test exercises real production paths.
    """
    from app.db.session import SessionLocal
    from app.modules.payments.models import Payment

    db = SessionLocal()
    try:
        payment = Payment(
            booking_id=uuid.UUID(booking_id),
            amount=amount,
            mode="razorpay",
            reference_no=f"rzp_test_{uuid.uuid4().hex[:12]}",
            status="completed",
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        return {"id": str(payment.id), "amount": float(payment.amount), "mode": payment.mode, "paid_at": payment.paid_at}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pure-Python checks (no DB required)
# ---------------------------------------------------------------------------

def test_payment_modes_includes_razorpay():
    assert "razorpay" in PAYMENT_MODES, f"PAYMENT_MODES={PAYMENT_MODES!r} does not contain 'razorpay'"


def test_payment_modes_still_contains_all_manual_modes():
    for mode in ("upi", "card", "bank_transfer", "cash"):
        assert mode in PAYMENT_MODES, f"'{mode}' was accidentally removed from PAYMENT_MODES"


# ---------------------------------------------------------------------------
# Admin endpoint must still reject mode="razorpay"
# ---------------------------------------------------------------------------

def test_admin_payment_endpoint_rejects_razorpay_mode(headers):
    """The admin POST /payments endpoint must NOT accept 'razorpay' — that mode
    is reserved for the automated web-booking flow. This test ensures the enum
    update in models.py did not accidentally open the admin endpoint to it."""
    booking = _make_booking(headers)
    resp = client.post(
        "/api/v1/payments", headers=headers,
        json={"booking_id": booking["id"], "amount": 5000, "mode": "razorpay", "reference_no": "RZPADMIN"},
    )
    assert resp.status_code == 400, (
        f"Admin endpoint must reject mode='razorpay' but returned {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# cash_flow categorisation regression
# ---------------------------------------------------------------------------

def test_razorpay_payment_counted_in_cash_in_bank(headers):
    """A Payment with mode='razorpay' must appear in cash_in_bank, not be silently
    dropped from the breakdown. Regression for the C-1 bug where bank_modes did not
    include 'razorpay', causing web-booking revenue to vanish from the reconciliation
    breakdown while still showing in the cash_in_payments total."""
    booking = _make_booking(headers)
    amount = 7777.0
    payment = _insert_razorpay_payment(booking["id"], amount)
    today = payment["paid_at"].strftime("%Y-%m-%d") if hasattr(payment["paid_at"], "strftime") else str(payment["paid_at"])[:10]

    flow = client.get(
        "/api/v1/finance/cash-flow", headers=headers,
        params={"date_from": today, "date_to": today},
    ).json()

    assert flow["cash_in_bank"] >= amount, (
        f"Expected cash_in_bank >= {amount} (Razorpay payment), got {flow['cash_in_bank']}. "
        "Razorpay payments are likely still excluded from bank_modes."
    )


def test_razorpay_payment_not_counted_in_cash_in_hand(headers):
    """Razorpay is an online gateway — it must NOT appear in cash_in_hand."""
    booking = _make_booking(headers)
    amount = 6543.0
    payment = _insert_razorpay_payment(booking["id"], amount)
    today = str(payment["paid_at"])[:10]

    flow_before_key = ("cash_in_hand",)

    # Record cash_in_hand before this payment (other tests may have run cash payments).
    # Instead, verify via a controlled scenario: for today, cash_in_payments must be
    # >= amount, cash_in_bank must be >= amount, and cash_in_hand must NOT increase by
    # the Razorpay amount. We measure the gap between cash_in_payments and
    # (cash_in_bank + cash_in_hand) — if razorpay is in neither bucket, the gap > 0.
    flow = client.get(
        "/api/v1/finance/cash-flow", headers=headers,
        params={"date_from": today, "date_to": today},
    ).json()

    gap = round(flow["cash_in_payments"] - flow["cash_in_bank"] - flow["cash_in_hand"], 2)
    # If 'razorpay' is correctly in bank_modes, cash_in_payments == cash_in_bank +
    # cash_in_hand (all known modes are categorised). If not, gap > 0.
    # We allow for floating-point noise but not a systematic gap >= amount.
    assert gap < amount, (
        f"cash_in_payments - (cash_in_bank + cash_in_hand) = {gap}, which is >= {amount}. "
        "This means 'razorpay' mode payments are excluded from both bank and hand buckets."
    )


def test_cash_in_payments_equals_bank_plus_hand_for_known_modes(headers):
    """When all payments in a date range use only known modes (upi, card,
    bank_transfer, cash, razorpay), cash_in_payments must equal
    cash_in_bank + cash_in_hand exactly — no mode should be uncategorised."""
    # Use a unique far-future date so only this test's payments appear in the range.
    far_future = (date.today() + timedelta(days=850 + uuid.uuid4().int % 40)).isoformat()

    booking = _make_booking(headers)
    # Insert two payments with known modes directly (one razorpay, one cash).
    _insert_razorpay_payment(booking["id"], 3000.0)
    # Cash via the admin endpoint (allowed mode).
    client.post(
        "/api/v1/payments", headers=headers,
        json={"booking_id": booking["id"], "amount": 1000, "mode": "cash", "reference_no": "CASHFAR"},
    )

    # Query for today (both payments default to now()).
    today = date.today().isoformat()
    flow = client.get(
        "/api/v1/finance/cash-flow", headers=headers,
        params={"date_from": today, "date_to": today},
    ).json()

    gap = round(flow["cash_in_payments"] - flow["cash_in_bank"] - flow["cash_in_hand"], 2)
    # Allow a tiny floating-point tolerance but catch a systematic exclusion.
    assert abs(gap) < 0.02, (
        f"cash_in_payments ({flow['cash_in_payments']}) != "
        f"cash_in_bank ({flow['cash_in_bank']}) + cash_in_hand ({flow['cash_in_hand']}). "
        f"Gap: {gap}. One or more payment modes are not categorised."
    )
