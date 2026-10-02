"""Focused tests for scheduled Zotel synchronization."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings, settings
from app.db.session import SessionLocal, engine
from app.main import app
from app.modules.notifications import scheduler as scheduler_module
from app.modules.zotel import client as zotel_client, service as zotel_service
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD


def test_interval_validation_accepts_safe_values_and_defaults_unsafe_values(monkeypatch):
    assert Settings(zotel_sync_interval_minutes="5").zotel_sync_interval_minutes == 5
    assert Settings(zotel_sync_interval_minutes=45).zotel_sync_interval_minutes == 45
    assert Settings(zotel_sync_interval_minutes="invalid").zotel_sync_interval_minutes == 15
    assert Settings(zotel_sync_interval_minutes=1).zotel_sync_interval_minutes == 15
    assert Settings(zotel_sync_interval_minutes=1441).zotel_sync_interval_minutes == 15

    monkeypatch.setattr(settings, "zotel_sync_interval_minutes", 2)
    assert scheduler_module._zotel_interval_minutes() == 15


def test_scheduler_registers_zotel_job_once_and_stops_cleanly(monkeypatch):
    class FakeScheduler:
        def __init__(self):
            self.jobs = []
            self.started = False
            self.shutdown_calls = []

        def add_job(self, function, trigger, **options):
            self.jobs.append((function, trigger, options))

        def start(self):
            self.started = True

        def shutdown(self, *, wait):
            self.shutdown_calls.append(wait)
            self.started = False

    schedulers = []

    def new_scheduler():
        scheduler = FakeScheduler()
        schedulers.append(scheduler)
        return scheduler

    monkeypatch.setattr(scheduler_module, "BackgroundScheduler", new_scheduler)
    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    monkeypatch.setattr(settings, "enable_scheduler", True)
    monkeypatch.setattr(settings, "zotel_sync_interval_minutes", 12)

    first = scheduler_module.start_scheduler()
    second = scheduler_module.start_scheduler()
    assert first is second
    assert len(schedulers) == 1
    assert first.started is True

    zotel_jobs = [job for job in first.jobs if job[2].get("id") == "zotel_booking_sync"]
    assert len(zotel_jobs) == 1
    function, trigger, options = zotel_jobs[0]
    assert function is scheduler_module._run_zotel_sync
    assert trigger == "interval"
    assert options["minutes"] == 12
    assert options["max_instances"] == 1
    assert options["coalesce"] is True
    assert options["replace_existing"] is True

    scheduler_module.stop_scheduler()
    scheduler_module.stop_scheduler()
    assert first.started is False
    assert first.shutdown_calls == [False]
    assert scheduler_module._scheduler is None


def test_scheduled_job_calls_existing_sync_service(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="notifications.scheduler")
    db = Mock()
    sync_log = SimpleNamespace(
        id="sync-log-id",
        status="success",
        records_fetched=2,
        records_imported=1,
        records_updated=0,
        records_skipped=1,
        records_conflicted=0,
        records_failed=0,
    )
    sync = Mock(return_value=sync_log)
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(zotel_client, "is_configured", lambda: True)
    monkeypatch.setattr(zotel_service, "sync_bookings", sync)

    scheduler_module._run_zotel_sync()

    sync.assert_called_once_with(db, triggered_by=None)
    db.close.assert_called_once_with()
    assert "Scheduled Zotel sync finished" in caplog.text
    assert "sync-log-id" in caplog.text


def test_unconfigured_zotel_skips_without_opening_db_or_calling_api(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="notifications.scheduler")
    session_factory = Mock(side_effect=AssertionError("database session should not be opened"))
    get_bookings = Mock(side_effect=AssertionError("Zotel API must not be called"))
    monkeypatch.setattr(scheduler_module, "SessionLocal", session_factory)
    monkeypatch.setattr(zotel_client, "is_configured", lambda: False)
    monkeypatch.setattr(zotel_client, "get_all_bookings", get_bookings)
    monkeypatch.setattr(scheduler_module, "_zotel_not_configured_logged", False)

    scheduler_module._run_zotel_sync()
    scheduler_module._run_zotel_sync()

    session_factory.assert_not_called()
    get_bookings.assert_not_called()
    assert caplog.text.count("Zotel is not configured") == 1


def test_scheduled_job_handles_sync_failure_and_closes_session(monkeypatch, caplog):
    db = Mock()
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(zotel_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        zotel_service,
        "sync_bookings",
        Mock(side_effect=RuntimeError("credential-like-error-must-not-be-logged")),
    )

    scheduler_module._run_zotel_sync()

    db.close.assert_called_once_with()
    assert "exception type=RuntimeError" in caplog.text
    assert "credential-like-error-must-not-be-logged" not in caplog.text


def test_scheduled_job_reuses_existing_advisory_lock_and_skips_busy_sync(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="notifications.scheduler")
    monkeypatch.setattr(zotel_client, "is_configured", lambda: True)
    get_bookings = Mock(side_effect=AssertionError("busy scheduled sync must not fetch reservations"))
    monkeypatch.setattr(zotel_client, "get_all_bookings", get_bookings)

    lock_connection = engine.connect()
    acquired = lock_connection.execute(
        select(func.pg_try_advisory_lock(zotel_service.ZOTEL_SYNC_ADVISORY_LOCK_KEY))
    ).scalar_one()
    lock_connection.commit()
    assert acquired is True
    try:
        scheduler_module._run_zotel_sync()
        get_bookings.assert_not_called()
        assert "another sync is already running" in caplog.text
    finally:
        assert lock_connection.execute(
            select(func.pg_advisory_unlock(zotel_service.ZOTEL_SYNC_ADVISORY_LOCK_KEY))
        ).scalar_one() is True
        lock_connection.commit()
        lock_connection.close()


def test_manual_sync_endpoint_still_uses_existing_sync_flow():
    api_client = TestClient(app)
    auth = api_client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert auth.status_code == 200, auth.text
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}

    # The manual endpoint remains service-backed. This fake avoids all Zotel
    # network calls while letting the existing service persist its normal log.
    with patch("app.modules.zotel.client.get_all_bookings", return_value=[]):
        response = api_client.post("/api/v1/zotel/sync/bookings", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["sync_type"] == "bookings_import"
    assert response.json()["status"] == "success"
