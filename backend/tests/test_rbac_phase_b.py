"""
RBAC Phase B — Part 1 verification tests.

Covers: Manager's new operational access (mirroring Staff exactly, per
routes identified Allow in the Phase B audit), confirmation that every
Admin-only route still 403s for Manager, the new price-only
PATCH /units/{id}/pricing endpoint (Manager+Admin, Staff excluded,
extra="forbid" schema), Manager gaining SeasonalRate/LongStayDiscountTier
CRUD, the two refund audit-logging additions, and confirmation that
existing Admin/Staff behavior is unchanged.

Run with: pytest tests/test_rbac_phase_b.py -v
"""
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def admin_headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _register_and_login(admin_headers, role: str) -> dict:
    email = f"phaseb_{role}_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    password = f"PhaseB-{uuid.uuid4().hex[:12]}!"
    resp = client.post(
        "/api/v1/auth/register",
        headers=admin_headers,
        json={"name": f"Phase B {role} test", "email": email, "password": password, "role": role},
    )
    assert resp.status_code == 201, resp.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return {"user_id": resp.json()["id"], "headers": {"Authorization": f"Bearer {login.json()['access_token']}"}}


@pytest.fixture(scope="module")
def manager_ctx(admin_headers):
    return _register_and_login(admin_headers, "manager")


@pytest.fixture(scope="module")
def staff_ctx(admin_headers):
    return _register_and_login(admin_headers, "staff")


def _make_property_unit(admin_headers, base_rate=2500):
    prop = client.post("/api/v1/properties", headers=admin_headers, json={"name": f"PhaseBProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=admin_headers, json={"property_id": prop["id"], "name": f"PhaseBUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate}).json()
    return prop, unit


def _make_booking_with_payment(admin_headers, unit_id, rate=2500, nights=2, days_offset=9750, paid_amount=None):
    guest = client.post("/api/v1/guests", headers=admin_headers, json={"name": "Phase B Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    booking = client.post(
        "/api/v1/bookings",
        headers=admin_headers,
        json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=nights)).isoformat()},
    ).json()
    amount = paid_amount if paid_amount is not None else rate * nights
    payment = client.post("/api/v1/payments", headers=admin_headers, json={"booking_id": booking["id"], "amount": amount, "mode": "upi", "reference_no": "PHASEBTEST"}).json()
    return booking, payment


# ---------------------------------------------------------------------------
# A. Manager allowed operational routes (spot-check across every category)
# ---------------------------------------------------------------------------

