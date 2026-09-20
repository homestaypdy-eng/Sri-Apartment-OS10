"""
Staff Management: Post/Designation defaults + employee-specific overrides,
automatic attendance/overtime computation, and leave-vs-entitlement
counting.

Covers:
- StaffDesignation CRUD + RBAC (admin-write, admin+staff-read)
- hr_policy.resolve_employee_policy: override wins over designation default,
  designation default used when no override, "unconfigured" when neither
  exists — pure unit tests, no DB
- hr_policy.compute_attendance_hours: normal shift, overnight shift, short
  attendance (clamped at zero), exact-boundary (zero overtime) — pure
  unit tests, no DB
- hr_policy leave-in-month counting: fully-inside-month, spanning a month
  boundary, exactly-at-entitlement, over-entitlement, rejected/pending
  excluded — pure unit tests, no DB
- End-to-end through the API: designation -> profile override -> resolved
  policy -> attendance check-in/check-out -> computed hours -> monthly
  summary
- Regression: existing generate_payroll/mark_payroll_paid flow unchanged

Run with: pytest tests/test_staff_designations.py -v
"""
import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.staff import hr_policy

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def staff_headers(headers):
    email = f"hrtest_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "HR Test Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_designation(headers, **overrides):
    payload = {
        "name": f"Designation {uuid.uuid4().hex[:8]}",
        "default_basic_salary": 16000,
        "default_shift_start": "09:00:00",
        "default_shift_end": "18:00:00",
        "default_lunch_break_minutes": 60,
        "standard_working_hours": 8.0,
        "leave_entitlement_per_month": 4,
        "overtime_mode": "tracked_no_pay",
        **overrides,
    }
    resp = client.post("/api/v1/staff/designations", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _register_and_login(headers, role="staff"):
    email = f"hrtest_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    resp = client.post("/api/v1/auth/register", headers=headers, json={"name": "HR Test User", "email": email, "password": "Pass12345!", "role": role})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"] if "id" in resp.json() else resp.json().get("user_id")


# ---------------------------------------------------------------------------
# 1. Designation CRUD + RBAC
# ---------------------------------------------------------------------------

def test_designation_create_requires_admin(staff_headers):
    payload = {
        "name": f"NoAuth {uuid.uuid4().hex[:6]}",
        "default_basic_salary": 15000,
        "default_shift_start": "08:00:00",
        "default_shift_end": "17:00:00",
    }
    resp = client.post("/api/v1/staff/designations", headers=staff_headers, json=payload)
    assert resp.status_code == 403


