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
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)

FAKE_RAZORPAY_KEY_SECRET = "test_key_secret_for_unit_tests"
FAKE_ORDER_ID = "order_test_fake123"
FAKE_PAYMENT_ID = "pay_test_fake456"


def _make_signature(order_id: str, payment_id: str, secret: str) -> str:
    payload = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


@pytest.fixture(scope="module")
def headers():
    assert ADMIN_PASSWORD, "BOOTSTRAP_ADMIN_PASSWORD must be set for this test module to run"
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
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


def _create_hold_raw(unit_id, check_in, check_out, guest_name, guest_phone):
    """Like _create_hold_mocked, but exposes guest_name/guest_phone directly
    for validation-boundary tests (WEBSITE-04A)."""
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": f"order_{uuid.uuid4().hex[:12]}"}
        return client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_name": guest_name,
            "guest_phone": guest_phone,
            "guest_email": "test@example.com",
        })


# ---------------------------------------------------------------------------
# WEBSITE-04A: guest_name / guest_phone validation on the public hold endpoint
# ---------------------------------------------------------------------------

def test_create_hold_rejects_empty_guest_name(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=740)
    check_out = check_in + timedelta(days=1)
    resp = _create_hold_raw(unit_id, check_in, check_out, guest_name="", guest_phone="9876543210")
    assert resp.status_code == 422


def test_create_hold_rejects_oversized_guest_name(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=741)
    check_out = check_in + timedelta(days=1)
    resp = _create_hold_raw(unit_id, check_in, check_out, guest_name="A" * 151, guest_phone="9876543210")
    assert resp.status_code == 422


def test_create_hold_rejects_too_short_guest_phone(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=742)
    check_out = check_in + timedelta(days=1)
    resp = _create_hold_raw(unit_id, check_in, check_out, guest_name="Valid Name", guest_phone="123")
    assert resp.status_code == 422


def test_create_hold_rejects_oversized_guest_phone(headers):
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=743)
    check_out = check_in + timedelta(days=1)
    resp = _create_hold_raw(unit_id, check_in, check_out, guest_name="Valid Name", guest_phone="1" * 21)
    assert resp.status_code == 422


