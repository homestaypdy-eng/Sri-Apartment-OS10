"""
Tests for the public booking payment gate.

Covers:
  1.  Public booking without payment cannot become confirmed
  2.  Expired hold cannot become confirmed
  3.  Valid payment converts hold to booking
  4.  Invalid Razorpay signature rejected
  5.  Wrong Razorpay order ID rejected
  6.  Maintenance unit cannot be booked (public flow)
  7.  Blocked unit cannot be booked (public flow)
  8.  Overlapping holds cannot both succeed
  9.  Overlapping confirmed bookings remain impossible
  10. Server-side price cannot be overridden by client
  11. GST remains server-calculated
  12. Existing admin booking flow still works (with unit status fix)
  13. Existing payment gateway tests still pass
  14. Admin booking on maintenance unit rejected

Run with: pytest tests/test_public_booking_payment_gate.py -v
"""
import hashlib
import hmac
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"

FAKE_RAZORPAY_KEY_SECRET = "test_key_secret_for_unit_tests"
FAKE_ORDER_ID = "order_test_fake123"
FAKE_PAYMENT_ID = "pay_test_fake456"


def _make_signature(order_id: str, payment_id: str, secret: str) -> str:
    payload = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_available_unit(headers, base_rate=2000):
    prop = client.post(
        "/api/v1/properties", headers=headers,
        json={"name": f"PGTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}
    ).json()
    unit = client.post(
        "/api/v1/units", headers=headers,
        json={"property_id": prop["id"], "name": f"UGTest {uuid.uuid4().hex[:6]}", "base_rate": base_rate}
    ).json()
    return prop["id"], unit["id"]


def _make_guest_and_unit(headers):
    _, unit_id = _make_available_unit(headers)
    guest = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "Admin Guest", "phone": f"88{uuid.uuid4().int % 10**8:08d}"}
    ).json()
    return unit_id, guest["id"]


def _create_hold_mocked(unit_id, check_in, check_out, guest_phone=None):
    """Creates a hold using mocked Razorpay create_order."""
    if guest_phone is None:
        guest_phone = f"77{uuid.uuid4().int % 10**8:08d}"

    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"

    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_name": "Test Guest",
            "guest_phone": guest_phone,
            "guest_email": "test@example.com",
        })
    return resp, fake_order_id


# ---------------------------------------------------------------------------
# Test 1: Public booking without payment cannot become confirmed
# ---------------------------------------------------------------------------

def test_hold_without_payment_does_not_create_booking(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=300)
    check_out = check_in + timedelta(days=2)

    resp, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 201, resp.text

    hold_id = resp.json()["hold_id"]

    # Verify no booking exists for these dates without calling verify-payment
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        bookings = db.scalars(
            sa_select(Booking).where(Booking.unit_id == unit_id)
        ).all()
        confirmed = [b for b in bookings if b.status == "confirmed"]
        assert len(confirmed) == 0, "Booking was confirmed without payment verification"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Test 2: Expired hold cannot become confirmed
# ---------------------------------------------------------------------------

def test_expired_hold_cannot_be_confirmed(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=301)
    check_out = check_in + timedelta(days=2)

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    # Manually expire the hold in DB
    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold_id)
        hold.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    sig = _make_signature(fake_order_id, FAKE_PAYMENT_ID, FAKE_RAZORPAY_KEY_SECRET)
    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": FAKE_PAYMENT_ID,
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": sig,
        })

    assert verify_resp.status_code == 410, verify_resp.text
    assert "expired" in verify_resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test 3: Valid payment converts hold to confirmed booking
# ---------------------------------------------------------------------------

