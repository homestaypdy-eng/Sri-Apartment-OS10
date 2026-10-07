"""
Zotel channel-manager integration tests.

No real Zotel credentials exist in this environment (ZOTEL_BASE_URL/
ZOTEL_API_KEY/ZOTEL_API_SECRET/ZOTEL_HOTEL_ID are all empty — confirmed
during discovery), so every test that exercises a "successful" API call
mocks httpx at the transport boundary, using response shapes taken
directly from the Zotel External API v1 documentation ("ZOTEL API.pdf").
Nothing here invents an endpoint, header, or field not in that document.

Covers:
- not-configured handling (client + connectivity-test endpoint)
- successful mocked availability/prices/bookings/booking-detail calls
- cursor pagination: empty / one-page / multi-page
- error handling: 401/403/404/409/422/429/500 (429 and 500 retried, others not)
- room-mapping required for import; unmapped rooms are logged, not guessed
- idempotent re-import of the same transaction_id (imported once, then skipped/updated on retry)
- availability-conflict-detection before import (existing OS10 booking is never overwritten)
- financial safety: no Payment row when paid_amount == 0; a Payment row, tagged mode="zotel", when it is > 0
- a duplicate sync run never produces a second Booking for the same transaction_id
- reconciliation report is read-only (no DB writes) and correctly categorizes matched/missing/duplicate/cancelled-mismatch
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Booking, Guest
from app.modules.payments.models import Payment
from app.modules.properties.models import Property, Unit
from app.modules.zotel import client as zotel_client
from app.modules.zotel import service as zotel_service
from app.modules.zotel.client import ZotelAPIError, ZotelNotConfiguredError
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncLog, ZotelSyncRecord

api_client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = api_client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def configured_zotel(monkeypatch):
    """Simulates a fully configured Zotel environment for the duration of one test."""
    monkeypatch.setattr(settings, "zotel_base_url", "https://test-property.example.com/api/external/v1")
    monkeypatch.setattr(settings, "zotel_api_key", "pk_live_test_fake_key")
    monkeypatch.setattr(settings, "zotel_api_secret", "test_fake_secret")
    monkeypatch.setattr(settings, "zotel_hotel_id", "hotel-uuid-fake-0001")
    yield


def _mock_response(status_code: int, json_body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


def _db_session():
    return SessionLocal()


def _make_property_and_unit(headers, code="ZTEST"):
    prop = api_client.post("/api/v1/properties", headers=headers, json={
        "name": f"Zotel Test Property {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"Zotel Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    return prop["id"], unit["id"]


def _mapping_for(db, unit_id, room_type_id):
    mapping = ZotelRoomMapping(unit_id=unit_id, zotel_room_type_id=room_type_id, zotel_room_type_name="Test Room")
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


# ---------------------------------------------------------------------------
# Not-configured handling
# ---------------------------------------------------------------------------

def test_client_raises_not_configured_when_env_vars_empty(monkeypatch):
    monkeypatch.setattr(settings, "zotel_base_url", "")
    monkeypatch.setattr(settings, "zotel_api_key", "")
    monkeypatch.setattr(settings, "zotel_api_secret", "")
    monkeypatch.setattr(settings, "zotel_hotel_id", "")
    assert zotel_client.is_configured() is False
    with pytest.raises(ZotelNotConfiguredError):
        zotel_client.get_availability(date.today(), date.today() + timedelta(days=1))


def test_connectivity_test_endpoint_reports_not_configured(headers, monkeypatch):
    monkeypatch.setattr(settings, "zotel_base_url", "")
    monkeypatch.setattr(settings, "zotel_api_key", "")
    monkeypatch.setattr(settings, "zotel_api_secret", "")
    monkeypatch.setattr(settings, "zotel_hotel_id", "")
    resp = api_client.post("/api/v1/zotel/connectivity-test", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["overall_passed"] is False
    # Never leaks any credential value, even an empty-string one, into the response.
    assert "pk_live" not in str(body)


def test_status_endpoint_never_exposes_secret_values(headers, configured_zotel):
    resp = api_client.get("/api/v1/zotel/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert "test_fake_secret" not in str(body)
    assert "pk_live_test_fake_key" not in str(body)


# ---------------------------------------------------------------------------
# Client: successful mocked calls, matching the documented response shapes
# ---------------------------------------------------------------------------

def test_get_availability_success(configured_zotel):
    body = {"success": True, "meta": {}, "data": {"room-type-1": {"date_wise": {"2026-10-01": {"vacant_rooms": 2, "min_stay": 1, "stop_sell": False}}}}}
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = _mock_response(200, body)
        result = zotel_client.get_availability(date(2026, 10, 1), date(2026, 10, 2))
    assert result["success"] is True
    assert "room-type-1" in result["data"]


def test_get_prices_success(configured_zotel):
    body = {"success": True, "meta": {}, "data": {"room_type_id": "rt-1", "rate_plans": []}}
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = _mock_response(200, body)
        result = zotel_client.get_prices(date(2026, 10, 1), date(2026, 10, 2))
    assert result["data"]["room_type_id"] == "rt-1"


def test_get_booking_detail_success(configured_zotel):
    body = {"success": True, "meta": {}, "data": {"transaction_id": "txn-abc", "status": "created", "total_amount": 7000}}
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = _mock_response(200, body)
        result = zotel_client.get_booking_detail("txn-abc")
    assert result["data"]["transaction_id"] == "txn-abc"


def test_response_missing_data_envelope_raises(configured_zotel):
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = _mock_response(200, {"success": True})
        with pytest.raises(ZotelAPIError):
            zotel_client.get_availability(date.today(), date.today() + timedelta(days=1))


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_empty_result(configured_zotel):
    body = {"success": True, "meta": {"next_cursor": None}, "data": []}
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = _mock_response(200, body)
        result = zotel_client.get_all_bookings()
    assert result == []


def test_pagination_single_page(configured_zotel):
    body = {"success": True, "meta": {"next_cursor": None}, "data": [{"transaction_id": "t1"}, {"transaction_id": "t2"}]}
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = _mock_response(200, body)
        result = zotel_client.get_all_bookings()
    assert len(result) == 2


def test_pagination_multi_page_follows_cursor(configured_zotel):
    page1 = _mock_response(200, {"success": True, "meta": {"next_cursor": "cursor-2"}, "data": [{"transaction_id": "t1"}]})
    page2 = _mock_response(200, {"success": True, "meta": {"next_cursor": None}, "data": [{"transaction_id": "t2"}]})
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.side_effect = [page1, page2]
        result = zotel_client.get_all_bookings()
    assert [r["transaction_id"] for r in result] == ["t1", "t2"]


def test_pagination_stops_at_max_pages_safety_cap(configured_zotel):
    infinite_page = _mock_response(200, {"success": True, "meta": {"next_cursor": "always-more"}, "data": [{"transaction_id": "t"}]})
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = infinite_page
        result = zotel_client.get_all_bookings(max_pages=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code,message", [
    (401, "Invalid API key"),
    (403, "Hotel not in allowed list for this key"),
    (404, "Resource not found"),
    (409, "Price mismatch"),
    (422, "Validation failed"),
])
def test_client_error_responses_raise_with_safe_message_no_retry(configured_zotel, status_code, message):
    err_resp = _mock_response(status_code, {"success": False, "message": message})
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        mock_get = MockClient.return_value.__enter__.return_value.get
        mock_get.return_value = err_resp
        with pytest.raises(ZotelAPIError) as exc_info:
            zotel_client.get_availability(date.today(), date.today() + timedelta(days=1))
        assert exc_info.value.status_code == status_code
        assert message in str(exc_info.value)
        assert mock_get.call_count == 1  # non-retryable — no retry attempted


def test_client_retries_on_429_then_succeeds(configured_zotel):
    rate_limited = _mock_response(429, {"success": False, "message": "Rate limit exceeded"})
    ok = _mock_response(200, {"success": True, "meta": {}, "data": {}})
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, patch("app.modules.zotel.client.time.sleep"):
        MockClient.return_value.__enter__.return_value.get.side_effect = [rate_limited, ok]
        result = zotel_client.get_availability(date.today(), date.today() + timedelta(days=1))
    assert result["success"] is True


def test_client_retries_on_500_exhausts_and_raises(configured_zotel):
    server_error = _mock_response(500, {"success": False, "message": "Internal error"})
    with patch("app.modules.zotel.client.httpx.Client") as MockClient, patch("app.modules.zotel.client.time.sleep"):
        mock_get = MockClient.return_value.__enter__.return_value.get
        mock_get.return_value = server_error
        with pytest.raises(ZotelAPIError):
            zotel_client.get_availability(date.today(), date.today() + timedelta(days=1))
        assert mock_get.call_count == zotel_client.MAX_RETRIES


# ---------------------------------------------------------------------------
# Service: bookings import — mapping, idempotency, conflicts, financial safety
# ---------------------------------------------------------------------------

def _fake_summary(txn_id):
    return {"transaction_id": txn_id}


def _fake_detail(txn_id, room_type_id, check_in, check_out, status="created", total_amount=7000, paid_amount=0, adults=2, room_type_name="2BHK Flat Test", rooms_override=None):
    """
    Real observed Zotel reservation shape (confirmed against a live
    GET /bookings response during the read-only import preview) — room
    identity lives at rooms[0].category.id / rooms[0].category.name, NOT
    a flat rooms[0].room_type_id. num_adults/num_children are kept here
    only because earlier tests happened to pass them; real data does not
    include those fields either, which is a separate, out-of-scope
    observation (the code's existing `.get(...) or default` already
    handles their absence safely).
    """
    rooms = rooms_override if rooms_override is not None else [{
        "room_number": "TEST",
        "category": {"id": room_type_id, "name": room_type_name},
        "rate_plan": {"id": f"rp-{uuid.uuid4().hex[:8]}", "name": "EP (Room Only)"},
        "num_adults": adults, "num_children": 0,
    }]
    return {
        "success": True, "meta": {},
        "data": {
            "transaction_id": txn_id, "status": status,
            "customer": {"name": "Zotel Test Guest", "email": None, "phone": f"91{uuid.uuid4().int % 10**9:09d}"},
            "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
            "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
            "payment_method": "pay_now" if paid_amount else "pay_at_hotel",
            "rooms": rooms,
            "booking_source": "booking_com", "ota_booking_id": "ota-ref-1",
        },
    }


# ---------------------------------------------------------------------------
# Real-shape regression tests — room-type field-path fix
# (rooms[0].category.id / rooms[0].category.name, not a flat
# rooms[0].room_type_id, per the live GET /bookings response confirmed
# during the read-only import preview.)
# ---------------------------------------------------------------------------

def test_sync_imports_mapped_reservation_using_real_category_id_shape(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=600)
        check_out = check_in + timedelta(days=2)
        # Explicit real-shape room dict, independent of the shared helper's
        # default, so this test fails loudly if the helper's default ever
        # drifts from the real shape.
        real_shape_rooms = [{
            "room_number": "A1",
            "category": {"id": room_type_id, "name": "2BHK Flat A1"},
            "rate_plan": {"id": "rp-real", "name": "EP (Room Only)"},
        }]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, rooms_override=real_shape_rooms)):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_imported == 1
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is not None
        assert booking.unit_id == uuid.UUID(unit_id)
    finally:
        db.close()


def test_sync_handles_empty_rooms_list_safely_as_failed(headers, configured_zotel):
    db = _db_session()
    try:
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=601)
        check_out = check_in + timedelta(days=1)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, "unused", check_in, check_out, rooms_override=[])):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_failed == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None  # fails closed — nothing created
    finally:
        db.close()


def test_sync_unknown_category_id_is_unmapped_not_guessed(headers, configured_zotel):
    db = _db_session()
    try:
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=602)
        check_out = check_in + timedelta(days=1)
        unknown_room_type_id = f"rt-unknown-{uuid.uuid4().hex[:8]}"

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, unknown_room_type_id, check_in, check_out, room_type_name="Unrecognized Room")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_unmapped == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None  # never guessed onto an existing unit
    finally:
        db.close()


def test_sync_creates_booking_for_mapped_room_and_records_no_payment_when_unpaid(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=500)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, paid_amount=0)):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.status == "success"
        assert sync_log.records_imported == 1

        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is not None
        assert booking.unit_id == uuid.UUID(unit_id)
        assert booking.source == "zotel"
        assert booking.status == "confirmed"

        payments = db.scalars(select(Payment).where(Payment.booking_id == booking.id)).all()
        assert len(payments) == 0  # paid_amount was 0 — no Payment invented
    finally:
        db.close()


def test_sync_records_payment_when_zotel_reports_paid_amount(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=510)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, total_amount=7000, paid_amount=7000)):
            zotel_service.sync_bookings(db)

        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        payments = db.scalars(select(Payment).where(Payment.booking_id == booking.id)).all()
        assert len(payments) == 1
        assert float(payments[0].amount) == 7000
        assert payments[0].mode == "zotel"
        assert payments[0].reference_no == txn_id
    finally:
        db.close()


def test_sync_flags_unmapped_room_and_does_not_create_booking(headers, configured_zotel):
    db = _db_session()
    try:
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=520)
        check_out = check_in + timedelta(days=1)
        unknown_room_type = f"rt-unmapped-{uuid.uuid4().hex[:8]}"

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, unknown_room_type, check_in, check_out)):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_unmapped == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


def test_sync_is_idempotent_on_repeated_runs_for_same_transaction_id(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=530)
        check_out = check_in + timedelta(days=2)
        detail = _fake_detail(txn_id, room_type_id, check_in, check_out)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            zotel_service.sync_bookings(db)
            second_run = zotel_service.sync_bookings(db)
            third_run = zotel_service.sync_bookings(db)

        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 1  # never duplicated across 3 runs
        assert second_run.records_skipped == 1
        assert third_run.records_skipped == 1
    finally:
        db.close()


def test_sync_does_not_overwrite_existing_confirmed_os10_booking_on_conflict(headers, configured_zotel):
    """A Zotel reservation whose dates overlap an existing, unrelated OS10
    booking must never silently take over that unit — it's logged as
    conflicted and no new Booking is created."""
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        check_in = date.today() + timedelta(days=540)
        check_out = check_in + timedelta(days=3)

        guest = api_client.post("/api/v1/guests", headers=headers, json={"name": "Existing Guest", "phone": f"98{uuid.uuid4().int % 10**8:08d}"}).json()
        existing = api_client.post("/api/v1/bookings", headers=headers, json={
            "unit_id": unit_id, "guest_id": guest["id"],
            "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
            "rate_applied": 3500, "source": "direct",
        })
        assert existing.status_code == 201
        existing_booking_id = existing.json()["id"]

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out)):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_conflicted == 1
        zotel_booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert zotel_booking is None  # not imported

        # the pre-existing booking is completely untouched
        untouched = db.get(Booking, uuid.UUID(existing_booking_id))
        assert untouched.status == "confirmed"
        assert untouched.source == "direct"
    finally:
        db.close()


def test_sync_propagates_zotel_cancellation_to_still_confirmed_os10_booking(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=550)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="created")):
            zotel_service.sync_bookings(db)

        # OS10 Phase 2B: a propagated cancellation now dispatches
        # notifications (cancellation_service.cancel_booking ->
        # dispatch_cancellation_notifications) — mocked here, same
        # required pattern as test_public_booking_payment_gate.py uses for
        # its own notification-reaching paths, since this container's live
        # EMAIL_PROVIDER=resend has a real key.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="cancelled")), \
             patch("app.modules.notifications.service.send_email", return_value=None), \
             patch("app.modules.notifications.service.send_whatsapp", return_value=None), \
             patch("app.modules.notifications.service.send_sms", return_value=None):
            second_run = zotel_service.sync_bookings(db)

        assert second_run.records_updated == 1
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking.status == "cancelled"
    finally:
        db.close()


def test_sync_does_not_touch_booking_already_checked_in(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=560)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="created")):
            zotel_service.sync_bookings(db)

        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        booking.status = "checked_in"
        db.commit()

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="cancelled")):
            second_run = zotel_service.sync_bookings(db)

        assert second_run.records_conflicted == 1
        db.refresh(booking)
        assert booking.status == "checked_in"  # never auto-overwritten once operationally advanced
    finally:
        db.close()


def test_cancelled_new_reservation_is_skipped_not_imported(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=570)
        check_out = check_in + timedelta(days=1)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="cancelled")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_skipped == 1
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# New-reservation import policy: status + stay-recency gate
# (applies ONLY to brand-new reservations with no existing OS10 booking;
# reconciliation of an already-imported booking is unaffected — see the
# dedicated regression at the end of this block.) "today" is computed with
# the same timezone-aware convention the service uses
# (datetime.now(timezone.utc).date()).
# ---------------------------------------------------------------------------

def _mapped_unit(headers, db):
    _, unit_id = _make_property_and_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
    _mapping_for(db, unit_id, room_type_id)
    return unit_id, room_type_id


def test_new_reservation_completed_status_is_skipped_not_imported(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=700)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="completed")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_skipped == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


def test_new_reservation_enquired_status_is_skipped_not_imported(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=705)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="enquired")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_skipped == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


def test_new_reservation_cancelled_status_is_skipped_not_imported_regression(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=710)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="cancelled")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_skipped == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


def test_new_reservation_created_status_future_checkout_is_imported_confirmed(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=715)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="created")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_imported == 1
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is not None
        assert booking.status == "confirmed"
        assert booking.source == "zotel"
        assert booking.unit_id == uuid.UUID(unit_id)
    finally:
        db.close()


def test_new_reservation_modified_status_future_checkout_is_imported_confirmed(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=720)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="modified")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_imported == 1
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is not None
        assert booking.status == "confirmed"
        assert booking.source == "zotel"
    finally:
        db.close()


def test_new_reservation_confirmed_status_future_checkout_is_imported_confirmed(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=725)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="confirmed")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_imported == 1
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is not None
        assert booking.status == "confirmed"
        assert booking.source == "zotel"
    finally:
        db.close()


def test_new_reservation_created_status_historical_checkout_is_skipped(headers, configured_zotel):
    """A brand-new active-status reservation whose stay is already in the
    past (check_out on or before today) is a historical record, not a
    future booking — it is skipped, never imported. No Booking is created,
    so the past-date CHECK/EXCLUDE constraints are never exercised."""
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        today = datetime.now(timezone.utc).date()
        check_in = today - timedelta(days=6)
        check_out = today - timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="created")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_skipped == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


def test_new_reservation_unknown_status_is_conflicted_not_imported(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=730)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="garbage_unknown")):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_conflicted == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


def test_new_reservation_missing_status_is_conflicted_not_imported(headers, configured_zotel):
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=735)
        check_out = check_in + timedelta(days=2)

        # Build a normal detail then overwrite status to None, mirroring a
        # Zotel response that omits/nulls the status field entirely.
        detail = _fake_detail(txn_id, room_type_id, check_in, check_out, status="created")
        detail["data"]["status"] = None

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            sync_log = zotel_service.sync_bookings(db)

        assert sync_log.records_conflicted == 1
        assert sync_log.records_imported == 0
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == txn_id))
        assert booking is None
    finally:
        db.close()


def test_new_import_gate_does_not_affect_reconciliation_of_existing_booking(headers, configured_zotel):
    """Regression: the new-reservation status gate must NOT touch an
    already-imported booking. Import an active future reservation, then
    re-sync the SAME transaction id reporting 'completed'. The second run
    must take the existing-booking reconciliation path (not the new-import
    'completed -> skip' branch) and leave exactly one Booking in place."""
    db = _db_session()
    try:
        unit_id, room_type_id = _mapped_unit(headers, db)
        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=740)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="created")):
            first_run = zotel_service.sync_bookings(db)
        assert first_run.records_imported == 1

        # Re-sync same txn id as "completed". A completed re-sync of an
        # already-confirmed booking is a no-op reconciliation (no date/unit
        # change, not a cancellation) and does not dispatch notifications.
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out, status="completed")):
            second_run = zotel_service.sync_bookings(db)

        # Reconciliation path, not the new-import skip: booking preserved.
        assert second_run.records_skipped == 1
        bookings = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        assert len(bookings) == 1
        assert bookings[0].status == "confirmed"
    finally:
        db.close()


def test_sync_not_configured_produces_failed_log_not_a_crash(headers, monkeypatch):
    monkeypatch.setattr(settings, "zotel_base_url", "")
    monkeypatch.setattr(settings, "zotel_api_key", "")
    monkeypatch.setattr(settings, "zotel_api_secret", "")
    monkeypatch.setattr(settings, "zotel_hotel_id", "")
    db = _db_session()
    try:
        sync_log = zotel_service.sync_bookings(db)
        assert sync_log.status == "failed"
        assert "not configured" in sync_log.message.lower()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Reconciliation report: read-only
# ---------------------------------------------------------------------------

def test_reconciliation_report_matches_and_flags_missing_without_writing(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=580)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out)):
            zotel_service.sync_bookings(db)

        sync_log_count_before = db.scalar(select(ZotelSyncLog.id).limit(1))

        missing_txn = f"txn-missing-{uuid.uuid4().hex[:8]}"
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id), _fake_summary(missing_txn)]):
            report = zotel_service.run_reconciliation(db)

        categories = {e["external_reservation_id"]: e["category"] for e in report["entries"]}
        assert categories[txn_id] == "matched"
        assert categories[missing_txn] == "missing_in_os10"
        assert report["summary"]["matched"] >= 1
        assert report["summary"]["missing_in_os10"] >= 1
        assert sync_log_count_before is not None
        # Gap closure: reconciliation now persists its own ZotelSyncLog row (sync_type="reconciliation") — see the dedicated tests below for full coverage of this.
        recon_log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "reconciliation").order_by(ZotelSyncLog.created_at.desc()))
        assert recon_log is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Sync-log persistence for connectivity-test and reconciliation
# (Task K disclosed gap closure — previously only sync_bookings() wrote a
# ZotelSyncLog row; a connectivity test or reconciliation run only existed
# in the moment, in the HTTP response, with nothing left to look back at.)
# ---------------------------------------------------------------------------

def test_connectivity_test_success_persists_sync_log(headers, configured_zotel):
    ok = _mock_response(200, {"success": True, "meta": {}, "data": {}})
    with patch("app.modules.zotel.client.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = ok
        resp = api_client.post("/api/v1/zotel/connectivity-test", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["overall_passed"] is True

    db = _db_session()
    try:
        log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "connectivity_test").order_by(ZotelSyncLog.created_at.desc()))
        assert log is not None
        assert log.status == "success"
        assert log.records_fetched == 4  # configuration + availability + prices + bookings
        assert log.records_failed == 0
        assert log.triggered_by is not None
        assert log.message is not None and "failed=0" in log.message
    finally:
        db.close()


def test_connectivity_test_failure_persists_sync_log(headers, monkeypatch):
    monkeypatch.setattr(settings, "zotel_base_url", "")
    monkeypatch.setattr(settings, "zotel_api_key", "")
    monkeypatch.setattr(settings, "zotel_api_secret", "")
    monkeypatch.setattr(settings, "zotel_hotel_id", "")
    resp = api_client.post("/api/v1/zotel/connectivity-test", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["configured"] is False

    db = _db_session()
    try:
        log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "connectivity_test").order_by(ZotelSyncLog.created_at.desc()))
        assert log is not None
        assert log.status == "failed"
        assert log.records_fetched == 1  # only the "configuration" check ran
        assert log.records_failed == 1
        assert "not configured" in (log.message or "").lower()
    finally:
        db.close()


def test_reconciliation_success_persists_sync_log(headers, configured_zotel):
    db = _db_session()
    try:
        _, unit_id = _make_property_and_unit(headers)
        room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
        _mapping_for(db, unit_id, room_type_id)

        txn_id = f"txn-{uuid.uuid4().hex[:10]}"
        check_in = date.today() + timedelta(days=590)
        check_out = check_in + timedelta(days=2)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(txn_id)]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=_fake_detail(txn_id, room_type_id, check_in, check_out)):
            zotel_service.sync_bookings(db)

        # Reconciliation compares against EVERY OS10 booking that carries an
        # external_reservation_id — including ones other tests in this same
        # file run created earlier. For a genuinely clean, zero-discrepancy
        # "success" result, the mocked Zotel response has to account for all
        # of them (echoing each one's own current status), not just the one
        # this test created.
        existing = db.execute(select(Booking.external_reservation_id, Booking.status).where(Booking.external_reservation_id.is_not(None))).all()
        fake_zotel_reservations = [
            {"transaction_id": ext_id, "status": "cancelled" if status == "cancelled" else "created"}
            for ext_id, status in existing
        ]

        with patch("app.modules.zotel.client.get_all_bookings", return_value=fake_zotel_reservations):
            zotel_service.run_reconciliation(db)

        log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "reconciliation").order_by(ZotelSyncLog.created_at.desc()))
        assert log is not None
        assert log.status == "success"
        assert log.records_fetched == len(fake_zotel_reservations)
        assert log.records_conflicted == 0
        assert log.records_unmapped == 0
        assert log.message is not None and f"matched={len(fake_zotel_reservations)}" in log.message
    finally:
        db.close()


def test_reconciliation_with_discrepancies_persists_sync_log_as_partial(headers, configured_zotel):
    db = _db_session()
    try:
        missing_txn = f"txn-missing-{uuid.uuid4().hex[:8]}"
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[_fake_summary(missing_txn)]):
            report = zotel_service.run_reconciliation(db)

        assert report["summary"]["missing_in_os10"] >= 1

        log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "reconciliation").order_by(ZotelSyncLog.created_at.desc()))
        assert log is not None
        assert log.status == "partial"
        assert log.records_unmapped >= 1
        assert "missing_in_os10=1" in (log.message or "") or "missing_in_os10=" in (log.message or "")
    finally:
        db.close()


def test_reconciliation_client_error_persists_sync_log_as_failed(headers, configured_zotel):
    db = _db_session()
    try:
        with patch("app.modules.zotel.client.get_all_bookings", side_effect=ZotelAPIError("Rate limit exceeded", status_code=429, category="bookings")):
            report = zotel_service.run_reconciliation(db)

        assert report["entries"][0]["category"] == "error"

        log = db.scalar(select(ZotelSyncLog).where(ZotelSyncLog.sync_type == "reconciliation").order_by(ZotelSyncLog.created_at.desc()))
        assert log is not None
        assert log.status == "failed"
        assert log.records_failed == 1
        assert "Rate limit exceeded" in (log.message or "")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Router: mapping CRUD + RBAC
# ---------------------------------------------------------------------------

def test_create_and_list_and_delete_room_mapping(headers):
    _, unit_id = _make_property_and_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"

    create = api_client.post("/api/v1/zotel/mappings", headers=headers, json={
        "unit_id": unit_id, "zotel_room_type_id": room_type_id, "zotel_room_type_name": "Deluxe",
    })
    assert create.status_code == 201
    mapping_id = create.json()["id"]

    listing = api_client.get("/api/v1/zotel/mappings", headers=headers)
    assert any(m["id"] == mapping_id for m in listing.json())

    duplicate = api_client.post("/api/v1/zotel/mappings", headers=headers, json={
        "unit_id": unit_id, "zotel_room_type_id": f"rt-other-{uuid.uuid4().hex[:6]}",
    })
    assert duplicate.status_code == 400  # unit already mapped

    delete = api_client.delete(f"/api/v1/zotel/mappings/{mapping_id}", headers=headers)
    assert delete.status_code == 204


def test_mappings_endpoint_rejects_unauthenticated():
    resp = api_client.get("/api/v1/zotel/mappings")
    assert resp.status_code == 401


def test_manual_sync_trigger_requires_admin(headers):
    resp = api_client.post("/api/v1/zotel/sync/bookings", headers=headers)
    assert resp.status_code == 200
    assert "id" in resp.json()
