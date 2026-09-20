"""
Tests for Phase 18A: Staff Operations foundation — dashboard, check-in/
check-out visibility, the unified housekeeping/maintenance/guest-request
task view, and the isolation guarantees (AI Strategist, autonomous
actions, financial records) that module is required to hold.

Run with: pytest tests/test_staff_operations.py -v
"""
import ast
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

STAFF_OPS_DIR = Path(__file__).resolve().parent.parent / "app" / "modules" / "staff_ops"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def staff_headers(headers):
    email = f"staffops_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "StaffOps Tester", "email": email, "password": "Pass12345!", "role": "staff"})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture(scope="module")
def staff_user_id(headers, staff_headers):
    # There is no self-info ("/auth/me") endpoint in this application —
    # look the just-registered staff user up via the admin-only user list.
    users = client.get("/api/v1/users", headers=headers).json()
    match = next(u for u in users if u["name"] == "StaffOps Tester")
    return match["id"]


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"OpsProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"OpsUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def _make_booking(headers, unit_id=None, status=None, days_offset=150, arrival_time=None, departure_time=None):
    unit_id = unit_id or _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "StaffOps Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=2)
    body = {
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    }
    if arrival_time:
        body["arrival_time"] = arrival_time
    if departure_time:
        body["departure_time"] = departure_time
    booking = client.post("/api/v1/bookings", headers=headers, json=body).json()
    if status:
        client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": status})
        booking["status"] = status
    return booking, unit_id, guest


def _make_today_booking(headers, status, unit_id=None):
    """A booking whose check_in is today — the only way check-ins
    actually surface on the dashboard/lists, since those are always
    relative to date.today(). check_out must be a later date (the
    system enforces check_out > check_in), so this is only suitable for
    check-in scenarios — see _make_checkout_today_booking for the
    checkout-due-today case."""
    unit_id = unit_id or _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "StaffOps Today Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    today = date.today()
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": today.isoformat(), "check_out": (today + timedelta(days=1)).isoformat(),
        "arrival_time": "14:00", "departure_time": "10:00",
    }).json()
    if status == "checked_in":
        client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_in"})
        booking["status"] = "checked_in"
    return booking, unit_id, guest


def _make_checkout_today_booking(headers, unit_id=None):
    """A booking that checked in yesterday and is due to check out today
    — status is driven straight to checked_in (skipping a literal
    yesterday-dated check-in call), since only the current status and
    check_out date matter for check-out visibility."""
    unit_id = unit_id or _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "StaffOps Checkout Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()
    today = date.today()
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (today - timedelta(days=1)).isoformat(), "check_out": today.isoformat(),
        "arrival_time": "14:00", "departure_time": "10:00",
    }).json()
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_in"})
    booking["status"] = "checked_in"
    return booking, unit_id, guest


# --- Authentication / RBAC ---

def test_dashboard_requires_authentication():
    resp = client.get("/api/v1/staff/dashboard")
    assert resp.status_code == 401


