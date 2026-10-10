"""
RBAC Phase A — foundation-only tests: the new `manager` role exists but
grants nothing yet, bank/UPI settings are split into their own admin-only
BankSettings resource, AuditLog captures the actor's role at write time,
and the initial-admin bootstrap password is no longer a hardcoded literal
anywhere in source. Phase B (wiring "manager" into the ~150 existing
require_role(...) allow-lists, granular resource.action permissions,
Manager pricing access) is explicitly out of scope for this file.

Run with: pytest tests/test_rbac_phase_a_foundation.py -v
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# Deliberately NOT a hardcoded literal here (unlike the ~60 older test
# files' own ADMIN_PASSWORD constants) — this is exactly the property
# section 4 of this file is testing for. The test harness invoking pytest
# is expected to set this (the same way it already sets
# TEST_DATABASE_URL/ENVIRONMENT), matching what a real deployment's own
# .env would provide on first run.
ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")


@pytest.fixture(scope="module")
def admin_headers():
    assert ADMIN_PASSWORD, "BOOTSTRAP_ADMIN_PASSWORD must be set for this test module to run"
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _register_and_login(admin_headers, role: str) -> dict:
    email = f"phasea_{role}_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    password = f"PhaseA-{uuid.uuid4().hex[:12]}!"
    resp = client.post(
        "/api/v1/auth/register",
        headers=admin_headers,
        json={"name": f"Phase A {role} test", "email": email, "password": password, "role": role},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return {"user_id": user_id, "headers": {"Authorization": f"Bearer {login.json()['access_token']}"}}


@pytest.fixture(scope="module")
def staff_ctx(admin_headers):
    return _register_and_login(admin_headers, "staff")


@pytest.fixture(scope="module")
def manager_ctx(admin_headers):
    return _register_and_login(admin_headers, "manager")


# ---------------------------------------------------------------------------
# Manager role foundation
# ---------------------------------------------------------------------------

def test_manager_role_exists_and_can_log_in(manager_ctx):
    """The role is real, seeded, and a user can authenticate as it — this
    alone proves it exists as a functioning Role row, not just an aspiration."""
    resp = client.get("/api/v1/auth/sessions", headers=manager_ctx["headers"])
    assert resp.status_code == 200


def test_manager_does_not_inherit_staff_access(manager_ctx):
    """GET /api/v1/settings is admin+staff today — Manager must NOT be
    silently treated as staff and get access to it in this phase."""
    resp = client.get("/api/v1/settings", headers=manager_ctx["headers"])
    assert resp.status_code == 403


def test_manager_does_not_inherit_admin_access(manager_ctx):
    """GET /api/v1/users is admin-only — Manager must not have it either."""
    resp = client.get("/api/v1/users", headers=manager_ctx["headers"])
    assert resp.status_code == 403


def test_manager_gained_booking_access_in_phase_b(manager_ctx):
    """RBAC Phase B has since deliberately granted Manager this exact
    access (guest/booking creation) — this test originally asserted 403
    here specifically to prove Phase A hadn't started Phase B's work yet.
    Now that Phase B has shipped, asserting 403 would be asserting a
    regression, not a security guarantee — updated to match the intended,
    documented outcome. Full Manager-boundary coverage (allowed vs.
    admin-only routes) lives in test_rbac_phase_b.py."""
    resp = client.post(
        "/api/v1/guests",
        headers=manager_ctx["headers"],
        json={"name": "Phase B Manager Probe", "phone": f"90{uuid.uuid4().int % 10**8:08d}"},
    )
    assert resp.status_code == 201


def test_existing_admin_behavior_unchanged(admin_headers):
    """Spot-check: admin can still reach both an admin+staff endpoint and
    an admin-only endpoint exactly as before this phase."""
    assert client.get("/api/v1/settings", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/users", headers=admin_headers).status_code == 200


def test_staff_cannot_read_sensitive_business_settings(staff_ctx):
    """Company/bank settings are owner-only; staff use dedicated operational APIs."""
    assert client.get("/api/v1/settings", headers=staff_ctx["headers"]).status_code == 403
    assert client.get("/api/v1/users", headers=staff_ctx["headers"]).status_code == 403


# ---------------------------------------------------------------------------
# BankSettings — admin-only split from BusinessSettings
# ---------------------------------------------------------------------------

def test_admin_can_read_bank_settings(admin_headers):
    resp = client.get("/api/v1/settings/bank", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "bank_account_number" in data
    assert "bank_ifsc" in data
    assert "upi_id" in data


def test_admin_can_update_bank_settings(admin_headers):
    original = client.get("/api/v1/settings/bank", headers=admin_headers).json()
    marker = f"{uuid.uuid4().int % 10**12:012d}"
    try:
        resp = client.put("/api/v1/settings/bank", headers=admin_headers, json={"bank_account_number": marker})
        assert resp.status_code == 200
        assert resp.json()["bank_account_number"] == marker
    finally:
        client.put("/api/v1/settings/bank", headers=admin_headers, json={"bank_account_number": original["bank_account_number"]})


def test_staff_cannot_read_bank_settings(staff_ctx):
    resp = client.get("/api/v1/settings/bank", headers=staff_ctx["headers"])
    assert resp.status_code == 403


def test_staff_cannot_update_bank_settings(staff_ctx):
    resp = client.put("/api/v1/settings/bank", headers=staff_ctx["headers"], json={"bank_name": "Should Not Apply"})
    assert resp.status_code == 403


def test_manager_cannot_read_bank_settings(manager_ctx):
    resp = client.get("/api/v1/settings/bank", headers=manager_ctx["headers"])
    assert resp.status_code == 403


def test_manager_cannot_update_bank_settings(manager_ctx):
    resp = client.put("/api/v1/settings/bank", headers=manager_ctx["headers"], json={"bank_name": "Should Not Apply"})
    assert resp.status_code == 403


def test_general_business_settings_no_longer_exposes_bank_fields(admin_headers, staff_ctx):
    """The core fix: neither the admin+staff-readable general settings
    response, for either role, contains any bank/UPI field any more."""
    bank_field_names = {"bank_name", "bank_account_name", "bank_account_number", "bank_ifsc", "bank_branch", "upi_id"}

    admin_view = client.get("/api/v1/settings", headers=admin_headers).json()
    assert bank_field_names.isdisjoint(admin_view.keys())

    staff_view = client.get("/api/v1/settings", headers=staff_ctx["headers"]).json()
    assert bank_field_names.isdisjoint(staff_view.keys())


# ---------------------------------------------------------------------------
# AuditLog actor_role
# ---------------------------------------------------------------------------

def test_audit_log_records_actor_role(admin_headers, staff_ctx):
    guest_resp = client.post(
        "/api/v1/guests",
        headers=staff_ctx["headers"],
        json={"name": "Audit Role Test Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"},
    )
    assert guest_resp.status_code == 201
    guest_id = guest_resp.json()["id"]

    logs = client.get(
        "/api/v1/audit/logs", headers=admin_headers, params={"entity_type": "guest", "entity_id": guest_id}
    ).json()
    assert len(logs) == 1
    assert logs[0]["action"] == "guest.create"
    assert logs[0]["actor_role"] == "staff"


def test_audit_log_actor_role_survives_later_role_change(admin_headers, staff_ctx):
    """The whole point of the column: once written, a row's actor_role
    must never change even if the acting user's role changes afterward."""
    guest_resp = client.post(
        "/api/v1/guests",
        headers=staff_ctx["headers"],
        json={"name": "Audit Role Immutability Test Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"},
    )
    assert guest_resp.status_code == 201
    guest_id = guest_resp.json()["id"]

    before = client.get(
        "/api/v1/audit/logs", headers=admin_headers, params={"entity_type": "guest", "entity_id": guest_id}
    ).json()
    assert before[0]["actor_role"] == "staff"
    log_id = before[0]["id"]

    role_change = client.patch(f"/api/v1/users/{staff_ctx['user_id']}", headers=admin_headers, json={"role": "manager"})
    assert role_change.status_code == 200
    assert role_change.json()["role"] == "manager"

    after = client.get(
        "/api/v1/audit/logs", headers=admin_headers, params={"entity_type": "guest", "entity_id": guest_id}
    ).json()
    assert after[0]["id"] == log_id
    assert after[0]["actor_role"] == "staff", "historical actor_role must not change after the user's role changes later"


