"""
Tests for the Final Validation round: staff apartment assignment,
staff performance notes, housekeeping/maintenance photo attachments,
dashboard apartment-wise profit + monthly trend, and apartment P&L
advance/outstanding fields.
Run with: pytest tests/test_final_validation.py -v
"""
import io
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
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"FVProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"FVUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def _make_staff_user(headers):
    email = f"fvstaff_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    resp = client.post("/api/v1/auth/register", headers=headers, json={"name": "FV Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    return resp.json()["id"]


def test_staff_apartment_assignment_create_list_delete(headers):
    unit_id = _make_unit(headers)
    user_id = _make_staff_user(headers)

    resp = client.post("/api/v1/staff/apartment-assignments", headers=headers, json={"user_id": user_id, "unit_id": unit_id})
    assert resp.status_code == 201, resp.text
    assignment_id = resp.json()["id"]

    listing = client.get("/api/v1/staff/apartment-assignments", headers=headers, params={"unit_id": unit_id}).json()
    assert any(a["id"] == assignment_id for a in listing)

    dup = client.post("/api/v1/staff/apartment-assignments", headers=headers, json={"user_id": user_id, "unit_id": unit_id})
    assert dup.status_code == 409

    delete_resp = client.delete(f"/api/v1/staff/apartment-assignments/{assignment_id}", headers=headers)
    assert delete_resp.status_code == 204

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "staff.apartment_assignment.create"}).json()
    assert any(l["entity_id"] == assignment_id for l in logs)


def test_staff_performance_note_create_and_list(headers):
    user_id = _make_staff_user(headers)
    resp = client.post("/api/v1/staff/performance-notes", headers=headers, json={"user_id": user_id, "note": "Handled a difficult guest complaint very well."})
    assert resp.status_code == 201, resp.text

    listing = client.get("/api/v1/staff/performance-notes", headers=headers, params={"user_id": user_id}).json()
    assert any("difficult guest" in n["note"] for n in listing)