def test_create_hold_accepts_valid_boundary_length_name_and_phone(headers):
    """150-char name and 6-digit phone are the documented valid boundaries — must still succeed."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=744)
    check_out = check_in + timedelta(days=1)
    resp = _create_hold_raw(unit_id, check_in, check_out, guest_name="A" * 150, guest_phone="123456")
    assert resp.status_code == 201, resp.text


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
# Double-booking overlap matrix + concurrency (booking/OS10/notification
# finalization audit): exercises the exact overlap patterns called out by
# that audit against the real no_overlapping_bookings DB constraint, plus
# genuine concurrent requests (not just sequential logic) against both the
# bookings and booking_holds EXCLUDE constraints.
# ---------------------------------------------------------------------------

def test_overlap_matrix_matches_half_open_date_convention(headers):
    """Booking A confirmed for day+950..+953 (3 nights). Every overlapping
    pattern (start before/at/inside A's range) must be rejected; the
    adjacent, non-overlapping pattern starting exactly on A's checkout day
    must succeed — the project's stay_range convention is a half-open
    interval [check_in, check_out), so a checkout date is free for a new
    check-in the same day."""
    unit_id, guest_id = _make_guest_and_unit(headers)
    base = date.today() + timedelta(days=950)
    a_in, a_out = base, base + timedelta(days=3)  # e.g. 18 -> 21

    r = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id), "guest_id": str(guest_id),
        "check_in": a_in.isoformat(), "check_out": a_out.isoformat(),
    })
    assert r.status_code == 201, r.text

    overlapping_offsets = [
        (-1, 1),   # 17-19: overlaps the start
        (0, 2),    # 18-20: fully inside
        (1, 3),    # 19-21: overlaps the end
        (0, 3),    # 18-21: identical
    ]
    for start_off, end_off in overlapping_offsets:
        ci = a_in + timedelta(days=start_off)
        co = a_in + timedelta(days=end_off)
        resp = client.post("/api/v1/bookings", headers=headers, json={
            "unit_id": str(unit_id), "guest_id": str(guest_id),
            "check_in": ci.isoformat(), "check_out": co.isoformat(),
        })
        assert resp.status_code == 409, f"Expected reject for {ci}->{co}, got {resp.status_code}: {resp.text}"

    # Adjacent, non-overlapping: checkout day of A becomes available again.
    adjacent_resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id), "guest_id": str(guest_id),
        "check_in": a_out.isoformat(), "check_out": (a_out + timedelta(days=3)).isoformat(),
    })
    assert adjacent_resp.status_code == 201, adjacent_resp.text


def test_concurrent_overlapping_bookings_only_one_confirmed(headers):
    """Real concurrency test (not just sequential logic): two genuinely
    simultaneous admin booking requests for the same unit and overlapping
    dates, fired from separate threads with separate DB connections. The
    no_overlapping_bookings GiST EXCLUDE constraint is what actually
    decides this — it enforces mutual exclusion at the Postgres index
    level regardless of application-level timing, so exactly one request
    must succeed no matter which one the OS thread scheduler runs first."""
    import concurrent.futures

    unit_id, guest_id = _make_guest_and_unit(headers)
    check_in = date.today() + timedelta(days=955)
    check_out = check_in + timedelta(days=3)
    overlapping_check_in = check_in + timedelta(days=1)
    overlapping_check_out = check_out + timedelta(days=1)

    def _book(ci, co):
        return client.post("/api/v1/bookings", headers=headers, json={
            "unit_id": str(unit_id), "guest_id": str(guest_id),
            "check_in": ci.isoformat(), "check_out": co.isoformat(),
        })

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_book, check_in, check_out)
        fut_b = pool.submit(_book, overlapping_check_in, overlapping_check_out)
        resp_a, resp_b = fut_a.result(), fut_b.result()

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [201, 409], (
        f"Expected exactly one 201 and one 409 for concurrent overlapping bookings, "
        f"got {resp_a.status_code} and {resp_b.status_code}"
    )

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        confirmed = db.scalars(sa_select(Booking).where(
            Booking.unit_id == uuid.UUID(unit_id), Booking.status == "confirmed",
        )).all()
        assert len(confirmed) == 1, f"Expected exactly 1 confirmed booking for this unit, got {len(confirmed)}"
    finally:
        db.close()


def test_concurrent_overlapping_holds_only_one_created(headers):
    """Same concurrency guarantee, one layer earlier: two simultaneous
    guests trying to hold the same unit for overlapping dates must not
    both succeed — the no_overlapping_holds EXCLUDE constraint on
    booking_holds (status IN pending_payment/paid) is what actually
    prevents two guests from both reaching a payment screen for the same
    dates in the first place."""
    import concurrent.futures

    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=960)
    check_out = check_in + timedelta(days=3)
    overlapping_check_in = check_in + timedelta(days=1)
    overlapping_check_out = check_out + timedelta(days=1)
    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"

    def _hold(ci, co, phone):
        return client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id), "check_in": ci.isoformat(), "check_out": co.isoformat(),
            "guest_name": "Concurrent Guest", "guest_phone": phone, "guest_email": "concurrent@example.com",
        })

    phone_a = f"79{uuid.uuid4().int % 10**8:08d}"
    phone_b = f"79{uuid.uuid4().int % 10**8:08d}"
    with patch("app.modules.public_booking.router.create_order", return_value={"id": fake_order_id}):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(_hold, check_in, check_out, phone_a)
            fut_b = pool.submit(_hold, overlapping_check_in, overlapping_check_out, phone_b)
            resp_a, resp_b = fut_a.result(), fut_b.result()

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [201, 409], (
        f"Expected exactly one 201 and one 409 for concurrent overlapping holds, "
        f"got {resp_a.status_code} and {resp_b.status_code}"
    )

    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        active_holds = db.scalars(sa_select(BookingHold).where(
            BookingHold.unit_id == uuid.UUID(unit_id), BookingHold.status == "pending_payment",
        )).all()
        assert len(active_holds) == 1, f"Expected exactly 1 active hold for this unit, got {len(active_holds)}"
    finally:
        db.close()


def test_admin_cannot_confirm_booking_against_an_active_website_hold(headers):
    """The theoretical gap this audit set out to check — an admin directly
    confirming a booking for a unit+date range while a guest's website
    hold for the SAME dates is still pending_payment — turns out to be
    already closed: create_booking() calls the shared unit_is_available()
    (see app/shared/availability.py), which explicitly checks active
    booking_holds (not just other Bookings) for exactly this reason,
    documented there as preventing "staff confirming a manual booking
    while a website payment for the same dates is in flight." This test
    verifies that real behavior rather than assuming it, and then confirms
    the guest's own payment still converts normally afterward — proving
    the pre-check didn't leave the hold itself unusable."""
    unit_id, guest_id = _make_guest_and_unit(headers)
    check_in = date.today() + timedelta(days=965)
    check_out = check_in + timedelta(days=3)
    phone = f"80{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["hold_id"]

    # Admin attempts a direct booking for the same unit+dates while the
    # guest's hold above is still pending_payment — must be rejected.
    admin_resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": str(unit_id), "guest_id": str(guest_id),
        "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    })
    assert admin_resp.status_code == 409, (
        f"Expected the admin booking to be rejected while a guest hold is active, got {admin_resp.status_code}"
    )

    # The guest's own payment must still convert normally — the pre-check
    # protecting their hold must not have consumed or blocked it.
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    sig = _make_signature(fake_order_id, payment_id, FAKE_RAZORPAY_KEY_SECRET)
    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": payment_id, "razorpay_order_id": fake_order_id, "razorpay_signature": sig,
        })
    assert verify_resp.status_code == 201, verify_resp.text

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        confirmed = db.scalars(sa_select(Booking).where(
            Booking.unit_id == uuid.UUID(unit_id), Booking.status == "confirmed",
        )).all()
        assert len(confirmed) == 1, "The guest's own booking must be the only confirmed booking for these dates"
        assert confirmed[0].guest_id != uuid.UUID(guest_id), "The confirmed booking must be the guest's, not the admin's rejected attempt"
    finally:
        db.close()


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


