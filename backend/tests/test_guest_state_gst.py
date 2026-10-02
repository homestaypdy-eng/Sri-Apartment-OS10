"""
Focused tests for OS10 Phase 2 Step 1B: Guest.state_code write path and its
effect on GST place-of-supply (CGST+SGST vs IGST), across both the OS10
admin API and the website hold-creation path.

Covers exactly the 8 scenarios approved for this step:
  1. Guest create with state_code (API round-trip)
  2. Guest update with state_code (API round-trip)
  3. Same-state GST (property=34, guest=34) -> CGST+SGST, IGST=0
  4. Inter-state GST (property=34, guest=33) -> IGST, CGST/SGST=0
  5. Missing-state fallback (guest state=None) -> IGST, unchanged
  6. Website hold path: submitted guest_state reaches the hold, persists to
     Guest on conversion, and produces the correct invoice GST split
  7. Existing guest matching: a new state on a matched (existing) guest
     updates that guest, does not create a duplicate
  8. Existing guest with a known state + a new hold with no state: the
     known state is NOT overwritten with NULL

No test in this file makes any external call. Hold creation mocks
Razorpay's create_order (same pattern as test_public_booking_payment_gate.
py). Hold-to-booking conversion calls _convert_hold_to_booking() directly
rather than going through POST /verify-payment, deliberately avoiding the
FastAPI BackgroundTask that endpoint schedules for post-payment
notifications — see _convert_hold_directly()'s docstring below for why
that matters on this container specifically.

Run with: pytest tests/test_guest_state_gst.py -v
"""
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_property(headers, state_code="34"):
    resp = client.post(
        "/api/v1/properties",
        headers=headers,
        json={"name": f"GstStateProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": state_code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_unit(headers, property_id, base_rate=2000):
    resp = client.post(
        "/api/v1/units",
        headers=headers,
        json={"property_id": property_id, "name": f"GstStateUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_booking(headers, unit_id, guest_id, check_in, check_out):
    resp = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_hold_mocked(unit_id, check_in, check_out, guest_phone, guest_state=None):
    """Creates a hold via the real API with Razorpay's create_order mocked
    out — no external call, matching test_public_booking_payment_gate.py's
    established pattern exactly."""
    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    payload = {
        "unit_id": str(unit_id),
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "guest_name": "GST State Test Guest",
        "guest_phone": guest_phone,
        "guest_email": "gststatetest@example.com",
    }
    if guest_state is not None:
        payload["guest_state"] = guest_state

    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        resp = client.post("/api/v1/public/holds", json=payload)
    return resp, fake_order_id


def _convert_hold_directly(hold_id):
    """
    Converts a hold to a booking by calling _convert_hold_to_booking()
    directly, exactly like test_public_booking_payment_gate.py's own
    test_convert_hold_to_booking_does_not_send_notifications_synchronously
    does — NOT via POST /verify-payment.

    This is deliberate, not a shortcut: going through the real HTTP
    endpoint schedules dispatch_booking_notifications as a FastAPI
    BackgroundTask, which DOES execute within the TestClient call (proven
    by test_booking_confirmed_even_when_every_notification_channel_fails
    in that same file, which has to patch notifications.service.send_email
    specifically to neutralize it before calling verify-payment). Since
    this container's live EMAIL_PROVIDER=resend with a real key, that path
    is unsafe to exercise unguarded. _convert_hold_to_booking() itself is
    proven (by that same test file) to do ONLY database work and send no
    notification of any kind — calling it directly is the safe, already-
    established way to test conversion logic without going anywhere near
    the notification/background-task path at all.
    """
    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    from app.modules.public_booking.router import _convert_hold_to_booking

    db = SessionLocal()
    try:
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        hold.status = "paid"
        hold.razorpay_payment_id = "pay_test_direct_" + uuid.uuid4().hex[:8]
        booking, invoice = _convert_hold_to_booking(db, hold)
        db.commit()
        return booking.id, invoice.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1 & 2. Guest create/update with state_code (OS10 API round-trip)
# ---------------------------------------------------------------------------

def test_guest_create_with_state_code_persists(headers):
    resp = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "State Test Guest", "phone": f"70{uuid.uuid4().int % 10**8:08d}", "state_code": "34"},
    )
    assert resp.status_code == 201, resp.text
    guest_id = resp.json()["id"]
    assert resp.json()["state_code"] == "34"

    get_resp = client.get(f"/api/v1/guests/{guest_id}/history", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["guest"]["state_code"] == "34"


def test_guest_create_without_state_code_defaults_to_null(headers):
    resp = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "No State Guest", "phone": f"71{uuid.uuid4().int % 10**8:08d}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["state_code"] is None


def test_guest_update_sets_state_code(headers):
    resp = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "Update State Guest", "phone": f"72{uuid.uuid4().int % 10**8:08d}"},
    )
    guest_id = resp.json()["id"]
    assert resp.json()["state_code"] is None

    update_resp = client.patch(f"/api/v1/guests/{guest_id}", headers=headers, json={"state_code": "27"})
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["state_code"] == "27"


# ---------------------------------------------------------------------------
# 3, 4, 5. GST behavior via the OS10 manual invoice path (calculate_gst()
# itself is untouched — these confirm the already-correct engine keeps
# working once a real state is actually reachable end-to-end).
# ---------------------------------------------------------------------------

def test_same_state_guest_produces_cgst_sgst(headers):
    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=2000)  # 5% slab
    guest = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "Same State", "phone": f"73{uuid.uuid4().int % 10**8:08d}", "state_code": "34"},
    ).json()

    booking_id = _make_booking(headers, unit_id, guest["id"], "2028-05-01", "2028-05-04")  # 3 nights = 6000
    invoice = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert invoice.status_code == 201, invoice.text
    data = invoice.json()

    assert data["cgst"] == 150.0
    assert data["sgst"] == 150.0
    assert data["igst"] == 0.0


