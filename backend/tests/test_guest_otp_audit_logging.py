"""
Phase 2A of the guest-auth hardening: guest OTP request/verify events are
now recorded in AuditLog (record_otp_audit, auth_service.py), and OTP
delivery attempts in production go through NotificationLog (send_otp_sms/
send_otp_email, notifications/service.py) instead of calling send_sms/
send_email directly and leaving no record on failure.

Every test here either stays in the default non-production environment
(where the credential-independent safety gate never calls a real sender
at all — see test_phone_otp_auth.py/test_email_otp_auth.py for that
proof) or explicitly mocks the underlying send_sms/send_email for the
handful of tests that flip to production — no real SMS/email is ever
sent by this file.

Run with: pytest tests/test_guest_otp_audit_logging.py -v
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.notifications.models import NotificationLog

client = TestClient(app)

PHONE_REQUEST_URL = "/api/v1/portal/auth/phone/request-otp"
PHONE_VERIFY_URL = "/api/v1/portal/auth/phone/verify-otp"
EMAIL_REQUEST_URL = "/api/v1/portal/auth/email/request-otp"
EMAIL_VERIFY_URL = "/api/v1/portal/auth/email/verify-otp"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clear_rate_limit_state():
    from app.shared import rate_limit as rate_limit_module

    rate_limit_module._attempts.clear()
    yield
    rate_limit_module._attempts.clear()


def _random_phone() -> str:
    return f"95{uuid.uuid4().int % 10**8:08d}"


def _random_email() -> str:
    return f"otpaudit-{uuid.uuid4().hex[:10]}@example.com"


def _audit_rows_for(db, action: str) -> list[AuditLog]:
    return db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at.desc())).all()


def _assert_no_secret_leakage(rows, secret: str) -> None:
    """rows can be AuditLog or NotificationLog instances — checks every
    text-bearing column that could plausibly carry a leaked OTP code."""
    for row in rows:
        for attr in ("details", "entity_id", "error_message", "recipient"):
            value = getattr(row, attr, None)
            if value is not None:
                assert secret not in value, f"secret leaked into {type(row).__name__}.{attr}: {value!r}"


# ---------------------------------------------------------------------------
# Phone OTP audit logging
# ---------------------------------------------------------------------------

def test_phone_otp_request_is_audit_logged(db):
    phone = _random_phone()
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 200
    code = resp.json()["dev_otp_code"]

    rows = _audit_rows_for(db, "guest_otp.phone_request")
    assert any(str(resp.json()["challenge_id"]) == r.entity_id for r in rows)
    _assert_no_secret_leakage(rows, code)


def test_phone_otp_verify_success_is_audit_logged(db):
    phone = _random_phone()
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]

    verify = client.post(PHONE_VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200

    rows = _audit_rows_for(db, "guest_otp.phone_verify_success")
    assert any(r.entity_id == challenge_id for r in rows)
    _assert_no_secret_leakage(rows, code)
    # The access token itself must never appear in an audit row either.
    _assert_no_secret_leakage(rows, verify.json()["access_token"])


def test_phone_otp_verify_failure_is_audit_logged(db):
    phone = _random_phone()
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    wrong = "000000" if code != "000000" else "111111"

    verify = client.post(PHONE_VERIFY_URL, json={"challenge_id": challenge_id, "code": wrong})
    assert verify.status_code == 401

    rows = _audit_rows_for(db, "guest_otp.phone_verify_failure")
    assert any(r.entity_id == challenge_id for r in rows)
    _assert_no_secret_leakage(rows, code)
    _assert_no_secret_leakage(rows, wrong)


def test_phone_otp_verify_failure_unknown_challenge_is_audit_logged(db):
    bogus_id = str(uuid.uuid4())
    verify = client.post(PHONE_VERIFY_URL, json={"challenge_id": bogus_id, "code": "123456"})
    assert verify.status_code == 401

    rows = _audit_rows_for(db, "guest_otp.phone_verify_failure")
    assert any(r.entity_id == bogus_id for r in rows)


# ---------------------------------------------------------------------------
# Email OTP audit logging (mirrors phone)
# ---------------------------------------------------------------------------

def test_email_otp_request_is_audit_logged(db):
    email = _random_email()
    resp = client.post(EMAIL_REQUEST_URL, json={"email": email})
    assert resp.status_code == 200
    code = resp.json()["dev_otp_code"]

    rows = _audit_rows_for(db, "guest_otp.email_request")
    assert any(str(resp.json()["challenge_id"]) == r.entity_id for r in rows)
    _assert_no_secret_leakage(rows, code)


def test_email_otp_verify_success_is_audit_logged(db):
    email = _random_email()
    resp = client.post(EMAIL_REQUEST_URL, json={"email": email})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]

    verify = client.post(EMAIL_VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200

    rows = _audit_rows_for(db, "guest_otp.email_verify_success")
    assert any(r.entity_id == challenge_id for r in rows)
    _assert_no_secret_leakage(rows, code)
    _assert_no_secret_leakage(rows, verify.json()["access_token"])


def test_email_otp_verify_failure_is_audit_logged(db):
    email = _random_email()
    resp = client.post(EMAIL_REQUEST_URL, json={"email": email})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    wrong = "000000" if code != "000000" else "111111"

    verify = client.post(EMAIL_VERIFY_URL, json={"challenge_id": challenge_id, "code": wrong})
    assert verify.status_code == 401

    rows = _audit_rows_for(db, "guest_otp.email_verify_failure")
    assert any(r.entity_id == challenge_id for r in rows)
    _assert_no_secret_leakage(rows, code)
    _assert_no_secret_leakage(rows, wrong)


# ---------------------------------------------------------------------------
# No OTP request/verify event is ever attached to a staff user_id — guests
# aren't rows in the `users` table AuditLog.user_id hard-FKs to.
# ---------------------------------------------------------------------------

def test_guest_otp_audit_rows_never_carry_a_staff_user_id(db):
    phone = _random_phone()
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    client.post(PHONE_VERIFY_URL, json={"challenge_id": challenge_id, "code": code})

    rows = db.scalars(select(AuditLog).where(AuditLog.action.like("guest_otp.%")).order_by(AuditLog.created_at.desc()).limit(5)).all()
    assert rows
    assert all(r.user_id is None for r in rows)


# ---------------------------------------------------------------------------
# OTP delivery logging (Phase 2A task 3): production-only, routed through
# NotificationLog via send_otp_sms/send_otp_email.
# ---------------------------------------------------------------------------

def test_phone_otp_delivery_success_is_logged_in_production(monkeypatch, db):
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "production")
    phone = _random_phone()
    with patch("app.modules.notifications.service.send_sms") as mock_send_sms:
        mock_send_sms.return_value = None
        resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 200
    assert resp.json()["dev_otp_code"] is None

    rows = db.scalars(
        select(NotificationLog).where(NotificationLog.event_type == "guest_otp_delivery", NotificationLog.channel == "sms").order_by(NotificationLog.created_at.desc())
    ).all()
    assert any(r.success is True for r in rows)


def test_phone_otp_delivery_not_configured_is_logged_in_production(monkeypatch, db):
    """This test environment genuinely has no SMS credentials (see
    test_phone_otp_auth.py's identical unmocked not-configured test) — the
    real send_sms call inside send_otp_sms raises SmsNotConfiguredError,
    which must still be logged as a failed delivery attempt before the
    router turns it into its existing 501 response."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "production")
    phone = _random_phone()
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 501

    rows = db.scalars(
        select(NotificationLog).where(NotificationLog.event_type == "guest_otp_delivery", NotificationLog.channel == "sms").order_by(NotificationLog.created_at.desc())
    ).all()
    assert any(r.success is False and "not configured" in (r.error_message or "").lower() for r in rows)


def test_email_otp_delivery_success_is_logged_in_production(monkeypatch, db):
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "production")
    email = _random_email()
    with patch("app.modules.notifications.service.send_email") as mock_send_email:
        mock_send_email.return_value = None
        resp = client.post(EMAIL_REQUEST_URL, json={"email": email})
    assert resp.status_code == 200
    assert resp.json()["dev_otp_code"] is None

    rows = db.scalars(
        select(NotificationLog).where(NotificationLog.event_type == "guest_otp_delivery", NotificationLog.channel == "email").order_by(NotificationLog.created_at.desc())
    ).all()
    assert any(r.success is True for r in rows)


def test_email_otp_delivery_not_configured_is_logged_in_production(monkeypatch, db):
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "production")
    email = _random_email()
    resp = client.post(EMAIL_REQUEST_URL, json={"email": email})
    assert resp.status_code == 501

    rows = db.scalars(
        select(NotificationLog).where(NotificationLog.event_type == "guest_otp_delivery", NotificationLog.channel == "email").order_by(NotificationLog.created_at.desc())
    ).all()
    assert any(r.success is False and "not configured" in (r.error_message or "").lower() for r in rows)


def test_dev_mode_otp_request_never_writes_a_notification_log_row(db):
    """Non-production: the safety gate skips the send entirely, so there
    is nothing to log — NotificationLog gets no guest_otp_delivery row at
    all for this request (as opposed to a row that would misleadingly
    claim success)."""
    phone = _random_phone()
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 200
    rows = db.scalars(select(NotificationLog).where(NotificationLog.event_type == "guest_otp_delivery", NotificationLog.recipient == phone)).all()
    assert rows == []