def test_create_order_still_fails_without_credentials(monkeypatch):
    from app.core.config import settings
    from app.modules.payment_gateway.service import create_order, GatewayNotConfiguredError
    # WEBSITE-04A: force the not-configured path explicitly — real
    # RAZORPAY_KEY_ID/SECRET are legitimately configured in this
    # environment since WEBSITE-03, so the ambient state can no longer be
    # relied on to exercise this contract.
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
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

def test_hold_response_includes_razorpay_key_id(headers, monkeypatch):
    """HoldOut must include razorpay_key_id so the frontend can initialise Razorpay checkout."""
    from app.core.config import settings
    # WEBSITE-04A: this test specifically checks the not-configured shape
    # of the field (present, but null) — create_order() is separately
    # mocked by _create_hold_mocked, but the razorpay_key_id field is read
    # directly from settings in the router, so it must be forced here too
    # rather than relying on this sandbox having no real credentials.
    monkeypatch.setattr(settings, "razorpay_key_id", "")

    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=720)
    check_out = check_in + timedelta(days=2)

    resp, _ = _create_hold_mocked(unit_id, check_in, check_out)
    assert resp.status_code == 201
    data = resp.json()

    assert "razorpay_key_id" in data
    # With RAZORPAY_KEY_ID forced empty above, field must still be present (as null)
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
# Phase 8B: automatic invoice-email trigger + duplicate safety
# ---------------------------------------------------------------------------