def test_manager_allowed_operational_routes(admin_headers, manager_ctx):
    h = manager_ctx["headers"]
    prop, unit = _make_property_unit(admin_headers)

    assert client.get("/api/v1/reports/dashboard", headers=h).status_code == 200
    assert client.post("/api/v1/guests", headers=h, json={"name": "Mgr Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).status_code == 201
    booking_resp = client.post(
        "/api/v1/bookings", headers=h,
        json={"unit_id": unit["id"], "guest_id": client.get("/api/v1/guests", headers=h).json()[0]["id"],
              "check_in": (date.today() + timedelta(days=9760)).isoformat(), "check_out": (date.today() + timedelta(days=9762)).isoformat()},
    )
    assert booking_resp.status_code == 201
    booking_id = booking_resp.json()["id"]
    assert client.get("/api/v1/bookings", headers=h).status_code == 200
    assert client.post("/api/v1/payments", headers=h, json={"booking_id": booking_id, "amount": 5000, "mode": "upi", "reference_no": "MGRPAY"}).status_code == 201
    assert client.post(f"/api/v1/invoices/generate/{booking_id}", headers=h).status_code == 201
    inquiry_resp = client.post(
        "/api/v1/inquiries", headers=h,
        json={"client_name": "Mgr Inquiry", "client_phone": f"95{uuid.uuid4().int % 10**8:08d}", "flat_room_label": "Test",
              "check_in_date": "2029-05-01", "check_out_date": "2029-05-03", "rate_per_night": 2000, "advance_amount": 500},
    )
    assert inquiry_resp.status_code == 201
    assert client.post("/api/v1/housekeeping-tasks", headers=h, json={"unit_id": unit["id"], "task_type": "routine"}).status_code in (200, 201)
    assert client.post("/api/v1/maintenance-tickets", headers=h, json={"unit_id": unit["id"], "description": "Mgr ticket", "priority": "medium"}).status_code in (200, 201)
    assert client.get("/api/v1/ota/channels", headers=h).status_code == 200
    assert client.post("/api/v1/inventory/items", headers=h, json={"name": f"MgrItem {uuid.uuid4().hex[:6]}", "category": "kitchen_items"}).status_code == 201
    assert client.get("/api/v1/staff/dashboard", headers=h).status_code == 200
    assert client.get("/api/v1/notifications/inbox", headers=h).status_code == 200
    assert client.get("/api/v1/finance/deposits", headers=h).status_code == 200
    assert client.get("/api/v1/properties", headers=h).status_code == 200
    assert client.get("/api/v1/units", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# B. Manager denied Admin-only routes (broad spot-check)
# ---------------------------------------------------------------------------

def test_manager_denied_admin_only_routes(manager_ctx):
    h = manager_ctx["headers"]
    assert client.get("/api/v1/users", headers=h).status_code == 403
    assert client.get("/api/v1/audit/logs", headers=h).status_code == 403
    assert client.get("/api/v1/audit/logins", headers=h).status_code == 403
    assert client.get("/api/v1/settings/system-info", headers=h).status_code == 403
    assert client.get("/api/v1/settings/backup", headers=h).status_code == 403
    assert client.put("/api/v1/settings", headers=h, json={"company_name": "x"}).status_code == 403
    assert client.get("/api/v1/reports/ai-strategist", headers=h).status_code == 403
    assert client.post("/api/v1/promo-codes", headers=h, json={"code": "X", "discount_type": "percentage", "value": 10, "valid_from": "2030-01-01", "valid_to": "2030-12-31"}).status_code == 403
    assert client.post("/api/v1/notifications/scan-reminders", headers=h).status_code == 403
    assert client.post("/api/v1/notifications/scan-escalations", headers=h).status_code == 403
    assert client.post("/api/v1/finance/owner-withdrawals", headers=h, json={"amount": 100}).status_code == 403
    assert client.get("/api/v1/finance/owner-withdrawals", headers=h).status_code == 403
    assert client.get("/api/v1/finance/loans", headers=h).status_code == 403
    assert client.post("/api/v1/finance/budgets", headers=h, json={"amount": 100}).status_code == 403


# ---------------------------------------------------------------------------
# C. Manager cannot access BankSettings
# ---------------------------------------------------------------------------

def test_manager_cannot_read_or_write_bank_settings(manager_ctx):
    h = manager_ctx["headers"]
    assert client.get("/api/v1/settings/bank", headers=h).status_code == 403
    assert client.put("/api/v1/settings/bank", headers=h, json={"bank_name": "x"}).status_code == 403


# ---------------------------------------------------------------------------
# D. Manager cannot access staff payroll/profiles
# ---------------------------------------------------------------------------

def test_manager_cannot_access_payroll_or_staff_profiles(manager_ctx):
    h = manager_ctx["headers"]
    assert client.get("/api/v1/staff/payroll", headers=h).status_code == 403
    assert client.get("/api/v1/staff/profiles", headers=h).status_code == 403
    assert client.post("/api/v1/staff/profiles", headers=h, json={"user_id": str(uuid.uuid4())}).status_code == 403


# ---------------------------------------------------------------------------
# E. Manager cannot DELETE/archive/restore bookings
# ---------------------------------------------------------------------------

def test_manager_cannot_delete_archive_restore_bookings(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers)
    booking, _ = _make_booking_with_payment(admin_headers, unit["id"], days_offset=9770)
    h = manager_ctx["headers"]
    assert client.delete(f"/api/v1/bookings/{booking['id']}", headers=h).status_code == 403
    assert client.post(f"/api/v1/bookings/{booking['id']}/archive", headers=h).status_code == 403
    assert client.post(f"/api/v1/bookings/{booking['id']}/restore", headers=h).status_code == 403
    assert client.post("/api/v1/guests/merge", headers=h, json={"primary_guest_id": str(uuid.uuid4()), "duplicate_guest_id": str(uuid.uuid4())}).status_code == 403


# ---------------------------------------------------------------------------
# F. Manager cannot issue payment refunds
# ---------------------------------------------------------------------------

def test_manager_cannot_issue_refunds(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers)
    _, payment = _make_booking_with_payment(admin_headers, unit["id"], days_offset=9780)
    h = manager_ctx["headers"]
    assert client.post(f"/api/v1/payments/{payment['id']}/refund", headers=h, json={"amount": 100}).status_code == 403
    assert client.post("/api/v1/payment-gateway/refunds", headers=h, json={"payment_id": payment["id"], "amount": 100}).status_code == 403


# ---------------------------------------------------------------------------
# G. Manager cannot create credit notes
# ---------------------------------------------------------------------------

def test_manager_cannot_create_credit_notes(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers)
    booking, _ = _make_booking_with_payment(admin_headers, unit["id"], days_offset=9790)
    invoice = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=admin_headers).json()
    h = manager_ctx["headers"]
    assert client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=h, json={"amount": 100, "reason": "test"}).status_code == 403


# ---------------------------------------------------------------------------
# H. Manager cannot modify OTA configuration
# ---------------------------------------------------------------------------

def test_manager_cannot_modify_ota_configuration(manager_ctx):
    h = manager_ctx["headers"]
    assert client.patch("/api/v1/ota/channels/booking_com", headers=h, json={"sync_enabled": True}).status_code == 403
    assert client.post("/api/v1/ota/mappings", headers=h, json={"channel": "booking_com", "unit_id": str(uuid.uuid4()), "external_listing_id": "x"}).status_code == 403
    # But Manager CAN view + trigger sync — confirming the boundary is precise, not a blanket OTA deny
    assert client.get("/api/v1/ota/channels", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# I. Manager can create/edit/delete SeasonalRate
# ---------------------------------------------------------------------------

def test_manager_can_manage_seasonal_rates(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers)
    h = manager_ctx["headers"]
    create = client.post(
        f"/api/v1/units/{unit['id']}/seasonal-rates", headers=h,
        json={"name": "Mgr Season", "start_date": "2030-12-20", "end_date": "2030-12-31", "rate_per_night": 5000},
    )
    assert create.status_code == 201, create.text
    rate_id = create.json()["id"]
    assert client.get(f"/api/v1/units/{unit['id']}/seasonal-rates", headers=h).status_code == 200
    assert client.delete(f"/api/v1/units/{unit['id']}/seasonal-rates/{rate_id}", headers=h).status_code == 204


# ---------------------------------------------------------------------------
# J. Manager can create/edit/delete LongStayDiscountTier
# ---------------------------------------------------------------------------

def test_manager_can_manage_discount_tiers(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers)
    h = manager_ctx["headers"]
    create = client.post(f"/api/v1/units/{unit['id']}/long-stay-discounts", headers=h, json={"min_nights": 7, "discount_percent": 10})
    assert create.status_code == 201, create.text
    tier_id = create.json()["id"]
    assert client.get(f"/api/v1/units/{unit['id']}/long-stay-discounts", headers=h).status_code == 200
    assert client.delete(f"/api/v1/units/{unit['id']}/long-stay-discounts/{tier_id}", headers=h).status_code == 204


# ---------------------------------------------------------------------------
# K/L/M. New pricing-only endpoint
# ---------------------------------------------------------------------------

def test_manager_can_update_base_and_weekend_rate_via_pricing_endpoint(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers, base_rate=2500)
    h = manager_ctx["headers"]
    resp = client.patch(f"/api/v1/units/{unit['id']}/pricing", headers=h, json={"base_rate": 3000, "weekend_rate": 3500})
    assert resp.status_code == 200, resp.text
    assert resp.json()["base_rate"] == 3000
    assert resp.json()["weekend_rate"] == 3500


def test_manager_cannot_update_unit_identity_via_pricing_endpoint(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers)
    h = manager_ctx["headers"]
    resp = client.patch(f"/api/v1/units/{unit['id']}/pricing", headers=h, json={"base_rate": 3000, "name": "Hacked Name"})
    assert resp.status_code == 422, resp.text

    # Confirm the name genuinely wasn't changed even though base_rate was in the same rejected payload
    current = client.get("/api/v1/units", headers=h).json()
    matching = [u for u in current if u["id"] == unit["id"]]
    assert matching[0]["name"] == unit["name"]


def test_unexpected_pricing_fields_rejected(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers)
    h = manager_ctx["headers"]
    resp = client.patch(f"/api/v1/units/{unit['id']}/pricing", headers=h, json={"status": "maintenance"})
    assert resp.status_code == 422
    resp2 = client.patch(f"/api/v1/units/{unit['id']}/pricing", headers=h, json={"totally_unknown_field": 123})
    assert resp2.status_code == 422


def test_staff_cannot_use_new_pricing_endpoint(admin_headers, staff_ctx):
    """Staff never had unit-price-edit access before Phase B; the new
    endpoint must not silently grant it."""
    _, unit = _make_property_unit(admin_headers)
    resp = client.patch(f"/api/v1/units/{unit['id']}/pricing", headers=staff_ctx["headers"], json={"base_rate": 3000})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# N. Pricing changes create correct audit entries with actor_role="manager"
# ---------------------------------------------------------------------------

def test_pricing_change_audit_entry_has_manager_actor_role(admin_headers, manager_ctx):
    _, unit = _make_property_unit(admin_headers, base_rate=2000)
    h = manager_ctx["headers"]
    resp = client.patch(f"/api/v1/units/{unit['id']}/pricing", headers=h, json={"base_rate": 2750})
    assert resp.status_code == 200

    logs = client.get(
        "/api/v1/audit/logs", headers=admin_headers, params={"entity_type": "unit", "entity_id": unit["id"], "action": "unit.pricing_update"}
    ).json()
    assert len(logs) == 1
    entry = logs[0]
    assert entry["actor_role"] == "manager"
    assert entry["user_id"] == manager_ctx["user_id"]
    assert "base_rate=2000" in entry["previous_value"]
    assert "base_rate=2750.0" in entry["new_value"] or "base_rate=2750" in entry["new_value"]


# ---------------------------------------------------------------------------
# O. Payment-gateway refund creates an audit entry
# ---------------------------------------------------------------------------

@pytest.fixture()
def _mock_create_refund(monkeypatch):
    # Phase 2 (payment/refund safety): issue_refund() now reads the real
    # Razorpay response's "id" to populate Refund.gateway_refund_id (the
    # field the new ux_refunds_gateway_refund_id uniqueness guarantee is
    # built on) — a plain `return_value=None` would make that a NoneType
    # AttributeError instead of exercising the audit-log path this test is
    # actually about.
    mock = MagicMock(return_value={"id": f"rfnd_test_{uuid.uuid4().hex}", "status": "processed"})
    monkeypatch.setattr("app.modules.payment_gateway.router.create_refund", mock)
    return mock


def test_payment_gateway_refund_creates_audit_entry(admin_headers, _mock_create_refund):
    _, unit = _make_property_unit(admin_headers)
    _, payment = _make_booking_with_payment(admin_headers, unit["id"], days_offset=9800, paid_amount=1000)
    # Model a captured provider payment at the persistence boundary. The
    # general payment endpoint intentionally represents manual-ledger modes;
    # gateway refunds require both Razorpay mode and provider payment ID.
    from app.db.session import SessionLocal
    from app.modules.payments.models import Payment

    db = SessionLocal()
    try:
        gateway_payment = db.get(Payment, uuid.UUID(payment["id"]))
        gateway_payment.mode = "razorpay"
        gateway_payment.reference_no = f"pay_test_{uuid.uuid4().hex}"
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/v1/payment-gateway/refunds", headers=admin_headers, json={"payment_id": payment["id"], "amount": 250, "reason": "audit test"})
    assert resp.status_code == 201, resp.text
    refund_id = resp.json()["id"]

    logs = client.get(
        "/api/v1/audit/logs", headers=admin_headers, params={"entity_type": "refund", "entity_id": refund_id, "action": "payment_gateway.refund"}
    ).json()
    assert len(logs) == 1
    assert logs[0]["actor_role"] == "admin"
    assert "refund_amount=250" in logs[0]["new_value"]


# ---------------------------------------------------------------------------
# P. Security-deposit refund creates an audit entry (with structured values)
# ---------------------------------------------------------------------------

def test_security_deposit_refund_creates_audit_entry(admin_headers):
    _, unit = _make_property_unit(admin_headers)
    booking, _ = _make_booking_with_payment(admin_headers, unit["id"], days_offset=9810, paid_amount=0)
    deposit = client.post("/api/v1/finance/deposits", headers=admin_headers, json={"booking_id": booking["id"], "amount": 2000}).json()

    resp = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=admin_headers, json={"refund_amount": 500})
    assert resp.status_code == 200, resp.text

    logs = client.get(
        "/api/v1/audit/logs", headers=admin_headers, params={"entity_type": "security_deposit", "entity_id": deposit["id"], "action": "finance.deposit.refund"}
    ).json()
    assert len(logs) == 1
    assert "refund_amount=0" in logs[0]["previous_value"] or "refund_amount=0.0" in logs[0]["previous_value"]
    assert "refund_amount=500" in logs[0]["new_value"]


# ---------------------------------------------------------------------------
# Q. Existing Admin behavior remains unchanged
# ---------------------------------------------------------------------------

def test_existing_admin_behavior_unchanged(admin_headers):
    assert client.get("/api/v1/users", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/settings/bank", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/audit/logs", headers=admin_headers).status_code == 200
    _, unit = _make_property_unit(admin_headers)
    assert client.patch(f"/api/v1/units/{unit['id']}", headers=admin_headers, json={"name": "Admin Renamed"}).status_code == 200
    assert client.patch(f"/api/v1/units/{unit['id']}/pricing", headers=admin_headers, json={"base_rate": 4000}).status_code == 200


# ---------------------------------------------------------------------------
# R. Existing Staff behavior remains unchanged
# ---------------------------------------------------------------------------

def test_existing_staff_behavior_unchanged(admin_headers, staff_ctx):
    h = staff_ctx["headers"]
    assert client.get("/api/v1/users", headers=h).status_code == 403
    assert client.get("/api/v1/settings/bank", headers=h).status_code == 403
    _, unit = _make_property_unit(admin_headers)
    assert client.patch(f"/api/v1/units/{unit['id']}", headers=h, json={"name": "x"}).status_code == 403
    assert client.get("/api/v1/bookings", headers=h).status_code == 200
    assert client.post("/api/v1/guests", headers=h, json={"name": "Staff Guest", "phone": f"96{uuid.uuid4().int % 10**8:08d}"}).status_code == 201
    create = client.post(
        f"/api/v1/units/{unit['id']}/seasonal-rates", headers=h,
        json={"name": "Staff Season", "start_date": "2030-11-01", "end_date": "2030-11-10", "rate_per_night": 4000},
    )
    assert create.status_code == 403, "Staff must not gain seasonal-rate create access as a side effect of granting it to Manager"


# ---------------------------------------------------------------------------
# S. Security-deposit refund authorization (finding I-1 fix)
#
# POST /finance/deposits/{deposit_id}/refund was mechanically swept into the
# "admin, staff" -> "admin, staff, manager" edit made elsewhere in this
# router during Phase B, but issuing a refund is explicitly out of scope for
# Manager (same policy as /payments/{id}/refund and /payment-gateway/refunds
# in section F above). Restored to admin+staff only; these tests pin that
# down explicitly since section F never covered this specific route.
# ---------------------------------------------------------------------------

def test_manager_cannot_refund_security_deposit(manager_ctx):
    # A random, never-created deposit id is enough here: require_role(...)
    # runs as a route dependency and rejects the caller with 403 before the
    # handler body ever looks up a deposit, so this never touches real data
    # and never needs a genuine deposit to exist.
    fake_deposit_id = uuid.uuid4()
    resp = client.post(f"/api/v1/finance/deposits/{fake_deposit_id}/refund", headers=manager_ctx["headers"], json={"refund_amount": 100})
    assert resp.status_code == 403, resp.text


def test_admin_can_still_refund_security_deposit(admin_headers):
    _, unit = _make_property_unit(admin_headers)
    booking, _ = _make_booking_with_payment(admin_headers, unit["id"], days_offset=9820, paid_amount=0)
    deposit = client.post("/api/v1/finance/deposits", headers=admin_headers, json={"booking_id": booking["id"], "amount": 1000}).json()

    resp = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=admin_headers, json={"refund_amount": 250})
    assert resp.status_code == 200, resp.text


