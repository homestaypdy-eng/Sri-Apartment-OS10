"""
Tests for OTA Channel Manager, Guest Portal, and the Public Direct Booking
Engine (with promo codes).
Run with: pytest tests/test_ota_portal_public_booking.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_property_and_unit(headers, base_rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"P {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"U {uuid.uuid4().hex[:6]}", "base_rate": base_rate}).json()
    return prop["id"], unit["id"]


# --- OTA Channel Manager ---

def test_channels_auto_created_and_not_live(headers):
    channels = client.get("/api/v1/ota/channels", headers=headers).json()
    names = {c["channel"] for c in channels}
    assert names == {"booking_com", "airbnb", "agoda"}
    assert all(c["is_live"] is False for c in channels)


def test_manual_sync_produces_mock_labeled_log(headers):
    resp = client.post("/api/v1/ota/sync", headers=headers, json={"channel": "airbnb", "sync_type": "reservation_import"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "[MOCK]" in data["message"]


def test_invalid_channel_rejected(headers):
    resp = client.post("/api/v1/ota/sync", headers=headers, json={"channel": "expedia", "sync_type": "reservation_import"})
    assert resp.status_code == 400


def test_invalid_sync_type_rejected(headers):
    resp = client.post("/api/v1/ota/sync", headers=headers, json={"channel": "agoda", "sync_type": "teleportation"})
    assert resp.status_code == 400


def test_listing_mapping_create_and_list(headers):
    _, unit_id = _make_property_and_unit(headers)
    mapping = client.post("/api/v1/ota/mappings", headers=headers, json={"channel": "booking_com", "unit_id": unit_id, "external_listing_id": "BDC-12345"})
    assert mapping.status_code == 201
    listing = client.get("/api/v1/ota/mappings", headers=headers, params={"channel": "booking_com"}).json()
    assert any(m["external_listing_id"] == "BDC-12345" for m in listing)


def test_sync_logs_recorded(headers):
    client.post("/api/v1/ota/sync", headers=headers, json={"channel": "agoda", "sync_type": "rate_sync"})
    logs = client.get("/api/v1/ota/sync-logs", headers=headers, params={"channel": "agoda"}).json()
    assert len(logs) >= 1


def test_channel_can_be_toggled(headers):
    resp = client.patch("/api/v1/ota/channels/airbnb", headers=headers, json={"sync_enabled": True, "notes": "test note"})
    assert resp.status_code == 200
    assert resp.json()["sync_enabled"] is True


# --- Guest Portal ---

def _make_guest_with_booking(headers, phone=None):
    phone = phone or f"97{uuid.uuid4().int % 10**8:08d}"
    _, unit_id = _make_property_and_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Portal Test Guest", "phone": phone, "email": "portaltest@example.com"}).json()
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (date.today() + timedelta(days=60)).isoformat(),
        "check_out": (date.today() + timedelta(days=63)).isoformat(),
    }).json()
    return phone, guest["id"], booking["id"]


def test_login_request_for_unknown_phone_does_not_leak_info():
    resp = client.post("/api/v1/portal/request-login", json={"phone": "0000000000"})
    assert resp.status_code == 200
    assert resp.json()["sent"] is False
    assert resp.json()["dev_magic_token"] is None


def test_full_magic_link_login_flow(headers):
    phone, guest_id, booking_id = _make_guest_with_booking(headers)

    req = client.post("/api/v1/portal/request-login", json={"phone": phone})
    assert req.status_code == 200
    magic_token = req.json()["dev_magic_token"]
    assert magic_token is not None

    verify = client.post("/api/v1/portal/verify", json={"token": magic_token})
    assert verify.status_code == 200
    guest_token = verify.json()["access_token"]

    me = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {guest_token}"})
    assert me.status_code == 200
    assert me.json()["id"] == guest_id

    bookings = client.get("/api/v1/portal/bookings", headers={"Authorization": f"Bearer {guest_token}"})
    assert bookings.status_code == 200
    assert any(b["id"] == booking_id for b in bookings.json())


def test_guest_cannot_use_magic_token_as_session_token(headers):
    phone, _, _ = _make_guest_with_booking(headers)
    magic_token = client.post("/api/v1/portal/request-login", json={"phone": phone}).json()["dev_magic_token"]

    resp = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {magic_token}"})
    assert resp.status_code == 403


def test_guest_cannot_see_other_guests_bookings(headers):
    phone_a, _, booking_a = _make_guest_with_booking(headers)
    phone_b, _, booking_b = _make_guest_with_booking(headers)

    token_a = client.post("/api/v1/portal/verify", json={"token": client.post("/api/v1/portal/request-login", json={"phone": phone_a}).json()["dev_magic_token"]}).json()["access_token"]

    bookings = client.get("/api/v1/portal/bookings", headers={"Authorization": f"Bearer {token_a}"}).json()
    booking_ids = {b["id"] for b in bookings}
    assert booking_a in booking_ids
    assert booking_b not in booking_ids


def test_guest_can_raise_complaint_and_staff_sees_it(headers):
    phone, _, booking_id = _make_guest_with_booking(headers)
    guest_token = client.post("/api/v1/portal/verify", json={"token": client.post("/api/v1/portal/request-login", json={"phone": phone}).json()["dev_magic_token"]}).json()["access_token"]

    complaint = client.post("/api/v1/portal/complaints", headers={"Authorization": f"Bearer {guest_token}"}, json={"booking_id": booking_id, "description": "Noisy neighbors"})
    assert complaint.status_code == 201
    assert complaint.json()["request_type"] == "complaint"

    staff_view = client.get("/api/v1/guest-requests", headers=headers, params={"request_type": "complaint"}).json()
    assert any(r["id"] == complaint.json()["id"] for r in staff_view)


def test_staff_can_resolve_guest_request(headers):
    phone, _, booking_id = _make_guest_with_booking(headers)
    guest_token = client.post("/api/v1/portal/verify", json={"token": client.post("/api/v1/portal/request-login", json={"phone": phone}).json()["dev_magic_token"]}).json()["access_token"]
    req = client.post("/api/v1/portal/requests", headers={"Authorization": f"Bearer {guest_token}"}, json={"booking_id": booking_id, "description": "Extra towels please"}).json()

    resolved = client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"status": "resolved", "staff_notes": "Delivered"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_at"] is not None


def test_extend_stay_request_type_inferred_from_date(headers):
    phone, _, booking_id = _make_guest_with_booking(headers)
    guest_token = client.post("/api/v1/portal/verify", json={"token": client.post("/api/v1/portal/request-login", json={"phone": phone}).json()["dev_magic_token"]}).json()["access_token"]

    req = client.post("/api/v1/portal/requests", headers={"Authorization": f"Bearer {guest_token}"}, json={
        "booking_id": booking_id, "description": "Want to stay 2 more nights",
        "requested_check_out_date": (date.today() + timedelta(days=65)).isoformat(),
    })
    assert req.status_code == 201
    assert req.json()["request_type"] == "extend_stay"


def test_unauthenticated_guest_endpoints_require_session():
    assert client.get("/api/v1/portal/me").status_code == 401
    assert client.get("/api/v1/portal/bookings").status_code == 401


# --- Public Direct Booking Engine ---

def test_public_units_listing_requires_no_auth(headers):
    _make_property_and_unit(headers)
    resp = client.get("/api/v1/public/units")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_public_availability_check(headers):
    _, unit_id = _make_property_and_unit(headers, base_rate=1500)
    check_in = (date.today() + timedelta(days=100)).isoformat()
    check_out = (date.today() + timedelta(days=102)).isoformat()

    resp = client.get("/api/v1/public/availability", params={"unit_id": unit_id, "check_in": check_in, "check_out": check_out})
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["nights"] == 2
    assert data["estimated_total"] == 3000.0


def test_public_availability_maintenance_unit_not_available(headers):
    _, unit_id = _make_property_and_unit(headers, base_rate=1500)
    # Set unit to maintenance
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "maintenance"})

    check_in = (date.today() + timedelta(days=200)).isoformat()
    check_out = (date.today() + timedelta(days=202)).isoformat()
    resp = client.get("/api/v1/public/availability", params={"unit_id": unit_id, "check_in": check_in, "check_out": check_out})
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_promo_code_creation_and_validation(headers):
    code = f"TEST{uuid.uuid4().hex[:6].upper()}"
    create = client.post("/api/v1/promo-codes", headers=headers, json={
        "code": code, "discount_type": "flat", "value": 500,
        "valid_from": "2020-01-01", "valid_to": "2099-01-01",
    })
    assert create.status_code == 201

    validate = client.post("/api/v1/public/promo/validate", json={"code": code, "subtotal": 3000})
    assert validate.json()["valid"] is True
    assert validate.json()["discount_amount"] == 500.0


def test_invalid_promo_code_rejected():
    resp = client.post("/api/v1/public/promo/validate", json={"code": "DOES_NOT_EXIST", "subtotal": 1000})
    assert resp.json()["valid"] is False


def test_flat_discount_never_exceeds_subtotal(headers):
    code = f"BIG{uuid.uuid4().hex[:6].upper()}"
    client.post("/api/v1/promo-codes", headers=headers, json={"code": code, "discount_type": "flat", "value": 99999, "valid_from": "2020-01-01", "valid_to": "2099-01-01"})
    resp = client.post("/api/v1/public/promo/validate", json={"code": code, "subtotal": 1000})
    assert resp.json()["discount_amount"] == 1000.0


def test_public_quote_returns_server_computed_price(headers):
    _, unit_id = _make_property_and_unit(headers, base_rate=2000)
    check_in = (date.today() + timedelta(days=120)).isoformat()
    check_out = (date.today() + timedelta(days=123)).isoformat()

    resp = client.get("/api/v1/public/quote", params={"unit_id": unit_id, "check_in": check_in, "check_out": check_out})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["nights"] == 3
    assert data["subtotal_before_discount"] == 6000.0
    assert data["advance_amount"] == data["total"]
    assert data["available"] is True


def test_public_quote_with_promo_reduces_total(headers):
    _, unit_id = _make_property_and_unit(headers, base_rate=2000)
    code = f"PROMO{uuid.uuid4().hex[:6].upper()}"
    client.post("/api/v1/promo-codes", headers=headers, json={"code": code, "discount_type": "percent", "value": 10, "valid_from": "2020-01-01", "valid_to": "2099-01-01"})

    check_in = (date.today() + timedelta(days=140)).isoformat()
    check_out = (date.today() + timedelta(days=143)).isoformat()
    resp = client.get("/api/v1/public/quote", params={"unit_id": unit_id, "check_in": check_in, "check_out": check_out, "promo_code": code})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["promo_discount_amount"] == 600.0


def test_public_quote_invalid_promo_rejected(headers):
    _, unit_id = _make_property_and_unit(headers)
    resp = client.get("/api/v1/public/quote", params={
        "unit_id": unit_id,
        "check_in": (date.today() + timedelta(days=160)).isoformat(),
        "check_out": (date.today() + timedelta(days=162)).isoformat(),
        "promo_code": "NONEXISTENT_CODE",
    })
    assert resp.status_code == 400


def test_hold_requires_razorpay_configuration(headers):
    """POST /holds returns 501 when Razorpay credentials are not configured (this environment)."""
    _, unit_id = _make_property_and_unit(headers, base_rate=2000)
    resp = client.post("/api/v1/public/holds", json={
        "unit_id": unit_id,
        "check_in": (date.today() + timedelta(days=180)).isoformat(),
        "check_out": (date.today() + timedelta(days=183)).isoformat(),
        "guest_name": "Hold Tester",
        "guest_phone": f"89{uuid.uuid4().int % 10**8:08d}",
    })
    assert resp.status_code == 501
