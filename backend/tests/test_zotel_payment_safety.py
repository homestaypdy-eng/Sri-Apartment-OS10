"""
Zotel payment-posting safety — F5 (no payment on a conflicted or cancelled
booking) and F4 (no over-recording when a multi-room reservation's room set
changes).

Both bugs live in _import_reservation's payment gate: it recorded the
per-room paid share for every room that resolved to a booking, including an
existing booking whose reconcile outcome was "conflicted" (Zotel dates no
longer match) or that the same sync had just cancelled. And when a room
disappeared from a reservation, paid_amount was re-allocated across only the
remaining rooms, so the remaining room's share grew and the F1 delta logic
recorded the difference on top of what the removed room already held.

No real Zotel call anywhere — client.get_all_bookings / get_booking_detail
are patched. No real email/WhatsApp/SMS sends.
"""
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Booking
from app.modules.payments.models import Payment
from app.modules.zotel import service as zotel_service
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncRecord
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

api_client = TestClient(app)


@pytest.fixture(scope="module")
def headers():
    resp = api_client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def configured_zotel(monkeypatch):
    monkeypatch.setattr(settings, "zotel_base_url", "https://test-property.example.com/api/external/v1")
    monkeypatch.setattr(settings, "zotel_api_key", "pk_live_test_fake_key")
    monkeypatch.setattr(settings, "zotel_api_secret", "test_fake_secret")
    monkeypatch.setattr(settings, "zotel_hotel_id", "hotel-uuid-fake-0001")


@pytest.fixture(autouse=True)
def _no_real_sends(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.send_email", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_whatsapp", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_sms", lambda *a, **kw: None)


def _make_unit(headers):
    prop = api_client.post("/api/v1/properties", headers=headers, json={
        "name": f"PS Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"PS Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return unit["id"]


def _mapped_category(headers) -> str:
    unit_id = _make_unit(headers)
    category_id = f"ps-cat-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=category_id, zotel_room_type_name="PS Room"))
        db.commit()
    finally:
        db.close()
    return category_id


def _room(category_id):
    return {
        "room_number": "R",
        "category": {"id": category_id, "name": "PS Room"},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:6]}", "name": "EP (Room Only)"},
        "num_adults": 2, "num_children": 0,
    }


def _detail(txn_id, rooms, check_in, check_out, status="created", total_amount=7000, paid_amount=0):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "PS Guest", "email": None, "phone": f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
        "rooms": rooms, "booking_source": "booking_com", "ota_booking_id": "ota-ref-ps",
    }}


def _sync(txn_id, detail) -> list[ZotelSyncRecord]:
    db = SessionLocal()
    try:
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            sync_log = zotel_service.sync_bookings(db)
        return db.scalars(select(ZotelSyncRecord).where(ZotelSyncRecord.sync_log_id == sync_log.id)).all()
    finally:
        db.close()


def _payments(txn_id) -> list[tuple[uuid.UUID, float]]:
    db = SessionLocal()
    try:
        rows = db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()
        return sorted((str(p.id), round(float(p.amount), 2)) for p in rows)
    finally:
        db.close()


def _bookings(txn_id) -> list[Booking]:
    db = SessionLocal()
    try:
        return db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# F5: a conflicted existing booking must not receive a payment.
# ---------------------------------------------------------------------------
def test_no_payment_posted_when_zotel_dates_conflict(headers, configured_zotel):
    category_id = _mapped_category(headers)
    txn_id = f"PS-F5D-{uuid.uuid4().hex[:8]}"
    check_in = date.today() + timedelta(days=40)

    first = _sync(txn_id, _detail(txn_id, [_room(category_id)], check_in, check_in + timedelta(days=2), paid_amount=0))
    assert [r.outcome for r in first] == ["imported"], [(r.outcome, r.detail) for r in first]

    # Zotel now reports different dates AND a payment.
    moved_in = check_in + timedelta(days=5)
    second = _sync(txn_id, _detail(txn_id, [_room(category_id)], moved_in, moved_in + timedelta(days=2),
                                   status="modified", paid_amount=3000))
    assert [r.outcome for r in second] == ["conflicted"], [(r.outcome, r.detail) for r in second]
    assert "payment" in second[0].detail.lower(), second[0].detail

    assert _payments(txn_id) == [], "a payment must not be posted to a booking whose Zotel dates conflict"
    (booking,) = _bookings(txn_id)
    assert (booking.status, booking.check_in) == ("confirmed", check_in), "the conflicted booking must be left untouched"


# ---------------------------------------------------------------------------
# F5: a booking the same sync just cancelled must not receive a payment.
# ---------------------------------------------------------------------------
def test_no_payment_posted_to_booking_cancelled_by_the_same_sync(headers, configured_zotel):
    category_id = _mapped_category(headers)
    txn_id = f"PS-F5C-{uuid.uuid4().hex[:8]}"
    check_in = date.today() + timedelta(days=50)
    check_out = check_in + timedelta(days=2)

    _sync(txn_id, _detail(txn_id, [_room(category_id)], check_in, check_out, paid_amount=0))
    second = _sync(txn_id, _detail(txn_id, [_room(category_id)], check_in, check_out, status="cancelled", paid_amount=2000))
    assert [r.outcome for r in second] == ["updated"], [(r.outcome, r.detail) for r in second]

    (booking,) = _bookings(txn_id)
    assert booking.status == "cancelled"
    assert _payments(txn_id) == [], "a cancelled booking must not receive a new Zotel payment"


# ---------------------------------------------------------------------------
# F4: removing a room from a multi-room reservation must not over-record, and
# must not touch the removed room's booking or the payments already recorded.
# ---------------------------------------------------------------------------
def test_room_removed_from_reservation_does_not_over_record(headers, configured_zotel):
    cat_a = _mapped_category(headers)
    cat_b = _mapped_category(headers)
    txn_id = f"PS-F4-{uuid.uuid4().hex[:8]}"
    check_in = date.today() + timedelta(days=60)
    check_out = check_in + timedelta(days=2)

    first = _sync(txn_id, _detail(txn_id, [_room(cat_a), _room(cat_b)], check_in, check_out, paid_amount=7000))
    assert sorted(r.outcome for r in first) == ["imported", "imported"], [(r.outcome, r.detail) for r in first]
    payments_before = _payments(txn_id)
    assert sum(amount for _, amount in payments_before) == 7000.0

    # Zotel now reports only room A; paid_amount unchanged.
    second = _sync(txn_id, _detail(txn_id, [_room(cat_a)], check_in, check_out, status="modified", paid_amount=7000))

    assert _payments(txn_id) == payments_before, "existing payments must be preserved and nothing added"
    bookings = _bookings(txn_id)
    assert sorted(b.status for b in bookings) == ["confirmed", "confirmed"], "the removed room's booking must not be auto-cancelled"

    room_a_booking = next(b for b in bookings if b.external_room_id.startswith(cat_a))
    room_b_booking = next(b for b in bookings if b.external_room_id.startswith(cat_b))
    flagged = [r for r in second if r.booking_id == room_b_booking.id]
    assert [r.outcome for r in flagged] == ["conflicted"], [(r.outcome, r.detail, r.booking_id) for r in second]
    assert any(r.booking_id == room_a_booking.id for r in second)