def test_guest_confirmation_email_carries_invoice_attachment(headers):
    """Booking/notification finalization audit: the guest's invoice used to
    be a SEPARATE invoice_delivery email on top of booking_confirmation —
    two client emails per booking. trigger_booking_confirmation now carries
    the invoice attachment itself (see its own docstring), so the guest
    gets exactly ONE confirmation email, and it has the PDF attached. The
    attempt/outcome (success or failure) is still logged under
    booking_confirmation — never silently skipped."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=750)
    check_out = check_in + timedelta(days=2)
    phone = f"68{uuid.uuid4().int % 10**8:08d}"

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
    booking_id = verify_resp.json()["booking_id"]

    # Booking must be confirmed regardless of whether the email actually sent
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.notifications.models import NotificationLog
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_id)
        assert booking is not None
        assert booking.status == "confirmed"  # failure-to-send must never cancel this

        # No separate invoice_delivery event for the automatic post-payment
        # flow anymore — that trigger is now admin-manual-resend-only.
        assert db.scalar(sa_select(NotificationLog).where(
            NotificationLog.booking_id == booking_id, NotificationLog.event_type == "invoice_delivery",
        )) is None

        logs = db.scalars(sa_select(NotificationLog).where(
            NotificationLog.booking_id == booking_id,
            NotificationLog.event_type == "booking_confirmation",
            NotificationLog.channel == "email",
        )).all()
        assert len(logs) == 1, f"Expected exactly 1 guest confirmation email log, got {len(logs)}"
        # Real SMTP is configured in this dev environment (see
        # tests/conftest.py's _fail_fast_on_real_smtp_connections), so the
        # attempt fails fast rather than with EmailNotConfiguredError — the
        # point here is that it was attempted and its outcome logged, not
        # silently skipped, regardless of which failure mode fired.
        assert logs[0].success is False
        assert logs[0].error_message
    finally:
        db.close()


def test_guest_confirmation_email_not_duplicated_on_verify_payment_retry(headers):
    """A retried verify-payment call on an already-converted hold must not
    send/log a second guest confirmation (invoice-carrying) email — it must
    inherit the same idempotency guarantee as booking creation itself,
    since notification dispatch is only ever scheduled once per hold (see
    dispatch_booking_notifications' docstring)."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=755)
    check_out = check_in + timedelta(days=2)
    phone = f"69{uuid.uuid4().int % 10**8:08d}"

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
    booking_id = resp1.json()["booking_id"]

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        resp2 = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)
    assert resp2.status_code == 201, resp2.text
    assert resp2.json()["booking_id"] == booking_id

    from app.db.session import SessionLocal
    from app.modules.notifications.models import NotificationLog
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        logs = db.scalars(sa_select(NotificationLog).where(
            NotificationLog.booking_id == booking_id,
            NotificationLog.event_type == "booking_confirmation",
            NotificationLog.channel == "email",
        )).all()
        assert len(logs) == 1, f"Expected exactly 1 guest confirmation email log despite retry, got {len(logs)}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Regression coverage for the payment -> booking-confirmation timeout bug:
# _convert_hold_to_booking() used to send up to 4 real, sequential SMTP
# emails in-line before returning to verify_payment(), which then returned
# the HTTP response. In this dev environment (real Gmail SMTP configured,
# daily send quota already exhausted) one of those sends was directly
# observed blocking for the full 15s smtplib timeout — comfortably past the
# website's 12s client-side request timeout — even though the booking had
# already committed successfully. The fix: _convert_hold_to_booking() only
# does DB work now; notification sending is dispatch_booking_notifications,
# scheduled as a FastAPI BackgroundTask (runs after the response is sent).
# ---------------------------------------------------------------------------

def test_convert_hold_to_booking_does_not_send_notifications_synchronously(headers):
    """Calling _convert_hold_to_booking() directly must create the booking
    and invoice but must NOT write any NotificationLog rows — proving
    notification-sending no longer happens inline as part of the DB
    conversion, which is the actual root-cause fix for the timeout bug."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=770)
    check_out = check_in + timedelta(days=2)
    phone = f"73{uuid.uuid4().int % 10**8:08d}"

    resp, _fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    from app.db.session import SessionLocal
    from app.modules.public_booking.router import _convert_hold_to_booking
    from app.modules.public_booking.models import BookingHold
    from app.modules.notifications.models import NotificationLog
    from sqlalchemy import select as sa_select

    db = SessionLocal()
    try:
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        hold.status = "paid"
        hold.razorpay_payment_id = "pay_sync_check_" + uuid.uuid4().hex[:8]

        booking, invoice = _convert_hold_to_booking(db, hold)
        db.commit()

        assert booking.status == "confirmed"
        assert invoice.booking_id == booking.id

        logs = db.scalars(sa_select(NotificationLog).where(NotificationLog.booking_id == booking.id)).all()
        assert logs == [], (
            f"_convert_hold_to_booking must not send notifications synchronously, found {len(logs)} log(s)"
        )
    finally:
        db.close()


def test_booking_confirmed_even_when_every_notification_channel_fails(headers):
    """The actual failure mode observed in this environment: a real SMTP
    send blocking/failing must never affect the booking's own success. With
    send_email patched to always raise (simulating the observed 15s socket
    timeout, without this test itself needing to wait 15s), verify-payment
    must still return 201 with a real booking_id, and the booking must be
    confirmed in the database — notification failures are recorded, never
    allowed to break or roll back the paid booking they're attached to."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=775)
    check_out = check_in + timedelta(days=2)
    phone = f"74{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    sig = _make_signature(fake_order_id, FAKE_PAYMENT_ID, FAKE_RAZORPAY_KEY_SECRET)
    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify, \
         patch("app.modules.notifications.service.send_email", side_effect=OSError("simulated SMTP timeout")):
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": FAKE_PAYMENT_ID,
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": sig,
        })

    assert verify_resp.status_code == 201, verify_resp.text
    booking_id = verify_resp.json()["booking_id"]
    assert booking_id is not None

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.notifications.models import NotificationLog
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_id)
        assert booking is not None
        assert booking.status == "confirmed"

        logs = db.scalars(sa_select(NotificationLog).where(
            NotificationLog.booking_id == booking_id, NotificationLog.channel == "email",
        )).all()
        assert len(logs) >= 1, "Expected email send attempts to be logged despite every one failing"
        assert all(log.success is False for log in logs)
    finally:
        db.close()


