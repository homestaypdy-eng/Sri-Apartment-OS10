"""
Tests completing the Users module to full production coverage: password
reset (a completely missing flow) and session management (turning
stateless JWTs into real, revocable sessions).
Run with: pytest tests/test_users_completion.py -v
"""
import hashlib
import logging
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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


def _make_test_user(headers):
    email = f"userscomp{uuid.uuid4().hex[:8]}@test.com"
    resp = client.post("/api/v1/auth/register", headers=headers, json={"name": "Users Completion Test", "email": email, "password": "OldPass123!", "role": "staff"})
    assert resp.status_code == 201, resp.text
    return email, resp.json()


def _make_reset_token(user_id, minutes=30):
    from app.db.session import SessionLocal
    from app.modules.auth.models import PasswordResetToken
    db = SessionLocal()
    try:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        row = PasswordResetToken(user_id=uuid.UUID(user_id), token_hash=token_hash, expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes))
        db.add(row)
        db.commit()
    finally:
        db.close()
    return raw_token


# --- forgot-password: SMTP mocked as configured (this environment has no
# real SMTP access) — matches the established pattern in
# test_lead_management.py::test_email_send_succeeds_with_mocked_smtp ---

@patch("smtplib.SMTP")
def test_forgot_password_always_returns_generic_message_for_unknown_email(mock_smtp_class, headers):
    from app.modules.auth import router as auth_router

    mock_server = MagicMock()
    mock_smtp_class.return_value.__enter__.return_value = mock_server

    with patch.object(auth_router.settings, "smtp_host", "smtp.example.com"), \
         patch.object(auth_router.settings, "smtp_user", "test@example.com"), \
         patch.object(auth_router.settings, "smtp_password", "fake"), \
         patch.object(auth_router.settings, "smtp_use_tls", True):
        resp = client.post("/api/v1/auth/forgot-password", json={"email": f"doesnotexist{uuid.uuid4().hex[:8]}@test.com"})
    assert resp.status_code == 200
    assert "if that email is registered" in resp.json()["message"].lower()


@patch("app.modules.notifications.service._PreferIPv6SMTP")
def test_forgot_password_creates_a_real_token_for_known_email(mock_smtp_class, headers):
    from app.modules.auth import router as auth_router

    email, user = _make_test_user(headers)

    mock_server = MagicMock()
    mock_smtp_class.return_value.__enter__.return_value = mock_server

    with patch.object(auth_router.settings, "smtp_host", "smtp.example.com"), \
         patch.object(auth_router.settings, "smtp_user", "test@example.com"), \
         patch.object(auth_router.settings, "smtp_password", "fake"), \
         patch.object(auth_router.settings, "smtp_use_tls", True):
        resp = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200

    from app.db.session import SessionLocal
    from app.modules.auth.models import PasswordResetToken
    db = SessionLocal()
    try:
        token_row = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == uuid.UUID(user["id"])).first()
        assert token_row is not None
        assert token_row.consumed_at is None
    finally:
        db.close()


# --- forgot-password: SMTP globally unconfigured ---

def test_forgot_password_smtp_unconfigured_known_email(headers):
    from app.modules.auth import router as auth_router

    email, user = _make_test_user(headers)
    with patch.object(auth_router.settings, "smtp_host", ""):
        resp = client.post("/api/v1/auth/forgot-password", json={"email": email})

    assert resp.status_code == 501
    assert resp.json() == {"detail": "Password reset email delivery is not configured. Contact your administrator."}

    from app.db.session import SessionLocal
    from app.modules.auth.models import PasswordResetToken
    db = SessionLocal()
    try:
        token_row = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == uuid.UUID(user["id"])).first()
        assert token_row is None, "no token should be created when SMTP is globally unconfigured"
    finally:
        db.close()


def test_forgot_password_smtp_unconfigured_unknown_email_matches_known_email_response(headers):
    """
    Proves enumeration protection for the new 501 path: a known and an
    unknown email must produce byte-identical responses when SMTP is
    globally unconfigured — the check happens before the user lookup,
    so it cannot depend on whether the submitted email exists.
    """
    from app.modules.auth import router as auth_router

    known_email, _user = _make_test_user(headers)
    unknown_email = f"doesnotexist{uuid.uuid4().hex[:8]}@test.com"

    with patch.object(auth_router.settings, "smtp_host", ""):
        known_resp = client.post("/api/v1/auth/forgot-password", json={"email": known_email})
        unknown_resp = client.post("/api/v1/auth/forgot-password", json={"email": unknown_email})

    assert known_resp.status_code == unknown_resp.status_code == 501
    assert known_resp.json() == unknown_resp.json()