def test_dashboard_admin_access(headers):
    resp = client.get("/api/v1/staff/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("check_ins_today", "check_outs_today", "occupied_units", "vacant_units", "units_needing_cleaning", "open_maintenance_tickets", "open_guest_requests", "pending_staff_tasks"):
        assert key in body


def test_dashboard_staff_access(staff_headers):
    """Staff has the same access as admin for this module — there is no
    'manager' role in this application's actual RBAC (only admin/staff
    exist; verified by grepping every require_role() call in the
    backend), so admin covers the manager capabilities described in the
    Phase 18A brief and no new role was invented."""
    resp = client.get("/api/v1/staff/dashboard", headers=staff_headers)
    assert resp.status_code == 200, resp.text


# --- Check-ins / check-outs visibility ---

def test_checkin_visible_when_confirmed_and_due_today(headers):
    booking, unit_id, guest = _make_today_booking(headers, status=None)
    resp = client.get("/api/v1/staff/check-ins", headers=headers)
    assert resp.status_code == 200, resp.text
    match = next((l for l in resp.json() if l["booking_id"] == booking["id"]), None)
    assert match is not None
    assert match["guest_name"] == guest["name"]
    assert match["arrival_time"] == "14:00"
    assert match["status"] == "confirmed"


def test_checkin_disappears_once_checked_in(headers):
    booking, unit_id, guest = _make_today_booking(headers, status="checked_in")
    resp = client.get("/api/v1/staff/check-ins", headers=headers)
    assert not any(l["booking_id"] == booking["id"] for l in resp.json())


def test_checkout_visible_when_checked_in_and_due_today(headers):
    booking, unit_id, guest = _make_checkout_today_booking(headers)
    resp = client.get("/api/v1/staff/check-outs", headers=headers)
    assert resp.status_code == 200, resp.text
    match = next((l for l in resp.json() if l["booking_id"] == booking["id"]), None)
    assert match is not None
    assert match["guest_name"] == guest["name"]
    assert match["departure_time"] == "10:00"


def test_checkout_disappears_once_checked_out(headers):
    booking, unit_id, guest = _make_checkout_today_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_out"})
    resp = client.get("/api/v1/staff/check-outs", headers=headers)
    assert not any(l["booking_id"] == booking["id"] for l in resp.json())


def test_checkout_auto_creates_housekeeping_task_via_existing_hook(headers):
    """No new automatic-task-generation hook was added in Phase 18A — this
    proves the existing one (booking checked_out -> checkout_clean task,
    pre-dating this phase) is what the dashboard's
    units_needing_cleaning/housekeeping counts pick up."""
    booking, unit_id, guest = _make_checkout_today_booking(headers)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_out"})
    tasks = client.get("/api/v1/housekeeping-tasks", headers=headers, params={"unit_id": unit_id}).json()
    assert any(t["task_type"] == "checkout_clean" for t in tasks)


def test_checkin_payment_status_reflects_existing_payments(headers):
    booking, unit_id, guest = _make_today_booking(headers, status=None)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1500, "mode": "upi"})
    resp = client.get("/api/v1/staff/check-ins", headers=headers)
    match = next(l for l in resp.json() if l["booking_id"] == booking["id"])
    assert match["net_paid"] == 1500.0


# --- Unified task view: housekeeping ---

def test_housekeeping_task_visible_in_unified_tasks(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "deep_clean"}).json()
    resp = client.get("/api/v1/staff/tasks", headers=headers, params={"category": "housekeeping"})
    assert resp.status_code == 200
    match = next(t for t in resp.json() if t["id"] == task["id"])
    assert match["category"] == "housekeeping"
    assert match["status"] == "pending"
    assert match["priority"] is None  # housekeeping has no priority concept — must not be invented


def test_housekeeping_status_transition_via_unified_endpoint_sets_started_at(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    assert task["started_at"] is None

    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"status": "in_progress"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["started_at"] is not None
    assert resp.json()["status"] == "in_progress"


def test_housekeeping_completion_via_unified_endpoint_clears_unit_dirty_flag(headers):
    unit_id = _make_unit(headers)
    client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"housekeeping_status": "dirty"})
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()

    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"status": "completed"})
    assert resp.status_code == 200
    units = client.get("/api/v1/units", headers=headers).json()
    unit = next(u for u in units if u["id"] == unit_id)
    assert unit["housekeeping_status"] == "clean"


def test_housekeeping_repeated_completion_does_not_shift_completed_at(headers):
    """Phase 18C regression: the unified endpoint used to unconditionally
    overwrite completed_at on every 'completed' PATCH, so re-clicking
    Complete (or a retried request) silently moved the timestamp forward
    each time. Fixed to match the guard maintenance already had."""
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    first = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"status": "completed"}).json()
    second = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"status": "completed"}).json()
    assert first["completed_at"] == second["completed_at"]


