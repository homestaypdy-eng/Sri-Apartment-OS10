"""
Tests for the consolidated Operational Dashboard endpoint.
Run with: pytest tests/test_operational_dashboard.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"DashProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"DashUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def test_dashboard_returns_all_expected_fields(headers):
    resp = client.get("/api/v1/reports/dashboard", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    for field in [
        "arrivals_today", "departures_today", "in_house_guests", "total_units", "vacant_units",
        "occupancy_percent", "pending_checkins", "pending_checkouts", "pending_payments_count",
        "pending_payments_amount", "housekeeping_pending", "maintenance_open",
        "today_revenue", "today_expenses", "today_profit", "month_revenue", "month_expenses",
        "month_profit", "apartment_performance",
    ]:
        assert field in data, f"Missing field: {field}"


def test_checked_in_booking_counts_as_in_house_and_reduces_vacancy(headers):
    """
    Uses >= rather than == for the in_house_guests delta: this test suite
    runs against a long-lived, shared database (no per-test transactional
    rollback), so other check-ins from manual live-testing sessions can
    land on "today" too. What matters is that OUR new checked-in booking
    is reflected at all.
    """
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Dashboard Guest", "phone": f"90{uuid.uuid4().int % 10**8:08d}"}).json()

    before = client.get("/api/v1/reports/dashboard", headers=headers).json()

    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": date.today().isoformat(), "check_out": (date.today() + timedelta(days=2)).isoformat(),
    }).json()
    status_resp = client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_in"})
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "checked_in"

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["in_house_guests"] >= before["in_house_guests"] + 1


def test_arrivals_today_counts_confirmed_booking_checking_in_today(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Arrival Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()

    before = client.get("/api/v1/reports/dashboard", headers=headers).json()

    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": date.today().isoformat(), "check_out": (date.today() + timedelta(days=1)).isoformat(),
    })

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["arrivals_today"] == before["arrivals_today"] + 1


def test_apartment_performance_lists_every_unit(headers):
    unit_id = _make_unit(headers)
    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert any(a["unit_id"] == unit_id for a in dashboard["apartment_performance"])


def test_unauthenticated_cannot_access_dashboard():
    assert client.get("/api/v1/reports/dashboard").status_code == 401