def test_valid_payment_converts_hold_to_booking(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=302)
    check_out = check_in + timedelta(days=3)
    phone = f"76{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    sig = _make_signature(fake_order_id, FAKE_PAYMENT_ID, FAKE_RAZORPAY_KEY_SECRET)

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": FAKE_PAYMENT_ID,
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": sig,
        })

    assert verify_resp.status_code == 201, verify_resp.text
    data = verify_resp.json()
    assert "booking_id" in data
    assert "invoice_no" in data
    assert data["invoice_no"] is not None

    # Confirm booking exists in DB with status=confirmed
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.public_booking.models import BookingHold
    db = SessionLocal()
    try:
        booking = db.get(Booking, data["booking_id"])
        assert booking is not None
        assert booking.status == "confirmed"
        assert booking.source == "website"

        hold = db.get(BookingHold, hold_id)
        assert hold.status == "converted"
        assert hold.booking_id == booking.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Test 4: Invalid Razorpay signature rejected
# ---------------------------------------------------------------------------

def test_invalid_razorpay_signature_rejected(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=305)
    check_out = check_in + timedelta(days=2)

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = False  # signature check fails
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": FAKE_PAYMENT_ID,
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": "invalid_signature_xyz",
        })

    assert verify_resp.status_code == 400, verify_resp.text
    assert "signature" in verify_resp.json()["detail"].lower()

    # Hold must still be pending — no booking created
    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold_id)
        assert hold.status == "pending_payment"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Test 5: Wrong Razorpay order ID rejected
# ---------------------------------------------------------------------------

def test_wrong_razorpay_order_id_rejected(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=308)
    check_out = check_in + timedelta(days=2)

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    wrong_order_id = "order_completely_different_xyz"

    # No need to mock verify_payment_signature — the order_id mismatch check
    # fires before the signature check
    verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
        "razorpay_payment_id": FAKE_PAYMENT_ID,
        "razorpay_order_id": wrong_order_id,
        "razorpay_signature": "any_sig",
    })

    assert verify_resp.status_code == 400, verify_resp.text
    assert "order_id" in verify_resp.json()["detail"].lower() or "order" in verify_resp.json()["detail"].lower()

    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold_id)
        assert hold.status == "pending_payment"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Test 6: Maintenance unit cannot be booked (public flow)
# ---------------------------------------------------------------------------

def test_maintenance_unit_cannot_be_held(headers):
    _, unit_id = _make_available_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "maintenance"})

    resp = client.post("/api/v1/public/holds", json={
        "unit_id": str(unit_id),
        "check_in": (date.today() + timedelta(days=400)).isoformat(),
        "check_out": (date.today() + timedelta(days=402)).isoformat(),
        "guest_name": "Blocked Guest",
        "guest_phone": f"75{uuid.uuid4().int % 10**8:08d}",
    })
    assert resp.status_code == 409, resp.text
    assert "maintenance" in resp.json()["detail"].lower()


def test_maintenance_unit_availability_returns_false(headers):
    _, unit_id = _make_available_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "maintenance"})

    resp = client.get("/api/v1/public/availability", params={
        "unit_id": str(unit_id),
        "check_in": (date.today() + timedelta(days=400)).isoformat(),
        "check_out": (date.today() + timedelta(days=402)).isoformat(),
    })
    assert resp.status_code == 200
    assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# Test 7: Blocked unit cannot be booked (public flow)
# ---------------------------------------------------------------------------

def test_blocked_unit_cannot_be_held(headers):
    _, unit_id = _make_available_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "blocked"})

    resp = client.post("/api/v1/public/holds", json={
        "unit_id": str(unit_id),
        "check_in": (date.today() + timedelta(days=410)).isoformat(),
        "check_out": (date.today() + timedelta(days=412)).isoformat(),
        "guest_name": "Blocked Guest",
        "guest_phone": f"74{uuid.uuid4().int % 10**8:08d}",
    })
    assert resp.status_code == 409, resp.text
    assert "blocked" in resp.json()["detail"].lower()


