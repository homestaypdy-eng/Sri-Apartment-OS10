"""Regression coverage for a changed Zotel room mapping on re-sync."""
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Booking
from app.modules.zotel import service as zotel_service
from app.modules.zotel.models import ZotelSyncRecord
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture
def configured_zotel(monkeypatch):
    monkeypatch.setattr(settings, "zotel_base_url", "https://test-property.example.com/api/external/v1")
    monkeypatch.setattr(settings, "zotel_api_key", "test-only-key")
    monkeypatch.setattr(settings, "zotel_api_secret", "test-only-secret")
    monkeypatch.setattr(settings, "zotel_hotel_id", "test-only-hotel")


def test_resync_with_changed_room_mapping_conflicts_without_moving_booking(configured_zotel):
    auth = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert auth.status_code == 200, auth.text
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}

    prop = client.post("/api/v1/properties", headers=headers, json={
        "name": f"Zotel Drift Property {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
    }).json()
    unit1 = client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"Mapped Unit A {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()
    unit2 = client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"Mapped Unit B {uuid.uuid4().hex[:6]}", "base_rate": 3500,
    }).json()

    room_type_id = f"rt-drift-{uuid.uuid4().hex[:8]}"
    create_mapping = client.post("/api/v1/zotel/mappings", headers=headers, json={
        "unit_id": unit1["id"], "zotel_room_type_id": room_type_id, "zotel_room_type_name": "Drift Test Room",
    })
    assert create_mapping.status_code == 201, create_mapping.text

    transaction_id = f"txn-drift-{uuid.uuid4().hex[:10]}"
    check_in = date.today() + timedelta(days=700)
    check_out = check_in + timedelta(days=2)
    summary = {"transaction_id": transaction_id}
    detail = {
        "success": True, "meta": {},
        "data": {
            "transaction_id": transaction_id, "status": "created",
            "customer": {"name": "Zotel Mapping Drift Guest", "phone": f"91{uuid.uuid4().int % 10**9:09d}"},
            "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
            "total_amount": 7000, "paid_amount": 0,
            "rooms": [{"category": {"id": room_type_id, "name": "Drift Test Room"}, "num_adults": 2}],
        },
    }

    db = SessionLocal()
    try:
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[summary]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            first_sync = zotel_service.sync_bookings(db)
        assert first_sync.records_imported == 1
        booking = db.scalar(select(Booking).where(Booking.external_reservation_id == transaction_id))
        assert booking is not None
        assert booking.unit_id == uuid.UUID(unit1["id"])

        delete_mapping = client.delete(
            f"/api/v1/zotel/mappings/{create_mapping.json()['id']}", headers=headers,
        )
        assert delete_mapping.status_code == 204
        remapped = client.post("/api/v1/zotel/mappings", headers=headers, json={
            "unit_id": unit2["id"], "zotel_room_type_id": room_type_id, "zotel_room_type_name": "Drift Test Room",
        })
        assert remapped.status_code == 201, remapped.text

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[summary]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            second_sync = zotel_service.sync_bookings(db)

        assert second_sync.records_conflicted == 1
        record = db.scalar(select(ZotelSyncRecord).where(ZotelSyncRecord.sync_log_id == second_sync.id))
        assert record is not None
        assert record.outcome == "conflicted"
        assert "current Zotel room mapping points to OS10 unit" in record.detail
        assert "will NOT be auto-moved" in record.detail
        assert "Manual review is required" in record.detail

        db.refresh(booking)
        assert booking.unit_id == uuid.UUID(unit1["id"])
        assert booking.unit_id != uuid.UUID(unit2["id"])
        assert len(db.scalars(select(Booking).where(Booking.external_reservation_id == transaction_id)).all()) == 1
    finally:
        db.close()
