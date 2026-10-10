"""Regression coverage for atomic staff onboarding and assigned-task boundaries."""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

client = TestClient(app)


@pytest.fixture(scope="module")
def admin_headers():
    response = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _user(headers, role="staff"):
    suffix = uuid.uuid4().hex[:10]
    email = f"foundation_{suffix}@example.com"
    created = client.post("/api/v1/auth/register", headers=headers, json={"name": f"Foundation {suffix}", "email": email, "password": "Secure12345", "role": role})
    assert created.status_code == 201, created.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Secure12345"})
    return created.json(), {"Authorization": f"Bearer {login.json()['access_token']}"}


def _unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"Foundation {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    return client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"Unit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()["id"]


def _booking(headers, unit_id):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Task Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=700)
    return client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": str(check_in), "check_out": str(check_in + timedelta(days=1))}).json()


def test_admin_onboarding_creates_user_profile_and_assignments_atomically(admin_headers):
    unit_id = _unit(admin_headers)
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/api/v1/staff/onboard", headers=admin_headers, json={
        "name": "Phase One Staff", "email": f"phase-one-{suffix}@example.com", "password": "StaffPass123",
        "profile": {"employee_id": f"EMP-{suffix}", "department": "Housekeeping", "is_active": True},
        "assigned_unit_ids": [unit_id],
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["role"] == "staff"
    assert body["profile"]["user_id"] == body["user"]["id"]
    assert "password_hash" not in body["user"]
    assignments = client.get("/api/v1/staff/apartment-assignments", headers=admin_headers, params={"user_id": body["user"]["id"]})
    assert assignments.status_code == 200
    assert [row["unit_id"] for row in assignments.json()] == [unit_id]


def test_staff_onboarding_validates_account_and_duplicate_employee_id(admin_headers):
    suffix = uuid.uuid4().hex[:8]
    payload = {"name": "Duplicate Probe", "email": f"employee-{suffix}@example.com", "password": "StaffPass123", "profile": {"employee_id": f"EMP-DUP-{suffix}"}}
    assert client.post("/api/v1/staff/onboard", headers=admin_headers, json=payload).status_code == 201
    duplicate_email = client.post("/api/v1/staff/onboard", headers=admin_headers, json=payload)
    assert duplicate_email.status_code == 409
    second = {**payload, "email": f"other-{suffix}@example.com"}
    assert client.post("/api/v1/staff/onboard", headers=admin_headers, json=second).status_code == 409
    invalid_password = client.post("/api/v1/staff/onboard", headers=admin_headers, json={**payload, "email": f"invalid-{suffix}@example.com", "password": "abcdefgh"})
    assert invalid_password.status_code == 422


def test_non_admin_cannot_use_onboarding_endpoint(admin_headers):
    _, staff_headers = _user(admin_headers)
    response = client.post("/api/v1/staff/onboard", headers=staff_headers, json={"name": "No", "email": "no@example.com", "password": "Password123"})
    assert response.status_code == 403


def test_staff_task_listing_is_server_scoped_and_other_staff_cannot_mutate(admin_headers):
    staff_a, headers_a = _user(admin_headers)
    staff_b, headers_b = _user(admin_headers)
    unit_id = _unit(admin_headers)
    own = client.post("/api/v1/housekeeping-tasks", headers=admin_headers, json={"unit_id": unit_id, "assigned_to": staff_a["id"], "task_type": "routine"}).json()
    other = client.post("/api/v1/housekeeping-tasks", headers=admin_headers, json={"unit_id": unit_id, "assigned_to": staff_b["id"], "task_type": "routine"}).json()

    listing = client.get("/api/v1/staff/tasks", headers=headers_a)
    assert listing.status_code == 200
    assert {task["id"] for task in listing.json()} == {own["id"]}
    spoof = client.get("/api/v1/staff/tasks", headers=headers_a, params={"assigned_to": staff_b["id"]})
    assert spoof.status_code == 403
    dedicated = client.get("/api/v1/housekeeping-tasks", headers=headers_a)
    assert [task["id"] for task in dedicated.json()] == [own["id"]]

    denied_other = client.patch(f"/api/v1/staff/tasks/{other['id']}", headers=headers_a, params={"category": "housekeeping"}, json={"status": "in_progress"})
    assert denied_other.status_code == 404
    denied_reassign = client.patch(f"/api/v1/housekeeping-tasks/{own['id']}", headers=headers_a, json={"assigned_to": None})
    assert denied_reassign.status_code == 403
    allowed = client.patch(f"/api/v1/housekeeping-tasks/{own['id']}", headers=headers_a, json={"status": "in_progress"})
    assert allowed.status_code == 200


def test_staff_maintenance_and_guest_request_ownership_and_admin_manager_access(admin_headers):
    staff_a, headers_a = _user(admin_headers)
    staff_b, headers_b = _user(admin_headers)
    manager, manager_headers = _user(admin_headers, "manager")
    unit_id = _unit(admin_headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=admin_headers, json={"unit_id": unit_id, "description": "Assigned issue", "assigned_to": staff_a["id"]}).json()
    assert client.get("/api/v1/maintenance-tickets", headers=headers_a).json()[0]["id"] == ticket["id"]
    assert client.get("/api/v1/maintenance-tickets", headers=headers_b).json() == []
    assert client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers_a, json={"cost": 100}).status_code == 403
    assert client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers_a, json={"status": "in_progress"}).status_code == 200
    assert client.get("/api/v1/maintenance-tickets", headers=admin_headers).json()
    assert client.get("/api/v1/maintenance-tickets", headers=manager_headers).json()

    booking = _booking(admin_headers, unit_id)
    request = client.post("/api/v1/guest-requests", headers=admin_headers, json={"booking_id": booking["id"], "request_type": "towels", "description": "Assigned request", "assigned_to": staff_a["id"]}).json()
    assert [row["id"] for row in client.get("/api/v1/guest-requests", headers=headers_a).json()] == [request["id"]]
    assert client.get("/api/v1/guest-requests", headers=headers_b).json() == []
    assert client.patch(f"/api/v1/guest-requests/{request['id']}", headers=headers_a, json={"assigned_to": None}).status_code == 403
    assert client.patch(f"/api/v1/guest-requests/{request['id']}", headers=headers_a, json={"priority": "urgent"}).status_code == 403
    assert client.patch(f"/api/v1/guest-requests/{request['id']}", headers=headers_a, json={"status": "in_progress", "staff_notes": "Started"}).status_code == 200


def test_manager_assignment_removal_matches_creation_scope(admin_headers):
    manager, manager_headers = _user(admin_headers, "manager")
    staff, _ = _user(admin_headers, "staff")
    unit_id = _unit(admin_headers)
    allowed = client.post("/api/v1/staff/apartment-assignments", headers=manager_headers, json={"user_id": staff["id"], "unit_id": unit_id})
    assert allowed.status_code == 201, allowed.text
    assert client.delete(f"/api/v1/staff/apartment-assignments/{allowed.json()['id']}", headers=manager_headers).status_code == 204

    users = client.get("/api/v1/users", headers=admin_headers).json()
    admin_id = next(row["id"] for row in users if row["role"] == "admin")
    protected = client.post("/api/v1/staff/apartment-assignments", headers=admin_headers, json={"user_id": admin_id, "unit_id": unit_id})
    assert protected.status_code == 201, protected.text
    denied = client.delete(f"/api/v1/staff/apartment-assignments/{protected.json()['id']}", headers=manager_headers)
    assert denied.status_code == 403
    assert client.get("/api/v1/staff/apartment-assignments", headers=admin_headers, params={"user_id": admin_id}).json()
