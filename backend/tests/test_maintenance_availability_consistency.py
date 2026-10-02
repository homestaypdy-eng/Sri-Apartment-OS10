"""
OS10 Phase 5A: maintenance blocking consistency across every booking/
availability path.

Covers:
  A. Regression — available unit bookable, maintenance/blocked unit is not,
     on both the shared availability functions directly and the OS10/
     public endpoints that call them.
  B. Hold -> booking conversion (verify-payment path) rejects a unit that
     became maintenance/blocked WHILE a hold was still pending_payment.
  C. Same race via the Razorpay webhook backup conversion path.
  D. Zotel import of a new reservation for a maintenance/blocked unit is
     routed to the existing "conflicted" outcome, never silently booked.
  E. Unit.status server-side validation (valid values accepted, invalid
     rejected).
  F. Existing date-overlap protection is unaffected by these changes.

SAFETY: every test mocks Razorpay (create_order/verify_payment_signature/
verify_webhook_signature) and Zotel (client.get_all_bookings/
get_booking_detail) at the source — no real external call is possible
from this file. An autouse fixture also mocks send_email/send_whatsapp/
send_sms, since a successful hold conversion schedules
dispatch_booking_notifications as a background task that FastAPI's
TestClient executes synchronously.

Run with: pytest tests/test_maintenance_availability_consistency.py -v
"""
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_sends(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.send_email", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_whatsapp", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_sms", lambda *a, **kw: None)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_unit(headers, status="available", base_rate=2200):
    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"MaintAvailProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"MaintAvailUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate}).json()
    if status != "available":
        r = client.patch(f"/api/v1/units/{unit['id']}", headers=headers, json={"status": status})
        assert r.status_code == 200, r.text
    return unit["id"]


def _make_guest(headers):
    return client.post("/api/v1/guests", headers=headers,
                        json={"name": "Maint Avail Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()["id"]


def _set_unit_status(headers, unit_id, status):
    r = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": status})
    assert r.status_code == 200, r.text


def _create_hold_mocked(unit_id, check_in, check_out, guest_phone=None):
    if guest_phone is None:
        guest_phone = f"90{uuid.uuid4().int % 10**8:08d}"
    fake_order_id = f"order_{uuid.uuid4().hex[:12]}"
    with patch("app.modules.public_booking.router.create_order") as mock_order:
        mock_order.return_value = {"id": fake_order_id}
        resp = client.post("/api/v1/public/holds", json={
            "unit_id": str(unit_id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_name": "Maint Avail Guest",
            "guest_phone": guest_phone,
            "guest_email": "maintavail@example.com",
        })
    return resp, fake_order_id


def _razorpay_captured_payload(order_id: str, payment_id: str) -> bytes:
    import json
    return json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"order_id": order_id, "id": payment_id}}},
    }).encode()


# ---------------------------------------------------------------------------
# A. Regression: shared availability functions + endpoints
# ---------------------------------------------------------------------------

def test_available_unit_bookable_when_dates_free(headers):
    unit_id = _make_unit(headers, status="available")
    guest_id = _make_guest(headers)
    check_in = date(2033, 3, 1)
    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat(),
    })
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("status", ["maintenance", "blocked"])
def test_os10_booking_rejected_for_non_available_unit(headers, status):
    unit_id = _make_unit(headers, status=status)
    guest_id = _make_guest(headers)
    check_in = date(2033, 3, 10)
    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat(),
    })
    assert resp.status_code == 409, resp.text


@pytest.mark.parametrize("status", ["maintenance", "blocked"])
def test_public_hold_rejected_for_non_available_unit(headers, status):
    unit_id = _make_unit(headers, status=status)
    check_in = date(2033, 3, 20)
    resp, _ = _create_hold_mocked(unit_id, check_in, check_in + timedelta(days=2))
    assert resp.status_code == 409, resp.text