def test_missing_meta_whatsapp_configuration_does_not_prevent_booking_creation(headers):
    """WhatsApp/Meta is not configured in this environment (no
    WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID) — that must never block
    booking creation. Every successful-payment test in this file already
    proves this implicitly (WhatsApp is never configured here and bookings
    still succeed); this test makes the requirement explicit and checks the
    actual logged reason, so the assumption can't silently rot if WhatsApp
    triggers change."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=780)
    check_out = check_in + timedelta(days=2)
    phone = f"75{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    verify_payload = {
        "razorpay_payment_id": FAKE_PAYMENT_ID,
        "razorpay_order_id": fake_order_id,
        "razorpay_signature": _make_signature(fake_order_id, FAKE_PAYMENT_ID, FAKE_RAZORPAY_KEY_SECRET),
    }
    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)
    assert verify_resp.status_code == 201, verify_resp.text
    booking_id = verify_resp.json()["booking_id"]

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.notifications.models import NotificationLog
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_id)
        assert booking is not None
        assert booking.status == "confirmed"

        whatsapp_logs = db.scalars(sa_select(NotificationLog).where(
            NotificationLog.booking_id == booking_id, NotificationLog.channel == "whatsapp",
        )).all()
        assert len(whatsapp_logs) >= 1, "Expected a WhatsApp attempt to be logged (and to fail, honestly)"
        for log in whatsapp_logs:
            assert log.success is False
            assert "not configured" in (log.error_message or "").lower()
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


# ---------------------------------------------------------------------------
# Confirmation-page notification honesty: "sent to your phone"/"and email"
# must reflect what actually happened, never an unconditional claim. This
# system has no automatic WhatsApp (only a manual staff wa.me link exists
# elsewhere) — confirmation_sms_sent is the one real "sent to your phone"
# channel this flow has, and it must never default to a blank/true value.
# ---------------------------------------------------------------------------

def test_confirmation_reports_actual_sms_outcome_not_a_blank_default(headers):
    """The guest always has a phone on file in this flow (guest_phone is
    required to create a hold), so an SMS send is always attempted — but
    since the payment->booking timeout fix (see
    dispatch_booking_notifications' docstring), notification sending now
    runs as a background task scheduled AFTER the confirmation response is
    built, so the real outcome isn't known yet on that first response —
    it's intentionally None (pending), not a blank/default True/False. A
    replay of the same call (the existing idempotent-replay path) reports
    the real, accurate outcome once background dispatch has completed —
    proving it was actually attempted and its outcome recorded, never
    silently skipped or left permanently unknown."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=760)
    check_out = check_in + timedelta(days=2)
    phone = f"71{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]

    verify_payload = {
        "razorpay_payment_id": FAKE_PAYMENT_ID,
        "razorpay_order_id": fake_order_id,
        "razorpay_signature": _make_signature(fake_order_id, FAKE_PAYMENT_ID, FAKE_RAZORPAY_KEY_SECRET),
    }
    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)
    assert verify_resp.status_code == 201, verify_resp.text
    # Dispatch was only just scheduled — outcome not yet known on this fast path.
    assert verify_resp.json()["confirmation_sms_sent"] is None

    # Replay (hold is now "converted") reads the real outcome back from
    # NotificationLog — TestClient runs the background dispatch task to
    # completion before returning, so it's already recorded by now.
    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        replay_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)
    assert replay_resp.status_code == 201, replay_resp.text
    assert replay_resp.json()["confirmation_sms_sent"] is not None
    assert isinstance(replay_resp.json()["confirmation_sms_sent"], bool)


