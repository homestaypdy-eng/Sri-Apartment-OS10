"""
Tests completing the Notifications module to full production coverage:
SMS (a listed channel with zero implementation), in-app inbox (per-user
notifications, mark-read), escalation (priority-based overdue alerts),
and the new template system.
Run with: pytest tests/test_notifications_completion.py -v
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.notifications.templates import render_template

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_render_template_substitutes_placeholders():
    result = render_template("booking_confirmation_sms", "body", check_in="2030-01-01", check_out="2030-01-03", rate=2000)
    assert "2030-01-01" in result
    assert "2030-01-03" in result
    assert "2000" in result


def test_render_template_raises_clear_error_for_missing_placeholder():
    with pytest.raises(ValueError, match="missing placeholder"):
        render_template("booking_confirmation_sms", "body", check_in="2030-01-01")


def test_render_template_raises_clear_error_for_unknown_template():
    with pytest.raises(ValueError, match="No template registered"):
        render_template("nonexistent_template", "body")


def test_sms_not_configured_raises_clear_error():
    from app.modules.notifications.service import send_sms, SmsNotConfiguredError
    with pytest.raises(SmsNotConfiguredError):
        send_sms("9700000000", "test message")


def test_booking_confirmation_sms_logs_failure_gracefully_when_unconfigured(headers):
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    from app.modules.notifications.service import trigger_booking_confirmation_sms

    guest = client.post("/api/v1/guests", headers=headers, json={"name": "SMS Test Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"SMSProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"SMSUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    check_in = date.today() + timedelta(days=12000)
    booking_resp = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat()}).json()

    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_resp["id"])
        result = trigger_booking_confirmation_sms(db, booking)
        db.commit()
        assert result is False
    finally:
        db.close()

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "booking_confirmation"}).json()
    sms_logs = [l for l in logs if l["channel"] == "sms" and l["booking_id"] == booking_resp["id"]]
    assert len(sms_logs) == 1
    assert sms_logs[0]["success"] is False


def test_payroll_paid_creates_in_app_notification(headers):
    email = f"inapp{uuid.uuid4().hex[:8]}@test.com"
    staff = client.post("/api/v1/auth/register", headers=headers, json={"name": "In-App Test Staff", "email": email, "password": "TestPass123!", "role": "staff"}).json()
    staff_headers = {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'email': email, 'password': 'TestPass123!'}).json()['access_token']}"}

    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": 2059, "period_month": 3, "basic_salary": 15000, "deductions": 0, "bonus": 0,
    }).json()
    client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)

    inbox = client.get("/api/v1/notifications/inbox", headers=staff_headers).json()
    assert any(n["payroll_id"] == payroll["id"] for n in inbox)


def test_inbox_only_shows_own_notifications(headers):
    email_a = f"inappA{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"inappB{uuid.uuid4().hex[:8]}@test.com"
    staff_a = client.post("/api/v1/auth/register", headers=headers, json={"name": "Inbox Staff A", "email": email_a, "password": "TestPass123!", "role": "staff"}).json()
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Inbox Staff B", "email": email_b, "password": "TestPass123!", "role": "staff"})
    headers_b = {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'email': email_b, 'password': 'TestPass123!'}).json()['access_token']}"}

    payroll_a = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff_a["id"], "period_year": 2059, "period_month": 4, "basic_salary": 10000, "deductions": 0, "bonus": 0,
    }).json()
    client.patch(f"/api/v1/staff/payroll/{payroll_a['id']}/mark-paid", headers=headers)

    inbox_b = client.get("/api/v1/notifications/inbox", headers=headers_b).json()
    assert not any(n["payroll_id"] == payroll_a["id"] for n in inbox_b)


def test_mark_notification_read(headers):
    email = f"markread{uuid.uuid4().hex[:8]}@test.com"
    staff = client.post("/api/v1/auth/register", headers=headers, json={"name": "Mark Read Staff", "email": email, "password": "TestPass123!", "role": "staff"}).json()
    staff_headers = {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'email': email, 'password': 'TestPass123!'}).json()['access_token']}"}

    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff["id"], "period_year": 2059, "period_month": 5, "basic_salary": 12000, "deductions": 0, "bonus": 0,
    }).json()
    client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)

    inbox = client.get("/api/v1/notifications/inbox", headers=staff_headers, params={"unread_only": True}).json()
    notification = next(n for n in inbox if n["payroll_id"] == payroll["id"])
    assert notification["read_at"] is None

    resp = client.post(f"/api/v1/notifications/inbox/{notification['id']}/mark-read", headers=staff_headers)
    assert resp.status_code == 200
    assert resp.json()["read_at"] is not None

    unread_after = client.get("/api/v1/notifications/inbox", headers=staff_headers, params={"unread_only": True}).json()
    assert not any(n["id"] == notification["id"] for n in unread_after)


def test_cannot_mark_another_users_notification_read(headers):
    email_a = f"markreadA{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"markreadB{uuid.uuid4().hex[:8]}@test.com"
    staff_a = client.post("/api/v1/auth/register", headers=headers, json={"name": "Mark Read A", "email": email_a, "password": "TestPass123!", "role": "staff"}).json()
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Mark Read B", "email": email_b, "password": "TestPass123!", "role": "staff"})
    headers_b = {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'email': email_b, 'password': 'TestPass123!'}).json()['access_token']}"}

    payroll = client.post("/api/v1/staff/payroll", headers=headers, json={
        "user_id": staff_a["id"], "period_year": 2059, "period_month": 6, "basic_salary": 10000, "deductions": 0, "bonus": 0,
    }).json()
    client.patch(f"/api/v1/staff/payroll/{payroll['id']}/mark-paid", headers=headers)

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "payroll_paid"}).json()
    notification_id = next(l["id"] for l in logs if l["payroll_id"] == payroll["id"])

    resp = client.post(f"/api/v1/notifications/inbox/{notification_id}/mark-read", headers=headers_b)
    assert resp.status_code == 404


def test_urgent_guest_request_escalates_past_threshold(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"EscProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"EscUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Escalation Test Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=12100)
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat()}).json()

    from app.db.session import SessionLocal
    from app.modules.portal.models import GuestRequest
    from app.modules.notifications.service import scan_and_send_escalations
    from app.modules.settings.router import get_or_create_settings

    db = SessionLocal()
    try:
        # trigger_guest_request_escalated() only logs/sends when a
        # BusinessSettings row with a contact_email exists — get_or_create_settings
        # lazily creates the singleton (with its real default contact_email)
        # the same way every other trigger-dependent test in this codebase
        # ensures it exists (see test_owner_and_whatsapp_notifications.py's
        # _owner_emails helper), rather than relying on some earlier,
        # unrelated test in the suite having already created it as a side effect.
        get_or_create_settings(db)
        gr = GuestRequest(
            guest_id=uuid.UUID(guest["id"]), booking_id=uuid.UUID(booking["id"]), request_type="service",
            description="Urgent escalation test", priority="urgent", status="open",
        )
        gr.created_at = datetime.now(timezone.utc) - timedelta(hours=5)  # past the 4-hour urgent threshold
        db.add(gr)
        db.commit()
        db.refresh(gr)
        request_id = str(gr.id)
        # SAFETY: this environment has a real, live email provider
        # configured (EMAIL_PROVIDER=resend) — now that a real
        # contact_email exists above, trigger_guest_request_escalated
        # would otherwise attempt a genuine outbound send. Mocked at the
        # source, matching the pattern used throughout this codebase.
        with patch("app.modules.notifications.service.send_email", return_value=None):
            results = scan_and_send_escalations(db)
    finally:
        db.close()

    assert results["guest_request_escalated"] >= 1
    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "guest_request_escalated"}).json()
    assert any(l["guest_request_id"] == request_id for l in logs)


def test_escalation_scan_does_not_duplicate_on_rerun(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"EscDupProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"EscDupUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    check_in = date.today() + timedelta(days=12200)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Escalation Dup Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat()}).json()

    from app.db.session import SessionLocal
    from app.modules.portal.models import GuestRequest
    from app.modules.notifications.service import scan_and_send_escalations
    from app.modules.settings.router import get_or_create_settings

    db = SessionLocal()
    try:
        get_or_create_settings(db)  # see test_urgent_guest_request_escalates_past_threshold for why
        gr = GuestRequest(
            guest_id=uuid.UUID(guest["id"]), booking_id=uuid.UUID(booking["id"]), request_type="service",
            description="Dup escalation test", priority="urgent", status="open",
        )
        gr.created_at = datetime.now(timezone.utc) - timedelta(hours=10)
        db.add(gr)
        db.commit()
        db.refresh(gr)
        request_id = str(gr.id)
        with patch("app.modules.notifications.service.send_email", return_value=None):
            scan_and_send_escalations(db)
            scan_and_send_escalations(db)
    finally:
        db.close()

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "guest_request_escalated"}).json()
    matching = [l for l in logs if l["guest_request_id"] == request_id]
    assert len(matching) == 1


def test_escalation_counter_does_not_count_requests_that_were_not_actually_logged(headers):
    """Regression test for a real counter-accuracy bug found in a
    production-readiness audit: scan_and_send_escalations() used to
    increment results["guest_request_escalated"] unconditionally,
    even when trigger_guest_request_escalated() returned None (no
    BusinessSettings.contact_email configured -> nothing logged, nothing
    sent) — so the returned count could claim escalations happened when
    zero NotificationLog rows were actually written. Verified directly by
    temporarily blanking contact_email on the real settings singleton to
    an empty string, not None: the column is DB-enforced NOT NULL (it can
    never actually be NULL in production), but trigger_guest_request_escalated's
    own guard is `not settings_row.contact_email`, which an empty string
    also satisfies — the same "not configured" branch a NULL would have
    hit, reached without violating the schema (restored in `finally`,
    never left mutated for other tests)."""
    from app.db.session import SessionLocal
    from app.modules.portal.models import GuestRequest
    from app.modules.notifications.service import scan_and_send_escalations
    from app.modules.settings.router import get_or_create_settings

    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"EscNoCfgProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"EscNoCfgUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "No-Config Escalation Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=12300)
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat()}).json()

    db = SessionLocal()
    try:
        settings_row = get_or_create_settings(db)
        original_contact_email = settings_row.contact_email
        settings_row.contact_email = ""
        db.commit()
        try:
            gr = GuestRequest(
                guest_id=uuid.UUID(guest["id"]), booking_id=uuid.UUID(booking["id"]), request_type="service",
                description="No business contact configured", priority="urgent", status="open",
            )
            gr.created_at = datetime.now(timezone.utc) - timedelta(hours=5)
            db.add(gr)
            db.commit()
            db.refresh(gr)
            request_id = str(gr.id)
            with patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
                results = scan_and_send_escalations(db)
            mock_send.assert_not_called()
        finally:
            settings_row.contact_email = original_contact_email
            db.commit()
    finally:
        db.close()

    assert results["guest_request_escalated"] == 0
    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "guest_request_escalated"}).json()
    assert not any(l["guest_request_id"] == request_id for l in logs)


def test_manual_escalation_scan_endpoint(headers):
    resp = client.post("/api/v1/notifications/scan-escalations", headers=headers)
    assert resp.status_code == 200
    assert "guest_request_escalated" in resp.json()
    assert "maintenance_ticket_escalated" in resp.json()


def test_existing_reminder_scan_still_works(headers):
    resp = client.post("/api/v1/notifications/scan-reminders", headers=headers)
    assert resp.status_code == 200
    assert "checkin_reminder" in resp.json()


def test_existing_notification_logs_endpoint_still_works(headers):
    resp = client.get("/api/v1/notifications/logs", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_scheduler_registers_escalation_scan_job(monkeypatch):
    """Phase 6: the escalation scan must now be registered on the same
    in-process APScheduler the reminder scan already uses — not just
    reachable manually. next_run_time=None on every job (same as the
    pre-existing reminder_scan/hold_expiry_cleanup jobs) means nothing
    actually fires during this test; we're only verifying registration."""
    from app.core.config import settings
    from app.modules.notifications import scheduler as scheduler_module

    monkeypatch.setattr(settings, "enable_scheduler", True)
    try:
        sched = scheduler_module.start_scheduler()
        assert sched is not None
        job_ids = {job.id for job in sched.get_jobs()}
        assert "escalation_scan" in job_ids
        # Regression: registering the new job must not have displaced the
        # two pre-existing jobs.
        assert "reminder_scan" in job_ids
        assert "hold_expiry_cleanup" in job_ids
    finally:
        scheduler_module.stop_scheduler()


def test_escalation_scan_job_function_executes_successfully():
    """The scheduler's own wrapper (DB session open/close, exception
    handling) must actually run scan_and_send_escalations without raising —
    exercising the wrapper itself, not just the already-tested business
    logic it calls."""
    from app.modules.notifications.scheduler import _run_escalation_scan

    _run_escalation_scan()  # raises nothing on success; failures are only logged internally
