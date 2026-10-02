"""
Tests for Audit Logs, Notification Engine, and Payment Gateway (Razorpay).
Run with: pytest tests/test_audit_notifications_gateway.py -v
"""
import hashlib
import hmac
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


# --- Audit logs ---

def test_settings_update_creates_audit_log(headers):
    client.put("/api/v1/settings", headers=headers, json={"tagline": f"Test tagline {uuid.uuid4().hex[:6]}"})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "settings.update"}).json()
    assert len(logs) >= 1
    assert logs[0]["action"] == "settings.update"


def test_user_role_change_creates_audit_log(headers):
    email = f"audit_test_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    user = client.post("/api/v1/auth/register", headers=headers, json={"name": "Audit Test", "email": email, "password": "Pass12345!", "role": "staff"}).json()
    client.patch(f"/api/v1/users/{user['id']}", headers=headers, json={"is_active": False})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "user.update"}).json()
    assert any(l["entity_id"] == user["id"] for l in logs)


def test_failed_login_is_logged():
    client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong_password_xyz"})
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    failed = client.get("/api/v1/audit/logins", headers=headers, params={"success": False}).json()
    assert any(e["email_attempted"] == ADMIN_EMAIL for e in failed)


def test_successful_login_is_logged(headers):
    events = client.get("/api/v1/audit/logins", headers=headers, params={"success": True}).json()
    assert len(events) >= 1


def test_non_admin_cannot_read_audit_logs():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"noaudit_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "N", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    assert client.get("/api/v1/audit/logs", headers=staff_headers).status_code == 403


# --- Notification engine ---

def test_manual_reminder_scan_runs_without_error(headers):
    resp = client.post("/api/v1/notifications/scan-reminders", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"checkin_reminder", "checkout_reminder", "review_request", "payment_reminder"}
    assert all(isinstance(v, int) for v in data.values())


def test_reminder_scan_sends_checkin_reminder_for_tomorrow_arrival(headers):
    from datetime import date, timedelta
    from app.db.session import SessionLocal
    from app.modules.properties.models import Property, Unit
    from app.modules.bookings.models import Guest, Booking

    db = SessionLocal()
    prop = Property(name=f"RemindProp {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
    db.add(prop)
    db.flush()
    unit = Unit(property_id=prop.id, name=f"RemindUnit {uuid.uuid4().hex[:6]}", base_rate=2000)
    db.add(unit)
    guest = Guest(name="Reminder Guest", phone=f"96{uuid.uuid4().int % 10**8:08d}", email="remindertest@example.com")
    db.add(guest)
    db.flush()
    booking = Booking(
        unit_id=unit.id, guest_id=guest.id,
        check_in=date.today() + timedelta(days=1), check_out=date.today() + timedelta(days=3),
        status="confirmed", source="direct", rate_applied=2000,
    )
    db.add(booking)
    db.commit()
    booking_id = booking.id
    db.close()

    resp = client.post("/api/v1/notifications/scan-reminders", headers=headers)
    assert resp.json()["checkin_reminder"] >= 1

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "checkin_reminder"}).json()
    assert any(l["booking_id"] == str(booking_id) for l in logs)

    client.post("/api/v1/notifications/scan-reminders", headers=headers)
    logs_after = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "checkin_reminder"}).json()
    matching = [l for l in logs_after if l["booking_id"] == str(booking_id)]
    assert len(matching) == 1


