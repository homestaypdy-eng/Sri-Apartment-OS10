"""
Tests for the Staff Profile / Staff Advance architecture: a 1:1 StaffProfile
attached to an existing User (not a replacement identity — Attendance,
LeaveRequest, Payroll, Shift, and StaffPerformanceNote all keep using
user_id exactly as before), plus a new StaffAdvance ledger with partial-
deduction/outstanding-balance tracking, since no existing advance
architecture was found to reuse.
Run with: pytest tests/test_staff_profile.py -v
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


def _make_staff_user(headers):
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"spstaff_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    resp = client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "SP Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    assert resp.status_code == 201, resp.text
    user = resp.json()
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return user, staff_headers


def _make_unit(headers, is_active=True):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"SPProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"SPUnit {uuid.uuid4().hex[:6]}", "base_rate": 1500}).json()
    if not is_active:
        client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)
    return unit


# --- StaffProfile: create/get/list/update ---

def test_create_staff_profile(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/profiles", headers=headers, json={
        "user_id": user["id"], "designation": "Housekeeping Attendant", "phone": "9876543210", "join_date": "2026-01-15",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["user_id"] == user["id"]
    assert data["designation"] == "Housekeeping Attendant"
    assert data["is_active"] is True


def test_create_staff_profile_invalid_user_rejected(headers):
    resp = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_create_staff_profile_duplicate_user_rejected(headers):
    user, _ = _make_staff_user(headers)
    first = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]})
    assert first.status_code == 201
    dup = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]})
    assert dup.status_code == 409


def test_get_staff_profile(headers):
    user, _ = _make_staff_user(headers)
    created = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"], "department": "Housekeeping"}).json()
    resp = client.get(f"/api/v1/staff/profiles/{created['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["department"] == "Housekeeping"


def test_get_staff_profile_404_unknown(headers):
    resp = client.get(f"/api/v1/staff/profiles/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


def test_update_staff_profile(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}", headers=headers, json={
        "designation": "Maintenance Technician", "weekly_off_day": "sunday", "phone": "9123456780",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["designation"] == "Maintenance Technician"
    assert resp.json()["weekly_off_day"] == "sunday"


def test_update_staff_profile_requires_at_least_one_field(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}", headers=headers, json={})
    assert resp.status_code == 400


def test_supervisor_cannot_be_self(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}", headers=headers, json={"supervisor_user_id": user["id"]})
    assert resp.status_code == 400


def test_supervisor_must_exist(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"], "supervisor_user_id": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_supervisor_valid_reference_accepted(headers):
    manager, _ = _make_staff_user(headers)
    report, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": report["id"], "supervisor_user_id": manager["id"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["supervisor_user_id"] == manager["id"]


# --- Deactivate / Reactivate ---

def test_deactivate_then_reactivate_staff_profile(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()

    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}/deactivate", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    resp2 = client.patch(f"/api/v1/staff/profiles/{profile['id']}/reactivate", headers=headers)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["is_active"] is True


def test_deactivate_twice_conflicts(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    client.patch(f"/api/v1/staff/profiles/{profile['id']}/deactivate", headers=headers)
    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}/deactivate", headers=headers)
    assert resp.status_code == 409


def test_reactivate_already_active_conflicts(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}/reactivate", headers=headers)
    assert resp.status_code == 409


def test_deactivate_does_not_touch_user_login_access(headers):
    """Employment status (StaffProfile.is_active) and login access
    (User.is_active) are independent — deactivating a profile must not
    lock the person out of the system."""
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    client.patch(f"/api/v1/staff/profiles/{profile['id']}/deactivate", headers=headers)

    users_list = client.get("/api/v1/users", headers=headers).json()
    match = next((u for u in users_list if u["id"] == user["id"]), None)
    assert match is not None
    assert match["is_active"] is True


# --- Permissions ---

def test_staff_cannot_create_staff_profile(headers):
    user, staff_headers = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/profiles", headers=staff_headers, json={"user_id": user["id"]})
    assert resp.status_code == 403


def test_staff_cannot_update_staff_profile(headers):
    user, staff_headers = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}", headers=staff_headers, json={"designation": "Manager"})
    assert resp.status_code == 403


def test_staff_cannot_deactivate_staff_profile(headers):
    user, staff_headers = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    resp = client.patch(f"/api/v1/staff/profiles/{profile['id']}/deactivate", headers=staff_headers)
    assert resp.status_code == 403


def test_staff_sees_only_own_profile_in_list(headers):
    user_a, staff_headers_a = _make_staff_user(headers)
    user_b, _ = _make_staff_user(headers)
    client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user_a["id"], "designation": "A's role"})
    client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user_b["id"], "designation": "B's role"})

    resp = client.get("/api/v1/staff/profiles", headers=staff_headers_a)
    assert resp.status_code == 200
    ids = [p["user_id"] for p in resp.json()]
    assert user_a["id"] in ids
    assert user_b["id"] not in ids


def test_staff_cannot_view_another_staff_profile_by_id(headers):
    user_a, _ = _make_staff_user(headers)
    user_b, staff_headers_b = _make_staff_user(headers)
    profile_a = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user_a["id"]}).json()

    resp = client.get(f"/api/v1/staff/profiles/{profile_a['id']}", headers=staff_headers_b)
    assert resp.status_code == 403


def test_staff_can_view_own_profile_by_id(headers):
    user, staff_headers = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    resp = client.get(f"/api/v1/staff/profiles/{profile['id']}", headers=staff_headers)
    assert resp.status_code == 200


# --- Audit logging ---

def test_staff_profile_create_logs_audit(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "staff.create"}).json()
    assert any(l["entity_id"] == profile["id"] for l in logs)


def test_staff_profile_update_logs_audit(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    client.patch(f"/api/v1/staff/profiles/{profile['id']}", headers=headers, json={"designation": "Driver"})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "staff.update"}).json()
    assert any(l["entity_id"] == profile["id"] for l in logs)


def test_staff_profile_deactivate_reactivate_log_audit(headers):
    user, _ = _make_staff_user(headers)
    profile = client.post("/api/v1/staff/profiles", headers=headers, json={"user_id": user["id"]}).json()
    client.patch(f"/api/v1/staff/profiles/{profile['id']}/deactivate", headers=headers)
    client.patch(f"/api/v1/staff/profiles/{profile['id']}/reactivate", headers=headers)

    deactivate_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "staff.deactivate"}).json()
    assert any(l["entity_id"] == profile["id"] for l in deactivate_logs)
    reactivate_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "staff.reactivate"}).json()
    assert any(l["entity_id"] == profile["id"] for l in reactivate_logs)


# --- StaffAdvance ---

def test_create_staff_advance(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/advances", headers=headers, json={
        "user_id": user["id"], "date": "2026-02-01", "amount": 5000, "category": "advance", "reason": "Medical emergency",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "pending"
    assert data["deducted_amount"] == 0
    assert data["outstanding_amount"] == 5000


def test_create_staff_advance_invalid_user_rejected(headers):
    resp = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": str(uuid.uuid4()), "date": "2026-02-01", "amount": 1000})
    assert resp.status_code == 404


def test_create_staff_advance_invalid_amount_rejected(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 0})
    assert resp.status_code == 422


def test_get_staff_advance(headers):
    user, _ = _make_staff_user(headers)
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 2000}).json()
    resp = client.get(f"/api/v1/staff/advances/{advance['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == advance["id"]


def test_list_staff_advances_filtered_by_user(headers):
    user_a, _ = _make_staff_user(headers)
    user_b, _ = _make_staff_user(headers)
    client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user_a["id"], "date": "2026-02-01", "amount": 1000})
    client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user_b["id"], "date": "2026-02-01", "amount": 2000})

    resp = client.get("/api/v1/staff/advances", headers=headers, params={"user_id": user_a["id"]})
    assert resp.status_code == 200
    assert all(a["user_id"] == user_a["id"] for a in resp.json())


def test_partial_deduction_updates_status_and_outstanding(headers):
    user, _ = _make_staff_user(headers)
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 5000}).json()

    resp = client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 2000})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "partially_deducted"
    assert data["deducted_amount"] == 2000
    assert data["outstanding_amount"] == 3000


def test_full_deduction_marks_fully_deducted(headers):
    user, _ = _make_staff_user(headers)
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 3000}).json()

    client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 1000})
    resp = client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 2000})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "fully_deducted"
    assert data["outstanding_amount"] == 0


def test_deduction_exceeding_outstanding_rejected(headers):
    user, _ = _make_staff_user(headers)
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 1000}).json()
    resp = client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 1500})
    assert resp.status_code == 400


def test_deduction_on_fully_deducted_rejected(headers):
    user, _ = _make_staff_user(headers)
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 1000}).json()
    client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 1000})
    resp = client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 100})
    assert resp.status_code == 409


def test_deduction_does_not_alter_payroll_calculation(headers):
    """Recording an advance deduction must never silently change an
    existing payroll record's own net_pay."""
    user, _ = _make_staff_user(headers)
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": user["id"], "period_year": 2054, "period_month": 3, "basic_salary": 20000, "deductions": 0, "bonus": 0,
    }).json()
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2054-03-01", "amount": 2000}).json()
    client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 2000, "payroll_id": payroll["id"]})

    reloaded = client.get("/api/v1/staff/payroll", headers=headers, params={"user_id": user["id"]}).json()
    match = next(p for p in reloaded if p["id"] == payroll["id"])
    assert match["net_pay"] == 20000.0
    assert match["basic_salary"] == 20000.0