def test_staff_can_still_refund_security_deposit(admin_headers, staff_ctx):
    # Staff's refund access predates Manager's existence and Phase B never
    # intended to touch it — this pins down that the fix (removing "manager"
    # from this route's allow-list) left "staff" exactly as it was.
    _, unit = _make_property_unit(admin_headers)
    booking, _ = _make_booking_with_payment(admin_headers, unit["id"], days_offset=9830, paid_amount=0)
    deposit = client.post("/api/v1/finance/deposits", headers=admin_headers, json={"booking_id": booking["id"], "amount": 1000}).json()

    resp = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=staff_ctx["headers"], json={"refund_amount": 250})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# T. Notification log HR-event exclusion for Manager (finding I-2 fix)
#
# GET /notifications/logs is the system-wide, unscoped notification log
# (unlike /inbox, which is self-scoped to the caller). Two event_type values
# reveal HR/payroll activity about OTHER staff members: "leave_reviewed" and
# "payroll_paid" (see trigger_leave_reviewed/trigger_payroll_paid in
# notifications/service.py). Manager must not see either, even via the
# endpoint's existing optional ?event_type= filter; Admin and Staff must be
# completely unaffected; every other event_type must stay visible to Manager;
# /inbox must stay untouched and self-scoped regardless of role.
# ---------------------------------------------------------------------------