def test_designation_crud_roundtrip(headers, staff_headers):
    created = _create_designation(headers, default_basic_salary=15000, default_shift_start="08:00:00", default_shift_end="17:00:00")
    designation_id = created["id"]
    assert created["default_basic_salary"] == 15000
    assert created["is_active"] is True
    assert created["overtime_mode"] == "tracked_no_pay"

    # staff role can read
    resp = client.get(f"/api/v1/staff/designations/{designation_id}", headers=staff_headers)
    assert resp.status_code == 200

    # admin can update
    resp = client.patch(f"/api/v1/staff/designations/{designation_id}", headers=headers, json={"default_basic_salary": 17000})
    assert resp.status_code == 200
    assert resp.json()["default_basic_salary"] == 17000

    # staff cannot update
    resp = client.patch(f"/api/v1/staff/designations/{designation_id}", headers=staff_headers, json={"default_basic_salary": 99999})
    assert resp.status_code == 403

    # deactivate / reactivate (soft, never deleted)
    resp = client.patch(f"/api/v1/staff/designations/{designation_id}/deactivate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    # deactivated designations excluded from default list
    resp = client.get("/api/v1/staff/designations", headers=headers)
    assert designation_id not in [d["id"] for d in resp.json()]
    resp = client.get("/api/v1/staff/designations?include_inactive=true", headers=headers)
    assert designation_id in [d["id"] for d in resp.json()]

    resp = client.patch(f"/api/v1/staff/designations/{designation_id}/reactivate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


def test_designation_name_must_be_unique(headers):
    name = f"Unique {uuid.uuid4().hex[:8]}"
    _create_designation(headers, name=name)
    resp = client.post(
        "/api/v1/staff/designations", headers=headers,
        json={"name": name, "default_basic_salary": 16000, "default_shift_start": "09:00:00", "default_shift_end": "18:00:00"},
    )
    assert resp.status_code == 409


def test_designation_invalid_overtime_mode_rejected(headers):
    resp = client.post(
        "/api/v1/staff/designations", headers=headers,
        json={
            "name": f"BadMode {uuid.uuid4().hex[:6]}", "default_basic_salary": 16000,
            "default_shift_start": "09:00:00", "default_shift_end": "18:00:00",
            "overtime_mode": "not_a_real_mode",
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 2. hr_policy.resolve_employee_policy — pure unit tests, no DB
# ---------------------------------------------------------------------------

def _designation(**kw):
    defaults = dict(
        default_basic_salary=16000, default_shift_start=time(9, 0), default_shift_end=time(18, 0),
        default_lunch_break_minutes=60, standard_working_hours=8.0, leave_entitlement_per_month=4,
        overtime_mode="tracked_no_pay", overtime_rate_multiplier=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _profile(**kw):
    defaults = dict(
        salary_override=None, shift_start_override=None, shift_end_override=None,
        lunch_break_override_minutes=None, standard_working_hours_override=None,
        leave_entitlement_override=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_resolve_policy_uses_designation_default_when_no_override():
    policy = hr_policy.resolve_employee_policy(_profile(), _designation())
    assert policy.basic_salary == 16000
    assert policy.shift_start == time(9, 0)
    assert policy.source["basic_salary"] == "designation"


def test_resolve_policy_employee_override_wins():
    policy = hr_policy.resolve_employee_policy(_profile(salary_override=20000, shift_start_override=time(10, 0)), _designation())
    assert policy.basic_salary == 20000
    assert policy.shift_start == time(10, 0)
    assert policy.source["basic_salary"] == "override"
    # untouched field still falls back to designation default
    assert policy.shift_end == time(18, 0)
    assert policy.source["shift_end"] == "designation"


def test_resolve_policy_unconfigured_when_no_override_and_no_designation():
    policy = hr_policy.resolve_employee_policy(_profile(), None)
    assert policy.basic_salary is None
    assert policy.shift_start is None
    assert policy.source["basic_salary"] == "unconfigured"


def test_resolve_policy_override_wins_even_with_no_designation():
    policy = hr_policy.resolve_employee_policy(_profile(salary_override=12345), None)
    assert policy.basic_salary == 12345
    assert policy.source["basic_salary"] == "override"


# ---------------------------------------------------------------------------
# 3. hr_policy.compute_attendance_hours — pure unit tests, no DB
# ---------------------------------------------------------------------------

def test_compute_hours_normal_shift():
    c = hr_policy.compute_attendance_hours(date(2026, 6, 1), time(9, 0), time(18, 0), lunch_break_minutes=60, standard_working_hours=8.0)
    assert c.total_presence_minutes == 9 * 60
    assert c.lunch_break_minutes_applied == 60
    assert c.working_minutes == 8 * 60
    assert c.overtime_minutes == 0


def test_compute_hours_with_overtime():
    c = hr_policy.compute_attendance_hours(date(2026, 6, 1), time(9, 0), time(20, 0), lunch_break_minutes=60, standard_working_hours=8.0)
    # 11h presence - 1h lunch = 10h working; 10h - 8h standard = 2h overtime
    assert c.working_minutes == 10 * 60
    assert c.overtime_minutes == 2 * 60


def test_compute_hours_overnight_shift():
    # Night security guard: 22:00 -> 06:00 crosses midnight
    c = hr_policy.compute_attendance_hours(date(2026, 6, 1), time(22, 0), time(6, 0), lunch_break_minutes=30, standard_working_hours=8.0)
    assert c.total_presence_minutes == 8 * 60  # 22:00 to next-day 06:00 = 8h
    assert c.working_minutes == 8 * 60 - 30


def test_compute_hours_short_attendance_clamps_at_zero():
    # arrived and left within what would be the lunch window
    c = hr_policy.compute_attendance_hours(date(2026, 6, 1), time(13, 0), time(13, 20), lunch_break_minutes=60, standard_working_hours=8.0)
    assert c.total_presence_minutes == 20
    assert c.working_minutes == 0  # clamped, never negative
    assert c.overtime_minutes == 0


def test_compute_hours_exact_boundary_zero_overtime():
    c = hr_policy.compute_attendance_hours(date(2026, 6, 1), time(9, 0), time(18, 0), lunch_break_minutes=60, standard_working_hours=8.0)
    assert c.overtime_minutes == 0  # working_minutes == standard exactly


def test_compute_hours_no_lunch_configured():
    c = hr_policy.compute_attendance_hours(date(2026, 6, 1), time(9, 0), time(17, 0), lunch_break_minutes=None, standard_working_hours=8.0)
    assert c.lunch_break_minutes_applied == 0
    assert c.working_minutes == 8 * 60


# ---------------------------------------------------------------------------
# 4. hr_policy leave-in-month counting — pure unit tests, no DB
# ---------------------------------------------------------------------------

def _leave(from_date, to_date, status="approved"):
    return SimpleNamespace(from_date=from_date, to_date=to_date, status=status)


def test_leave_fully_inside_month():
    days = hr_policy.count_approved_leave_days_in_month([_leave(date(2026, 6, 5), date(2026, 6, 7))], 2026, 6)
    assert days == 3


def test_leave_spanning_month_boundary_only_counts_days_in_target_month():
    lr = _leave(date(2026, 5, 30), date(2026, 6, 2))  # May 30, 31, Jun 1, 2
    assert hr_policy.count_approved_leave_days_in_month([lr], 2026, 5) == 2  # May 30, 31
    assert hr_policy.count_approved_leave_days_in_month([lr], 2026, 6) == 2  # Jun 1, 2


def test_leave_pending_and_rejected_excluded():
    rows = [
        _leave(date(2026, 6, 5), date(2026, 6, 5), status="pending"),
        _leave(date(2026, 6, 6), date(2026, 6, 6), status="rejected"),
        _leave(date(2026, 6, 7), date(2026, 6, 7), status="approved"),
    ]
    assert hr_policy.count_approved_leave_days_in_month(rows, 2026, 6) == 1


def test_split_free_and_excess_leave_within_entitlement():
    free, excess = hr_policy.split_free_and_excess_leave(3, 4)
    assert (free, excess) == (3, 0)


def test_split_free_and_excess_leave_exactly_at_entitlement():
    free, excess = hr_policy.split_free_and_excess_leave(4, 4)
    assert (free, excess) == (4, 0)


def test_split_free_and_excess_leave_over_entitlement():
    free, excess = hr_policy.split_free_and_excess_leave(6, 4)
    assert (free, excess) == (4, 2)  # excess counted, no deduction amount invented


def test_split_free_and_excess_leave_no_entitlement_configured():
    free, excess = hr_policy.split_free_and_excess_leave(2, None)
    assert (free, excess) == (0, 2)


# ---------------------------------------------------------------------------
# 5. End-to-end through the API
# ---------------------------------------------------------------------------

def test_resolved_policy_endpoint_reflects_override(headers):
    designation = _create_designation(headers, default_basic_salary=15000, default_shift_start="08:00:00", default_shift_end="17:00:00")
    user_id = _register_and_login(headers)

    profile_resp = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user_id, "designation_id": designation["id"]})
    assert profile_resp.status_code == 201, profile_resp.text
    profile_id = profile_resp.json()["id"]

    # Before override: resolved policy matches designation default
    resp = client.get(f"/api/v1/staff/policy/{user_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["basic_salary"] == 15000
    assert body["source"]["basic_salary"] == "designation"

    # Employee-specific override
    resp = client.patch(f"/api/v1/staff/profiles/{profile_id}", headers=headers, json={"salary_override": 18500, "shift_start_override": "10:00:00"})
    assert resp.status_code == 200

    resp = client.get(f"/api/v1/staff/policy/{user_id}", headers=headers)
    body = resp.json()
    assert body["basic_salary"] == 18500
    assert body["source"]["basic_salary"] == "override"
    assert body["shift_start"] == "10:00:00"
    # untouched fields still resolve to the designation default
    assert body["shift_end"] == "17:00:00"
    assert body["source"]["shift_end"] == "designation"


def test_attendance_checkin_then_checkout_computes_hours(headers):
    designation = _create_designation(headers, default_lunch_break_minutes=60, standard_working_hours=8.0)
    user_id = _register_and_login(headers)
    client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user_id, "designation_id": designation["id"]})

    today = date.today().isoformat()
    resp = client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": user_id, "date": today, "check_in_time": "09:00:00", "status": "present"})
    assert resp.status_code == 201, resp.text
    attendance = resp.json()
    assert attendance["working_minutes"] is None  # no check-out yet — never guessed

    resp = client.patch(f"/api/v1/staff/attendance/{attendance['id']}", headers=headers, json={"check_out_time": "19:00:00"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_presence_minutes"] == 10 * 60
    assert body["working_minutes"] == 9 * 60
    assert body["overtime_minutes"] == 1 * 60


def test_attendance_monthly_summary_aggregates(headers):
    designation = _create_designation(headers, default_lunch_break_minutes=60, standard_working_hours=8.0, leave_entitlement_per_month=4)
    user_id = _register_and_login(headers)
    client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user_id, "designation_id": designation["id"]})

    year, month = 2026, 6
    for day, (ci, co) in enumerate([("09:00:00", "18:00:00"), ("09:00:00", "20:00:00")], start=10):
        client.post("/api/v1/staff/attendance", headers=headers, json={
            "user_id": user_id, "date": f"{year}-{month:02d}-{day:02d}",
            "check_in_time": ci, "check_out_time": co, "status": "present",
        })

    for from_day, to_day in [(15, 17)]:  # 3 approved leave days inside the month
        lr = client.post("/api/v1/staff/leave", headers=headers, json={
            "user_id": user_id, "from_date": f"{year}-{month:02d}-{from_day:02d}", "to_date": f"{year}-{month:02d}-{to_day:02d}",
        }).json()
        client.patch(f"/api/v1/staff/leave/{lr['id']}", headers=headers, json={"status": "approved"})

    resp = client.get(f"/api/v1/staff/attendance/monthly-summary?user_id={user_id}&year={year}&month={month}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["days_recorded"] == 2
    assert body["total_working_minutes"] == 8 * 60 + 10 * 60
    assert body["total_overtime_minutes"] == 2 * 60
    assert body["approved_leave_days_in_month"] == 3
    assert body["free_leave_days"] == 3
    assert body["excess_leave_days"] == 0


def test_staff_cannot_view_other_users_monthly_summary(headers, staff_headers):
    designation = _create_designation(headers)
    other_user_id = _register_and_login(headers)
    client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": other_user_id, "designation_id": designation["id"]})
    resp = client.get(f"/api/v1/staff/attendance/monthly-summary?user_id={other_user_id}&year=2026&month=6", headers=staff_headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 6. Regression: existing payroll flow unchanged
# ---------------------------------------------------------------------------

def test_generate_payroll_still_accepts_manual_values_unchanged(headers):
    user_id = _register_and_login(headers)
    resp = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": user_id, "period_year": 2026, "period_month": 6,
        "basic_salary": 16000, "deductions": 500, "bonus": 200,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["net_pay"] == 16000 - 500 + 200
    assert body["status"] == "draft"

    resp = client.patch(f"/api/v1/staff/payroll/{body['id']}/mark-paid", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"
    assert resp.json()["linked_expense_id"] is not None
