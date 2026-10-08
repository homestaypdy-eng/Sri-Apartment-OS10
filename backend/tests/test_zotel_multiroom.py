"""
Multi-room Zotel reservation sync.

A single Zotel reservation (transaction_id) can contain an array of N rooms;
each room must become its own OS10 Booking (one per room), keyed for
idempotency on the composite (external_reservation_id, external_room_id).

Observed real reservation shape (see tests/test_zotel_integration.py and
app/modules/zotel/service.py): rooms carry rooms[i].category.id (mapping key)
and no stable per-room id; there is NO per-room amount — only the single
reservation-level total_amount and paid_amount. So accommodation and the
single payment are allocated across the rooms in exact integer minor units,
summing EXACTLY to the reservation totals, never multiplied by room count.

Covers:
1. a 2-room reservation creates 2 bookings; accommodation split sums exactly
   to total_amount
2. the single payment is split to sum exactly to paid_amount, never doubled,
   and never re-recorded on re-sync
3. idempotent re-sync (no duplicates) via the composite key
4. same-unit collision flagged for manual review, no overlapping booking,
   transaction not reported as a clean success, payment deferred; and a mixed
   reservation where the non-colliding room still imports
5. a Zotel cancellation propagates to ALL room-bookings of the reservation
6. phone/guest + status/date safeguards still applied per room

No real Zotel call anywhere — client.get_all_bookings / get_booking_detail are
patched, exactly like the existing Zotel suite.
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
        "name": f"MR Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"MR Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return unit["id"]


def _mapping(db, unit_id, room_type_id):
    db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="MR Room"))
    db.commit()


def _room(category_id, name="MR Room", adults=2, children=0, room_number="R"):
    return {
        "room_number": room_number,
        "category": {"id": category_id, "name": name},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:6]}", "name": "EP (Room Only)"},
        "num_adults": adults, "num_children": children,
    }


def _detail(txn_id, rooms, check_in, check_out, status="created", total_amount=7000, paid_amount=0, phone=None):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "MR Guest", "email": None, "phone": phone or f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
        "rooms": rooms, "booking_source": "booking_com", "ota_booking_id": "ota-ref-mr",
    }}


# ---------------------------------------------------------------------------
# 1 + 2. Two rooms -> two bookings; accommodation + payment sum exactly
# ---------------------------------------------------------------------------

def test_two_room_reservation_creates_two_bookings_with_exact_accommodation_split(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a, cat_b = f"rt-a-{uuid.uuid4().hex[:8]}", f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=900)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_a, room_number="A"), _room(cat_b, room_number="B")]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=0)):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_imported == 2
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 2
        assert {b.unit_id for b in bookings} == {uuid.UUID(unit_a), uuid.UUID(unit_b)}
        # Each room is a distinct OS10 booking with its own stable room key.
        assert len({b.external_room_id for b in bookings}) == 2
        assert all(b.external_room_id for b in bookings)
        # Accommodation split sums EXACTLY to total_amount (nights=2).
        nights = (co - ci).days
        total_accommodation = round(sum(round(float(b.rate_applied) * nights, 2) for b in bookings), 2)
        assert total_accommodation == 7000.0
    finally:
        db.close()


def test_single_payment_split_sums_exactly_and_is_never_doubled(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a, cat_b = f"rt-a-{uuid.uuid4().hex[:8]}", f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=910)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_a, room_number="A"), _room(cat_b, room_number="B")]
        # Odd paise so the remainder is genuinely distributed (not an even split).
        paid = 7000.01

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, total_amount=7000.01, paid_amount=paid)):
            zotel_service.sync_bookings(db)

        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 2
        payments = db.scalars(
            select(Payment).where(Payment.booking_id.in_([b.id for b in bookings]))
        ).all()
        # One payment per room-booking, each tagged mode="zotel" with the txn ref.
        assert len(payments) == 2
        assert all(p.mode == "zotel" for p in payments)
        assert all(p.reference_no == txn_id for p in payments)
        # Sum is EXACTLY paid_amount — never N x paid_amount.
        assert round(sum(float(p.amount) for p in payments), 2) == 7000.01
        # No single room was charged the whole reservation payment.
        assert all(float(p.amount) < paid for p in payments)
        # Remainder distributed: shares are 3500.01 and 3500.00.
        assert sorted(round(float(p.amount), 2) for p in payments) == [3500.0, 3500.01]
    finally:
        db.close()


def test_unpaid_multiroom_records_no_payment(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a, cat_b = f"rt-a-{uuid.uuid4().hex[:8]}", f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=915)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_a), _room(cat_b)]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, paid_amount=0)):
            zotel_service.sync_bookings(db)

        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        payments = db.scalars(select(Payment).where(Payment.booking_id.in_([b.id for b in bookings]))).all()
        assert payments == []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. Idempotent re-sync via the composite key
# ---------------------------------------------------------------------------

def test_multiroom_resync_is_idempotent_no_duplicate_bookings_or_payments(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a, cat_b = f"rt-a-{uuid.uuid4().hex[:8]}", f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=920)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_a, room_number="A"), _room(cat_b, room_number="B")]
        detail = _detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=7000)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            first = zotel_service.sync_bookings(db)
            second = zotel_service.sync_bookings(db)
            third = zotel_service.sync_bookings(db)

        assert first.records_imported == 2
        assert second.records_skipped == 2
        assert third.records_skipped == 2

        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 2  # never duplicated across 3 runs
        payments = db.scalars(select(Payment).where(Payment.booking_id.in_([b.id for b in bookings]))).all()
        assert len(payments) == 2  # payment recorded once, never re-doubled on re-sync
        assert round(sum(float(p.amount) for p in payments), 2) == 7000.0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. Same-unit collision -> manual review, no overlapping booking, deferred payment
# ---------------------------------------------------------------------------

def test_same_unit_collision_is_flagged_and_creates_no_overlapping_booking(headers, configured_zotel):
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-coll-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)  # BOTH rooms share this category -> same OS10 unit

        txn_id = f"txn-coll-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=930)
        co = ci + timedelta(days=2)
        rooms = [_room(cat, room_number="C1"), _room(cat, room_number="C2")]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, paid_amount=7000)):
            sync_log = zotel_service.sync_bookings(db)

        # Both colliding rooms flagged for manual review; no booking created.
        assert sync_log.records_conflicted == 2
        assert sync_log.records_imported == 0
        # The reservation is NOT reported as a clean success.
        assert sync_log.status != "success"
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert bookings == []
        # Payment deferred for the whole reservation.
        payments = db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()
        assert payments == []
        # The sync record names the transaction, the rooms, and the unit.
        from app.modules.zotel.models import ZotelSyncRecord
        recs = db.scalars(select(ZotelSyncRecord).where(ZotelSyncRecord.sync_log_id == sync_log.id)).all()
        assert all(r.outcome == "conflicted" for r in recs)
        assert any(txn_id in (r.detail or "") and str(unit) in (r.detail or "") for r in recs)
    finally:
        db.close()


def test_mixed_reservation_imports_noncolliding_room_but_defers_payment(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a = f"rt-a-{uuid.uuid4().hex[:8]}"
        cat_b = f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-mix-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=940)
        co = ci + timedelta(days=2)
        # A -> unit_a (unique), B and C both -> unit_b (collide).
        rooms = [_room(cat_a, room_number="A"), _room(cat_b, room_number="B"), _room(cat_b, room_number="C")]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, total_amount=9000, paid_amount=9000)):
            sync_log = zotel_service.sync_bookings(db)

        # Non-colliding room A imported; B and C conflicted.
        assert sync_log.records_imported == 1
        assert sync_log.records_conflicted == 2
        assert sync_log.status != "success"
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 1
        assert bookings[0].unit_id == uuid.UUID(unit_a)
        # Payment deferred for the ENTIRE reservation (collision unresolved) —
        # even the cleanly-imported room records no payment yet.
        payments = db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()
        assert payments == []
        from app.modules.zotel.models import ZotelSyncRecord
        imported = db.scalars(select(ZotelSyncRecord).where(
            ZotelSyncRecord.sync_log_id == sync_log.id, ZotelSyncRecord.outcome == "imported")).all()
        assert len(imported) == 1
        assert "payment deferred" in (imported[0].detail or "").lower()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Cancellation propagates to ALL room-bookings of a reservation
# ---------------------------------------------------------------------------

def test_cancellation_propagates_to_all_room_bookings(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a, cat_b = f"rt-a-{uuid.uuid4().hex[:8]}", f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=950)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_a, room_number="A"), _room(cat_b, room_number="B")]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, status="created")):
            first = zotel_service.sync_bookings(db)
        assert first.records_imported == 2

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, status="cancelled")):
            second = zotel_service.sync_bookings(db)

        # Both room-bookings propagated to cancelled.
        assert second.records_updated == 2
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 2
        assert all(b.status == "cancelled" for b in bookings)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 6. Guest + status/date safeguards applied per room
# ---------------------------------------------------------------------------

def test_single_guest_created_and_shared_across_room_bookings(headers, configured_zotel):
    db = _db()
    try:
        unit_a, unit_b = _make_unit(headers), _make_unit(headers)
        cat_a, cat_b = f"rt-a-{uuid.uuid4().hex[:8]}", f"rt-b-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)
        _mapping(db, unit_b, cat_b)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=960)
        co = ci + timedelta(days=2)
        phone = f"91{uuid.uuid4().int % 10**9:09d}"
        rooms = [_room(cat_a, room_number="A"), _room(cat_b, room_number="B")]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, phone=phone)):
            zotel_service.sync_bookings(db)

        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 2
        # The reservation's single customer is resolved to ONE Guest, reused
        # for every room-booking (phone handling unchanged, applied once).
        assert len({b.guest_id for b in bookings}) == 1
        guests = db.scalars(select(Guest).where(Guest.phone == phone)).all()
        assert len(guests) == 1
    finally:
        db.close()


def test_missing_checkout_fails_whole_reservation_without_creating_bookings(headers, configured_zotel):
    db = _db()
    try:
        unit_a = _make_unit(headers)
        cat_a = f"rt-a-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=970)
        rooms = [_room(cat_a)]
        detail = _detail(txn_id, rooms, ci, ci + timedelta(days=2))
        detail["data"]["checkout"] = None  # status/date validation safeguard

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_failed == 1
        assert sync_log.records_imported == 0
        assert db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id)) is None
    finally:
        db.close()


def test_per_room_malformed_entry_fails_only_that_room(headers, configured_zotel):
    db = _db()
    try:
        unit_a = _make_unit(headers)
        cat_a = f"rt-a-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_a, cat_a)

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=980)
        co = ci + timedelta(days=2)
        # One good room + one room with no category id (malformed).
        rooms = [_room(cat_a, room_number="A"), {"room_number": "B", "category": {}}]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co)):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_imported == 1  # the good room still imports
        assert sync_log.records_failed == 1    # the malformed room fails closed
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 1
        assert bookings[0].unit_id == uuid.UUID(unit_a)
    finally:
        db.close()


def test_maintenance_unit_room_conflicts_within_multiroom_reservation(headers, configured_zotel):
    db = _db()
    try:
        unit_ok = _make_unit(headers)
        # Create a unit then mark it under maintenance via the model directly.
        unit_bad = _make_unit(headers)
        cat_ok = f"rt-ok-{uuid.uuid4().hex[:8]}"
        cat_bad = f"rt-bad-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_ok, cat_ok)
        _mapping(db, unit_bad, cat_bad)
        from app.modules.properties.models import Unit
        bad = db.get(Unit, uuid.UUID(unit_bad))
        bad.status = "maintenance"
        db.commit()

        txn_id = f"txn-mr-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=990)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_ok, room_number="OK"), _room(cat_bad, room_number="BAD")]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_detail(txn_id, rooms, ci, co, paid_amount=0)):
            sync_log = zotel_service.sync_bookings(db)

        # Good room imports; maintenance-unit room is conflicted per room.
        assert sync_log.records_imported == 1
        assert sync_log.records_conflicted == 1
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 1
        assert bookings[0].unit_id == uuid.UUID(unit_ok)
    finally:
        db.close()