def test_notification_send_honestly_fails_without_smtp(monkeypatch):
    """
    Genuine defect found in a production-readiness audit: this test used
    to call the real send_email() with zero mocking, relying on the
    ambient environment happening to have no email provider configured.
    This container has EMAIL_PROVIDER=resend with a live API key (see
    test_cancellation_workflow.py's own docstring for the same fact) — so
    the un-mocked call actually took the Resend branch and made a REAL,
    live HTTP request to api.resend.com, which failed with a 422 from
    Resend's own validation (rejecting the test's example.com address) —
    a completely different exception than the EmailNotConfiguredError this
    test claims to verify, and a real external call this test must never
    make. Fixed by forcing the exact "not configured" precondition
    directly via settings, the same monkeypatch-settings pattern already
    used elsewhere in this file (see test_razorpay_order_creation_requires_configuration
    above) — this exercises the REAL _send_email_via_gmail_smtp code path
    and its real "if not settings.smtp_host: raise" guard, not a mocked
    stand-in, while guaranteeing the provider selection can never reach
    Resend regardless of what's actually configured in this environment —
    the exact same "verify the not-configured contract itself, not this
    sandbox's ambient credential state" pattern this same file already
    uses for Razorpay (see test_create_order_honestly_fails_without_credentials
    below). The httpx.post patch is a second, independent tripwire: if any
    future change ever made this reach a real network call anyway, the
    test fails loudly here instead of silently hitting a live API.
    """
    import httpx
    from unittest.mock import patch
    from app.core.config import settings
    from app.modules.notifications.service import send_email, EmailNotConfiguredError

    monkeypatch.setattr(settings, "email_provider", "gmail_smtp")
    monkeypatch.setattr(settings, "smtp_host", "")
    with patch.object(httpx, "post", side_effect=AssertionError("no real HTTP call should ever occur in this test")):
        with pytest.raises(EmailNotConfiguredError):
            send_email("test@example.com", "subject", "body")


def test_staff_can_view_notification_logs(headers):
    resp = client.get("/api/v1/notifications/logs", headers=headers)
    assert resp.status_code == 200


# --- Payment gateway (Razorpay) ---

def test_webhook_signature_verification_logic_is_correct():
    from app.modules.payment_gateway.service import verify_webhook_signature

    secret = "test_secret_123"
    payload = b'{"event":"payment.captured"}'
    correct_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(payload, correct_sig, webhook_secret=secret) is True
    assert verify_webhook_signature(payload, "wrong_signature", webhook_secret=secret) is False


def test_webhook_signature_verification_raises_when_not_configured():
    from app.modules.payment_gateway.service import verify_webhook_signature, GatewayNotConfiguredError
    with pytest.raises(GatewayNotConfiguredError):
        verify_webhook_signature(b"{}", "any_signature", webhook_secret="")


def test_webhook_endpoint_rejects_when_not_configured():
    resp = client.post(
        "/api/v1/payment-gateway/webhook",
        content=b'{"event":"payment.captured"}',
        headers={"X-Razorpay-Signature": "irrelevant", "Content-Type": "application/json"},
    )
    assert resp.status_code == 501


def test_create_order_honestly_fails_without_credentials(monkeypatch):
    from app.core.config import settings
    from app.modules.payment_gateway.service import create_order, GatewayNotConfiguredError
    # WEBSITE-04A: this test verifies the not-configured contract itself,
    # not this sandbox's ambient credential state — real RAZORPAY_KEY_ID/
    # SECRET are legitimately configured in this environment (WEBSITE-03),
    # so the not-configured path must be forced explicitly to stay
    # deterministic regardless of what's in the real environment.
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    with pytest.raises(GatewayNotConfiguredError):
        create_order(1000, "receipt_123")


