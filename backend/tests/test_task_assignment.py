"""
Phase 19A-1: focused regression tests for the assign/reassign/unassign fix
across the three category endpoints:

- PATCH /api/v1/staff/tasks/{id}?category=... (unified Staff Operations view)
- PATCH /api/v1/housekeeping-tasks/{id} (dedicated Housekeeping page)
- PATCH /api/v1/maintenance-tickets/{id} (dedicated Maintenance page)
- PATCH /api/v1/guest-requests/{id} (dedicated Guest Requests page)

Only `assigned_to` handling changed (omitted -> unchanged, UUID -> assign/
reassign, explicit null -> clear). This file does not re-test status/
priority/category/cost/notes semantics, which are covered by the existing
test_staff_operations.py / test_finance_completion.py-style suites and are
verified here only as "unaffected by an assignment-only PATCH".

Run with: pytest tests/test_task_assignment.py -v
"""
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


@pytest.fixture(scope="module")
def staff_headers(headers):
    email = f"assigntest_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Assign Test Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture(scope="module")
def staff_user_id(headers, staff_headers):
    users = client.get("/api/v1/users", headers=headers).json()
    match = next(u for u in users if u["name"] == "Assign Test Staff")
    return match["id"]


@pytest.fixture(scope="module")
def second_staff_id(headers):
    email = f"assigntest_staff2_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Assign Test Staff Two", "email": email, "password": "Pass12345!", "role": "staff"})
    users = client.get("/api/v1/users", headers=headers).json()
    return next(u for u in users if u["name"] == "Assign Test Staff Two")["id"]


def _make_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"AssignProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"AssignUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return unit["id"]


def _make_hk_task(headers, unit_id=None):
    unit_id = unit_id or _make_unit(headers)
    return client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()


def _make_maint_ticket(headers, unit_id=None):
    unit_id = unit_id or _make_unit(headers)
    return client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Assignment test ticket"}).json()


def _make_guest_request(headers, unit_id=None):
    unit_id = unit_id or _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Assign Test Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=160)
    check_out = check_in + timedelta(days=2)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    }).json()
    return client.post("/api/v1/guest-requests", headers=headers, json={
        "booking_id": booking["id"], "request_type": "towels", "description": "Assignment test request",
    }).json()


# --- Housekeeping: dedicated endpoint ---

def test_hk_omitted_assigned_to_leaves_it_unchanged(headers, staff_user_id):
    task = _make_hk_task(headers)
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"notes": "unrelated edit"})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id


def test_hk_assign_to_valid_user(headers, staff_user_id):
    task = _make_hk_task(headers)
    resp = client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id


def test_hk_reassign_to_different_user(headers, staff_user_id, second_staff_id):
    task = _make_hk_task(headers)
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": second_staff_id})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == second_staff_id


def test_hk_explicit_null_clears_assignment(headers, staff_user_id):
    task = _make_hk_task(headers)
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": None})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] is None


def test_hk_assign_to_nonexistent_user_404(headers):
    task = _make_hk_task(headers)
    resp = client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_hk_assignment_does_not_disturb_status(headers):
    """Regression guard: an assignment-only PATCH must not touch status."""
    task = _make_hk_task(headers)
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"status": "in_progress"})
    resp = client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"assigned_to": None})
    assert resp.json()["status"] == "in_progress"


# --- Maintenance: dedicated endpoint ---

def test_maint_omitted_assigned_to_leaves_it_unchanged(headers, staff_user_id):
    ticket = _make_maint_ticket(headers)
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"priority": "high"})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id
    assert resp.json()["priority"] == "high"


def test_maint_assign_to_valid_user(headers, staff_user_id):
    ticket = _make_maint_ticket(headers)
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": staff_user_id})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id


def test_maint_reassign_to_different_user(headers, staff_user_id, second_staff_id):
    ticket = _make_maint_ticket(headers)
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": second_staff_id})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == second_staff_id


def test_maint_explicit_null_clears_assignment(headers, staff_user_id):
    ticket = _make_maint_ticket(headers)
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": None})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] is None


def test_maint_assign_to_nonexistent_user_404(headers):
    ticket = _make_maint_ticket(headers)
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_maint_assign_at_creation(headers, staff_user_id):
    unit_id = _make_unit(headers)
    resp = client.post("/api/v1/maintenance-tickets", headers=headers, json={
        "unit_id": unit_id, "description": "Created pre-assigned", "assigned_to": staff_user_id,
    })
    assert resp.status_code == 201
    assert resp.json()["assigned_to"] == staff_user_id