def test_inter_state_guest_produces_igst(headers):
    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=2000)
    guest = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "Inter State", "phone": f"74{uuid.uuid4().int % 10**8:08d}", "state_code": "33"},
    ).json()

    booking_id = _make_booking(headers, unit_id, guest["id"], "2028-05-05", "2028-05-08")
    invoice = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert invoice.status_code == 201, invoice.text
    data = invoice.json()

    assert data["igst"] == 300.0
    assert data["cgst"] == 0.0
    assert data["sgst"] == 0.0


def test_missing_state_guest_still_falls_back_to_igst(headers):
    """Regression guard: a guest with no state must keep producing IGST,
    exactly as before this change — the fallback must never be silently
    altered."""
    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=2000)
    guest = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "No State", "phone": f"75{uuid.uuid4().int % 10**8:08d}"},
    ).json()
    assert guest["state_code"] is None

    booking_id = _make_booking(headers, unit_id, guest["id"], "2028-05-09", "2028-05-12")
    invoice = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert invoice.status_code == 201, invoice.text
    data = invoice.json()

    assert data["igst"] == 300.0
    assert data["cgst"] == 0.0
    assert data["sgst"] == 0.0


# ---------------------------------------------------------------------------
# 6. Website hold path: submitted guest_state reaches the hold, persists to
# Guest on conversion, and the resulting invoice reflects it.
# ---------------------------------------------------------------------------

def test_website_hold_with_guest_state_reaches_gst_and_persists_to_guest(headers):
    from app.db.session import SessionLocal
    from app.modules.public_booking.models import BookingHold
    from app.modules.payments.models import Invoice
    from app.modules.bookings.models import Booking, Guest

    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=2000)  # 5% slab
    phone = f"76{uuid.uuid4().int % 10**8:08d}"
    check_in = date.today() + timedelta(days=320)
    check_out = check_in + timedelta(days=3)  # 3 nights = 6000 subtotal

    resp, _fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, phone, guest_state="34")
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["hold_id"]

    # GST is decided at hold-creation time and already correct here.
    db = SessionLocal()
    try:
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        assert hold.guest_state == "34"
        assert float(hold.cgst) == 150.0
        assert float(hold.sgst) == 150.0
        assert float(hold.igst) == 0.0
    finally:
        db.close()

    booking_id, invoice_id = _convert_hold_directly(hold_id)

    # The invoice carries the same, already-decided GST split through, and
    # the newly-created Guest got the submitted state.
    db = SessionLocal()
    try:
        invoice = db.get(Invoice, invoice_id)
        assert float(invoice.cgst) == 150.0
        assert float(invoice.sgst) == 150.0
        assert float(invoice.igst) == 0.0

        booking = db.get(Booking, booking_id)
        guest = db.get(Guest, booking.guest_id)
        assert guest.phone == phone
        assert guest.state_code == "34"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 7. Existing guest matching: a new state on a matched (existing) guest
# updates that guest and does not create a duplicate.
# ---------------------------------------------------------------------------

def test_website_hold_updates_existing_matched_guest_state_no_duplicate(headers):
    phone = f"77{uuid.uuid4().int % 10**8:08d}"

    # Guest already exists (e.g. created by staff, or from a prior stay), no state.
    existing = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "Existing No State", "phone": phone},
    ).json()
    assert existing["state_code"] is None

    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=2000)
    check_in = date.today() + timedelta(days=330)
    check_out = check_in + timedelta(days=2)

    resp, _fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, phone, guest_state="27")
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["hold_id"]

    _convert_hold_directly(hold_id)

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Guest
    from sqlalchemy import select, func
    db = SessionLocal()
    try:
        count = db.scalar(select(func.count()).select_from(Guest).where(Guest.phone == phone))
        assert count == 1, "a matched existing guest must be updated, never duplicated"

        guest = db.scalar(select(Guest).where(Guest.phone == phone))
        assert guest.id == uuid.UUID(existing["id"])
        assert guest.state_code == "27"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8. Existing guest with a known state + a new hold with no state: the
# known state is NOT overwritten with NULL.
# ---------------------------------------------------------------------------

def test_website_hold_without_state_does_not_overwrite_existing_known_state(headers):
    phone = f"78{uuid.uuid4().int % 10**8:08d}"

    existing = client.post(
        "/api/v1/guests", headers=headers,
        json={"name": "Existing Known State", "phone": phone, "state_code": "29"},
    ).json()
    assert existing["state_code"] == "29"

    property_id = _make_property(headers, state_code="34")
    unit_id = _make_unit(headers, property_id, base_rate=2000)
    check_in = date.today() + timedelta(days=340)
    check_out = check_in + timedelta(days=2)

    # This hold submits no guest_state at all.
    resp, _fake_order_id = _create_hold_mocked(unit_id, check_in, check_out, phone, guest_state=None)
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["hold_id"]

    _convert_hold_directly(hold_id)

    from app.db.session import SessionLocal
    from app.modules.bookings.models import Guest
    db = SessionLocal()
    try:
        guest = db.get(Guest, uuid.UUID(existing["id"]))
        assert guest.state_code == "29", "an existing known state must never be overwritten with NULL"
    finally:
        db.close()
