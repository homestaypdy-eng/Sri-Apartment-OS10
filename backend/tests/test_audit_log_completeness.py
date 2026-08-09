"""
Regression tests for the CTO production audit's remaining verified gap:
13 write endpoints across Staff and Properties with zero audit trail
(the 14th, inquiry.convert_to_booking, was already fixed and tested in
test_inquiry_conversion.py).
Run with: pytest tests/test_audit_log_completeness.py -v
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


def _admin_user_id(headers):
    return client.get("/api/v1/users", headers=headers).json()[0]["id"]


def _assert_logged(headers, action, entity_id):
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": action}).json()
    assert any(l["entity_id"] == entity_id for l in logs), f"No audit log found for action='{action}' entity_id='{entity_id}'"


def test_mark_attendance_logs_audit(headers):
    user_id = _admin_user_id(headers)
    unique_date = (date.today() + timedelta(days=8000 + uuid.uuid4().int % 1000)).isoformat()
    resp = client.post("/api/v1/staff/attendance", headers=headers, json={"user_id": user_id, "date": unique_date, "status": "present"})
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "staff.attendance.mark", resp.json()["id"])


def test_apply_leave_logs_audit(headers):
    user_id = _admin_user_id(headers)
    start = date.today() + timedelta(days=8100)
    resp = client.post("/api/v1/staff/leave", headers=headers, json={
        "user_id": user_id, "from_date": start.isoformat(), "to_date": (start + timedelta(days=1)).isoformat(), "reason": "Test leave",
    })
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "staff.leave.apply", resp.json()["id"])


def test_review_leave_logs_audit(headers):
    user_id = _admin_user_id(headers)
    start = date.today() + timedelta(days=8200)
    leave = client.post("/api/v1/staff/leave", headers=headers, json={
        "user_id": user_id, "from_date": start.isoformat(), "to_date": (start + timedelta(days=1)).isoformat(), "reason": "Review test",
    }).json()
    resp = client.patch(f"/api/v1/staff/leave/{leave['id']}", headers=headers, json={"status": "approved"})
    assert resp.status_code == 200, resp.text
    _assert_logged(headers, "staff.leave.review", leave["id"])


def test_generate_payroll_logs_audit(headers):
    user_id = _admin_user_id(headers)
    year = 2200 + uuid.uuid4().int % 900
    resp = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": user_id, "period_year": year, "period_month": 6, "basic_salary": 20000, "deductions": 0, "bonus": 0,
    })
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "staff.payroll.generate", resp.json()["id"])


def test_mark_payroll_paid_logs_audit(headers):
    user_id = _admin_user_id(headers)
    year = 2300 + uuid.uuid4().int % 900
    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": user_id, "period_year": year, "period_month": 7, "basic_salary": 15000, "deductions": 0, "bonus": 0,
    }).json()
    resp = client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)
    assert resp.status_code == 200, resp.text
    _assert_logged(headers, "staff.payroll.mark_paid", payroll["id"])


def test_create_shift_logs_audit(headers):
    user_id = _admin_user_id(headers)
    unique_date = (date.today() + timedelta(days=8400 + uuid.uuid4().int % 1000)).isoformat()
    resp = client.post("/api/v1/staff/shifts", headers=headers, json={"user_id": user_id, "date": unique_date, "shift_start": "09:00", "shift_end": "17:00"})
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "staff.shift.create", resp.json()["id"])


def test_create_holiday_logs_audit(headers):
    unique_date = (date.today() + timedelta(days=8500 + uuid.uuid4().int % 1000)).isoformat()
    resp = client.post("/api/v1/staff/holidays", headers=headers, json={"date": unique_date, "name": "Test Holiday"})
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "staff.holiday.create", resp.json()["id"])


def test_create_property_logs_audit(headers):
    resp = client.post("/api/v1/properties", headers=headers, json={"name": f"Audit Test Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"})
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "property.create", resp.json()["id"])


def test_create_unit_logs_audit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Unit Audit Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    resp = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Audit Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000})
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "unit.create", resp.json()["id"])


def test_update_unit_logs_audit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Update Audit Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Update Audit Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    resp = client.patch(f"/api/v1/units/{unit['id']}", headers=headers, json={"base_rate": 2500})
    assert resp.status_code == 200, resp.text
    _assert_logged(headers, "unit.update", unit["id"])


def test_create_seasonal_rate_logs_audit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Season Audit Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Season Audit Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    resp = client.post(f"/api/v1/units/{unit['id']}/seasonal-rates", headers=headers, json={
        "name": "Test Season", "start_date": "2033-12-01", "end_date": "2033-12-31", "rate_per_night": 3000,
    })
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "seasonal_rate.create", resp.json()["id"])


def test_delete_seasonal_rate_logs_audit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Del Season Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Del Season Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    rate = client.post(f"/api/v1/units/{unit['id']}/seasonal-rates", headers=headers, json={
        "name": "Delete Test Season", "start_date": "2034-01-01", "end_date": "2034-01-31", "rate_per_night": 3500,
    }).json()
    resp = client.delete(f"/api/v1/units/{unit['id']}/seasonal-rates/{rate['id']}", headers=headers)
    assert resp.status_code == 204, resp.text
    _assert_logged(headers, "seasonal_rate.delete", rate["id"])


def test_create_discount_tier_logs_audit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Discount Audit Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Discount Audit Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    resp = client.post(f"/api/v1/units/{unit['id']}/long-stay-discounts", headers=headers, json={"min_nights": 7, "discount_percent": 10})
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "discount_tier.create", resp.json()["id"])


def test_delete_discount_tier_logs_audit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Del Discount Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Del Discount Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    tier = client.post(f"/api/v1/units/{unit['id']}/long-stay-discounts", headers=headers, json={"min_nights": 14, "discount_percent": 15}).json()
    resp = client.delete(f"/api/v1/units/{unit['id']}/long-stay-discounts/{tier['id']}", headers=headers)
    assert resp.status_code == 204, resp.text
    _assert_logged(headers, "discount_tier.delete", tier["id"])


def test_generate_invoice_logs_audit(headers):
    """A genuinely financial action (invoice generation) had zero
    logging at all — found in the final CTO audit re-sweep."""
    import uuid as _uuid
    from datetime import date as _date, timedelta as _timedelta
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Invoice Audit Prop {_uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Invoice Audit Unit {_uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Invoice Audit Guest", "phone": f"96{_uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = _date.today() + _timedelta(days=9000 + _uuid.uuid4().int % 1000)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + _timedelta(days=2)).isoformat(),
    }).json()

    resp = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)
    assert resp.status_code == 201, resp.text
    _assert_logged(headers, "invoice.generate", resp.json()["id"])