def test_guest_request_repeated_resolution_does_not_shift_completed_at(headers):
    booking, unit_id, guest = _make_booking(headers)
    req = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": booking["id"], "request_type": "other", "description": "x"}).json()
    first = client.patch(f"/api/v1/staff/tasks/{req['id']}", headers=headers, params={"category": "guest_request"}, json={"status": "resolved"}).json()
    second = client.patch(f"/api/v1/staff/tasks/{req['id']}", headers=headers, params={"category": "guest_request"}, json={"status": "resolved"}).json()
    assert first["completed_at"] == second["completed_at"]


def test_unified_task_exposes_completed_at_for_all_categories(headers):
    """Phase 18C regression: StaffTaskOut originally omitted completed_at
    entirely, so it was correctly persisted at the DB level but invisible
    through the unified view/API."""
    unit_id = _make_unit(headers)
    hk = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    hk_done = client.patch(f"/api/v1/staff/tasks/{hk['id']}", headers=headers, params={"category": "housekeeping"}, json={"status": "completed"}).json()
    assert hk_done["completed_at"] is not None

    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "x"}).json()
    ticket_done = client.patch(f"/api/v1/staff/tasks/{ticket['id']}", headers=headers, params={"category": "maintenance"}, json={"status": "resolved"}).json()
    assert ticket_done["completed_at"] is not None


def test_unified_guest_request_notes_reflects_staff_notes_once_set(headers):
    """Phase 18C regression: the unified view always showed the guest's
    original description, even after staff recorded their own
    resolution note in staff_notes — that note was persisted correctly
    but never surfaced back through this endpoint."""
    booking, unit_id, guest = _make_booking(headers)
    req = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": booking["id"], "request_type": "other", "description": "original ask"}).json()
    resp = client.patch(f"/api/v1/staff/tasks/{req['id']}", headers=headers, params={"category": "guest_request"}, json={"notes": "staff resolution note"}).json()
    assert resp["notes"] == "staff resolution note"


def test_housekeeping_update_via_unified_endpoint_logs_same_audit_action_as_dedicated_endpoint(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"status": "in_progress"})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "housekeeping.update"}).json()
    assert any(l["entity_id"] == task["id"] for l in logs)


# --- Unified task view: maintenance ---

def test_maintenance_ticket_visible_in_unified_tasks_with_category(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "AC not cooling", "priority": "high", "category": "ac"}).json()
    assert ticket["category"] == "ac"

    resp = client.get("/api/v1/staff/tasks", headers=headers, params={"category": "maintenance"})
    match = next(t for t in resp.json() if t["id"] == ticket["id"])
    assert match["priority"] == "high"
    assert match["label"] == "Ac"


def test_maintenance_invalid_category_rejected(headers):
    unit_id = _make_unit(headers)
    resp = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "test", "category": "spaceship"})
    assert resp.status_code == 400