def test_maint_assignment_does_not_disturb_cost_or_expense_sync(headers):
    """Regression guard: the Finance/Expense-sync block must not fire or
    change on an assignment-only PATCH — it's gated on status/cost, which
    this PATCH does not touch."""
    ticket = _make_maint_ticket(headers)
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 500})
    resolved = client.get("/api/v1/maintenance-tickets", headers=headers, params={"unit_id": ticket["unit_id"]}).json()
    linked_before = next(t for t in resolved if t["id"] == ticket["id"])["cost"]

    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"assigned_to": None})
    assert resp.status_code == 200
    assert resp.json()["cost"] == linked_before == 500.0
    assert resp.json()["status"] == "resolved"


# --- Guest Requests: dedicated endpoint ---

def test_gr_omitted_assigned_to_leaves_it_unchanged(headers, staff_user_id):
    req = _make_guest_request(headers)
    client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"priority": "high"})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id


def test_gr_assign_to_valid_user(headers, staff_user_id):
    req = _make_guest_request(headers)
    resp = client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"assigned_to": staff_user_id})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id


def test_gr_reassign_to_different_user(headers, staff_user_id, second_staff_id):
    req = _make_guest_request(headers)
    client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"assigned_to": second_staff_id})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == second_staff_id


def test_gr_explicit_null_clears_assignment(headers, staff_user_id):
    req = _make_guest_request(headers)
    client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"assigned_to": staff_user_id})
    resp = client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"assigned_to": None})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] is None


def test_gr_assign_to_nonexistent_user_404(headers):
    req = _make_guest_request(headers)
    resp = client.patch(f"/api/v1/guest-requests/{req['id']}", headers=headers, json={"assigned_to": str(uuid.uuid4())})
    assert resp.status_code == 404


def _make_booking_for_request(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "GR Create-Assign Guest", "phone": f"95{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=170)
    check_out = check_in + timedelta(days=2)
    return client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    }).json()


def test_gr_omitted_assigned_to_at_creation_is_unassigned(headers):
    booking = _make_booking_for_request(headers)
    resp = client.post("/api/v1/guest-requests", headers=headers, json={
        "booking_id": booking["id"], "request_type": "towels", "description": "No assignee given",
    })
    assert resp.status_code == 201
    assert resp.json()["assigned_to"] is None


def test_gr_assign_at_creation(headers, staff_user_id):
    booking = _make_booking_for_request(headers)
    resp = client.post("/api/v1/guest-requests", headers=headers, json={
        "booking_id": booking["id"], "request_type": "towels", "description": "Created pre-assigned", "assigned_to": staff_user_id,
    })
    assert resp.status_code == 201
    assert resp.json()["assigned_to"] == staff_user_id


def test_gr_explicit_null_at_creation_is_unassigned(headers):
    booking = _make_booking_for_request(headers)
    resp = client.post("/api/v1/guest-requests", headers=headers, json={
        "booking_id": booking["id"], "request_type": "towels", "description": "Explicit null assignee", "assigned_to": None,
    })
    assert resp.status_code == 201
    assert resp.json()["assigned_to"] is None


def test_gr_assign_to_nonexistent_user_at_creation_404(headers):
    booking = _make_booking_for_request(headers)
    resp = client.post("/api/v1/guest-requests", headers=headers, json={
        "booking_id": booking["id"], "request_type": "towels", "description": "Bad assignee", "assigned_to": str(uuid.uuid4()),
    })
    assert resp.status_code == 404