def _insert_notification_log(event_type: str, user_id=None):
    from app.db.session import SessionLocal
    from app.modules.notifications.models import NotificationLog

    db = SessionLocal()
    try:
        log = NotificationLog(
            event_type=event_type, channel="email",
            recipient=f"{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com",
            user_id=user_id, success=True,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log.id
    finally:
        db.close()


def _event_types_in(resp_json) -> set:
    return {row["event_type"] for row in resp_json}


def test_admin_sees_leave_reviewed_and_payroll_paid(admin_headers):
    _insert_notification_log("leave_reviewed")
    _insert_notification_log("payroll_paid")
    resp = client.get("/api/v1/notifications/logs", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    types = _event_types_in(resp.json())
    assert "leave_reviewed" in types
    assert "payroll_paid" in types


def test_staff_sees_leave_reviewed_and_payroll_paid(staff_ctx):
    _insert_notification_log("leave_reviewed")
    _insert_notification_log("payroll_paid")
    resp = client.get("/api/v1/notifications/logs", headers=staff_ctx["headers"])
    assert resp.status_code == 200, resp.text
    types = _event_types_in(resp.json())
    assert "leave_reviewed" in types
    assert "payroll_paid" in types


def test_manager_does_not_see_leave_reviewed_or_payroll_paid(manager_ctx):
    _insert_notification_log("leave_reviewed")
    _insert_notification_log("payroll_paid")
    resp = client.get("/api/v1/notifications/logs", headers=manager_ctx["headers"])
    assert resp.status_code == 200, resp.text
    types = _event_types_in(resp.json())
    assert "leave_reviewed" not in types
    assert "payroll_paid" not in types


def test_manager_still_sees_non_hr_events(manager_ctx):
    _insert_notification_log("booking_confirmation")
    resp = client.get("/api/v1/notifications/logs", headers=manager_ctx["headers"])
    assert resp.status_code == 200, resp.text
    assert "booking_confirmation" in _event_types_in(resp.json())


def test_manager_cannot_bypass_exclusion_with_event_type_filter(manager_ctx):
    _insert_notification_log("payroll_paid")
    _insert_notification_log("leave_reviewed")
    h = manager_ctx["headers"]

    resp = client.get("/api/v1/notifications/logs", headers=h, params={"event_type": "payroll_paid"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    resp = client.get("/api/v1/notifications/logs", headers=h, params={"event_type": "leave_reviewed"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_notification_inbox_unchanged_and_self_scoped(manager_ctx, staff_ctx):
    manager_user_id = uuid.UUID(manager_ctx["user_id"])
    own_log_id = _insert_notification_log("payroll_paid", user_id=manager_user_id)
    # A different (real) user's payroll_paid notification must never appear
    # in the Manager's own /inbox, regardless of the /logs exclusion above.
    _insert_notification_log("payroll_paid", user_id=uuid.UUID(staff_ctx["user_id"]))

    resp = client.get("/api/v1/notifications/inbox", headers=manager_ctx["headers"])
    assert resp.status_code == 200, resp.text
    inbox_ids = {row["id"] for row in resp.json()}
    inbox_types = _event_types_in(resp.json())
    # /inbox is self-scoped by user_id, not filtered by event_type — a
    # Manager's OWN payroll_paid notification (about themselves) must still
    # reach their own inbox unaffected by the /logs exclusion.
    assert str(own_log_id) in inbox_ids
    assert "payroll_paid" in inbox_types
    assert len(resp.json()) == 1, "inbox must contain only this Manager's own notification, not the other user's"


# ---------------------------------------------------------------------------
# U. /settings/roles-permissions documentation accuracy (finding I-4 fix)
#
# GET /settings/roles-permissions is a purely informational, hardcoded
# endpoint (no DB query, no relationship to require_role(...) or the
# Permission table) — its own docstring had drifted to describe only "the
# two roles" from before Manager existed. These tests confirm the content
# now represents Manager accurately, that Admin/Staff's existing entries are
# unchanged (aside from the "delete" -> "deactivate" wording correction),
# and — most importantly — that this content-only change has zero effect on
# real authorization: Manager still gets 403 from this endpoint itself and
# from every Admin-only route, exactly as before.
# ---------------------------------------------------------------------------

def _role_entry(roles_payload, role_name):
    return next((r for r in roles_payload if r["role"] == role_name), None)


def test_roles_permissions_admin_sees_manager_entry(admin_headers):
    resp = client.get("/api/v1/settings/roles-permissions", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    manager_entry = _role_entry(resp.json()["roles"], "manager")
    assert manager_entry is not None
    assert "pricing" in " ".join(manager_entry["can_do"]).lower()
    assert "bank" in " ".join(manager_entry["cannot_do"]).lower()
    assert "credit note" in " ".join(manager_entry["cannot_do"]).lower()


def test_roles_permissions_staff_sees_manager_entry(staff_ctx):
    resp = client.get("/api/v1/settings/roles-permissions", headers=staff_ctx["headers"])
    assert resp.status_code == 200, resp.text
    manager_entry = _role_entry(resp.json()["roles"], "manager")
    assert manager_entry is not None
    assert "pricing" in " ".join(manager_entry["can_do"]).lower()


def test_roles_permissions_manager_still_denied(manager_ctx):
    resp = client.get("/api/v1/settings/roles-permissions", headers=manager_ctx["headers"])
    assert resp.status_code == 403, resp.text


def test_roles_permissions_admin_entry_unchanged(admin_headers):
    resp = client.get("/api/v1/settings/roles-permissions", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    admin_entry = _role_entry(resp.json()["roles"], "admin")
    assert admin_entry is not None
    assert admin_entry["cannot_do"] == []
    can_do_text = " ".join(admin_entry["can_do"])
    assert "deactivate" in can_do_text
    assert "delete" not in can_do_text.lower()
    assert "issue refunds and credit notes" in can_do_text


def test_roles_permissions_staff_entry_unchanged(admin_headers):
    resp = client.get("/api/v1/settings/roles-permissions", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    staff_entry = _role_entry(resp.json()["roles"], "staff")
    assert staff_entry is not None
    assert staff_entry["can_do"] == [
        "Create and manage bookings, guests, inquiries, housekeeping and maintenance tasks",
        "Record payments and generate invoices/quotations",
        "View reports, dashboards, and their own notifications",
        "Update their own attendance and leave requests",
    ]
    assert staff_entry["cannot_do"] == [
        "Approve/reject expenses or mark payroll as paid",
        "Create staff accounts or change another user's role",
        "Edit business settings or upload logo/QR",
        "Issue credit notes",
    ]


def test_roles_permissions_endpoint_has_no_authorization_side_effect(admin_headers, manager_ctx):
    h = manager_ctx["headers"]
    # Calling the (Manager-inaccessible) informational endpoint...
    assert client.get("/api/v1/settings/roles-permissions", headers=h).status_code == 403
    # ...has no bearing on real enforcement — every actual Admin-only
    # boundary for Manager remains exactly as it was.
    assert client.get("/api/v1/settings/bank", headers=h).status_code == 403
    assert client.get("/api/v1/users", headers=h).status_code == 403
    assert client.get("/api/v1/audit/logs", headers=h).status_code == 403
    # And an Admin call to the fixed endpoint still works normally.
    assert client.get("/api/v1/settings/roles-permissions", headers=admin_headers).status_code == 200


# ---------------------------------------------------------------------------
# V. Permission table remains dormant (finding I-5 documentation fix)
#
# These tests don't test any new behavior — the I-5 fix is comments/
# docstrings only. They exist to pin down, and catch future regression of,
# the exact claim those docstrings now make: the Permission table is seeded
# but never consulted by require_role(...) or any other runtime
# authorization code. If someone ever wires this table into enforcement
# without updating these tests, they should fail loudly.
# ---------------------------------------------------------------------------

def _permission_rows_for_role(role_name: str) -> set:
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.modules.auth.models import Permission, Role

    db = SessionLocal()
    try:
        role = db.scalar(select(Role).where(Role.name == role_name))
        rows = db.scalars(select(Permission).where(Permission.role_id == role.id)).all()
        return {(p.resource, p.action) for p in rows}
    finally:
        db.close()


def test_manager_permission_row_count_remains_zero():
    assert _permission_rows_for_role("manager") == set()


def test_admin_permission_rows_remain_wildcard_only():
    assert _permission_rows_for_role("admin") == {("*", "*")}


def test_staff_permission_rows_remain_the_existing_six():
    assert _permission_rows_for_role("staff") == {
        ("bookings", "create"), ("bookings", "read"), ("bookings", "update"),
        ("guests", "create"), ("guests", "read"), ("units", "read"),
    }


def test_removing_permission_rows_does_not_change_staff_authorization(admin_headers, staff_ctx):
    from sqlalchemy import delete, select
    from app.db.session import SessionLocal
    from app.modules.auth.models import Permission, Role

    db = SessionLocal()
    try:
        role = db.scalar(select(Role).where(Role.name == "staff"))
        db.execute(delete(Permission).where(Permission.role_id == role.id))
        db.commit()
    finally:
        db.close()

    try:
        # With every Permission row for "staff" gone, require_role(...)
        # must still grant exactly the same access as before — it never
        # looked at these rows in the first place.
        h = staff_ctx["headers"]
        assert client.get("/api/v1/bookings", headers=h).status_code == 200
        assert client.post(
            "/api/v1/guests", headers=h,
            json={"name": "I-5 Staff Guest", "phone": f"97{uuid.uuid4().int % 10**8:08d}"},
        ).status_code == 201
        assert client.get("/api/v1/settings/bank", headers=h).status_code == 403
    finally:
        # Restore the deleted rows so later tests (and this module-scoped
        # staff_ctx) see the normal, documented seeded state again.
        from sqlalchemy import select

        db = SessionLocal()
        try:
            role = db.scalar(select(Role).where(Role.name == "staff"))
            for resource, action in [
                ("bookings", "create"), ("bookings", "read"), ("bookings", "update"),
                ("guests", "create"), ("guests", "read"), ("units", "read"),
            ]:
                db.add(Permission(role_id=role.id, resource=resource, action=action))
            db.commit()
        finally:
            db.close()


def test_permission_rows_not_consulted_by_manager_authorization(manager_ctx):
    # Manager already has zero Permission rows (see
    # test_manager_permission_row_count_remains_zero above) yet has real,
    # substantial operational access via require_role(...) alone — the
    # clearest possible proof that these two things are unrelated.
    h = manager_ctx["headers"]
    assert _permission_rows_for_role("manager") == set()
    assert client.get("/api/v1/bookings", headers=h).status_code == 200
    assert client.get("/api/v1/reports/dashboard", headers=h).status_code == 200