def test_blocked_unit_availability_returns_false(headers):
    _, unit_id = _make_available_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "blocked"})

    resp = client.get("/api/v1/public/availability", params={
        "unit_id": str(unit_id),
        "check_in": (date.today() + timedelta(days=410)).isoformat(),
        "check_out": (date.today() + timedelta(days=412)).isoformat(),
    })
    assert resp.status_code == 200
    assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# Test 8: Overlapping holds cannot both succeed
# ---------------------------------------------------------------------------

def test_overlapping_holds_only_one_succeeds(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=420)
    check_out = check_in + timedelta(days=3)

    resp1, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp1.status_code == 201, resp1.text

    # Second hold for same unit+dates must fail
    resp2, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp2.status_code == 409, resp2.text


# ---------------------------------------------------------------------------
# Test 9: Overlapping confirmed bookings remain impossible
# ---------------------------------------------------------------------------

def test_confirmed_booking_blocks_new_hold(headers):
    unit_id, guest_id = _make_guest_and_unit(headers)
    check_in = date.today() + timedelta(days=430)
    check_out = check_in + timedelta(days=3)

    # Admin creates confirmed booking
    booking_resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id),
        "guest_id": str(guest_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    })
    assert booking_resp.status_code == 201

    # Now try to create a hold for same dates — should be rejected by application check
    resp, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 409, resp.text


def test_admin_overlapping_booking_rejected_by_db_constraint(headers):
    unit_id, guest_id = _make_guest_and_unit(headers)
    check_in = date.today() + timedelta(days=440)
    check_out = check_in + timedelta(days=3)

    r1 = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id), "guest_id": str(guest_id),
        "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    })
    assert r1.status_code == 201

    r2 = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id), "guest_id": str(guest_id),
        "check_in": (check_in + timedelta(days=1)).isoformat(),
        "check_out": (check_out + timedelta(days=1)).isoformat(),
    })
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Test 10: Server-side price cannot be overridden by client
# ---------------------------------------------------------------------------

def test_client_cannot_override_price(headers):
    _, unit_id = _make_available_unit(headers, base_rate=2000)
    check_in = date.today() + timedelta(days=450)
    check_out = check_in + timedelta(days=3)  # 3 nights × 2000 = 6000

    # CreateHoldRequest has no price field — FastAPI ignores unknown fields.
    # Attempt to pass a fake "total" field that should be ignored.
    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_name": "Price Hack Attempt",
            "guest_phone": f"73{uuid.uuid4().int % 10**8:08d}",
            "total": 1.0,          # attacker-supplied — must be ignored
            "amount": 1.0,         # attacker-supplied — must be ignored
            "advance_amount": 1.0, # attacker-supplied — must be ignored
        })

    assert resp.status_code == 201, resp.text
    data = resp.json()
    # Server-computed amount must be based on unit.base_rate × nights + GST
    # base=2000, 3 nights → subtotal=6000, 5% GST → total=6300
    assert data["amount"] == 6300.0, f"Expected 6300.0, got {data['amount']}"


def test_quote_amount_matches_hold_amount(headers):
    _, unit_id = _make_available_unit(headers, base_rate=2000)
    check_in = date.today() + timedelta(days=460)
    check_out = check_in + timedelta(days=2)

    quote_resp = client.get("/api/v1/public/quote", params={
        "unit_id": str(unit_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    })
    assert quote_resp.status_code == 200
    expected_total = quote_resp.json()["total"]

    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        hold_resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_name": "Quote Match Guest",
            "guest_phone": f"72{uuid.uuid4().int % 10**8:08d}",
        })
    assert hold_resp.status_code == 201
    assert hold_resp.json()["amount"] == expected_total


# ---------------------------------------------------------------------------
# Test 11: GST remains server-calculated
# ---------------------------------------------------------------------------

