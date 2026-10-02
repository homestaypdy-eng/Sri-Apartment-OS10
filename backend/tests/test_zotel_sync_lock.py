"""Deterministic PostgreSQL advisory-lock tests for Zotel booking sync."""

import threading
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.session import SessionLocal, engine
from app.main import app
from app.modules.bookings.models import Booking
from app.modules.properties.models import Property, Unit
from app.modules.zotel import service
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncRecord
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD


def _run_empty_sync():
    db = SessionLocal()
    try:
        return service.sync_bookings(db)
    finally:
        db.close()


def test_successful_sync_releases_lock_for_later_sync():
    with patch("app.modules.zotel.client.get_all_bookings", return_value=[]) as get_bookings:
        first = _run_empty_sync()
        second = _run_empty_sync()

    assert first.status == "success"
    assert second.status == "success"
    assert get_bookings.call_count == 2


def test_concurrent_sync_is_rejected_before_fetching_reservations():
    fetch_started = threading.Event()
    allow_fetch_to_finish = threading.Event()
    results = []
    errors = []

    def slow_fetch(*, status=None):
        fetch_started.set()
        if not allow_fetch_to_finish.wait(timeout=15):
            raise TimeoutError("test did not release the first sync")
        return []

    def run_first_sync():
        db = SessionLocal()
        try:
            results.append(service.sync_bookings(db))
        except BaseException as exc:  # surfaced in the test thread below
            errors.append(exc)
        finally:
            db.close()

    worker = threading.Thread(target=run_first_sync, daemon=True)
    with patch("app.modules.zotel.client.get_all_bookings", side_effect=slow_fetch) as get_bookings:
        worker.start()
        try:
            assert fetch_started.wait(timeout=10), "first sync did not reach reservation fetch"
            second_db = SessionLocal()
            try:
                with pytest.raises(service.ZotelSyncAlreadyRunning):
                    service.sync_bookings(second_db)
            finally:
                second_db.close()
            assert get_bookings.call_count == 1
        finally:
            allow_fetch_to_finish.set()
            worker.join(timeout=15)

    assert not worker.is_alive(), "first sync worker did not finish"
    assert not errors
    assert len(results) == 1
    assert results[0].status == "success"


def test_sync_exception_releases_lock_and_session_can_be_reused():
    db = SessionLocal()
    try:
        with patch("app.modules.zotel.client.get_all_bookings", side_effect=RuntimeError("mock fetch failure")):
            with pytest.raises(RuntimeError, match="mock fetch failure"):
                service.sync_bookings(db)

        with patch("app.modules.zotel.client.get_all_bookings", return_value=[]):
            later_sync = service.sync_bookings(db)
        assert later_sync.status == "success"
    finally:
        db.close()


def test_lock_store_failure_fails_closed_without_fetching():
    class BrokenBind:
        def connect(self):
            raise RuntimeError("private database detail")

    class BrokenSession:
        def get_bind(self):
            return BrokenBind()

    with patch("app.modules.zotel.client.get_all_bookings") as get_bookings:
        with pytest.raises(service.ZotelSyncLockUnavailable):
            service.sync_bookings(BrokenSession())
    get_bookings.assert_not_called()


def test_sync_endpoint_returns_conflict_when_another_worker_owns_lock():
    api_client = TestClient(app)
    auth = api_client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert auth.status_code == 200, auth.text
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}

    lock_connection = engine.connect()
    acquired = lock_connection.execute(
        select(func.pg_try_advisory_lock(service.ZOTEL_SYNC_ADVISORY_LOCK_KEY))
    ).scalar_one()
    lock_connection.commit()
    assert acquired is True
    try:
        with patch("app.modules.zotel.client.get_all_bookings") as get_bookings:
            response = api_client.post("/api/v1/zotel/sync/bookings", headers=headers)
        assert response.status_code == 409
        assert response.json() == {
            "detail": "A Zotel booking sync is already running. Please try again when it finishes."
        }
        get_bookings.assert_not_called()
    finally:
        released = lock_connection.execute(
            select(func.pg_advisory_unlock(service.ZOTEL_SYNC_ADVISORY_LOCK_KEY))
        ).scalar_one()
        lock_connection.commit()
        lock_connection.close()
        assert released is True


@pytest.mark.parametrize("unit_status", ["maintenance", "blocked"])
def test_sync_lock_does_not_change_maintenance_or_blocked_unit_protection(unit_status):
    db = SessionLocal()
    try:
        property_ = Property(name=f"Lock test {unit_status}", address="Test", state_code="34")
        db.add(property_)
        db.flush()
        unit = Unit(
            property_id=property_.id,
            name=f"{unit_status} unit",
            base_rate=2500,
            status=unit_status,
        )
        db.add(unit)
        db.flush()
        room_type_id = f"rt-{unit_status}-{unit.id.hex[:8]}"
        db.add(ZotelRoomMapping(
            unit_id=unit.id,
            zotel_room_type_id=room_type_id,
            zotel_room_type_name="Lock Test Room",
        ))
        db.commit()

        transaction_id = f"txn-{unit_status}-{unit.id.hex[:8]}"
        check_in = date.today() + timedelta(days=800)
        detail = {
            "data": {
                "transaction_id": transaction_id,
                "status": "created",
                "checkin": check_in.isoformat(),
                "checkout": (check_in + timedelta(days=1)).isoformat(),
                "rooms": [{"category": {"id": room_type_id, "name": "Lock Test Room"}}],
            }
        }
        with patch("app.modules.zotel.client.get_all_bookings", return_value=[{"transaction_id": transaction_id}]), \
             patch("app.modules.zotel.client.get_booking_detail", return_value=detail):
            sync_log = service.sync_bookings(db)

        record = db.scalar(select(ZotelSyncRecord).where(ZotelSyncRecord.sync_log_id == sync_log.id))
        assert sync_log.records_conflicted == 1
        assert record.outcome == "conflicted"
        assert unit_status in record.detail
        assert db.scalar(select(Booking).where(Booking.external_reservation_id == transaction_id)) is None
    finally:
        db.close()