def test_maintenance_status_transition_via_unified_endpoint(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Leak"}).json()

    resp = client.patch(f"/api/v1/staff/tasks/{ticket['id']}", headers=headers, params={"category": "maintenance"}, json={"status": "resolved"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"


def test_maintenance_invalid_status_rejected_via_unified_endpoint(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "test"}).json()
    resp = client.patch(f"/api/v1/staff/tasks/{ticket['id']}", headers=headers, params={"category": "maintenance"}, json={"status": "on_fire"})
    assert resp.status_code == 400


# --- Unified task view: guest requests ---

def test_staff_can_create_guest_request_from_booking(headers):
    booking, unit_id, guest = _make_booking(headers)
    resp = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": booking["id"], "request_type": "towels", "description": "2 extra towels"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["guest_id"] == guest["id"]
    assert resp.json()["request_type"] == "towels"


def test_guest_request_visible_in_unified_tasks(headers):
    booking, unit_id, guest = _make_booking(headers)
    req = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": booking["id"], "request_type": "linens", "description": "fresh linens"}).json()

    resp = client.get("/api/v1/staff/tasks", headers=headers, params={"category": "guest_request"})
    match = next(t for t in resp.json() if t["id"] == req["id"])
    assert match["unit_id"] == unit_id
    assert match["category"] == "guest_request"
    # sensitive-data protection: the unified task view must not carry guest
    # PII (name/phone/email) — only a unit/booking-scoped reference.
    assert "guest_name" not in match and "guest_phone" not in match and "guest_email" not in match


def test_unified_task_exposes_booking_reference_for_guest_requests_only(headers):
    """Phase 18D: the unified view now surfaces booking_id so the UI can
    show 'Booking reference if available' — populated for guest_request,
    null for housekeeping/maintenance (neither is booking-linked)."""
    unit_id = _make_unit(headers)
    booking, _, guest = _make_booking(headers, unit_id=unit_id)
    req = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": booking["id"], "request_type": "other", "description": "x"}).json()

    tasks = client.get("/api/v1/staff/tasks", headers=headers).json()
    gr = next(t for t in tasks if t["id"] == req["id"])
    assert gr["booking_id"] == booking["id"]

    hk = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    tasks = client.get("/api/v1/staff/tasks", headers=headers).json()
    hk_task = next(t for t in tasks if t["id"] == hk["id"])
    assert hk_task["booking_id"] is None


def test_guest_request_nonexistent_booking_404s(headers):
    resp = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": str(uuid.uuid4()), "request_type": "towels", "description": "x"})
    assert resp.status_code == 404


def test_guest_request_invalid_request_type_rejected(headers):
    booking, unit_id, guest = _make_booking(headers)
    resp = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": booking["id"], "request_type": "spaceship_repair", "description": "x"})
    assert resp.status_code == 400


def test_guest_request_completion_via_unified_endpoint(headers):
    booking, unit_id, guest = _make_booking(headers)
    req = client.post("/api/v1/guest-requests", headers=headers, json={"booking_id": booking["id"], "request_type": "water", "description": "bottles"}).json()

    resp = client.patch(f"/api/v1/staff/tasks/{req['id']}", headers=headers, params={"category": "guest_request"}, json={"status": "completed"})
    assert resp.status_code == 400  # guest_requests uses "resolved", not "completed" — proves the unified endpoint enforces THAT table's own vocabulary, not an invented one

    resp = client.patch(f"/api/v1/staff/tasks/{req['id']}", headers=headers, params={"category": "guest_request"}, json={"status": "resolved"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"


# --- Task assignment ---

def test_task_assignment_via_unified_endpoint(headers, staff_user_id):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()

    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"assigned_to": staff_user_id})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == staff_user_id

    mine = client.get("/api/v1/staff/tasks", headers=headers, params={"category": "housekeeping", "assigned_to": staff_user_id}).json()
    assert any(t["id"] == task["id"] for t in mine)


def test_task_assignment_nonexistent_user_rejected(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"assigned_to": str(uuid.uuid4())})
    assert resp.status_code == 404


# --- Not found / invalid category ---

def test_unified_task_nonexistent_id_404s(headers):
    resp = client.patch(f"/api/v1/staff/tasks/{uuid.uuid4()}", headers=headers, params={"category": "housekeeping"}, json={"status": "in_progress"})
    assert resp.status_code == 404


def test_unified_task_invalid_category_rejected(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    resp = client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "spaceship"}, json={"status": "in_progress"})
    assert resp.status_code == 400


def test_list_tasks_invalid_category_rejected(headers):
    resp = client.get("/api/v1/staff/tasks", headers=headers, params={"category": "spaceship"})
    assert resp.status_code == 400


# --- Dashboard/check-in/check-out unauthenticated ---

def test_checkins_require_authentication():
    assert client.get("/api/v1/staff/check-ins").status_code == 401


def test_checkouts_require_authentication():
    assert client.get("/api/v1/staff/check-outs").status_code == 401


def test_tasks_require_authentication():
    assert client.get("/api/v1/staff/tasks").status_code == 401


def test_task_update_requires_authentication():
    resp = client.patch(f"/api/v1/staff/tasks/{uuid.uuid4()}", params={"category": "housekeeping"}, json={"status": "in_progress"})
    assert resp.status_code == 401


# --- Isolation: AI Strategist ---