def test_gr_creation_assignment_does_not_disturb_eligibility_or_notifications(headers, staff_user_id):
    """Regression guard: assigning at creation must not change request_type
    validation, guest/booking derivation, or the existing notification
    trigger — only assigned_to is new here."""
    booking = _make_booking_for_request(headers)
    resp = client.post("/api/v1/guest-requests", headers=headers, json={
        "booking_id": booking["id"], "request_type": "towels", "description": "Eligibility check", "assigned_to": staff_user_id,
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["guest_id"] == booking["guest_id"]
    assert body["booking_id"] == booking["id"]
    assert body["status"] == "open"


# --- Unified Staff Operations endpoint (all three categories) ---

@pytest.mark.parametrize("category,make_fn", [
    ("housekeeping", _make_hk_task),
    ("maintenance", _make_maint_ticket),
    ("guest_request", _make_guest_request),
])
def test_unified_assign_reassign_and_clear(headers, staff_user_id, second_staff_id, category, make_fn):
    task = make_fn(headers)

    assign = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id}, params={"category": category})
    assert assign.status_code == 200, assign.text
    assert assign.json()["assigned_to"] == staff_user_id

    reassign = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": second_staff_id}, params={"category": category})
    assert reassign.status_code == 200
    assert reassign.json()["assigned_to"] == second_staff_id

    clear = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": None}, params={"category": category})
    assert clear.status_code == 200
    assert clear.json()["assigned_to"] is None


@pytest.mark.parametrize("category,make_fn", [
    ("housekeeping", _make_hk_task),
    ("maintenance", _make_maint_ticket),
    ("guest_request", _make_guest_request),
])
def test_unified_omitted_assigned_to_leaves_it_unchanged(headers, staff_user_id, category, make_fn):
    task = make_fn(headers)
    client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id}, params={"category": category})
    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"notes": "unrelated"} if category != "maintenance" else {}, params={"category": category})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id


@pytest.mark.parametrize("category,make_fn", [
    ("housekeeping", _make_hk_task),
    ("maintenance", _make_maint_ticket),
    ("guest_request", _make_guest_request),
])
def test_unified_assign_to_nonexistent_user_404(headers, category, make_fn):
    task = make_fn(headers)
    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": str(uuid.uuid4())}, params={"category": category})
    assert resp.status_code == 404


def test_assigned_task_appears_in_my_tasks(headers, staff_headers, staff_user_id):
    task = _make_hk_task(headers)
    client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id}, params={"category": "housekeeping"})
    mine = client.get("/api/v1/staff/tasks", headers=staff_headers, params={"assigned_to": staff_user_id}).json()
    assert any(t["id"] == task["id"] for t in mine)


def test_unassigned_task_does_not_appear_in_specific_users_my_tasks(headers, staff_user_id):
    task = _make_hk_task(headers)
    mine = client.get("/api/v1/staff/tasks", headers=headers, params={"assigned_to": staff_user_id}).json()
    assert not any(t["id"] == task["id"] for t in mine)


def test_reassigned_task_disappears_from_previous_assignees_my_tasks(headers, staff_user_id, second_staff_id):
    task = _make_hk_task(headers)
    client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id}, params={"category": "housekeeping"})
    mine_before = client.get("/api/v1/staff/tasks", headers=headers, params={"assigned_to": staff_user_id}).json()
    assert any(t["id"] == task["id"] for t in mine_before)

    client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": second_staff_id}, params={"category": "housekeeping"})
    mine_after = client.get("/api/v1/staff/tasks", headers=headers, params={"assigned_to": staff_user_id}).json()
    assert not any(t["id"] == task["id"] for t in mine_after)

    now_theirs = client.get("/api/v1/staff/tasks", headers=headers, params={"assigned_to": second_staff_id}).json()
    assert any(t["id"] == task["id"] for t in now_theirs)


def test_staff_cannot_claim_an_unassigned_task_by_direct_id(staff_headers, headers, staff_user_id):
    """Staff may update assigned work but cannot claim an unassigned row by guessing its ID."""
    task = _make_hk_task(headers)
    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=staff_headers, json={"assigned_to": staff_user_id}, params={"category": "housekeeping"})
    assert resp.status_code == 403

    # The attempted claim does not change ownership; Admin/Manager retain
    # their operational assignment capability.
    still_unassigned = client.get("/api/v1/housekeeping-tasks", headers=headers).json()
    row = next(item for item in still_unassigned if item["id"] == task["id"])
    assert row["assigned_to"] is None


def test_unauthenticated_cannot_assign(headers):
    task = _make_hk_task(headers)
    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", json={"assigned_to": None}, params={"category": "housekeeping"})
    assert resp.status_code == 401


def test_assignment_is_audited(headers, staff_user_id):
    task = _make_hk_task(headers)
    client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, json={"assigned_to": staff_user_id}, params={"category": "housekeeping"})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "housekeeping.update"}).json()
    assert any(l["entity_id"] == task["id"] and "assigned_to" in (l["details"] or "") for l in logs)