@pytest.mark.parametrize("status", ["maintenance", "blocked"])
def test_shared_unit_is_available_false_for_non_available_unit_even_with_free_dates(headers, status):
    """Direct, unit-level test of the actual Phase 5A code change: no
    booking/hold exists for these dates at all — the ONLY reason this
    must return False is unit.status."""
    unit_id = _make_unit(headers, status=status)
    from app.shared.availability import unit_is_available
    db = SessionLocal()
    try:
        result = unit_is_available(db, uuid.UUID(unit_id), date(2033, 4, 1), date(2033, 4, 3))
    finally:
        db.close()
    assert result is False


def test_shared_units_availability_batch_excludes_blocked_unit(headers):
    available_id = _make_unit(headers, status="available")
    blocked_id = _make_unit(headers, status="blocked")
    from app.shared.availability import units_availability
    db = SessionLocal()
    try:
        result = units_availability(db, [uuid.UUID(available_id), uuid.UUID(blocked_id)], date(2033, 4, 10), date(2033, 4, 12))
    finally:
        db.close()
    assert result[uuid.UUID(available_id)] is True
    assert result[uuid.UUID(blocked_id)] is False


# ---------------------------------------------------------------------------
# B. Hold -> booking conversion (verify-payment) rejects a mid-flight
#    maintenance/blocked status change
# ---------------------------------------------------------------------------

def test_hold_conversion_rejected_when_unit_marked_maintenance_before_verify_payment(headers):
    unit_id = _make_unit(headers, status="available")
    check_in = date(2033, 5, 1)
    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_in + timedelta(days=2))
    assert resp.status_code == 201, resp.text
    hold_id = resp.json()["hold_id"]

    # Admin marks the unit under maintenance WHILE the hold is still
    # pending_payment — the exact race this fix closes.
    _set_unit_status(headers, unit_id, "maintenance")

    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": "pay_maint_race_test",
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": "irrelevant-mocked",
        })

    assert verify_resp.status_code == 409, verify_resp.text

    from app.modules.bookings.models import Booking
    from app.modules.public_booking.models import BookingHold
    from sqlalchemy import select
    db = SessionLocal()
    try:
        bookings = db.scalars(select(Booking).where(Booking.unit_id == unit_id)).all()
        assert bookings == [], "No Booking row should exist after a maintenance-blocked conversion attempt"
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        # Rolled back to its pre-attempt state, not stuck at "paid" with
        # no booking and no record of what happened.
        assert hold.status == "pending_payment", f"Expected hold rolled back to pending_payment, got {hold.status}"
    finally:
        db.close()


def test_hold_conversion_succeeds_normally_when_unit_stays_available(headers):
    """Regression: the ordinary, non-race case must keep working exactly
    as before this change."""
    unit_id = _make_unit(headers, status="available")
    check_in = date(2033, 5, 10)
    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_in + timedelta(days=2))
    hold_id = resp.json()["hold_id"]

    with patch("app.modules.public_booking.router.verify_payment_signature", return_value=True):
        verify_resp = client.post(f"/api/v1/public/holds/{hold_id}/verify-payment", json={
            "razorpay_payment_id": "pay_normal_test",
            "razorpay_order_id": fake_order_id,
            "razorpay_signature": "irrelevant-mocked",
        })
    assert verify_resp.status_code == 201, verify_resp.text
    assert "booking_id" in verify_resp.json()


# ---------------------------------------------------------------------------
# C. Same race via the Razorpay webhook backup conversion path
# ---------------------------------------------------------------------------

