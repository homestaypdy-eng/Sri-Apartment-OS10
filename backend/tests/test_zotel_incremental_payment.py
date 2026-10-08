"""
F1 — incremental / top-up payment recording.

Defect: _maybe_record_payment skipped recording whenever ANY positive
mode="zotel" Payment row already existed on the booking. So when Zotel's
reservation-level paid_amount later INCREASED (a second installment / part
payment), the additional money was never recorded in OS10 — a silent
under-recording of a top-up.

Correct behaviour: recording is DELTA-based and idempotent. For each
room-booking the recorded positive mode="zotel" payment sum is compared
against the per-room allocated share of the current paid_amount:
  - target - recorded > 1 paise  -> record a new row for the delta
  - target <= recorded           -> no-op (idempotent re-sync)
  - recorded > target            -> never auto-reverse; left for reconciliation
Per-room integer-paise allocation (and never N x paid_amount) is preserved.

No real Zotel call anywhere — client.get_all_bookings / get_booking_detail
are patched exactly like the rest of the Zotel suite.
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
from app.modules.zotel.models import ZotelRoomMapping
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


def _db():
    return SessionLocal()


def _make_unit(headers):
    prop = api_client.post("/api/v1/properties", headers=headers, json={
        "name": f"INC Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"INC Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return unit["id"]


def _mapping(db, unit_id, room_type_id):
    db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="INC Room"))
    db.commit()


def _room(category_id, name="INC Room", adults=2, children=0, room_number="R"):
    return {
        "room_number": room_number,
        "category": {"id": category_id, "name": name},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:6]}", "name": "EP (Room Only)"},
        "num_adults": adults, "num_children": children,
    }


def _detail(txn_id, rooms, check_in, check_out, status="created", total_amount=7000, paid_amount=0, phone=None):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "INC Guest", "email": None, "phone": phone or f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
        "rooms": rooms, "booking_source": "booking_com", "ota_booking_id": "ota-ref-inc",
    }}


def _payments_for(db, txn_id):
    return db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()


def _total(db, txn_id):
    return round(sum(float(p.amount) for p in _payments_for(db, txn_id)), 2)


# ---------------------------------------------------------------------------
# Single-room: installments recorded as deltas, idempotent re-sync.
# ---------------------------------------------------------------------------

def test_single_room_topup_records_incremental_delta(headers, configured_zotel):
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-inc-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)

        txn_id = f"txn-inc-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1300)
        co = ci + timedelta(days=2)
        rooms = [_room(cat)]

        # First installment: paid=3500 -> one 3500 payment.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=3500)):
            zotel_service.sync_bookings(db)
        assert _total(db, txn_id) == 3500.0
        assert len(_payments_for(db, txn_id)) == 1

        # Second installment: paid increased to 7000 -> additional 3500 recorded.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=7000)):
            zotel_service.sync_bookings(db)
        assert _total(db, txn_id) == 7000.0
        assert len(_payments_for(db, txn_id)) == 2

        # Third sync: unchanged paid=7000 -> no new row (idempotent).
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=7000)):
            zotel_service.sync_bookings(db)
        assert _total(db, txn_id) == 7000.0
        assert len(_payments_for(db, txn_id)) == 2

        assert all(p.mode == "zotel" and p.reference_no == txn_id and p.status == "completed"
                   for p in _payments_for(db, txn_id))
    finally:
        db.close()


def test_single_room_decrease_is_not_auto_reversed(headers, configured_zotel):
    """An OTA amount DECREASE must never write a negative/reversing row — it is
    left for reconciliation to flag, so recorded money is never silently
    clawed back by the importer."""
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-inc-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)

        txn_id = f"txn-inc-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1310)
        co = ci + timedelta(days=2)
        rooms = [_room(cat)]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=7000)):
            zotel_service.sync_bookings(db)
        assert _total(db, txn_id) == 7000.0

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=3500)):
            zotel_service.sync_bookings(db)

        # No negative row, recorded total unchanged.
        payments = _payments_for(db, txn_id)
        assert all(float(p.amount) > 0 for p in payments)
        assert _total(db, txn_id) == 7000.0
        assert len(payments) == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Multi-room: top-up deltas allocated per room, sum matches.
# ---------------------------------------------------------------------------

def test_multiroom_topup_allocates_delta_per_room(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a, cat_b = f"rt-a-{uuid.uuid4().hex[:8]}", f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-inc-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1320)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_a, room_number="A"), _room(cat_b, room_number="B")]

        # First installment: paid=4000 -> split 2000/2000.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=8000, paid_amount=4000)):
            zotel_service.sync_bookings(db)
        assert _total(db, txn_id) == 4000.0
        assert len(_payments_for(db, txn_id)) == 2

        # Second installment: paid=8000 (odd paise to force remainder) -> deltas per room.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=8000.01, paid_amount=8000.01)):
            zotel_service.sync_bookings(db)

        assert _total(db, txn_id) == 8000.01
        # Per room-booking the recorded sum equals its exact paise allocation
        # of 8000.01: shares are 4000.01 and 4000.00.
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 2
        per_room = []
        for b in bookings:
            rows = db.scalars(select(Payment).where(Payment.booking_id == b.id, Payment.mode == "zotel")).all()
            per_room.append(round(sum(float(p.amount) for p in rows), 2))
        assert sorted(per_room) == [4000.0, 4000.01]

        # Third sync: unchanged -> no new rows.
        before = len(_payments_for(db, txn_id))
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=8000.01, paid_amount=8000.01)):
            zotel_service.sync_bookings(db)
        assert len(_payments_for(db, txn_id)) == before
        assert _total(db, txn_id) == 8000.01
    finally:
        db.close()