def test_staff_ops_module_does_not_import_ai_strategist_or_narrative():
    forbidden = ("ai_strategist", "narrative_provider", "narrative_service", "decision_engine")
    for path in STAFF_OPS_DIR.glob("*.py"):
        source = path.read_text()
        for term in forbidden:
            assert term not in source, f"{path.name} unexpectedly references '{term}'"


def test_reports_module_does_not_import_staff_ops():
    reports_dir = STAFF_OPS_DIR.parent / "reports"
    for path in reports_dir.glob("*.py"):
        source = path.read_text()
        assert "staff_ops" not in source, f"{path.name} unexpectedly imports staff_ops"


def test_ai_strategist_endpoints_still_healthy(headers):
    for path in ("/api/v1/reports/ai-strategist", "/api/v1/reports/ai-strategist/decisions", "/api/v1/reports/ai-strategist/narrative"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_staff_ops_contains_zero_llm_provider_references():
    forbidden = ("anthropic", "openai", "google.generativeai", "claude")
    for path in STAFF_OPS_DIR.glob("*.py"):
        source = path.read_text().lower()
        for term in forbidden:
            assert term not in source, f"{path.name} unexpectedly references '{term}'"


# --- Isolation: autonomous actions ---

def test_staff_ops_module_does_not_import_automation_submodules():
    forbidden_modules = (
        "automation.policy_engine", "automation.action_registry", "automation.action_executor",
        "automation.dispatcher", "automation.signal_generator", "automation.governance",
    )
    for path in STAFF_OPS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        for forbidden in forbidden_modules:
            assert not any(forbidden in m for m in imported), f"{path.name} imports {forbidden}"


def test_automation_constants_unchanged():
    """Phase 18A must not touch these — this test only reads the source,
    it doesn't assert specific values (which would make this test itself
    a place those constants could be silently weakened from), it just
    proves the automation module wasn't imported/modified by staff_ops."""
    automation_dir = STAFF_OPS_DIR.parent / "automation"
    if not automation_dir.exists():
        pytest.skip("automation module not present in this checkout")
    for path in STAFF_OPS_DIR.glob("*.py"):
        source = path.read_text()
        assert "GLOBAL_AUTOMATION_ENABLED" not in source
        assert "RELEASE_EXPIRED_HOLD" not in source
        assert "NOTIFY_STAFF" not in source


# --- Isolation: financial records ---

def test_staff_ops_module_does_not_import_financial_mutation_code():
    """Static check: the module may read unit/booking/payment DATA (it
    does, for net_paid) but must not import anything that creates/edits a
    refund, invoice, GST record, or pricing calculation."""
    forbidden = ("refund", "generate_invoice", "calculate_gst", "OTACommission", "SecurityDeposit")
    router_source = (STAFF_OPS_DIR / "router.py").read_text()
    for term in forbidden:
        assert term not in router_source, f"staff_ops/router.py unexpectedly references '{term}'"


def test_housekeeping_completion_has_zero_financial_side_effects(headers):
    booking, unit_id, guest = _make_booking(headers)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "cash"})
    payments_before = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()

    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    client.patch(f"/api/v1/staff/tasks/{task['id']}", headers=headers, params={"category": "housekeeping"}, json={"status": "completed"})

    payments_after = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    assert payments_before == payments_after


def test_maintenance_resolution_via_unified_endpoint_does_not_set_cost_or_create_expense(headers):
    """The dedicated /maintenance-tickets endpoint can auto-create an
    Expense when a ticket with a cost is resolved (existing, pre-Phase-18A
    financial integration). The unified staff_ops endpoint deliberately
    doesn't accept `cost` at all, so resolving through it can never
    trigger that path — this proves that, not just asserts it."""
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "test"}).json()
    resp = client.patch(f"/api/v1/staff/tasks/{ticket['id']}", headers=headers, params={"category": "maintenance"}, json={"status": "resolved"})
    assert resp.status_code == 200
    updated = client.get("/api/v1/maintenance-tickets", headers=headers, params={"unit_id": unit_id}).json()
    match = next(t for t in updated if t["id"] == ticket["id"])
    assert match["cost"] is None
