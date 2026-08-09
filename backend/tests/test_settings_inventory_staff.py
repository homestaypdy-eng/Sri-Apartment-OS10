"""
Tests for Settings, Inventory Management, and Staff Management.
Run with: pytest tests/test_settings_inventory_staff.py -v
"""
import uuid

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


# --- Settings ---

def test_get_settings_returns_valid_structure(headers):
    """Settings is a genuine singleton, so its values can be changed by
    prior runs/manual testing — verify structure and known-stable fields
    rather than asserting exact defaults for editable ones."""
    resp = client.get("/api/v1/settings", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["company_name"], str) and len(data["company_name"]) > 0
    assert data["bank_account_number"] == "925020053638481"  # never touched by any test or manual run so far
    assert data["state_code"] == "34"


def test_update_settings_persists(headers):
    resp = client.put("/api/v1/settings", headers=headers, json={"contact_phone": "+91 99999 00000"})
    assert resp.status_code == 200
    assert resp.json()["contact_phone"] == "+91 99999 00000"

    reread = client.get("/api/v1/settings", headers=headers)
    assert reread.json()["contact_phone"] == "+91 99999 00000"


def test_staff_cannot_update_settings():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"settings_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "S", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.put("/api/v1/settings", headers=staff_headers, json={"contact_phone": "hacked"})
    assert resp.status_code == 403


# --- Inventory ---

def test_create_item_and_stock_lifecycle(headers):
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"Towel {uuid.uuid4().hex[:6]}", "category": "towels", "minimum_stock": 5}).json()
    assert item["current_stock"] == 0
    assert item["is_low_stock"] is True  # 0 <= 5

    stock_in = client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 20, "unit_cost": 150})
    assert stock_in.status_code == 201
    assert stock_in.json()["movement_type"] == "stock_in"

    stock_out = client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 8, "reason": "housekeeping"})
    assert stock_out.status_code == 201

    updated = client.get("/api/v1/inventory/items", headers=headers).json()
    updated_item = next(i for i in updated if i["id"] == item["id"])
    assert updated_item["current_stock"] == 12  # 20 - 8
    assert updated_item["is_low_stock"] is False  # 12 > 5


def test_stock_out_cannot_exceed_current_stock(headers):
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"Pillow {uuid.uuid4().hex[:6]}", "category": "pillow_covers"}).json()
    resp = client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 5})
    assert resp.status_code == 400


def test_invalid_category_rejected(headers):
    resp = client.post("/api/v1/inventory/items", headers=headers, json={"name": "Bad Item", "category": "spaceship_parts"})
    assert resp.status_code == 400


def test_low_stock_filter(headers):
    low_item = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"LowStock {uuid.uuid4().hex[:6]}", "category": "toiletries", "minimum_stock": 100}).json()
    resp = client.get("/api/v1/inventory/items", headers=headers, params={"low_stock_only": True})
    assert any(i["id"] == low_item["id"] for i in resp.json())


def test_supplier_crud(headers):
    supplier = client.post("/api/v1/inventory/suppliers", headers=headers, json={"name": f"Supplier {uuid.uuid4().hex[:6]}", "phone": "9111111111"}).json()
    listing = client.get("/api/v1/inventory/suppliers", headers=headers).json()
    assert any(s["id"] == supplier["id"] for s in listing)


def test_inventory_report_reflects_stock(headers):
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"ReportItem {uuid.uuid4().hex[:6]}", "category": "kitchen_items"}).json()
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 5, "unit_cost": 100})

    report = client.get("/api/v1/inventory/reports/summary", headers=headers).json()
    assert report["total_items"] >= 1
    assert report["total_stock_value"] > 0


# --- Staff management ---

def _make_staff_user(headers):
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"staffmgmt_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    resp = client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "Staff Mgmt Test", "email": email, "password": "Pass12345!", "role": "staff"})
    return resp.json()["id"]


def test_attendance_unique_per_day(headers):
    user_id = _make_staff_user(headers)
    r1 = client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": user_id, "date": "2030-01-01", "status": "present"})
    assert r1.status_code == 201
    r2 = client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": user_id, "date": "2030-01-01", "status": "absent"})
    assert r2.status_code == 409


def test_leave_lifecycle(headers):
    user_id = _make_staff_user(headers)
    leave = client.post("/api/v1/staff/leave", headers=headers, json={"user_id": user_id, "from_date": "2030-02-01", "to_date": "2030-02-03", "reason": "test"}).json()
    assert leave["status"] == "pending"

    approved = client.patch(f"/api/v1/staff/leave/{leave['id']}", headers=headers, json={"status": "approved"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["reviewed_by"] is not None


def test_leave_invalid_date_range_rejected(headers):
    user_id = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/leave", headers=headers, json={"user_id": user_id, "from_date": "2030-03-05", "to_date": "2030-03-01"})
    assert resp.status_code == 400


def test_payroll_net_pay_calculated_correctly(headers):
    user_id = _make_staff_user(headers)
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={"user_id": user_id, "period_year": 2030, "period_month": 3, "basic_salary": 25000, "deductions": 1500, "bonus": 500}).json()
    assert payroll["net_pay"] == 24000.0  # 25000 - 1500 + 500
    assert payroll["status"] == "draft"


def test_payroll_unique_per_period(headers):
    user_id = _make_staff_user(headers)
    p1 = client.post("/api/v1/staff/payroll", headers=headers, json={"user_id": user_id, "period_year": 2030, "period_month": 4, "basic_salary": 20000})
    assert p1.status_code == 201
    p2 = client.post("/api/v1/staff/payroll", headers=headers, json={"user_id": user_id, "period_year": 2030, "period_month": 4, "basic_salary": 20000})
    assert p2.status_code == 409


def test_payroll_mark_paid(headers):
    user_id = _make_staff_user(headers)
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={"user_id": user_id, "period_year": 2030, "period_month": 5, "basic_salary": 20000}).json()
    paid = client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"
    assert paid.json()["paid_at"] is not None


def test_shift_and_holiday_creation(headers):
    user_id = _make_staff_user(headers)
    shift = client.post("/api/v1/staff/shifts", headers=headers, json={"user_id": user_id, "date": "2030-06-01", "shift_start": "09:00:00", "shift_end": "17:00:00"})
    assert shift.status_code == 201

    from datetime import date, timedelta
    unique_date = (date(2031, 1, 1) + timedelta(days=uuid.uuid4().int % 3650)).isoformat()
    holiday = client.post("/api/v1/staff/holidays", headers=headers, json={"date": unique_date, "name": "Test Holiday"})
    assert holiday.status_code == 201


def test_unauthenticated_cannot_access_new_modules():
    assert client.get("/api/v1/settings").status_code == 401
    assert client.get("/api/v1/inventory/items").status_code == 401
    assert client.get("/api/v1/staff/attendance").status_code == 401