# ---------------------------------------------------------------------------
# Bootstrap security — no hardcoded default admin password in source
# ---------------------------------------------------------------------------

def test_no_hardcoded_default_admin_password_in_seed_source():
    seed_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "db", "seed.py")
    with open(seed_path, "r") as f:
        source = f.read()
    assert "Sri@2026" not in source, "the old hardcoded default admin password must not appear in seed.py"


def test_no_hardcoded_default_admin_password_in_config_source():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "core", "config.py")
    with open(config_path, "r") as f:
        source = f.read()
    assert "Sri@2026" not in source, "the old hardcoded default admin password must not appear in config.py"
    # The new field must have NO literal default value baked in — only an
    # empty-string "not configured" sentinel, same convention as every
    # other credential in this file.
    assert 'bootstrap_admin_password: str = ""' in source


def test_dev_test_bootstrap_still_works_without_printing_the_password(admin_headers):
    """Proves the env-var-driven bootstrap path genuinely works end to end
    in this isolated test environment — admin_headers succeeding at all is
    itself the proof, since it required a real login with the value from
    BOOTSTRAP_ADMIN_PASSWORD. This test additionally confirms the variable
    is actually set (not e.g. silently skipped) without ever printing it."""
    assert os.environ.get("BOOTSTRAP_ADMIN_PASSWORD"), "BOOTSTRAP_ADMIN_PASSWORD must be set for dev/test bootstrap to work"
    assert admin_headers["Authorization"].startswith("Bearer ")
