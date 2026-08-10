"""
Tests for Phase 2 (payments/GST invoicing) and user management.
Run with: pytest tests/test_payments_and_users.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Guest
from app.modules.properties.models import Property, Unit

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(scope="module")
def admin_token():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def _make_property(headers, state_code="33"):
    resp = client.post(
        "/api/v1/properties",
        headers=headers,
        json={"name": f"Prop {uuid.uuid4().hex[:6]}", "address": "Test", "gst_number": "33ABCDE1234F1Z5", "state_code": state_code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_unit(headers, property_id, base_rate=2000):
    resp = client.post(
        "/api/v1/units",
        headers=headers,
        json={"property_id": property_id, "name": f"Unit {uuid.uuid4().hex[:6]}", "base_rate": base_rate},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_guest(headers, state_code=None):
    resp = client.post(
        "/api/v1/guests",
        headers=headers,
        json={"name": "Test Guest", "phone": "9999999999"},
    )
    guest_id = resp.json()["id"]
    if state_code:
        db = SessionLocal()
        guest = db.get(Guest, uuid.UUID(guest_id))
        guest.state_code = state_code
        db.commit()
        db.close()
    return guest_id


def _make_booking(headers, unit_id, guest_id, check_in, check_out):
    resp = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_slab_0_percent_for_budget_tariff(headers):
    """Tariff <= Rs 1,000/night -> 0% GST (effective 22 Sep 2025 rules)."""
    property_id = _make_property(headers, state_code="33")
    unit_id = _make_unit(headers, property_id, base_rate=800)  # budget tariff
    guest_id = _make_guest(headers, state_code="33")
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-03-01", "2027-03-03")  # 2 nights

    resp = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["subtotal"] == 1600.0
    assert data["gst_rate_percent"] == 0.0
    assert data["cgst"] == 0.0
    assert data["sgst"] == 0.0
    assert data["igst"] == 0.0
    assert data["total"] == 1600.0
    assert data["itc_available"] is False


def test_slab_5_percent_same_state_uses_cgst_sgst(headers):
    """Tariff Rs 1,001-7,500/night -> 5% GST, no ITC, CGST+SGST if same state."""
    property_id = _make_property(headers, state_code="33")
    unit_id = _make_unit(headers, property_id, base_rate=2000)  # mid-range tariff
    guest_id = _make_guest(headers, state_code="33")  # same state as property
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-03-10", "2027-03-13")  # 3 nights

    resp = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["subtotal"] == 6000.0
    assert data["gst_rate_percent"] == 5.0
    assert data["cgst"] == 150.0
    assert data["sgst"] == 150.0
    assert data["igst"] == 0.0
    assert data["total"] == 6300.0
    assert data["itc_available"] is False
    assert data["invoice_no"].startswith("INV/")


def test_slab_5_percent_different_state_uses_igst(headers):
    """Same 5% slab, but inter-state guest -> IGST instead of CGST/SGST."""
    property_id = _make_property(headers, state_code="33")  # Tamil Nadu
    unit_id = _make_unit(headers, property_id, base_rate=1200)  # mid-range tariff
    guest_id = _make_guest(headers, state_code="27")  # Maharashtra — different state
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-03-20", "2027-03-22")  # 2 nights

    resp = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["subtotal"] == 2400.0
    assert data["gst_rate_percent"] == 5.0
    assert data["cgst"] == 0.0
    assert data["sgst"] == 0.0
    assert data["igst"] == 120.0
    assert data["total"] == 2520.0


def test_slab_18_percent_for_luxury_tariff(headers):
    """Tariff > Rs 7,500/night -> 18% GST, ITC available."""
    property_id = _make_property(headers, state_code="33")
    unit_id = _make_unit(headers, property_id, base_rate=10000)  # luxury tariff
    guest_id = _make_guest(headers, state_code="33")
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-04-01", "2027-04-02")  # 1 night

    resp = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["subtotal"] == 10000.0
    assert data["gst_rate_percent"] == 18.0
    assert data["cgst"] == 900.0
    assert data["sgst"] == 900.0
    assert data["total"] == 11800.0
    assert data["itc_available"] is True


def test_duplicate_invoice_rejected(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest_id = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-04-01", "2027-04-03")

    r1 = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert r1.status_code == 201

    r2 = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert r2.status_code == 409


def test_invoice_pdf_downloadable(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest_id = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-05-01", "2027-05-02")

    inv = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers).json()
    pdf_resp = client.get(f"/api/v1/invoices/{inv['id']}/pdf", headers=headers)
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert pdf_resp.content[:4] == b"%PDF"


def test_payment_recording(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest_id = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-06-01", "2027-06-03")

    resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"booking_id": booking_id, "amount": 4480, "mode": "upi", "reference_no": "TESTUPI001"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "completed"

    listing = client.get(f"/api/v1/payments/{booking_id}", headers=headers)
    assert len(listing.json()) == 1


def test_invalid_payment_mode_rejected(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest_id = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest_id, "2027-06-10", "2027-06-12")

    resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"booking_id": booking_id, "amount": 1000, "mode": "bitcoin"},
    )
    assert resp.status_code == 400


def test_admin_can_list_and_deactivate_user(headers):
    email = f"deactivate_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    reg = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={"name": "Deactivate Me", "email": email, "password": "Pass12345!", "role": "staff"},
    )
    user_id = reg.json()["id"]

    listing = client.get("/api/v1/users", headers=headers)
    assert listing.status_code == 200
    assert any(u["id"] == user_id for u in listing.json())

    deact = client.patch(f"/api/v1/users/{user_id}", headers=headers, json={"is_active": False})
    assert deact.status_code == 200
    assert deact.json()["is_active"] is False

    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    assert login.status_code == 403


def test_staff_cannot_access_user_management():
    email = f"nonadmin_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    client.post(
        "/api/v1/auth/register",
        headers=admin_headers,
        json={"name": "Staff NoAccess", "email": email, "password": "Pass12345!", "role": "staff"},
    )
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/users", headers=staff_headers)
    assert resp.status_code == 403


# --- Phase 4: User Self-Lockout Protection ---
#
# `require_role`/`get_current_user` (app/shared/rbac.py) re-check the actor's
# is_active + role live from the DB on every request — a deactivated user
# can never authenticate at all. That means the "last remaining active
# admin" guard in update_user() can only ever be triggered by an admin
# acting on THEIR OWN account (the acting admin is always, by construction,
# one of the currently-active admins, so removing someone else's admin
# status can never alone reach zero). These tests build an isolated group
# of freshly-registered admins so they never touch the seed admin the
# `headers` fixture depends on, and drive each account down to "last admin"
# through legitimate sequential API calls before proving the final
# self-action is blocked.

def _register_admin(headers, label):
    email = f"{label}_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    resp = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={"name": label, "email": email, "password": "Pass12345!", "role": "admin"},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    assert login.status_code == 200
    admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return user_id, admin_headers


def test_admin_cannot_deactivate_own_account(headers):
    user_id, own_headers = _register_admin(headers, "SelfDeactivate")

    resp = client.patch(f"/api/v1/users/{user_id}", headers=own_headers, json={"is_active": False})
    assert resp.status_code == 400

    listing = {u["id"]: u for u in client.get("/api/v1/users", headers=headers).json()}
    assert listing[user_id]["is_active"] is True


def test_admin_cannot_demote_own_role(headers):
    user_id, own_headers = _register_admin(headers, "SelfDemote")

    resp = client.patch(f"/api/v1/users/{user_id}", headers=own_headers, json={"role": "staff"})
    assert resp.status_code == 400

    listing = {u["id"]: u for u in client.get("/api/v1/users", headers=headers).json()}
    assert listing[user_id]["role"] == "admin"


def test_last_remaining_admin_cannot_deactivate_self(headers):
    """Two freshly-created admins, isolated from the seed account: one
    deactivates the other (allowed — a second admin still remains), leaving
    exactly one active admin standing, who must then be unable to
    deactivate themselves."""
    survivor_id, survivor_headers = _register_admin(headers, "LastAdminDeactA")
    other_id, _ = _register_admin(headers, "LastAdminDeactB")

    deact_other = client.patch(f"/api/v1/users/{other_id}", headers=survivor_headers, json={"is_active": False})
    assert deact_other.status_code == 200

    resp = client.patch(f"/api/v1/users/{survivor_id}", headers=survivor_headers, json={"is_active": False})
    assert resp.status_code == 400

    listing = {u["id"]: u for u in client.get("/api/v1/users", headers=headers).json()}
    assert listing[survivor_id]["is_active"] is True


def test_last_remaining_admin_cannot_demote_self(headers):
    survivor_id, survivor_headers = _register_admin(headers, "LastAdminDemoteA")
    other_id, _ = _register_admin(headers, "LastAdminDemoteB")

    deact_other = client.patch(f"/api/v1/users/{other_id}", headers=survivor_headers, json={"is_active": False})
    assert deact_other.status_code == 200

    resp = client.patch(f"/api/v1/users/{survivor_id}", headers=survivor_headers, json={"role": "staff"})
    assert resp.status_code == 400

    listing = {u["id"]: u for u in client.get("/api/v1/users", headers=headers).json()}
    assert listing[survivor_id]["role"] == "admin"


def test_admin_can_deactivate_other_admin_when_multiple_active_admins_remain(headers):
    """Regression: managing a DIFFERENT admin must still work normally as
    long as at least one active admin remains afterward."""
    actor_id, actor_headers = _register_admin(headers, "MultiAdminActor")
    target_id, _ = _register_admin(headers, "MultiAdminTarget")

    resp = client.patch(f"/api/v1/users/{target_id}", headers=actor_headers, json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_admin_can_demote_other_admin_when_multiple_active_admins_remain(headers):
    actor_id, actor_headers = _register_admin(headers, "MultiDemoteActor")
    target_id, _ = _register_admin(headers, "MultiDemoteTarget")

    resp = client.patch(f"/api/v1/users/{target_id}", headers=actor_headers, json={"role": "staff"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "staff"


def test_admin_can_still_manage_staff_users_normally(headers):
    """Regression: the new self/last-admin checks must not affect ordinary
    staff-user management at all."""
    email = f"staffmgmt_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    reg = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={"name": "Staff Managed", "email": email, "password": "Pass12345!", "role": "staff"},
    )
    user_id = reg.json()["id"]

    promote = client.patch(f"/api/v1/users/{user_id}", headers=headers, json={"role": "admin"})
    assert promote.status_code == 200

    deact = client.patch(f"/api/v1/users/{user_id}", headers=headers, json={"is_active": False})
    assert deact.status_code == 200
    assert deact.json()["is_active"] is False


def test_rejected_self_lockout_attempt_does_not_create_audit_log_entry(headers):
    user_id, own_headers = _register_admin(headers, "AuditNoLog")

    before = client.get("/api/v1/audit/logs", headers=headers, params={"entity_type": "user", "entity_id": user_id, "action": "user.update"})
    assert before.status_code == 200
    before_count = len(before.json())

    resp = client.patch(f"/api/v1/users/{user_id}", headers=own_headers, json={"is_active": False})
    assert resp.status_code == 400

    after = client.get("/api/v1/audit/logs", headers=headers, params={"entity_type": "user", "entity_id": user_id, "action": "user.update"})
    assert len(after.json()) == before_count


def test_unrelated_self_update_still_allowed(headers):
    """Precision check: self-editing your OWN account with a harmless,
    non-lockout-causing payload (re-affirming the admin role you already
    hold) must not be blocked — only actual deactivation/demotion is."""
    user_id, own_headers = _register_admin(headers, "SelfHarmless")

    resp = client.patch(f"/api/v1/users/{user_id}", headers=own_headers, json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