def test_gst_server_calculated_high_tariff(headers):
    """Unit with base_rate > 7500 must use 18% GST slab."""
    _, unit_id = _make_available_unit(headers, base_rate=8000)
    check_in = date.today() + timedelta(days=470)
    check_out = check_in + timedelta(days=1)  # 1 night × 8000 = 8000

    resp = client.get("/api/v1/public/quote", params={
        "unit_id": str(unit_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["gst_rate_percent"] == 18.0
    assert data["subtotal_after_discount"] == 8000.0
    assert data["total"] == 9440.0  # 8000 × 1.18


def test_gst_server_calculated_low_tariff(headers):
    """Unit with base_rate <= 1000 must use 0% GST slab."""
    _, unit_id = _make_available_unit(headers, base_rate=900)
    check_in = date.today() + timedelta(days=475)
    check_out = check_in + timedelta(days=2)

    resp = client.get("/api/v1/public/quote", params={
        "unit_id": str(unit_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["gst_rate_percent"] == 0.0
    assert data["total"] == data["subtotal_after_discount"]


# ---------------------------------------------------------------------------
# Test 12: Existing admin booking flow still works
# ---------------------------------------------------------------------------

def test_admin_booking_flow_unchanged(headers):
    """Admin/staff can still create confirmed bookings directly without Razorpay."""
    unit_id, guest_id = _make_guest_and_unit(headers)
    check_in = date.today() + timedelta(days=500)
    check_out = check_in + timedelta(days=2)

    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id),
        "guest_id": str(guest_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "confirmed"
    assert data["source"] in ("direct", "walk_in", "phone", "whatsapp")


# ---------------------------------------------------------------------------
# Test 13: Existing payment gateway tests still pass (regression guards)
# ---------------------------------------------------------------------------

def test_webhook_signature_verification_still_correct():
    from app.modules.payment_gateway.service import verify_webhook_signature
    secret = "test_secret_123"
    payload = b'{"event":"payment.captured"}'
    correct_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(payload, correct_sig, webhook_secret=secret) is True
    assert verify_webhook_signature(payload, "wrong", webhook_secret=secret) is False


def test_verify_payment_signature_function():
    """New function — verifies client-payment HMAC with key_secret."""
    from app.modules.payment_gateway.service import verify_payment_signature
    order_id = "order_abc"
    payment_id = "pay_xyz"
    secret = "my_key_secret"
    payload = f"{order_id}|{payment_id}".encode()
    correct_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert verify_payment_signature(order_id, payment_id, correct_sig, key_secret=secret) is True
    assert verify_payment_signature(order_id, payment_id, "wrong_sig", key_secret=secret) is False


def test_create_order_still_fails_without_credentials():
    from app.modules.payment_gateway.service import create_order, GatewayNotConfiguredError
    with pytest.raises(GatewayNotConfiguredError):
        create_order(1000, "receipt_test")


def test_webhook_endpoint_still_returns_501_without_configuration():
    resp = client.post(
        "/api/v1/payment-gateway/webhook",
        content=b'{"event":"payment.captured"}',
        headers={"X-Razorpay-Signature": "irrelevant", "Content-Type": "application/json"},
    )
    assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Test 14: Admin booking on maintenance/blocked unit rejected
# ---------------------------------------------------------------------------

def test_admin_cannot_book_maintenance_unit(headers):
    unit_id, guest_id = _make_guest_and_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "maintenance"})

    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id),
        "guest_id": str(guest_id),
        "check_in": (date.today() + timedelta(days=600)).isoformat(),
        "check_out": (date.today() + timedelta(days=602)).isoformat(),
    })
    assert resp.status_code == 409, resp.text
    assert "maintenance" in resp.json()["detail"].lower()


def test_admin_cannot_book_blocked_unit(headers):
    unit_id, guest_id = _make_guest_and_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "blocked"})

    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id),
        "guest_id": str(guest_id),
        "check_in": (date.today() + timedelta(days=610)).isoformat(),
        "check_out": (date.today() + timedelta(days=612)).isoformat(),
    })
    assert resp.status_code == 409, resp.text
    assert "blocked" in resp.json()["detail"].lower()


