"""
Tests completing the Staff module to full production coverage: Finance
integration (paid payroll -> real Expense), notification triggers (leave
reviewed, payroll paid), and Dashboard staff present/absent counts.
Run with: pytest tests/test_staff_completion.py -v
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
    email = f"staffcomp{uuid.uuid4().hex[:8]}@test.com"
    resp = client.post("/api/v1/auth/register", headers=headers, json={
        "name": "Staff Completion Test", "email": email, "password": "TestPass123!", "role": "staff",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_marking_payroll_paid_creates_real_expense(headers):
    staff = _make_staff_user(headers)
    year, month = 2046, 3
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": year, "period_month": month,
        "basic_salary": 20000, "deductions": 1000, "bonus": 500,
    }).json()
    assert payroll["net_pay"] == 19500.0

    resp = client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["linked_expense_id"] is not None

    today = date.today().isoformat()
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": today, "date_to": today}).json()
    matching = [e for e in expenses if e["id"] == resp.json()["linked_expense_id"]]
    assert len(matching) == 1
    assert matching[0]["category"] == "salary"
    assert matching[0]["amount"] == 19500.0


def test_payroll_expense_is_central_cost_not_tied_to_any_unit(headers):
    staff = _make_staff_user(headers)
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": 2047, "period_month": 5,
        "basic_salary": 15000, "deductions": 0, "bonus": 0,
    }).json()
    client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)

    today = date.today().isoformat()
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": today, "date_to": today}).json()
    salary_expense = next(e for e in expenses if e["category"] == "salary" and e["amount"] == 15000.0)
    assert salary_expense["unit_id"] is None


def test_expense_creation_from_payroll_logs_audit(headers):
    staff = _make_staff_user(headers)
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": 2048, "period_month": 7,
        "basic_salary": 10000, "deductions": 0, "bonus": 0,
    }).json()
    resp = client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)
    expense_id = resp.json()["linked_expense_id"]

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.expense.create"}).json()
    assert any(l["entity_id"] == expense_id for l in logs)


def test_payroll_paid_attempts_notification(headers):
    staff = _make_staff_user(headers)
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": 2049, "period_month": 8,
        "basic_salary": 12000, "deductions": 0, "bonus": 0,
    }).json()
    client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "payroll_paid"}).json()
    assert any(l["payroll_id"] == payroll["id"] for l in logs)


def test_leave_review_attempts_notification(headers):
    staff = _make_staff_user(headers)
    leave = client.post("/api/v1/staff/leave", headers=headers, json={
        "user_id": staff["id"],
        "from_date": (date.today() + timedelta(days=9000)).isoformat(),
        "to_date": (date.today() + timedelta(days=9002)).isoformat(),
        "reason": "Test leave",
    }).json()
    resp = client.patch(f"/api/v1/staff/leave/{leave['id']}", headers=headers, json={"status": "approved"})
    assert resp.status_code == 200

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "leave_reviewed"}).json()
    assert any(l["leave_request_id"] == leave["id"] for l in logs)


def test_dashboard_reflects_staff_attendance(headers):
    staff = _make_staff_user(headers)
    today = date.today().isoformat()

    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    resp = client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": staff["id"], "date": today, "status": "present"})
    assert resp.status_code == 201, resp.text

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["staff_present_today"] >= before["staff_present_today"] + 1


def test_dashboard_reflects_staff_absence(headers):
    staff = _make_staff_user(headers)
    today = date.today().isoformat()

    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": staff["id"], "date": today, "status": "absent"})

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["staff_absent_today"] >= before["staff_absent_today"] + 1


def test_full_payroll_lifecycle_still_works(headers):
    staff = _make_staff_user(headers)
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": 2051, "period_month": 9,
        "basic_salary": 30000, "deductions": 2000, "bonus": 3000,
    }).json()
    assert payroll["status"] == "draft"
    assert payroll["net_pay"] == 31000.0

    resp = client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)
    assert resp.json()["status"] == "paid"
    assert resp.json()["paid_at"] is not None

    dup = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": 2051, "period_month": 9,
        "basic_salary": 30000, "deductions": 0, "bonus": 0,
    })
    assert dup.status_code == 409
