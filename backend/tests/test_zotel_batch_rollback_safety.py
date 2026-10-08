"""
F2 — transaction rollback safety (savepoint, stop whole-batch loss).

Defect: _place_new_booking caught an IntegrityError at db.flush() (the
bookings.no_overlapping_bookings EXCLUDE constraint firing as the final
race backstop) and called db.rollback() on the WHOLE import Session. The
batch commits only once, at the very end of _sync_bookings_locked, so that
whole-session rollback discarded every EARLIER reservation's booking and
payment that had already been placed in the same batch — a single
end-of-batch collision silently wiped unrelated, already-successful work.

Correct behaviour: wrap the risky add/flush in a SAVEPOINT (db.begin_nested)
so only the offending row rolls back; the batch's earlier reservations
survive to the single end-of-batch commit, and the colliding reservation is
recorded as conflicted/not-imported. No exception escapes.

To make the EXCLUDE actually fire (the pre-flight unit_is_available read
would normally short-circuit a visible overlap into a graceful conflict),
this test reproduces the race the backstop exists for: a real committed
overlapping booking exists on the second reservation's unit, and the
pre-flight read is forced to pass so the real INSERT hits the real
Postgres EXCLUDE constraint in the test database.

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
from app.modules.bookings.models import Booking, Guest
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


def _db():
    return SessionLocal()


def _make_unit(headers):
    prop = api_client.post("/api/v1/properties", headers=headers, json={
        "name": f"BR Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"BR Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return unit["id"]


def _mapping(db, unit_id, room_type_id):
    db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="BR Room"))
    db.commit()


def _room(category_id, name="BR Room", adults=2, children=0, room_number="R"):
    return {
        "room_number": room_number,
        "category": {"id": category_id, "name": name},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:6]}", "name": "EP (Room Only)"},
        "num_adults": adults, "num_children": children,
    }


def _detail(txn_id, rooms, check_in, check_out, status="created", total_amount=7000, paid_amount=0, phone=None):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "BR Guest", "email": None, "phone": phone or f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
        "rooms": rooms, "booking_source": "booking_com", "ota_booking_id": "ota-ref-br",
    }}


def _seed_overlapping_confirmed_booking(db, unit_id, check_in, check_out):
    """A real, committed confirmed booking on this unit/date range so a later
    INSERT for the same unit/overlap hits the Postgres EXCLUDE constraint."""
    guest = Guest(name="Existing Guest", phone=f"91{uuid.uuid4().int % 10**9:09d}")
    db.add(guest)
    db.flush()
    booking = Booking(
        unit_id=uuid.UUID(unit_id), guest_id=guest.id, check_in=check_in, check_out=check_out,
        status="confirmed", source="direct", rate_applied=3500, adults=1, children=0,
    )
    db.add(booking)
    db.commit()
    return booking.id


def test_batch_collision_savepoint_preserves_earlier_reservations(headers, configured_zotel):
    db = _db()
    try:
        unit_ok, unit_clash = _make_unit(headers), _make_unit(headers)
        cat_ok = f"rt-ok-{uuid.uuid4().hex[:8]}"
        cat_clash = f"rt-clash-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_ok, cat_ok)
        _mapping(db, unit_clash, cat_clash)

        ci = date.today() + timedelta(days=1400)
        co = ci + timedelta(days=2)
        # Pre-existing committed booking on unit_clash for the same range.
        _seed_overlapping_confirmed_booking(db, unit_clash, ci, co)

        txn_ok = f"txn-ok-{uuid.uuid4().hex[:10]}"
        txn_clash = f"txn-clash-{uuid.uuid4().hex[:10]}"
        # Batch order: the clean reservation FIRST (so it is already placed in
        # the session before the colliding one is processed), colliding SECOND.
        summaries = [{"transaction_id": txn_ok}, {"transaction_id": txn_clash}]
        detail_ok = _detail(txn_ok, [_room(cat_ok)], ci, co, total_amount=7000, paid_amount=7000)
        detail_clash = _detail(txn_clash, [_room(cat_clash)], ci, co, total_amount=5000, paid_amount=5000)

        def _detail_router(transaction_id):
            return detail_ok if transaction_id == txn_ok else detail_clash

        # Force the pre-flight availability read to pass so the colliding
        # INSERT genuinely reaches the DB and the real EXCLUDE constraint
        # fires at flush — reproducing the race the backstop exists for.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=summaries), \
             patch("app.modules.zotel.client.get_booking_detail", side_effect=_detail_router), \
             patch("app.modules.zotel.service.unit_is_available", return_value=True):
            sync_log = zotel_service.sync_bookings(db)

        # No exception escaped; the clean reservation imported, the collision
        # was recorded as conflicted/not-imported.
        assert sync_log.records_imported == 1
        assert sync_log.records_conflicted == 1

        # Reservation #1's Booking PERSISTS after the single end-of-batch commit.
        ok_bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_ok)).all()
        assert len(ok_bookings) == 1, "the earlier reservation's booking must survive the later collision"
        assert ok_bookings[0].unit_id == uuid.UUID(unit_ok)

        # Reservation #1's Payment PERSISTS (7000 recorded, not wiped).
        ok_payments = db.scalars(select(Payment).where(Payment.reference_no == txn_ok)).all()
        assert round(sum(float(p.amount) for p in ok_payments), 2) == 7000.0

        # Reservation #2 created no booking at all.
        clash_bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_clash)).all()
        assert clash_bookings == []

        # And it is recorded as a conflict naming the insert-time rejection.
        recs = db.scalars(select(ZotelSyncRecord).where(
            ZotelSyncRecord.sync_log_id == sync_log.id,
            ZotelSyncRecord.external_reservation_id == txn_clash)).all()
        assert len(recs) == 1
        assert recs[0].outcome == "conflicted"
        assert "insert time" in (recs[0].detail or "").lower()
    finally:
        db.close()