def test_admin_availability_check_maintenance_unit(headers):
    unit_id, _ = _make_guest_and_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "maintenance"})

    resp = client.get(f"/api/v1/units/{unit_id}/availability", headers=headers, params={
        "check_in": (date.today() + timedelta(days=620)).isoformat(),
        "check_out": (date.today() + timedelta(days=622)).isoformat(),
    })
    assert resp.status_code == 200
    assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# P0-1: Payment record created after hold → booking conversion
# ---------------------------------------------------------------------------

def test_payment_record_created_after_hold_conversion(headers):
    """A Payment row with mode='razorpay' must exist after verify-payment succeeds."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=700)
    check_out = check_in + timedelta(days=2)
    phone = f"71{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": FAKE_PAYMENT_ID,
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": "sig",
        })

    assert verify_resp.status_code == 201, verify_resp.text
    booking_id = verify_resp.json()["booking_id"]

    from app.db.session import SessionLocal
    from app.modules.payments.models import Payment
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        payments = db.scalars(sa_select(Payment).where(Payment.booking_id == booking_id)).all()
        assert len(payments) == 1, f"Expected 1 payment, got {len(payments)}"
        pmt = payments[0]
        assert pmt.status == "completed"
        assert pmt.mode == "razorpay"
        assert pmt.reference_no == FAKE_PAYMENT_ID
        assert float(pmt.amount) > 0
    finally:
        db.close()


def test_payment_visible_to_admin_after_web_booking(headers):
    """Admin payments list for a web-booking must include the Razorpay payment."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=703)
    check_out = check_in + timedelta(days=2)
    phone = f"70{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": FAKE_PAYMENT_ID,
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": "sig",
        })

    assert verify_resp.status_code == 201
    booking_id = verify_resp.json()["booking_id"]

    payments_resp = client.get(f"/api/v1/payments/{booking_id}", headers=headers)
    assert payments_resp.status_code == 200
    payments = payments_resp.json()
    assert len(payments) >= 1
    assert any(p["mode"] == "razorpay" for p in payments)


# ---------------------------------------------------------------------------
# P0-2: dev_magic_token hidden in production environment
# ---------------------------------------------------------------------------

def test_dev_magic_token_hidden_in_production(headers):
    """In production environment, dev_magic_token must be None — never leaked in response."""
    phone = f"69{uuid.uuid4().int % 10**8:08d}"
    client.post("/api/v1/guests", headers=headers, json={"name": "P02 Prod Guest", "phone": phone})

    with patch("app.modules.portal.router.settings") as mock_settings:
        mock_settings.environment = "production"
        resp = client.post("/api/v1/portal/request-login", json={"phone": phone})

    assert resp.status_code == 200
    assert resp.json()["dev_magic_token"] is None


def test_dev_magic_token_present_in_development(headers):
    """In development environment, dev_magic_token must be present for testability."""
    phone = f"68{uuid.uuid4().int % 10**8:08d}"
    client.post("/api/v1/guests", headers=headers, json={"name": "P02 Dev Guest", "phone": phone})

    with patch("app.modules.portal.router.settings") as mock_settings:
        mock_settings.environment = "development"
        resp = client.post("/api/v1/portal/request-login", json={"phone": phone})

    assert resp.status_code == 200
    assert resp.json()["dev_magic_token"] is not None


# ---------------------------------------------------------------------------
# P0-3: razorpay_key_id present in HoldOut response
# ---------------------------------------------------------------------------

