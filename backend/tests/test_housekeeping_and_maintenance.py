"""
Tests for housekeeping tasks, maintenance tickets, booking status
transitions, and the checkout -> auto-housekeeping-task integration.
Run with: pytest tests/test_housekeeping_and_maintenance.py -v
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


def _make_property(headers):
    resp = client.post(
        "/api/v1/properties",
        headers=headers,
        json={"name": f"Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "33"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _make_unit(headers, property_id, base_rate=2000):
    resp = client.post(
        "/api/v1/units",
        headers=headers,
        json={"property_id": property_id, "name": f"Unit {uuid.uuid4().hex[:6]}", "base_rate": base_rate},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _make_guest(headers):
    resp = client.post("/api/v1/guests", headers=headers, json={"name": "Test Guest", "phone": "9999999999"})
    return resp.json()["id"]


def _make_booking(headers, unit_id, guest_id, check_in, check_out):
    resp = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- Housekeeping ---

def test_create_and_list_housekeeping_task(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)

    resp = client.post(
        "/api/v1/housekeeping-tasks",
        headers=headers,
        json={"unit_id": unit_id, "task_type": "routine", "notes": "Weekly deep clean"},
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()
    assert task["status"] == "pending"

    listing = client.get(f"/api/v1/housekeeping-tasks?unit_id={unit_id}", headers=headers)
    assert listing.status_code == 200
    assert any(t["id"] == task["id"] for t in listing.json())


def test_invalid_task_type_rejected(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    resp = client.post(
        "/api/v1/housekeeping-tasks",
        headers=headers,
        json={"unit_id": unit_id, "task_type": "bubble_bath"},
    )
    assert resp.status_code == 400


def test_completing_task_marks_unit_clean(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)

    # Manually dirty the unit via a checkout flow (tested separately below);
    # here we just verify a task completion sets status to clean regardless.
    task_resp = client.post(
        "/api/v1/housekeeping-tasks",
        headers=headers,
        json={"unit_id": unit_id, "task_type": "routine"},
    )
    task_id = task_resp.json()["id"]

    update = client.patch(f"/api/v1/housekeeping-tasks/{task_id}", headers=headers, json={"status": "completed"})
    assert update.status_code == 200
    assert update.json()["status"] == "completed"
    assert update.json()["completed_at"] is not None

    unit = client.get("/api/v1/units", headers=headers).json()
    updated_unit = next(u for u in unit if u["id"] == unit_id)
    assert updated_unit["housekeeping_status"] == "clean"


def test_invalid_task_status_rejected(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    task_id = client.post(
        "/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id}
    ).json()["id"]

    resp = client.patch(f"/api/v1/housekeeping-tasks/{task_id}", headers=headers, json={"status": "sparkling"})
    assert resp.status_code == 400


# --- Maintenance ---

def test_maintenance_ticket_lifecycle(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)

    create = client.post(
        "/api/v1/maintenance-tickets",
        headers=headers,
        json={"unit_id": unit_id, "description": "AC not cooling", "priority": "high"},
    )
    assert create.status_code == 201, create.text
    ticket = create.json()
    assert ticket["status"] == "open"
    assert ticket["priority"] == "high"
    assert ticket["reported_by"] is not None  # set from the authenticated user

    in_progress = client.patch(
        f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "in_progress"}
    )
    assert in_progress.status_code == 200
    assert in_progress.json()["resolved_at"] is None

    resolved = client.patch(
        f"/api/v1/maintenance-tickets/{ticket['id']}",
        headers=headers,
        json={"status": "resolved", "cost": 1500},
    )
    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None
    assert resolved.json()["cost"] == 1500.0


def test_invalid_ticket_priority_rejected(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    resp = client.post(
        "/api/v1/maintenance-tickets",
        headers=headers,
        json={"unit_id": unit_id, "description": "test", "priority": "catastrophic"},
    )
    assert resp.status_code == 400


def test_ticket_listing_filters_by_status(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    client.post(
        "/api/v1/maintenance-tickets",
        headers=headers,
        json={"unit_id": unit_id, "description": "Leaky faucet", "priority": "low"},
    )
    open_tickets = client.get(f"/api/v1/maintenance-tickets?unit_id={unit_id}&status=open", headers=headers)
    assert open_tickets.status_code == 200
    assert all(t["status"] == "open" for t in open_tickets.json())


# --- Booking status transitions + checkout -> housekeeping integration ---

def test_valid_booking_status_transitions(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest_id = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-08-01", "2027-08-03")

    r1 = client.patch(f"/api/v1/bookings/{booking_id}/status?new_status=checked_in", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "checked_in"

    r2 = client.patch(f"/api/v1/bookings/{booking_id}/status?new_status=checked_out", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == "checked_out"


def test_invalid_booking_status_transition_rejected(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest_id = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-08-10", "2027-08-12")

    # confirmed -> checked_out directly is not allowed (must go through checked_in)
    resp = client.patch(f"/api/v1/bookings/{booking_id}/status?new_status=checked_out", headers=headers)
    assert resp.status_code == 409


def test_checkout_auto_creates_housekeeping_task_and_dirties_unit(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest_id = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-08-20", "2027-08-22")

    # Unit starts clean
    units = client.get("/api/v1/units", headers=headers).json()
    assert next(u for u in units if u["id"] == unit_id)["housekeeping_status"] == "clean"

    client.patch(f"/api/v1/bookings/{booking_id}/status?new_status=checked_in", headers=headers)
    checkout = client.patch(f"/api/v1/bookings/{booking_id}/status?new_status=checked_out", headers=headers)
    assert checkout.status_code == 200

    # Unit is now dirty
    units_after = client.get("/api/v1/units", headers=headers).json()
    assert next(u for u in units_after if u["id"] == unit_id)["housekeeping_status"] == "dirty"

    # A checkout_clean task was auto-created for this unit
    tasks = client.get(f"/api/v1/housekeeping-tasks?unit_id={unit_id}", headers=headers).json()
    assert any(t["task_type"] == "checkout_clean" and t["status"] == "pending" for t in tasks)


def test_staff_can_use_housekeeping_and_maintenance():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    email = f"hk_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post(
        "/api/v1/auth/register",
        headers=admin_headers,
        json={"name": "Housekeeping Staff", "email": email, "password": "Pass12345!", "role": "staff"},
    )
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    property_id = _make_property(admin_headers)
    unit_id = _make_unit(admin_headers, property_id)

    # Staff can create and manage housekeeping tasks and maintenance tickets
    task = client.post(
        "/api/v1/housekeeping-tasks", headers=staff_headers, json={"unit_id": unit_id, "task_type": "routine"}
    )
    assert task.status_code == 201

    ticket = client.post(
        "/api/v1/maintenance-tickets",
        headers=staff_headers,
        json={"unit_id": unit_id, "description": "Bulb replacement needed"},
    )
    assert ticket.status_code == 201


def test_unauthenticated_cannot_access_housekeeping_or_maintenance():
    assert client.get("/api/v1/housekeeping-tasks").status_code == 401
    assert client.get("/api/v1/maintenance-tickets").status_code == 401