def test_housekeeping_photo_upload_list_download_delete(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "inspection"}).json()

    fake_file = io.BytesIO(b"fake inspection photo bytes")
    upload = client.post(
        f"/api/v1/housekeeping-tasks/{task['id']}/photos",
        headers=headers,
        params={"caption": "Stain found on carpet"},
        files={"file": ("stain.jpg", fake_file, "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    photo = upload.json()
    assert photo["caption"] == "Stain found on carpet"

    listing = client.get(f"/api/v1/housekeeping-tasks/{task['id']}/photos", headers=headers).json()
    assert any(p["id"] == photo["id"] for p in listing)

    download = client.get(f"/api/v1/housekeeping-tasks/{task['id']}/photos/{photo['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"fake inspection photo bytes"

    delete_resp = client.delete(f"/api/v1/housekeeping-tasks/{task['id']}/photos/{photo['id']}", headers=headers)
    assert delete_resp.status_code == 204


def test_maintenance_before_after_photos(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Leaking tap", "priority": "high"}).json()

    before_file = io.BytesIO(b"before photo bytes")
    before_upload = client.post(
        f"/api/v1/maintenance-tickets/{ticket['id']}/photos",
        headers=headers,
        params={"photo_type": "before"},
        files={"file": ("before.jpg", before_file, "image/jpeg")},
    )
    assert before_upload.status_code == 201, before_upload.text

    after_file = io.BytesIO(b"after photo bytes")
    after_upload = client.post(
        f"/api/v1/maintenance-tickets/{ticket['id']}/photos",
        headers=headers,
        params={"photo_type": "after"},
        files={"file": ("after.jpg", after_file, "image/jpeg")},
    )
    assert after_upload.status_code == 201

    listing = client.get(f"/api/v1/maintenance-tickets/{ticket['id']}/photos", headers=headers).json()
    types = {p["photo_type"] for p in listing}
    assert types == {"before", "after"}


def test_maintenance_photo_invalid_type_rejected(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Test"}).json()
    fake_file = io.BytesIO(b"bytes")
    resp = client.post(
        f"/api/v1/maintenance-tickets/{ticket['id']}/photos",
        headers=headers,
        params={"photo_type": "during"},
        files={"file": ("x.jpg", fake_file, "image/jpeg")},
    )
    assert resp.status_code == 400


def test_dashboard_apartment_performance_includes_profit(headers):
    unit_id = _make_unit(headers, rate=2000)
    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    line = next((a for a in dashboard["apartment_performance"] if a["unit_id"] == unit_id), None)
    assert line is not None
    assert "month_expenses" in line
    assert "month_profit" in line
    assert line["month_profit"] == line["month_revenue"] - line["month_expenses"]


def test_dashboard_monthly_trend_has_six_points(headers):
    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert len(dashboard["monthly_trend"]) == 6
    for point in dashboard["monthly_trend"]:
        assert point["profit"] == round(point["revenue"] - point["expenses"], 2)


def test_apartment_pnl_includes_advance_and_outstanding(headers):
    unit_id = _make_unit(headers, rate=2000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "PnL Advance Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2033, 6
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-04",
    }).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 2500, "mode": "upi", "reference_no": "PNLADVTEST"})

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert pnl["revenue"] == 6000.0
    assert pnl["advance_received"] == 2500.0
    assert pnl["outstanding_balance"] == 3500.0


def test_custom_expense_category_end_to_end(headers):
    resp = client.post("/api/v1/finance/expenses", headers=headers, json={"category": "Pool Maintenance", "amount": 1200, "expense_date": "2033-01-01"})
    assert resp.status_code == 201
    assert resp.json()["category"] == "Pool Maintenance"


def test_audit_log_has_structured_previous_and_new_value(headers):
    unit_id = _make_unit(headers, rate=2000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Audit Struct Guest", "phone": f"90{uuid.uuid4().int % 10**8:08d}"}).json()
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (date.today() + timedelta(days=850)).isoformat(), "check_out": (date.today() + timedelta(days=852)).isoformat(),
    }).json()
    client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"rate_applied": 4321, "reason": "structured field test", "expected_updated_at": booking["updated_at"]})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "booking.edit"}).json()
    match = next((l for l in logs if l["entity_id"] == booking["id"]), None)
    assert match is not None
    assert match["previous_value"] is not None
    assert match["new_value"] is not None
    assert "4321" in match["new_value"]


def test_housekeeping_timeline_and_audit_logging(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"status": "in_progress"})

    timeline = client.get(f"/api/v1/housekeeping-tasks/{task['id']}/timeline", headers=headers).json()
    actions = [e["action"] for e in timeline["events"]]
    assert actions == ["housekeeping.create", "housekeeping.update"]
    assert "pending -> in_progress" in timeline["events"][1]["details"]


def test_maintenance_timeline_and_audit_logging(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Broken fan"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"cost": 500, "status": "resolved"})

    timeline = client.get(f"/api/v1/maintenance-tickets/{ticket['id']}/timeline", headers=headers).json()
    actions = [e["action"] for e in timeline["events"]]
    assert actions == ["maintenance.create", "maintenance.update"]
    assert "cost:" in timeline["events"][1]["details"]
    assert "500" in timeline["events"][1]["details"]


def test_housekeeping_photo_rejects_non_image_content_type(headers):
    """
    Security fix found during the Phase 6 review: photo upload endpoints
    previously accepted ANY file type despite being presented in the UI
    as image uploads. Now rejects non-image content types with 415.
    """
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    fake_file = io.BytesIO(b"<script>alert(1)</script>")
    resp = client.post(
        f"/api/v1/housekeeping-tasks/{task['id']}/photos",
        headers=headers,
        files={"file": ("evil.html", fake_file, "text/html")},
    )
    assert resp.status_code == 415


def test_maintenance_photo_rejects_non_image_content_type(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Test"}).json()
    fake_file = io.BytesIO(b"not an image")
    resp = client.post(
        f"/api/v1/maintenance-tickets/{ticket['id']}/photos",
        headers=headers,
        params={"photo_type": "before"},
        files={"file": ("malware.exe", fake_file, "application/x-msdownload")},
    )
    assert resp.status_code == 415
