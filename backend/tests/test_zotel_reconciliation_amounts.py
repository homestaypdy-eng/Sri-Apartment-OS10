"""
F3 — monetary reconciliation (compare amounts, not just existence).

Before: run_reconciliation() only compared existence and cancelled-status
between Zotel and OS10. Two reservations that both exist and agree on
cancellation were reported "matched" even if OS10's recorded money differed
from what Zotel says was paid — a financial disagreement that went
completely unreported.

Fix: for reservations matched on both sides, compare the Zotel
reservation-level paid_amount against the OS10 recorded mode="zotel" payment
sum for that reservation (summed across its room-bookings), in exact
integer paise / Decimal (1-paise tolerance, no float drift). A mismatch is
categorized into a new amount_mismatch bucket and counts toward the
conflicted/partial status. It stays REPORT-ONLY (no Booking/Payment/Guest
writes). When amounts agree, nothing changes.

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
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncLog
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
        "name": f"RA Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"RA Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return unit["id"]


def _mapping(db, unit_id, room_type_id):
    db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="RA Room"))
    db.commit()


def _room(category_id, name="RA Room", room_number="R"):
    return {
        "room_number": room_number,
        "category": {"id": category_id, "name": name},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:6]}", "name": "EP (Room Only)"},
        "num_adults": 2, "num_children": 0,
    }


def _detail(txn_id, rooms, check_in, check_out, status="created", total_amount=7000, paid_amount=0, phone=None):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "RA Guest", "email": None, "phone": phone or f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
        "rooms": rooms, "booking_source": "booking_com", "ota_booking_id": "ota-ref-ra",
    }}


def _summary(txn_id, status="created", paid_amount=None):
    s = {"transaction_id": txn_id, "status": status}
    if paid_amount is not None:
        s["paid_amount"] = paid_amount
    return s


def _import(db, txn_id, rooms, ci, co, paid_amount):
    with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
         patch("app.modules.zotel.client.get_booking_detail",
               return_value=_detail(txn_id, rooms, ci, co, total_amount=paid_amount, paid_amount=paid_amount)):
        zotel_service.sync_bookings(db)


# ---------------------------------------------------------------------------
# Equal amounts -> matched, no amount_mismatch.
# ---------------------------------------------------------------------------

def test_matched_with_equal_amounts_is_matched(headers, configured_zotel):
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-ra-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)

        txn_id = f"txn-ra-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1500)
        co = ci + timedelta(days=2)
        _import(db, txn_id, [_room(cat)], ci, co, paid_amount=7000)

        recorded = round(sum(float(p.amount) for p in
                             db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()), 2)
        assert recorded == 7000.0

        # Reconciliation compares against EVERY OS10 booking carrying an
        # external_reservation_id, including ones other tests created. Echo
        # each one's own status so the run is genuinely clean; only this
        # reservation carries a paid_amount (the amount check skips the rest).
        existing = db.execute(select(Booking.external_reservation_id, Booking.status).where(Booking.external_reservation_id.is_not(None))).all()
        seen: dict[str, dict] = {}
        for ext_id, status in existing:
            seen[ext_id] = {"transaction_id": ext_id, "status": "cancelled" if status == "cancelled" else "created"}
        seen[txn_id] = _summary(txn_id, paid_amount=7000)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=list(seen.values())):
            report = zotel_service.run_reconciliation(db)

        categories = {e["external_reservation_id"]: e["category"] for e in report["entries"]}
        assert categories[txn_id] == "matched"
        assert report["summary"].get("amount_mismatch", 0) == 0

        log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "reconciliation").order_by(ZotelSyncLog.created_at.desc()))
        assert log.status == "success"
        assert "amount_mismatch=0" in (log.message or "")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Zotel paid differs from OS10 recorded -> amount_mismatch + status partial.
# ---------------------------------------------------------------------------

def test_amount_mismatch_flagged_and_marks_partial(headers, configured_zotel):
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-ra-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)

        txn_id = f"txn-ra-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1510)
        co = ci + timedelta(days=2)
        # OS10 records 7000; Zotel now reports paid_amount=9000.
        _import(db, txn_id, [_room(cat)], ci, co, paid_amount=7000)

        with patch("app.modules.zotel.client.get_all_bookings",
                   return_value=[_summary(txn_id, paid_amount=9000)]):
            report = zotel_service.run_reconciliation(db)

        # New amount_mismatch bucket is present and counted.
        assert report["summary"]["amount_mismatch"] == 1
        entries = [e for e in report["entries"]
                   if e["external_reservation_id"] == txn_id and e["category"] == "amount_mismatch"]
        assert len(entries) == 1
        # Detail surfaces both figures so staff can see the disagreement.
        detail = entries[0]["detail"]
        assert "9000" in detail and "7000" in detail

        # Report-only: no Booking/Payment mutated by reconciliation.
        payments = db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()
        assert round(sum(float(p.amount) for p in payments), 2) == 7000.0
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 1

        # Persisted reconciliation log reflects the mismatch as a partial run.
        log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "reconciliation").order_by(ZotelSyncLog.created_at.desc()))
        assert log.status == "partial"
        assert "amount_mismatch=1" in (log.message or "")
    finally:
        db.close()


def test_small_amount_difference_within_tolerance_is_matched(headers, configured_zotel):
    """A sub-paise representational difference must not be flagged (Decimal /
    integer-paise comparison, 1-paise tolerance — no float drift)."""
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-ra-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)

        txn_id = f"txn-ra-{uuid.uuid4().hex[:10]}"
        ci = date.today() + timedelta(days=1520)
        co = ci + timedelta(days=2)
        _import(db, txn_id, [_room(cat)], ci, co, paid_amount=7000.01)

        with patch("app.modules.zotel.client.get_all_bookings",
                   return_value=[_summary(txn_id, paid_amount=7000.01)]):
            report = zotel_service.run_reconciliation(db)

        assert report["summary"].get("amount_mismatch", 0) == 0
        categories = [e["category"] for e in report["entries"] if e["external_reservation_id"] == txn_id]
        assert "amount_mismatch" not in categories
    finally:
        db.close()