def test_confirmation_replay_shows_same_notification_outcome_as_original(headers):
    """A repeated verify-payment call on an already-converted hold must
    report a STABLE notification outcome across replays — reconstructed
    from the notification log every time, never re-randomized. The very
    first (fast-path) response is intentionally None/pending — see
    dispatch_booking_notifications' docstring: notification sending is
    deferred to a background task precisely so the HTTP response is never
    blocked on live SMTP (the root cause of the payment->booking timeout
    bug) — so this compares two REPLAY calls against each other, once
    dispatch has actually completed, rather than the original fast-path
    response against a replay."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=765)
    check_out = check_in + timedelta(days=2)
    phone = f"72{uuid.uuid4().int % 10**8:08d}"

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
    # Fast path: dispatch just scheduled, outcome not yet known.
    assert resp1.json()["confirmation_emailed"] is None
    assert resp1.json()["confirmation_sms_sent"] is None

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        resp2 = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)
    assert resp2.status_code == 201, resp2.text
    assert resp2.json()["confirmation_sms_sent"] is not None

    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        resp3 = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json=verify_payload)
    assert resp3.status_code == 201, resp3.text

    assert resp3.json()["confirmation_emailed"] == resp2.json()["confirmation_emailed"]
    assert resp3.json()["confirmation_sms_sent"] == resp2.json()["confirmation_sms_sent"]


# ---------------------------------------------------------------------------
# Webhook backup-path conversion + duplicate-webhook idempotency (section
# 15.D / 8 of the website payment-flow audit): the webhook handler's own
# _convert_hold_to_booking call must behave identically to verify-payment's
# — one booking, one payment, one invoice, even when the same webhook event
# is delivered twice (Razorpay does redeliver on a slow/failed response).
# ---------------------------------------------------------------------------

def _razorpay_captured_payload(order_id: str, payment_id: str) -> bytes:
    import json
    return json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"order_id": order_id, "id": payment_id}}},
    }).encode()


def test_webhook_backup_path_converts_pending_hold_to_booking(headers):
    """If the browser never calls verify-payment (closed mid-flow), the
    webhook alone must still confirm the booking exactly once."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=770)
    check_out = check_in + timedelta(days=2)
    phone = f"73{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]
    payment_id = f"pay_webhook_{uuid.uuid4().hex[:10]}"

    body = _razorpay_captured_payload(fake_order_id, payment_id)
    with patch("app.modules.payment_gateway.router.verify_webhook_signature") as mock_verify:
        mock_verify.return_value = True
        resp1 = client.post("/api/v1/payment-gateway/webhook", content=body, headers={"X-Razorpay-Signature": "sig"})
    assert resp1.status_code == 200, resp1.text

    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    from app.modules.bookings.models import Booking
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, hold_id)
        assert hold.status == "converted", f"Expected hold converted by webhook alone, got {hold.status}"
        bookings = db.scalars(sa_select(Booking).where(Booking.unit_id == unit_id, Booking.status == "confirmed")).all()
        assert len(bookings) == 1, f"Expected exactly 1 confirmed booking from webhook backup path, got {len(bookings)}"
    finally:
        db.close()