@patch("app.modules.notifications.service._PreferIPv6SMTP")
def test_forgot_password_logs_delivery_failure_without_exposing_token(mock_smtp_class, headers, caplog):
    """
    SMTP is configured, but the actual send raises smtplib.SMTPException —
    the caller must still see the ordinary generic success response (never
    exposing the per-request delivery failure, which would otherwise leak
    enumeration), the token must still exist (it was created before the
    send attempt), the failure must be logged, and neither the raw token
    nor its hash may appear anywhere in the logged message.

    Patches notifications.service._PreferIPv6SMTP (the class send_email()
    actually constructs) rather than smtplib.SMTP directly: _PreferIPv6SMTP
    only overrides _get_socket and is referenced by its own name at the
    call site, so patching the base class name no longer reaches it — see
    that class's own docstring for the full reasoning.
    """
    from app.modules.auth import router as auth_router

    email, user = _make_test_user(headers)

    mock_server = MagicMock()
    mock_server.send_message.side_effect = smtplib.SMTPException("relay refused")
    mock_smtp_class.return_value.__enter__.return_value = mock_server

    with patch.object(auth_router.settings, "smtp_host", "smtp.example.com"), \
         patch.object(auth_router.settings, "smtp_user", "test@example.com"), \
         patch.object(auth_router.settings, "smtp_password", "fake"), \
         patch.object(auth_router.settings, "smtp_use_tls", True), \
         caplog.at_level(logging.ERROR, logger="sri_apartment_os"):
        resp = client.post("/api/v1/auth/forgot-password", json={"email": email})

    assert resp.status_code == 200
    assert "if that email is registered" in resp.json()["message"].lower()

    from app.db.session import SessionLocal
    from app.modules.auth.models import PasswordResetToken
    db = SessionLocal()
    try:
        token_row = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == uuid.UUID(user["id"])).first()
        assert token_row is not None, "token must still be created before the send attempt"
        token_hash = token_row.token_hash
    finally:
        db.close()

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR and "delivery failed" in r.getMessage()]
    assert len(error_records) == 1, "the delivery failure must be logged exactly once"
    log_message = error_records[0].getMessage()
    assert f"user_id={user['id']}" in log_message
    assert "SMTPException" in log_message
    assert "relay refused" in log_message
    assert token_hash not in log_message


def test_reset_password_with_valid_token_actually_changes_password(headers):
    email, user = _make_test_user(headers)
    raw_token = _make_reset_token(user["id"])

    resp = client.post("/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "BrandNewPass456!"})
    assert resp.status_code == 200, resp.text

    old_login = client.post("/api/v1/auth/login", json={"email": email, "password": "OldPass123!"})
    assert old_login.status_code == 401

    new_login = client.post("/api/v1/auth/login", json={"email": email, "password": "BrandNewPass456!"})
    assert new_login.status_code == 200


def test_reset_password_token_is_single_use(headers):
    email, user = _make_test_user(headers)
    raw_token = _make_reset_token(user["id"])

    first = client.post("/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "FirstReset123!"})
    assert first.status_code == 200

    second = client.post("/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "SecondReset123!"})
    assert second.status_code == 400


def test_reset_password_rejects_expired_token(headers):
    email, user = _make_test_user(headers)
    raw_token = _make_reset_token(user["id"], minutes=-1)

    resp = client.post("/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "TooLate123!"})
    assert resp.status_code == 400


def test_reset_password_rejects_garbage_token():
    resp = client.post("/api/v1/auth/reset-password", json={"token": "not-a-real-token", "new_password": "Whatever123!"})
    assert resp.status_code == 400


def test_password_reset_revokes_all_existing_sessions(headers):
    from app.db.session import SessionLocal
    from app.modules.auth.models import UserSession

    email, user = _make_test_user(headers)
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": "OldPass123!"})
    assert login_resp.status_code == 200

    db = SessionLocal()
    try:
        active_before = db.query(UserSession).filter(UserSession.user_id == uuid.UUID(user["id"]), UserSession.revoked_at.is_(None)).count()
        assert active_before >= 1
    finally:
        db.close()

    raw_token = _make_reset_token(user["id"])
    client.post("/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "PostResetPass123!"})

    db = SessionLocal()
    try:
        active_after = db.query(UserSession).filter(UserSession.user_id == uuid.UUID(user["id"]), UserSession.revoked_at.is_(None)).count()
        assert active_after == 0
    finally:
        db.close()


def test_password_reset_logs_audit(headers):
    email, user = _make_test_user(headers)
    raw_token = _make_reset_token(user["id"])
    client.post("/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "AuditCheckPass123!"})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "auth.password_reset"}).json()
    assert any(l["entity_id"] == user["id"] for l in logs)


def test_login_creates_a_real_session(headers):
    email, user = _make_test_user(headers)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "OldPass123!"})
    assert login.status_code == 200
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    sessions = client.get("/api/v1/auth/sessions", headers=user_headers).json()
    assert len(sessions) >= 1


def test_revoked_session_blocks_refresh(headers):
    email, user = _make_test_user(headers)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "OldPass123!"})
    refresh_token = login.json()["refresh_token"]
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    sessions = client.get("/api/v1/auth/sessions", headers=user_headers).json()
    session_id = sessions[0]["id"]

    revoke_resp = client.post(f"/api/v1/auth/sessions/{session_id}/revoke", headers=user_headers)
    assert revoke_resp.status_code == 204

    refresh_resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_resp.status_code == 401


def test_revoke_all_sessions_logs_out_everywhere(headers):
    email, user = _make_test_user(headers)
    login1 = client.post("/api/v1/auth/login", json={"email": email, "password": "OldPass123!"})
    refresh1 = login1.json()["refresh_token"]
    user_headers1 = {"Authorization": f"Bearer {login1.json()['access_token']}"}

    client.post("/api/v1/auth/sessions/revoke-all", headers=user_headers1)

    refresh_resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert refresh_resp.status_code == 401


def test_cannot_revoke_another_users_session(headers):
    email_a, user_a = _make_test_user(headers)
    email_b, user_b = _make_test_user(headers)
    login_a = client.post("/api/v1/auth/login", json={"email": email_a, "password": "OldPass123!"})
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
    sessions_a = client.get("/api/v1/auth/sessions", headers=headers_a).json()
    session_a_id = sessions_a[0]["id"]

    login_b = client.post("/api/v1/auth/login", json={"email": email_b, "password": "OldPass123!"})
    headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

    resp = client.post(f"/api/v1/auth/sessions/{session_a_id}/revoke", headers=headers_b)
    assert resp.status_code == 404


def test_valid_refresh_still_works_after_this_change(headers):
    email, user = _make_test_user(headers)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "OldPass123!"})
    refresh_token = login.json()["refresh_token"]

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_existing_login_flow_still_works(headers):
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    assert "access_token" in resp.json()
    assert "refresh_token" in resp.json()
