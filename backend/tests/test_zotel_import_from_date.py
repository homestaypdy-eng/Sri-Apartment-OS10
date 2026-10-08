"""
H1 — safe historical-sync import_from_date filter (ADDITIVE, non-regressive).

sync_bookings accepts an optional import_from_date (a CHECK-IN lower bound).
It is passed to Zotel as the checkin_from API filter AND re-enforced as a
local defense-in-depth gate on NEW active imports: a reservation whose
check-in falls before import_from_date is skipped (disposition "skipped",
detail mentioning the configured import_from_date). The lower bound uses the
CHECK-IN date (not stay-overlap), matching the Zotel API's checkin_from
semantics.

When import_from_date is None (and the ZOTEL_IMPORT_FROM_DATE setting is
unset, as in the test environment), behaviour is byte-for-byte unchanged:
no checkin_from is sent and the gate never fires. The pre-existing
check_out <= today gate is untouched.

No real Zotel call anywhere — client.get_all_bookings / get_booking_detail
are patched exactly like the rest of the Zotel suite.
"""
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Booking
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
        "name": f"IFD Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"IFD Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return unit["id"]


def _mapping(db, unit_id, room_type_id):
    db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit_id), zotel_room_type_id=room_type_id, zotel_room_type_name="IFD Room"))
    db.commit()


def _room(category_id, name="IFD Room", room_number="R"):
    return {
        "room_number": room_number,
        "category": {"id": category_id, "name": name},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:6]}", "name": "EP (Room Only)"},
        "num_adults": 2, "num_children": 0,
    }


def _detail(txn_id, rooms, check_in, check_out, status="created"):
    return {"success": True, "meta": {}, "data": {
        "transaction_id": txn_id, "status": status,
        "customer": {"name": "IFD Guest", "email": None, "phone": f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "total_amount": 7000, "paid_amount": 0, "due_amount": 7000,
        "payment_method": "pay_at_hotel",
        "rooms": rooms, "booking_source": "booking_com", "ota_booking_id": "ota-ref-ifd",
    }}


IMPORT_FROM = date(2026, 1, 1)
# Both stays have check_out in the future (today is well before these), so the
# pre-existing check_out<=today gate does not apply — isolating the new
# import_from_date (check-in) gate.
PRE_CI, PRE_CO = date(2025, 11, 1), date(2027, 12, 1)   # check-in BEFORE import_from_date
POST_CI, POST_CO = date(2027, 11, 1), date(2027, 11, 3)  # check-in AFTER import_from_date


def test_import_from_date_skips_pre_bound_checkin_imports_post_bound(headers, configured_zotel):
    db = _db()
    try:
        unit_pre, unit_post = _make_unit(headers), _make_unit(headers)
        cat_pre, cat_post = f"rt-pre-{uuid.uuid4().hex[:8]}", f"rt-post-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit_pre, cat_pre)
        _mapping(db, unit_post, cat_post)

        txn_pre = f"txn-pre-{uuid.uuid4().hex[:10]}"
        txn_post = f"txn-post-{uuid.uuid4().hex[:10]}"
        summaries = [{"transaction_id": txn_pre}, {"transaction_id": txn_post}]
        details = {
            txn_pre: _detail(txn_pre, [_room(cat_pre)], PRE_CI, PRE_CO),
            txn_post: _detail(txn_post, [_room(cat_post)], POST_CI, POST_CO),
        }

        gab = MagicMock(return_value=summaries)
        with patch("app.modules.zotel.client.get_all_bookings", gab), \
             patch("app.modules.zotel.client.get_booking_detail", side_effect=lambda t: details[t]):
            sync_log = zotel_service.sync_bookings(db, import_from_date=IMPORT_FROM)

        # The CHECK-IN lower bound is passed through to the Zotel client.
        assert gab.call_args.kwargs.get("checkin_from") == IMPORT_FROM

        # Pre-bound check-in skipped; post-bound imported.
        assert sync_log.records_imported == 1
        assert sync_log.records_skipped == 1

        pre_bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_pre)).all()
        post_bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_post)).all()
        assert pre_bookings == []
        assert len(post_bookings) == 1 and post_bookings[0].unit_id == uuid.UUID(unit_post)

        # The skip is surfaced with the configured bound named.
        skipped = db.scalars(select(ZotelSyncRecord).where(
            ZotelSyncRecord.sync_log_id == sync_log.id,
            ZotelSyncRecord.external_reservation_id == txn_pre)).all()
        assert len(skipped) == 1 and skipped[0].outcome == "skipped"
        assert "import_from_date" in (skipped[0].detail or "")
    finally:
        db.close()


def test_import_from_date_none_changes_nothing(headers, configured_zotel):
    """With import_from_date=None (and the setting unset), the same pre-bound
    check-in reservation is imported — no checkin_from sent, gate never fires."""
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-none-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)

        txn = f"txn-none-{uuid.uuid4().hex[:10]}"
        gab = MagicMock(return_value=[{"transaction_id": txn}])
        with patch("app.modules.zotel.client.get_all_bookings", gab), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn, [_room(cat)], PRE_CI, PRE_CO)):
            sync_log = zotel_service.sync_bookings(db, import_from_date=None)

        # No lower bound is sent to Zotel.
        assert gab.call_args.kwargs.get("checkin_from") is None
        # The pre-2026 check-in is imported unchanged (gate never applies).
        assert sync_log.records_imported == 1
        assert sync_log.records_skipped == 0
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn)).all()
        assert len(bookings) == 1
    finally:
        db.close()


def test_configured_default_import_from_date_is_used_when_param_omitted(headers, configured_zotel, monkeypatch):
    """A set ZOTEL_IMPORT_FROM_DATE acts as the default when no explicit
    import_from_date is passed (e.g. the scheduler's automatic run)."""
    monkeypatch.setattr(settings, "zotel_import_from_date", IMPORT_FROM.isoformat())
    db = _db()
    try:
        unit = _make_unit(headers)
        cat = f"rt-cfg-{uuid.uuid4().hex[:8]}"
        _mapping(db, unit, cat)

        txn = f"txn-cfg-{uuid.uuid4().hex[:10]}"
        gab = MagicMock(return_value=[{"transaction_id": txn}])
        with patch("app.modules.zotel.client.get_all_bookings", gab), \
             patch("app.modules.zotel.client.get_booking_detail",
                   return_value=_detail(txn, [_room(cat)], PRE_CI, PRE_CO)):
            sync_log = zotel_service.sync_bookings(db)  # no explicit import_from_date

        assert gab.call_args.kwargs.get("checkin_from") == IMPORT_FROM
        assert sync_log.records_skipped == 1
        assert sync_log.records_imported == 0
    finally:
        db.close()
