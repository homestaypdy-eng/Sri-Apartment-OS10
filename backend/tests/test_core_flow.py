"""
Integration tests against a real Postgres instance (uses the same DB the
app runs against — set DATABASE_URL env var, or defaults to local dev DB).
Run with: pytest tests/ -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.auth.models import Role, User
from app.modules.properties.models import Property

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(scope="module")
def admin_token():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def property_id():
    db = SessionLocal()
    prop = db.scalar(select(Property).limit(1))
    if prop is None:
        prop = Property(name="Test Property", address="Test", state_code="33")
        db.add(prop)
        db.commit()
        db.refresh(prop)
    pid = prop.id
    db.close()
    return pid


def test_login_rejects_bad_password():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
    assert resp.status_code == 401


def test_no_token_is_unauthorized():
    resp = client.get("/api/v1/bookings")
    assert resp.status_code == 401


def test_staff_cannot_create_unit(admin_token, property_id):
    unique = uuid.uuid4().hex[:6]
    email = f"staff_{unique}@sriapartmenthomestay.com"
    reg = client.post(
        "/api/v1/auth/register",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Staff Test", "email": email, "password": "StaffPass123!", "role": "staff"},
    )
    assert reg.status_code == 201, reg.text

    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StaffPass123!"})
    staff_token = login.json()["access_token"]

    resp = client.post(
        "/api/v1/units",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"property_id": str(property_id), "name": "Should Fail", "base_rate": 1000},
    )
    assert resp.status_code == 403


def test_overlap_safe_booking(admin_token, property_id):
    headers = {"Authorization": f"Bearer {admin_token}"}

    unit_resp = client.post(
        "/api/v1/units",
        headers=headers,
        json={"property_id": str(property_id), "name": f"Test Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000},
    )
    assert unit_resp.status_code == 201, unit_resp.text
    unit_id = unit_resp.json()["id"]

    guest_resp = client.post(
        "/api/v1/guests",
        headers=headers,
        json={"name": "Test Guest", "phone": "9999999999"},
    )
    guest_id = guest_resp.json()["id"]

    # First booking succeeds
    b1 = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": "2027-01-10", "check_out": "2027-01-15"},
    )
    assert b1.status_code == 201, b1.text

    # Overlapping booking is rejected by the DB EXCLUDE constraint
    b2 = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": "2027-01-12", "check_out": "2027-01-18"},
    )
    assert b2.status_code == 409, b2.text

    # Adjacent (non-overlapping) booking succeeds — checkout day is exclusive
    b3 = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": "2027-01-15", "check_out": "2027-01-20"},
    )
    assert b3.status_code == 201, b3.text

    # Availability check reflects reality
    avail = client.get(
        f"/api/v1/units/{unit_id}/availability",
        headers=headers,
        params={"check_in": "2027-01-11", "check_out": "2027-01-13"},
    )
    assert avail.json()["available"] is False


def test_checkout_must_be_after_checkin(admin_token, property_id):
    headers = {"Authorization": f"Bearer {admin_token}"}
    unit_resp = client.post(
        "/api/v1/units",
        headers=headers,
        json={"property_id": str(property_id), "name": f"Bad Date Unit {uuid.uuid4().hex[:6]}", "base_rate": 1500},
    )
    unit_id = unit_resp.json()["id"]
    guest_resp = client.post("/api/v1/guests", headers=headers, json={"name": "G", "phone": "1"})
    guest_id = guest_resp.json()["id"]

    resp = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": "2027-02-10", "check_out": "2027-02-05"},
    )
    assert resp.status_code == 422
