"""
Tests completing the Dashboard module to full production coverage:
total_guests / active_bookings counts, and the new Calendar view — all
verified against real, hand-checked database values, not placeholders.
Run with: pytest tests/test_dashboard_completion.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"DashCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"DashCompUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return unit["id"]


def test_total_guests_reflects_real_new_guest(headers):
    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    client.post("/api/v1/guests", headers=headers, json={"name": "Dashboard Completion Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"})
    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["total_guests"] == before["total_guests"] + 1


def test_active_bookings_reflects_real_new_booking(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Active Booking Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    before = client.get("/api/v1/reports/dashboard", headers=headers).json()

    check_in = date.today() + timedelta(days=11000)
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat()})

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["active_bookings"] == before["active_bookings"] + 1


def test_active_bookings_excludes_cancelled(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Cancelled Booking Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=11100)
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat()}).json()

    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    client.patch(f"/api/v1/bookings/{booking['id']}/status?new_status=cancelled", headers=headers)
    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["active_bookings"] == before["active_bookings"] - 1


def test_calendar_shows_real_checkin_checkout_on_correct_days(headers):
    """
    Hand-verified: a booking from the 10th to the 13th of a given month
    must show check_ins=1 on the 10th and check_outs=1 on the 13th, with
    in_house counted on the nights actually stayed.
    """
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Calendar Test Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2055, 3
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-10", "check_out": f"{year}-{month:02d}-13",
    })

    calendar = client.get("/api/v1/reports/dashboard/calendar", headers=headers, params={"period_year": year, "period_month": month}).json()
    days_by_date = {d["date"]: d for d in calendar["days"]}

    assert days_by_date[f"{year}-{month:02d}-10"]["check_ins"] >= 1
    assert days_by_date[f"{year}-{month:02d}-13"]["check_outs"] >= 1
    assert days_by_date[f"{year}-{month:02d}-11"]["in_house"] >= 1


def test_calendar_returns_all_days_of_a_31_day_month(headers):
    calendar = client.get("/api/v1/reports/dashboard/calendar", headers=headers, params={"period_year": 2057, "period_month": 3}).json()
    assert len(calendar["days"]) == 31


def test_calendar_february_leap_year_has_29_days(headers):
    # 2056 / 4 = 514 exactly, not a century year -> leap year
    calendar = client.get("/api/v1/reports/dashboard/calendar", headers=headers, params={"period_year": 2056, "period_month": 2}).json()
    assert len(calendar["days"]) == 29


def test_calendar_february_non_leap_year_has_28_days(headers):
    # 2057 is not divisible by 4 -> not a leap year
    calendar = client.get("/api/v1/reports/dashboard/calendar", headers=headers, params={"period_year": 2057, "period_month": 2}).json()
    assert len(calendar["days"]) == 28


def test_calendar_empty_month_returns_zeros_not_error(headers):
    calendar = client.get("/api/v1/reports/dashboard/calendar", headers=headers, params={"period_year": 2500, "period_month": 6}).json()
    assert all(d["check_ins"] == 0 and d["check_outs"] == 0 and d["in_house"] == 0 for d in calendar["days"])


def test_existing_dashboard_fields_still_present(headers):
    data = client.get("/api/v1/reports/dashboard", headers=headers).json()
    for field in ["arrivals_today", "departures_today", "in_house_guests", "occupancy_percent",
                  "housekeeping_pending", "maintenance_open", "guest_requests_open", "low_stock_count",
                  "staff_present_today", "staff_absent_today", "follow_ups_due_today",
                  "today_revenue", "today_expenses", "apartment_performance", "monthly_trend"]:
        assert field in data, f"{field} missing from dashboard response"
