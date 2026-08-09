"""
Tests completing the Units module to full production coverage: unit
photos (the one genuine gap found), plus verification that the
Maintenance/Housekeeping links, Availability check, and Occupancy
reporting that already existed actually work as claimed rather than
assumed.
Run with: pytest tests/test_units_completion.py -v
"""
import io
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


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"UnitCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"UnitCompUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def test_unit_photo_upload_list_download_delete(headers):
    unit_id = _make_unit(headers)
    fake_file = io.BytesIO(b"fake unit exterior photo")
    upload = client.post(
        f"/api/v1/units/{unit_id}/photos", headers=headers,
        params={"caption": "Exterior view"},
        files={"file": ("exterior.jpg", fake_file, "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    photo = upload.json()
    assert photo["caption"] == "Exterior view"

    listing = client.get(f"/api/v1/units/{unit_id}/photos", headers=headers).json()
    assert any(p["id"] == photo["id"] for p in listing)

    download = client.get(f"/api/v1/units/{unit_id}/photos/{photo['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"fake unit exterior photo"

    delete_resp = client.delete(f"/api/v1/units/{unit_id}/photos/{photo['id']}", headers=headers)
    assert delete_resp.status_code == 204


def test_unit_photo_rejects_non_image_content_type(headers):
    unit_id = _make_unit(headers)
    fake_file = io.BytesIO(b"<script>alert(1)</script>")
    resp = client.post(
        f"/api/v1/units/{unit_id}/photos", headers=headers,
        files={"file": ("evil.html", fake_file, "text/html")},
    )
    assert resp.status_code == 415


def test_unit_photo_upload_logs_audit(headers):
    unit_id = _make_unit(headers)
    fake_file = io.BytesIO(b"test content")
    client.post(f"/api/v1/units/{unit_id}/photos", headers=headers, files={"file": ("test.jpg", fake_file, "image/jpeg")})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "unit.photo_upload"}).json()
    assert any(l["entity_id"] == unit_id for l in logs)


def test_unit_photo_404_for_unknown_unit(headers):
    fake_file = io.BytesIO(b"test")
    resp = client.post(f"/api/v1/units/{uuid.uuid4()}/photos", headers=headers, files={"file": ("test.jpg", fake_file, "image/jpeg")})
    assert resp.status_code == 404


def test_maintenance_tickets_filterable_by_unit(headers):
    unit_id = _make_unit(headers)
    other_unit_id = _make_unit(headers)
    client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Unit-scoped test ticket"})
    client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": other_unit_id, "description": "Different unit ticket"})

    tickets = client.get("/api/v1/maintenance-tickets", headers=headers, params={"unit_id": unit_id}).json()
    assert all(t["unit_id"] == unit_id for t in tickets)
    assert any(t["description"] == "Unit-scoped test ticket" for t in tickets)
    assert not any(t["description"] == "Different unit ticket" for t in tickets)


def test_housekeeping_tasks_filterable_by_unit(headers):
    unit_id = _make_unit(headers)
    other_unit_id = _make_unit(headers)
    client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"})
    client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": other_unit_id, "task_type": "routine"})

    tasks = client.get("/api/v1/housekeeping-tasks", headers=headers, params={"unit_id": unit_id}).json()
    assert all(t["unit_id"] == unit_id for t in tasks)


def test_availability_check_detects_real_conflict(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Availability Test Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=9500)
    check_out = check_in + timedelta(days=3)
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat()})

    resp = client.get(f"/api/v1/units/{unit_id}/availability", headers=headers, params={"check_in": check_in.isoformat(), "check_out": check_out.isoformat()})
    assert resp.status_code == 200
    assert resp.json()["available"] is False

    free_check_in = check_out + timedelta(days=5)
    free_check_out = free_check_in + timedelta(days=2)
    resp2 = client.get(f"/api/v1/units/{unit_id}/availability", headers=headers, params={"check_in": free_check_in.isoformat(), "check_out": free_check_out.isoformat()})
    assert resp2.json()["available"] is True


def test_unit_performance_report_includes_occupancy(headers):
    unit_id = _make_unit(headers, rate=1000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Occupancy Test Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2053, 6
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-04"})

    date_from = f"{year}-{month:02d}-01"
    date_to = f"{year}-{month:02d}-30"
    performance = client.get("/api/v1/reports/unit-performance", headers=headers, params={"date_from": date_from, "date_to": date_to}).json()
    match = next((u for u in performance if u["unit_id"] == unit_id), None)
    assert match is not None
    assert match["room_nights_sold"] == 3
    assert match["revenue"] == 3000.0
    assert match["occupancy_rate_percent"] > 0


def test_existing_unit_update_still_works(headers):
    unit_id = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"floor": "3", "amenities": "AC, WiFi, TV"})
    assert resp.status_code == 200
    assert resp.json()["floor"] == "3"
    assert resp.json()["amenities"] == "AC, WiFi, TV"