def test_create_gateway_order_endpoint_returns_501_without_credentials(headers, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    resp = client.post("/api/v1/payment-gateway/orders", headers=headers, json={"amount": 1000, "receipt": "test_receipt"})
    assert resp.status_code == 501


def test_refund_exceeding_original_payment_rejected(headers):
    from app.db.session import SessionLocal
    from app.modules.properties.models import Property, Unit
    from app.modules.bookings.models import Guest, Booking
    from app.modules.payments.models import Payment
    from datetime import date, timedelta

    db = SessionLocal()
    prop = Property(name=f"RefundProp {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
    db.add(prop)
    db.flush()
    unit = Unit(property_id=prop.id, name=f"RefundUnit {uuid.uuid4().hex[:6]}", base_rate=2000)
    db.add(unit)
    guest = Guest(name="Refund Guest", phone=f"95{uuid.uuid4().int % 10**8:08d}")
    db.add(guest)
    db.flush()
    booking = Booking(unit_id=unit.id, guest_id=guest.id, check_in=date.today() + timedelta(days=30), check_out=date.today() + timedelta(days=32), status="confirmed", source="direct", rate_applied=2000)
    db.add(booking)
    db.flush()
    payment = Payment(
        booking_id=booking.id, amount=1000, mode="razorpay", status="completed",
        reference_no=f"pay_test_{uuid.uuid4().hex}",
    )
    db.add(payment)
    db.commit()
    payment_id = payment.id
    db.close()

    resp = client.post("/api/v1/payment-gateway/refunds", headers=headers, json={"payment_id": str(payment_id), "amount": 5000})
    assert resp.status_code == 400


def test_webhook_is_publicly_accessible_no_auth_needed():
    resp = client.post("/api/v1/payment-gateway/webhook", content=b"{}", headers={"X-Razorpay-Signature": "x"})
    assert resp.status_code != 401


def test_payment_reconciliation_queue_is_rbac_protected_and_sanitized(headers):
    from app.db.session import SessionLocal
    from app.modules.payment_gateway.models import WebhookEvent
    from app.modules.bookings.models import Booking, Guest
    from app.modules.payment_gateway.models import Refund
    from app.modules.payments.models import Payment
    from app.modules.properties.models import Property, Unit
    from datetime import date, timedelta

    secret_marker = "do-not-return-signature-or-raw-payload"
    payload = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_reconcile_fixture", "order_id": "missing_order_fixture",
            "amount": 125000, "currency": "INR", "status": "captured",
        }}, "private_marker": secret_marker},
    }
    with SessionLocal() as db:
        db.add(WebhookEvent(event_type="payment.captured", signature_valid=True, payload=__import__("json").dumps(payload), processed=False))
        prop = Property(name=f"ReconProp {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
        db.add(prop)
        db.flush()
        unit = Unit(property_id=prop.id, name=f"ReconUnit {uuid.uuid4().hex[:6]}", base_rate=2000)
        guest = Guest(name="Reconciliation Guest", phone=f"97{uuid.uuid4().int % 10**8:08d}")
        db.add_all([unit, guest])
        db.flush()
        booking = Booking(unit_id=unit.id, guest_id=guest.id, check_in=date.today() + timedelta(days=90), check_out=date.today() + timedelta(days=92), status="confirmed", source="direct", rate_applied=2000)
        db.add(booking)
        db.flush()
        payment = Payment(booking_id=booking.id, amount=1000, mode="razorpay", status="completed", reference_no="pay_ambiguous_fixture")
        db.add(payment)
        db.flush()
        unresolved_refund = Refund(payment_id=payment.id, amount=300, status="created", reason="timeout fixture")
        db.add(unresolved_refund)
        db.commit()

    response = client.get("/api/v1/payment-gateway/reconciliation", headers=headers)
    assert response.status_code == 200, response.text
    item = next(case for case in response.json() if case["event_or_payment_reference"] == "pay_reconcile_fixture")
    assert item["case_type"] == "webhook_event"
    assert item["amount"] == 1250
    assert item["status"] == "unresolved"
    assert "no matching OS10 order" in item["reason"]
    assert secret_marker not in response.text
    assert "payload" not in item and "signature" not in item
    refund_case = next(case for case in response.json() if case["case_type"] == "refund" and case["event_or_payment_reference"] == "pay_ambiguous_fixture")
    assert refund_case["status"] == "unresolved"
    assert refund_case["amount"] == 300
    assert "before any retry" in refund_case["reason"]
    assert client.get("/api/v1/payment-gateway/reconciliation").status_code == 401


def test_payment_reconciliation_queue_denies_staff(headers):
    email = f"reconcile_staff_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    registered = client.post("/api/v1/auth/register", headers=headers, json={"name": "Reconcile Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    assert registered.status_code == 201, registered.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/v1/payment-gateway/reconciliation", headers=staff_headers).status_code == 403