def test_duplicate_webhook_delivery_does_not_create_second_booking(headers):
    """Razorpay may redeliver the same payment.captured event — the second
    delivery must be a no-op, not a second booking/payment/invoice."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=775)
    check_out = check_in + timedelta(days=2)
    phone = f"74{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]
    payment_id = f"pay_webhook_dup_{uuid.uuid4().hex[:10]}"

    body = _razorpay_captured_payload(fake_order_id, payment_id)
    with patch("app.modules.payment_gateway.router.verify_webhook_signature") as mock_verify:
        mock_verify.return_value = True
        resp1 = client.post("/api/v1/payment-gateway/webhook", content=body, headers={"X-Razorpay-Signature": "sig"})
        assert resp1.status_code == 200, resp1.text
        # Second, duplicate delivery of the exact same event
        resp2 = client.post("/api/v1/payment-gateway/webhook", content=body, headers={"X-Razorpay-Signature": "sig"})
        assert resp2.status_code == 200, resp2.text

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.payments.models import Payment, Invoice
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        bookings = db.scalars(sa_select(Booking).where(Booking.unit_id == unit_id, Booking.status == "confirmed")).all()
        assert len(bookings) == 1, f"Expected exactly 1 booking despite duplicate webhook, got {len(bookings)}"
        payments = db.scalars(sa_select(Payment).where(Payment.booking_id == bookings[0].id)).all()
        assert len(payments) == 1, f"Expected exactly 1 payment despite duplicate webhook, got {len(payments)}"
        invoices = db.scalars(sa_select(Invoice).where(Invoice.booking_id == bookings[0].id)).all()
        assert len(invoices) == 1, f"Expected exactly 1 invoice despite duplicate webhook, got {len(invoices)}"
    finally:
        db.close()


def test_verify_payment_after_webhook_already_converted_is_idempotent(headers):
    """If the webhook wins the race and converts the hold first, a
    subsequent browser verify-payment call must return the SAME booking
    (the existing hold.status == "converted" branch), not a 409 or a
    second booking."""
    _, unit_id = _make_available_unit(headers)
    check_in = date.today() + timedelta(days=780)
    check_out = check_in + timedelta(days=2)
    phone = f"75{uuid.uuid4().int % 10**8:08d}"

    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, guest_phone=phone)
    assert resp.status_code == 201
    hold_id = resp.json()["hold_id"]
    payment_id = f"pay_race_{uuid.uuid4().hex[:10]}"

    body = _razorpay_captured_payload(fake_order_id, payment_id)
    with patch("app.modules.payment_gateway.router.verify_webhook_signature") as mock_verify:
        mock_verify.return_value = True
        webhook_resp = client.post("/api/v1/payment-gateway/webhook", content=body, headers={"X-Razorpay-Signature": "sig"})
    assert webhook_resp.status_code == 200

    sig = _make_signature(fake_order_id, payment_id, FAKE_RAZORPAY_KEY_SECRET)
    with patch("app.modules.public_booking.router.verify_payment_signature") as mock_verify:
        mock_verify.return_value = True
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": payment_id,
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": sig,
        })
    assert verify_resp.status_code == 201, verify_resp.text

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from sqlalchemy import select as sa_select
    db = SessionLocal()
    try:
        bookings = db.scalars(sa_select(Booking).where(Booking.unit_id == unit_id, Booking.status == "confirmed")).all()
        assert len(bookings) == 1, f"Expected exactly 1 booking after webhook-then-verify race, got {len(bookings)}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# GET /api/v1/public/business-info — single source of truth for the
# website's footer/contact/location pages (previously a separate hardcoded/
# env-var copy on the frontend). No auth required; must never leak
# GST/UDYAM/bank/UPI fields, which stay admin-only elsewhere.
# ---------------------------------------------------------------------------

def test_public_business_info_returns_contact_fields_without_auth():
    resp = client.get("/api/v1/public/business-info")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"company_name", "address", "contact_phone", "contact_email", "whatsapp_business_number", "house_rules"}
    assert body["contact_email"]
    assert body["address"]


def test_public_business_info_never_exposes_sensitive_settings_fields():
    resp = client.get("/api/v1/public/business-info")
    assert resp.status_code == 200
    body = resp.json()
    for sensitive_key in ("gst_number", "udyam_number", "bank_name", "account_number", "ifsc", "upi_id", "signatory_name"):
        assert sensitive_key not in body


def test_public_business_info_reflects_an_admin_edit_immediately(headers):
    """Proves OS10 Business Settings is the live, single source of truth:
    an admin PUT to /api/v1/settings must be visible on the very next call
    to the public endpoint the website reads, with zero code/deploy step
    in between."""
    original = client.get("/api/v1/settings", headers=headers).json()
    new_address = f"ADMIN-CONTROL-TEST address {uuid.uuid4().hex[:8]}"
    try:
        update_resp = client.put("/api/v1/settings", headers=headers, json={"address": new_address})
        assert update_resp.status_code == 200

        public_resp = client.get("/api/v1/public/business-info")
        assert public_resp.status_code == 200
        assert public_resp.json()["address"] == new_address
    finally:
        client.put("/api/v1/settings", headers=headers, json={"address": original["address"]})