def test_webhook_conversion_rejected_when_unit_marked_maintenance_before_webhook(headers):
    unit_id = _make_unit(headers, status="available")
    check_in = date(2033, 6, 1)
    resp, fake_order_id = _create_hold_mocked(unit_id, check_in, check_in + timedelta(days=2))
    hold_id = resp.json()["hold_id"]

    _set_unit_status(headers, unit_id, "blocked")

    payment_id = f"pay_webhook_maint_{uuid.uuid4().hex[:10]}"
    body = _razorpay_captured_payload(fake_order_id, payment_id)
    with patch("app.modules.payment_gateway.router.verify_webhook_signature", return_value=True):
        webhook_resp = client.post("/api/v1/payment-gateway/webhook", content=body, headers={"X-Razorpay-Signature": "sig"})

    # Razorpay's webhook contract requires a 200 ack regardless of
    # internal outcome — the rejection is caught, not surfaced as an
    # error status, exactly as the pre-existing overlap-conflict case
    # already behaves for this same endpoint.
    assert webhook_resp.status_code == 200, webhook_resp.text

    from app.modules.bookings.models import Booking
    from app.modules.public_booking.models import BookingHold
    from sqlalchemy import select
    db = SessionLocal()
    try:
        bookings = db.scalars(select(Booking).where(Booking.unit_id == unit_id)).all()
        assert bookings == [], "No Booking row should exist after a maintenance-blocked webhook conversion attempt"
        hold = db.get(BookingHold, uuid.UUID(hold_id))
        assert hold.status == "pending_payment", f"Expected hold rolled back to pending_payment, got {hold.status}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# D. Zotel: a new reservation for a maintenance/blocked unit is flagged
#    "conflicted", never silently booked
# ---------------------------------------------------------------------------

def _fake_zotel_detail(txn_id, room_type_id, check_in, check_out):
    return {
        "success": True, "meta": {},
        "data": {
            "transaction_id": txn_id, "status": "created",
            "customer": {"name": "Zotel Maint Test Guest", "email": None, "phone": f"91{uuid.uuid4().int % 10**9:09d}"},
            "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
            "total_amount": 5000, "paid_amount": 0, "due_amount": 5000,
            "payment_method": "pay_at_hotel",
            "rooms": [{
                "room_number": "TEST", "category": {"id": room_type_id, "name": "Test Room"},
                "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:8]}", "name": "EP (Room Only)"},
                "num_adults": 2, "num_children": 0,
            }],
        },
    }


def test_zotel_import_for_maintenance_unit_is_conflicted_not_silently_booked(headers):
    from app.core.config import settings
    unit_id = _make_unit(headers, status="maintenance")
    room_type_id = f"room-type-{uuid.uuid4().hex[:8]}"

    from app.modules.zotel.models import ZotelRoomMapping
    from app.modules.zotel import service as zotel_service
    db = SessionLocal()
    try:
        db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="Test Room"))
        db.commit()

        check_in = date(2033, 7, 1)
        txn_id = f"zotel-maint-{uuid.uuid4().hex[:10]}"
        detail = _fake_zotel_detail(txn_id, room_type_id, check_in, check_in + timedelta(days=2))

        with patch.object(settings, "zotel_base_url", "https://test.example.com"), \
             patch.object(settings, "zotel_api_key", "test-key"), \
             patch.object(settings, "zotel_api_secret", "test-secret"), \
             patch.object(settings, "zotel_hotel_id", "test-hotel"), \
             patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_conflicted == 1, f"Expected 1 conflicted record, got conflicted={sync_log.records_conflicted} imported={sync_log.records_imported}"
        assert sync_log.records_imported == 0

        from app.modules.zotel.models import ZotelSyncRecord
        from sqlalchemy import select
        record = db.scalar(select(ZotelSyncRecord).where(ZotelSyncRecord.external_reservation_id == txn_id))
        assert record.outcome == "conflicted"
        assert "maintenance" in record.detail.lower()

        from app.modules.bookings.models import Booking
        bookings = db.scalars(select(Booking).where(Booking.unit_id == uuid.UUID(unit_id))).all()
        assert bookings == [], "Zotel must not silently create a normal confirmed Booking for a maintenance-status unit"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# E. Unit.status server-side validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["available", "maintenance", "blocked"])
def test_unit_status_update_accepts_valid_values(headers, status):
    unit_id = _make_unit(headers, status="available")
    resp = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": status})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == status


