"""
R1 regression — multi-room payment reconciliation must not silently
under-record when a room in a multi-room reservation is UNPLACEABLE.

Defect (R1): a Zotel reservation carries a single reservation-level
paid_amount, which service._import_reservation allocates across ALL
allocatable rooms (one share per room). If one of those rooms then cannot
be placed into an OS10 booking (its mapped unit is under maintenance,
already booked/overlapping, over max-occupancy, or rejected by the DB
EXCLUDE backstop), its allocated share of the payment was simply dropped:
the placeable rooms recorded their (smaller) shares and the unplaceable
room's share vanished. Net effect — OS10 recorded a Payment total STRICTLY
LESS than what Zotel reported as paid, with nothing flagged. That is silent
financial under-recording.

Correct behaviour (mirrors the already-correct same-unit collision path):
the single reservation payment must be recorded in FULL or DEFERRED in full
for manual review — never partially. When any paid room is unplaceable the
whole reservation payment is deferred (and the fact is surfaced on the
imported room's sync record), so the amount on OS10 is never quietly short.
Once the obstruction is resolved and the reservation re-synced, the full
amount is recorded exactly once (no under-recording, no double-recording).

No real Zotel call anywhere — client.get_all_bookings / get_booking_detail
are patched, exactly like the rest of the Zotel suite.
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
from app.modules.properties.models import Unit
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
        "name": f"UR Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"UR Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return unit["id"]


def _mapping(db, unit_id, room_type_id):
    db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="UR Room"))
    db.commit()


def _room(category_id, name="UR Room", adults=2, children=0, room_number="R"):
    return {
        "room_number": room_number,
        "category": {"id": category_id, "name": name},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:6]}", "name": "EP (Room Only)"},
        "num_adults": adults, "num_children": children,
    }


def _detail(txn_id, rooms, check_in, check_out, status="created", total_amount=7000, paid_amount=0, phone=None):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "UR Guest", "email": None, "phone": phone or f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
        "rooms": rooms, "booking_source": "booking_com", "ota_booking_id": "ota-ref-ur",
    }}


def _recorded_total(db, txn_id) -> float:
    payments = db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()
    return round(sum(float(p.amount) for p in payments), 2), payments


# ---------------------------------------------------------------------------
# R1: an unplaceable room must NOT cause silent under-recording.
# ---------------------------------------------------------------------------

def test_unplaceable_room_does_not_silently_underrecord_payment(headers, configured_zotel):
    """A 2-room PAID reservation where one mapped unit is under maintenance.

    Pre-fix: the placeable room records its ~half share, the unplaceable
    room's share is dropped -> OS10 records ~3500 of a 7000 payment: a
    silent under-recording. This test asserts the correct behaviour (full
    amount or nothing, never partial) so it FAILS against the buggy code.
    """
    db = _db()
    try:
        unit_ok, unit_bad = _make_unit(headers), _make_unit(headers)
        cat_ok = f"rt-ok-{uuid.uuid4().hex[:8]}"
        cat_bad = f"rt-bad-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_ok, cat_ok)
        _mapping(db, unit_bad, cat_bad)

        # Make the second unit unplaceable.
        bad = db.get(Unit, uuid.UUID(unit_bad))
        bad.status = "maintenance"
        db.commit()

        txn_id = f"txn-ur-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1200)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_ok, room_number="OK"), _room(cat_bad, room_number="BAD")]
        paid = 7000.0

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=paid)):
            sync_log = zotel_service.sync_bookings(db)

        # One room places, one is conflicted (unplaceable) — unchanged.
        assert sync_log.records_imported == 1
        assert sync_log.records_conflicted == 1
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 1 and bookings[0].unit_id == uuid.UUID(unit_ok)

        # THE DEFECT: recorded payment must be the FULL amount or ZERO,
        # never a partial under-recording.
        total, payments = _recorded_total(db, txn_id)
        assert total in (0.0, paid), (
            f"R1 under-recording: recorded {total} for a reservation that paid {paid} "
            f"({len(payments)} payment rows) — a partial amount means money was silently lost"
        )

        # The chosen safe behaviour is full deferral while a room is
        # unplaceable, and it must be surfaced (not silent).
        assert payments == [], "payment must be deferred in full while a room is unplaceable"
        imported = db.scalars(select(ZotelSyncRecord).where(
            ZotelSyncRecord.sync_log_id == sync_log.id, ZotelSyncRecord.outcome == "imported")).all()
        assert len(imported) == 1
        assert "payment deferred" in (imported[0].detail or "").lower(), (
            "the imported room must record that payment was deferred, so under-recording is never silent"
        )
    finally:
        db.close()


def test_payment_recorded_in_full_once_unplaceable_room_is_resolved(headers, configured_zotel):
    """After the obstruction clears and the reservation is re-synced, the
    FULL paid_amount is recorded exactly once — accurate accounting, no
    duplicate payments on further re-syncs."""
    db = _db()
    try:
        unit_ok, unit_bad = _make_unit(headers), _make_unit(headers)
        cat_ok = f"rt-ok-{uuid.uuid4().hex[:8]}"
        cat_bad = f"rt-bad-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_ok, cat_ok)
        _mapping(db, unit_bad, cat_bad)

        bad = db.get(Unit, uuid.UUID(unit_bad))
        bad.status = "maintenance"
        db.commit()

        txn_id = f"txn-ur-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1210)
        co = ci + timedelta(days=2)
        rooms = [_room(cat_ok, room_number="OK"), _room(cat_bad, room_number="BAD")]
        paid = 7000.0
        detail = _detail(txn_id, rooms, ci, co, total_amount=7000, paid_amount=paid)

        # First sync: BAD unplaceable -> payment deferred in full.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            zotel_service.sync_bookings(db)
        total_before, _ = _recorded_total(db, txn_id)
        assert total_before == 0.0

        # Resolve the obstruction.
        bad = db.get(Unit, uuid.UUID(unit_bad))
        bad.status = "available"
        db.commit()

        # Re-sync twice: full amount recorded, and never doubled.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            zotel_service.sync_bookings(db)
            zotel_service.sync_bookings(db)

        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 2  # both rooms now placed, no duplicates
        total_after, payments = _recorded_total(db, txn_id)
        assert total_after == paid, f"expected full {paid} recorded after resolution, got {total_after}"
        assert len(payments) == 2, "exactly one payment per room-booking, never doubled on re-sync"
        assert all(p.mode == "zotel" and p.reference_no == txn_id for p in payments)
    finally:
        db.close()