def test_hold_response_includes_razorpay_key_id(headers):
    """HoldOut must include razorpay_key_id so the frontend can initialise Razorpay checkout."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=720)
    check_out = check_in + timedelta(days=2)

    resp, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 201
    data = resp.json()

    assert "razorpay_key_id" in data
    # In the test environment RAZORPAY_KEY_ID is empty — field must still be present (as null)
    assert data["razorpay_key_id"] is None


# ---------------------------------------------------------------------------
# P0-4: Expired hold does not permanently block new holds
# ---------------------------------------------------------------------------

def test_expired_hold_does_not_block_new_hold(headers):
    """An expired hold must be cleaned up automatically, not block new guests."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=730)
    check_out = check_in + timedelta(days=2)

    resp1, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp1.status_code == 201
    hold1_id = resp1.json()["hold_id"]

    # Manually expire hold in DB (simulates TTL elapse)
    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold1_id)
        hold.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    # New hold for same unit/dates must succeed after the expired one is cleaned
    resp2, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp2.status_code == 201, f"Expected 201, got {resp2.status_code}: {resp2.text}"


def test_expired_hold_status_updated_to_expired_on_next_create(headers):
    """After a new hold succeeds, the old hold's status must be 'expired'."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=733)
    check_out = check_in + timedelta(days=2)

    resp1, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp1.status_code == 201
    hold1_id = resp1.json()["hold_id"]

    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold1_id)
        hold.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    _create_hold_mocked(unit_id, check_in, check_out)  # triggers cleanup

    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold1_id)
        assert hold.status == "expired", f"Expected 'expired', got '{hold.status}'"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# P1-1: Idempotent verify-payment (network retry safety)
# ---------------------------------------------------------------------------

def test_verify_payment_is_idempotent(headers):
    """Calling verify-payment twice on the same hold must return the same booking, not 409."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=740)
    check_out = check_in + timedelta(days=2)
    phone = f"65{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    verify_payload = {
        "razorpay_payment_id": FAKE_PAYMENT_ID,
        "razorpay_order_id": fake_order_id,
        "razorpay_signature": "sig",
    }

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        resp1 = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)
    assert resp1.status_code == 201, resp1.text

    # Second call — hold is now "converted"
    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        resp2 = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)

    assert resp2.status_code == 201, f"Expected 201 on retry, got {resp2.status_code}: {resp2.text}"
    assert resp1.json()["booking_id"] == resp2.json()["booking_id"]

    # Exactly one booking and one payment must exist — no duplicates
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        bookings = db.scalars(sa_select(Booking).where(
            Booking.unit_id == unit_id, Booking.status == "confirmed"
        )).all()
        assert len(bookings) == 1, f"Expected 1 booking, got {len(bookings)}"

        payments = db.scalars(sa_select(Payment).where(Payment.booking_id == bookings[0].id)).all()
        assert len(payments) == 1, f"Expected 1 payment, got {len(payments)}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# P1-3: Inquiry → booking conversion blocked on non-available units
# ---------------------------------------------------------------------------

def test_inquiry_convert_blocked_on_maintenance_unit(headers):
    """Inquiry conversion must be rejected with 409 when the unit is in maintenance."""
    _, unit_id = _make_available_unit(headers, base_rate=2000)
    phone = f"64{uuid.uuid4().int % 10**8:08d}"

    inquiry_resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Inquiry Maintenance Test",
        "client_phone": phone,
        "unit_id": str(unit_id),
        "flat_room_label": "Test Room",
        "check_in_date": (date.today() + timedelta(days=750)).isoformat(),
        "check_out_date": (date.today() + timedelta(days=752)).isoformat(),
        "source": "phone",
        "priority": "medium",
        "advance_amount": 1000,
    })
    assert inquiry_resp.status_code == 201, inquiry_resp.text
    inquiry_id = inquiry_resp.json()["id"]

    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "maintenance"})

    convert_resp = client.post(
        f"/api/v1/inquiries/{inquiry_id}/convert-to-booking",
        headers=headers, json={},
    )
    assert convert_resp.status_code == 409, convert_resp.text
    assert "maintenance" in convert_resp.json()["detail"].lower()