def test_unit_status_update_rejects_invalid_value(headers):
    unit_id = _make_unit(headers, status="available")
    resp = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"status": "on_vacation"})
    assert resp.status_code == 400, resp.text

    from app.modules.properties.models import Unit
    db = SessionLocal()
    try:
        unit = db.get(Unit, uuid.UUID(unit_id))
        assert unit.status == "available", "An invalid status must never be persisted"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# F. Existing date-overlap protection is unaffected
# ---------------------------------------------------------------------------

def test_existing_date_overlap_protection_still_works(headers):
    unit_id = _make_unit(headers, status="available")
    guest_id = _make_guest(headers)
    check_in = date(2033, 8, 1)
    check_out = check_in + timedelta(days=3)
    first = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    })
    assert first.status_code == 201, first.text

    second = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": (check_in + timedelta(days=1)).isoformat(), "check_out": (check_out + timedelta(days=1)).isoformat(),
    })
    assert second.status_code == 409, second.text


# ---------------------------------------------------------------------------
# G. Phase 7 — MaintenanceTicket itself is independent of availability.
# Everything above proves unit.status='maintenance' blocks booking (that's
# the existing, authoritative availability engine). None of it proves the
# separate question: does merely having a MaintenanceTicket row (open or
# resolved) on a unit affect availability on its own? maintenance/
# router.py never writes to unit.status or calls into shared/availability.py
# — these tests prove that by behavior, matching the same pattern used for
# Housekeeping's dirty/clean independence in Phase 6.
# ---------------------------------------------------------------------------

def test_open_maintenance_ticket_does_not_block_booking_when_unit_status_available(headers):
    unit_id = _make_unit(headers, status="available")
    guest_id = _make_guest(headers)
    client.post("/api/v1/maintenance-tickets", headers=headers, json={
        "unit_id": unit_id, "description": "Squeaky door", "priority": "low",
    })

    check_in = date(2033, 9, 1)
    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat(),
    })
    assert resp.status_code == 201, resp.text


def test_resolving_maintenance_ticket_does_not_silently_change_unit_status(headers):
    """A unit deliberately put into 'maintenance' by staff must stay in
    'maintenance' after its ticket is resolved — resolving the ticket is
    a record-keeping action, not an availability action. Auto-flipping
    the unit back to 'available' would be new, undocumented behavior this
    architecture doesn't define; staff decide separately when the unit is
    actually ready to book again (the same separation of concerns as
    Housekeeping's dirty/clean vs. unit.status)."""
    unit_id = _make_unit(headers, status="maintenance")
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={
        "unit_id": unit_id, "description": "AC compressor replacement",
    }).json()

    resolved = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved"})
    assert resolved.status_code == 200, resolved.text

    unit = next(u for u in client.get("/api/v1/units", headers=headers).json() if u["id"] == unit_id)
    assert unit["status"] == "maintenance"


def test_same_day_turnover_unaffected_by_open_maintenance_ticket_on_unit(headers):
    """Step 3.E: an open maintenance ticket reported during a stay must
    not disturb the same-day checkout/check-in boundary — Booking A ends
    the 13th, Booking B starts the 13th, and neither the ticket nor its
    absence of any unit.status change gets in the way."""
    unit_id = _make_unit(headers, status="available")
    guest_id = _make_guest(headers)

    booking_a = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": "2033-10-10", "check_out": "2033-10-13",
    }).json()
    client.patch(f"/api/v1/bookings/{booking_a['id']}/status?new_status=checked_in", headers=headers)

    client.post("/api/v1/maintenance-tickets", headers=headers, json={
        "unit_id": unit_id, "description": "Guest reported dripping faucet mid-stay", "priority": "medium",
    })

    checkout = client.patch(f"/api/v1/bookings/{booking_a['id']}/status?new_status=checked_out", headers=headers)
    assert checkout.status_code == 200, checkout.text

    booking_b = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id, "check_in": "2033-10-13", "check_out": "2033-10-16",
    })
    assert booking_b.status_code == 201, booking_b.text