def test_staff_cannot_create_staff_advance(headers):
    user, staff_headers = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/advances", headers=staff_headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 500})
    assert resp.status_code == 403


def test_staff_advance_create_logs_audit(headers):
    user, _ = _make_staff_user(headers)
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 1000}).json()
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "staff.advance.create"}).json()
    assert any(l["entity_id"] == advance["id"] for l in logs)


def test_staff_advance_deduction_logs_audit(headers):
    user, _ = _make_staff_user(headers)
    advance = client.post("/api/v1/staff/advances", headers=headers, json={"user_id": user["id"], "date": "2026-02-01", "amount": 1000}).json()
    client.post(f"/api/v1/staff/advances/{advance['id']}/deduct", headers=headers, json={"amount": 500})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "staff.advance.deduction"}).json()
    assert any(l["entity_id"] == advance["id"] for l in logs)


# --- Apartment assignment validation (gap fix on the existing endpoint) ---

def test_apartment_assignment_invalid_unit_rejected(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/apartment-assignments", headers=headers, json={"user_id": user["id"], "unit_id": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_apartment_assignment_invalid_user_rejected(headers):
    unit = _make_unit(headers)
    resp = client.post("/api/v1/staff/apartment-assignments", headers=headers, json={"user_id": str(uuid.uuid4()), "unit_id": unit["id"]})
    assert resp.status_code == 404


def test_apartment_assignment_inactive_unit_rejected(headers):
    user, _ = _make_staff_user(headers)
    unit = _make_unit(headers, is_active=False)
    resp = client.post("/api/v1/staff/apartment-assignments", headers=headers, json={"user_id": user["id"], "unit_id": unit["id"]})
    assert resp.status_code == 409


def test_apartment_assignment_active_unit_still_works(headers):
    """Regression: the new validation must not break the existing happy path."""
    user, _ = _make_staff_user(headers)
    unit = _make_unit(headers)
    resp = client.post("/api/v1/staff/apartment-assignments", headers=headers, json={"user_id": user["id"], "unit_id": unit["id"]})
    assert resp.status_code == 201, resp.text


# --- Regression: existing sub-modules unaffected by StaffProfile/StaffAdvance ---

def test_existing_attendance_unaffected(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": user["id"], "date": str(date.today() + timedelta(days=5000)), "status": "present"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["user_id"] == user["id"]


def test_existing_leave_unaffected(headers):
    user, _ = _make_staff_user(headers)
    from_date = date.today() + timedelta(days=5100)
    to_date = from_date + timedelta(days=2)
    resp = client.post("/api/v1/staff/leave", headers=headers, json={"user_id": user["id"], "from_date": str(from_date), "to_date": str(to_date), "reason": "Regression check"})
    assert resp.status_code == 201, resp.text


def test_existing_payroll_unaffected(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/payroll", headers=headers, json={"user_id": user["id"], "period_year": 2055, "period_month": 7, "basic_salary": 15000})
    assert resp.status_code == 201, resp.text
    assert resp.json()["net_pay"] == 15000.0


def test_existing_shift_unaffected(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/shifts", headers=headers, json={"user_id": user["id"], "date": str(date.today() + timedelta(days=5200)), "shift_start": "09:00:00", "shift_end": "17:00:00"})
    assert resp.status_code == 201, resp.text


def test_existing_performance_notes_unaffected(headers):
    user, _ = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/performance-notes", headers=headers, json={"user_id": user["id"], "note": "Regression check note"})
    assert resp.status_code == 201, resp.text


def test_staff_profile_optional_existing_endpoints_work_without_one(headers):
    """A User with no StaffProfile row at all must keep working in every
    existing sub-module — StaffProfile is purely additive/optional-attach."""
    user, _ = _make_staff_user(headers)
    att = client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": user["id"], "date": str(date.today() + timedelta(days=5300)), "status": "present"})
    assert att.status_code == 201
    profiles = client.get("/api/v1/staff/profiles", headers=headers).json()
    assert not any(p["user_id"] == user["id"] for p in profiles)