def test_inquiry_convert_blocked_on_blocked_unit(headers):
    """Inquiry conversion must be rejected with 409 when the unit is blocked."""
    _, unit_id = _make_available_unit(headers, base_rate=2000)
    phone = f"63{uuid.uuid4().int % 10**8:08d}"

    inquiry_resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Inquiry Blocked Test",
        "client_phone": phone,
        "unit_id": str(unit_id),
        "flat_room_label": "Test Room",
        "check_in_date": (date.today() + timedelta(days=760)).isoformat(),
        "check_out_date": (date.today() + timedelta(days=762)).isoformat(),
        "source": "phone",
        "priority": "medium",
        "advance_amount": 1000,
    })
    assert inquiry_resp.status_code == 201, inquiry_resp.text
    inquiry_id = inquiry_resp.json()["id"]

    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "blocked"})

    convert_resp = client.post(
        f"/api/v1/inquiries/{inquiry_id}/convert-to-booking",
        headers=headers, json={},
    )
    assert convert_resp.status_code == 409, convert_resp.text
    assert "blocked" in convert_resp.json()["detail"].lower()


def test_inquiry_convert_succeeds_on_available_unit(headers):
    """Control: inquiry conversion on an available unit must still produce a confirmed booking."""
    _, unit_id = _make_available_unit(headers, base_rate=2000)
    phone = f"62{uuid.uuid4().int % 10**8:08d}"

    inquiry_resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Inquiry OK Test",
        "client_phone": phone,
        "unit_id": str(unit_id),
        "flat_room_label": "Test Room",
        "check_in_date": (date.today() + timedelta(days=770)).isoformat(),
        "check_out_date": (date.today() + timedelta(days=772)).isoformat(),
        "source": "phone",
        "priority": "medium",
        "advance_amount": 1000,
    })
    assert inquiry_resp.status_code == 201, inquiry_resp.text
    inquiry_id = inquiry_resp.json()["id"]

    convert_resp = client.post(
        f"/api/v1/inquiries/{inquiry_id}/convert-to-booking",
        headers=headers, json={},
    )
    assert convert_resp.status_code == 201, convert_resp.text
    assert "booking_id" in convert_resp.json()


# ---------------------------------------------------------------------------
# P1-5 & P1-6: Production startup validation
# ---------------------------------------------------------------------------

def test_validate_production_config_blocks_default_jwt():
    """RuntimeError must be raised in production when JWT is still the default placeholder."""
    from app.main import _validate_production_config, DEFAULT_JWT_SECRET
    with patch("app.main.settings") as mock_settings:
        mock_settings.environment = "production"
        mock_settings.jwt_secret_key = DEFAULT_JWT_SECRET
        mock_settings.cors_allowed_origins = "https://myapp.example.com"
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            _validate_production_config()


def test_validate_production_config_blocks_localhost_cors():
    """RuntimeError must be raised in production when CORS is localhost-only."""
    from app.main import _validate_production_config
    with patch("app.main.settings") as mock_settings:
        mock_settings.environment = "production"
        mock_settings.jwt_secret_key = "a_real_secret_not_the_default"
        mock_settings.cors_allowed_origins = "http://localhost:5173"
        with pytest.raises(RuntimeError, match="CORS"):
            _validate_production_config()


def test_validate_production_config_passes_in_development():
    """No RuntimeError in development even with default JWT and localhost CORS."""
    from app.main import _validate_production_config, DEFAULT_JWT_SECRET
    with patch("app.main.settings") as mock_settings:
        mock_settings.environment = "development"
        mock_settings.jwt_secret_key = DEFAULT_JWT_SECRET
        mock_settings.cors_allowed_origins = "http://localhost:5173"
        _validate_production_config()  # must not raise


def test_validate_production_config_passes_with_real_production_settings():
    """No RuntimeError when production config has proper JWT and real domain in CORS."""
    from app.main import _validate_production_config
    with patch("app.main.settings") as mock_settings:
        mock_settings.environment = "production"
        mock_settings.jwt_secret_key = "a_real_secret_not_the_default"
        mock_settings.cors_allowed_origins = "https://myapp.sriapartment.com"
        _validate_production_config()  # must not raise
