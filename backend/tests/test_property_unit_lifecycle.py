"""
Tests for Property/Unit Edit + Deactivate/Reactivate — the CRUD gap
identified in the architecture inspection: Property edit and Unit's
backend PATCH already existed, but there was no way to deactivate/
reactivate either entity, UnitUpdate was missing name/unit_type/
max_occupancy, and unit creation didn't validate property_id.
Run with: pytest tests/test_property_unit_lifecycle.py -v
"""
import uuid

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


@pytest.fixture(scope="module")
def staff_headers():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"plstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "PL Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    return {"Authorization": f"Bearer {staff_login.json()['access_token']}"}


def _make_property(headers, **overrides):
    payload = {"name": f"LifecycleProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}
    payload.update(overrides)
    resp = client.post("/api/v1/properties", headers=headers, json=payload)
    return resp.json()


def _make_unit(headers, property_id=None, rate=2000, **overrides):
    property_id = property_id or _make_property(headers)["id"]
    payload = {"property_id": property_id, "name": f"LifecycleUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}
    payload.update(overrides)
    resp = client.post("/api/v1/units", headers=headers, json=payload)
    return resp.json()


# --- PROPERTY ---

def test_property_edit_updates_fields(headers):
    prop = _make_property(headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}", headers=headers, json={"name": "Renamed Property"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed Property"
    assert resp.json()["is_active"] is True


def test_property_created_active_by_default(headers):
    prop = _make_property(headers)
    assert prop["is_active"] is True


def test_property_deactivate_then_reactivate(headers):
    prop = _make_property(headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}/deactivate", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    resp2 = client.patch(f"/api/v1/properties/{prop['id']}/reactivate", headers=headers)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["is_active"] is True


def test_property_deactivate_twice_conflicts(headers):
    prop = _make_property(headers)
    client.patch(f"/api/v1/properties/{prop['id']}/deactivate", headers=headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}/deactivate", headers=headers)
    assert resp.status_code == 409


def test_property_reactivate_already_active_conflicts(headers):
    prop = _make_property(headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}/reactivate", headers=headers)
    assert resp.status_code == 409


def test_property_deactivate_404_for_unknown_property(headers):
    resp = client.patch(f"/api/v1/properties/{uuid.uuid4()}/deactivate", headers=headers)
    assert resp.status_code == 404


def test_property_deactivate_requires_admin(headers, staff_headers):
    prop = _make_property(headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}/deactivate", headers=staff_headers)
    assert resp.status_code == 403


def test_property_reactivate_requires_admin(headers, staff_headers):
    prop = _make_property(headers)
    client.patch(f"/api/v1/properties/{prop['id']}/deactivate", headers=headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}/reactivate", headers=staff_headers)
    assert resp.status_code == 403


def test_property_deactivate_reactivate_log_audit(headers):
    prop = _make_property(headers)
    client.patch(f"/api/v1/properties/{prop['id']}/deactivate", headers=headers)
    client.patch(f"/api/v1/properties/{prop['id']}/reactivate", headers=headers)

    deactivate_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "property.deactivate"}).json()
    assert any(l["entity_id"] == prop["id"] for l in deactivate_logs)
    reactivate_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "property.reactivate"}).json()
    assert any(l["entity_id"] == prop["id"] for l in reactivate_logs)


def test_deactivated_property_blocks_new_booking_for_its_units(headers):
    prop = _make_property(headers)
    unit = _make_unit(headers, property_id=prop["id"])
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Lifecycle Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()

    client.patch(f"/api/v1/properties/{prop['id']}/deactivate", headers=headers)

    avail = client.get(f"/api/v1/units/{unit['id']}/availability", headers=headers, params={"check_in": "2070-01-01", "check_out": "2070-01-03"})
    assert avail.json()["available"] is False

    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"], "check_in": "2070-01-01", "check_out": "2070-01-03",
    })
    assert resp.status_code == 409
    assert "deactivated" in resp.json()["detail"].lower()


# --- UNIT ---

def test_unit_created_active_by_default(headers):
    unit = _make_unit(headers)
    assert unit["is_active"] is True


def test_unit_edit_updates_name_type_occupancy(headers):
    unit = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit['id']}", headers=headers, json={
        "name": "Renamed Unit", "unit_type": "villa", "max_occupancy": 6,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed Unit"
    assert resp.json()["unit_type"] == "villa"
    assert resp.json()["max_occupancy"] == 6


def test_unit_create_invalid_property_id_rejected(headers):
    resp = client.post("/api/v1/units", headers=headers, json={
        "property_id": str(uuid.uuid4()), "name": "Orphan Unit", "base_rate": 1500,
    })
    assert resp.status_code == 404


def test_unit_duplicate_code_and_name_allowed_no_constraint_exists(headers):
    """
    Documents current behavior: unit_code/name have no uniqueness
    constraint at the model or DB level, so two units under the same (or
    different) property with identical code/name are both accepted. If a
    uniqueness rule is ever added, this test should be updated to expect
    a 409 instead.
    """
    prop = _make_property(headers)
    u1 = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": "Twin Unit", "unit_code": "DUP-1", "base_rate": 1500})
    u2 = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": "Twin Unit", "unit_code": "DUP-1", "base_rate": 1500})
    assert u1.status_code == 201
    assert u2.status_code == 201


def test_unit_deactivate_then_reactivate(headers):
    unit = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    resp2 = client.patch(f"/api/v1/units/{unit['id']}/reactivate", headers=headers)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["is_active"] is True


def test_unit_deactivate_twice_conflicts(headers):
    unit = _make_unit(headers)
    client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)
    resp = client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)
    assert resp.status_code == 409


def test_unit_deactivate_requires_admin(headers, staff_headers):
    unit = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=staff_headers)
    assert resp.status_code == 403


def test_unit_deactivate_reactivate_log_audit(headers):
    unit = _make_unit(headers)
    client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)
    client.patch(f"/api/v1/units/{unit['id']}/reactivate", headers=headers)

    deactivate_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "unit.deactivate"}).json()
    assert any(l["entity_id"] == unit["id"] for l in deactivate_logs)
    reactivate_logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "unit.reactivate"}).json()
    assert any(l["entity_id"] == unit["id"] for l in reactivate_logs)


def test_deactivated_unit_excluded_from_availability(headers):
    unit = _make_unit(headers)
    client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)

    resp = client.get(f"/api/v1/units/{unit['id']}/availability", headers=headers, params={"check_in": "2071-01-01", "check_out": "2071-01-03"})
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_deactivated_unit_rejected_for_new_booking(headers):
    unit = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Lifecycle Guest 2", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)

    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"], "check_in": "2072-01-01", "check_out": "2072-01-03",
    })
    assert resp.status_code == 409
    assert "deactivated" in resp.json()["detail"].lower()


def test_reactivated_unit_available_again_for_booking(headers):
    unit = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Lifecycle Guest 3", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    client.patch(f"/api/v1/units/{unit['id']}/deactivate", headers=headers)
    client.patch(f"/api/v1/units/{unit['id']}/reactivate", headers=headers)

    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"], "check_in": "2073-01-01", "check_out": "2073-01-03",
    })
    assert resp.status_code == 201, resp.text
