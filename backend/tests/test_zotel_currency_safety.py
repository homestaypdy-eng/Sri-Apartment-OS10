"""
Zotel F7 — currency safety.

OS10 records every amount in INR (Payment and Booking have no currency
column). The documented Zotel booking-detail shape carries no currency
field, so a missing currency is treated as the property's INR. But when a
reservation explicitly reports any other currency (or a malformed value),
its amounts must never be recorded as if they were rupees: no Payment row
and no rupee nightly rate. The booking itself is still imported, so the
unit stays blocked for those dates.

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

_MISSING = object()


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


def _mapped_category(headers) -> str:
    prop = api_client.post("/api/v1/properties", headers=headers, json={
        "name": f"CS Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit = api_client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"CS Unit {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    category_id = f"cs-cat-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.add(ZotelRoomMapping(unit_id=uuid.UUID(unit["id"]), zotel_room_type_id=category_id, zotel_room_type_name="CS Room"))
        db.commit()
    finally:
        db.close()
    return category_id


def _detail(txn_id, category_id, check_in, total_amount=400, paid_amount=200, currency=_MISSING):
    data = {
        "transaction_id": txn_id, "status": "created",
        "customer": {"name": "CS Guest", "email": None, "phone": f"91{uuid.uuid4().int % 10**9:09d}"},
        "checkin": check_in.isoformat(), "checkout": (check_in + timedelta(days=2)).isoformat(),
        "total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount,
        "payment_method": "pay_now",
        "rooms": [{
            "room_number": "R",
            "category": {"id": category_id, "name": "CS Room"},
            "rate_plan": {"id": "rp-cs", "name": "EP (Room Only)"},
            "num_adults": 2, "num_children": 0,
        }],
        "booking_source": "booking_com", "ota_booking_id": "ota-ref-cs",
    }
    if currency is not _MISSING:
        data["currency"] = currency
    return {"success": True, "meta": {}, "data": data}


def _sync(txn_id, detail) -> list[ZotelSyncRecord]:
    db = SessionLocal()
    try:
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": txn_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            sync_log = zotel_service.sync_bookings(db)
        return db.scalars(select(ZotelSyncRecord).where(ZotelSyncRecord.sync_log_id == sync_log.id)).all()
    finally:
        db.close()


def _recorded(txn_id) -> float:
    db = SessionLocal()
    try:
        return round(sum(float(p.amount) for p in db.scalars(select(Payment).where(Payment.reference_no == txn_id)).all()), 2)
    finally:
        db.close()


def _booking(txn_id) -> Booking:
    db = SessionLocal()
    try:
        (booking,) = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        return booking
    finally:
        db.close()


@pytest.mark.parametrize("currency", ["INR", " inr "])
def test_explicit_inr_is_recorded(headers, configured_zotel, currency):
    category_id = _mapped_category(headers)
    txn_id = f"CS-INR-{uuid.uuid4().hex[:8]}"
    records = _sync(txn_id, _detail(txn_id, category_id, date.today() + timedelta(days=30), currency=currency))

    assert [r.outcome for r in records] == ["imported"], [(r.outcome, r.detail) for r in records]
    assert _recorded(txn_id) == 200.0
    assert float(_booking(txn_id).rate_applied) == 200.0  # 400 total over 2 nights


def test_missing_currency_is_treated_as_inr(headers, configured_zotel):
    # The documented Zotel booking-detail shape has no currency field at all.
    category_id = _mapped_category(headers)
    txn_id = f"CS-NONE-{uuid.uuid4().hex[:8]}"
    records = _sync(txn_id, _detail(txn_id, category_id, date.today() + timedelta(days=32)))

    assert [r.outcome for r in records] == ["imported"], [(r.outcome, r.detail) for r in records]
    assert _recorded(txn_id) == 200.0
    assert float(_booking(txn_id).rate_applied) == 200.0


def test_usd_amount_is_never_recorded_as_rupees(headers, configured_zotel):
    category_id = _mapped_category(headers)
    txn_id = f"CS-USD-{uuid.uuid4().hex[:8]}"
    check_in = date.today() + timedelta(days=34)
    records = _sync(txn_id, _detail(txn_id, category_id, check_in, currency="USD"))

    assert _recorded(txn_id) == 0.0, "200 USD must not be recorded as a 200 rupee payment"
    booking = _booking(txn_id)
    assert (booking.status, booking.check_in) == ("confirmed", check_in), "the booking still blocks the unit"
    assert float(booking.rate_applied) == 0.0, "no rupee nightly rate derived from a USD total"
    assert [r.outcome for r in records] == ["imported"], [(r.outcome, r.detail) for r in records]
    assert "USD" in records[0].detail and "not recorded" in records[0].detail, records[0].detail


@pytest.mark.parametrize("currency", ["RS", "₹", "", "INR,USD", 123, {"code": "INR"}, ["INR"]])
def test_unsupported_currency_values_are_not_recorded(headers, configured_zotel, currency):
    category_id = _mapped_category(headers)
    txn_id = f"CS-BAD-{uuid.uuid4().hex[:8]}"
    records = _sync(txn_id, _detail(txn_id, category_id, date.today() + timedelta(days=36), currency=currency))

    assert _recorded(txn_id) == 0.0
    assert float(_booking(txn_id).rate_applied) == 0.0
    assert "not recorded" in records[0].detail, records[0].detail


def test_non_inr_top_up_on_existing_booking_is_not_recorded(headers, configured_zotel):
    category_id = _mapped_category(headers)
    txn_id = f"CS-TOP-{uuid.uuid4().hex[:8]}"
    check_in = date.today() + timedelta(days=38)
    _sync(txn_id, _detail(txn_id, category_id, check_in, paid_amount=0))
    assert _recorded(txn_id) == 0.0

    records = _sync(txn_id, _detail(txn_id, category_id, check_in, paid_amount=200, currency="EUR"))
    assert _recorded(txn_id) == 0.0, "a EUR top-up must not be recorded as rupees on re-sync"
    assert "EUR" in records[0].detail, records[0].detail


def test_non_inr_reservation_is_flagged_by_reconciliation(headers, configured_zotel):
    category_id = _mapped_category(headers)
    txn_id = f"CS-REC-{uuid.uuid4().hex[:8]}"
    detail = _detail(txn_id, category_id, date.today() + timedelta(days=40), currency="USD")
    _sync(txn_id, detail)

    summary_row = {"transaction_id": txn_id, "status": "created", "paid_amount": 200, "currency": "USD"}
    db = SessionLocal()
    try:
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[summary_row]):
            report = zotel_service.run_reconciliation(db)
    finally:
        db.close()
    categories = {e["category"] for e in report["entries"] if e["external_reservation_id"] == txn_id}
    assert "amount_mismatch" in categories, report["entries"]
