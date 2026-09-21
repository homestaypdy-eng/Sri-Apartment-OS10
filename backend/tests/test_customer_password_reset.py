"""
Phase 3D: customer forgot-password / reset-password tests.

POST /api/v1/customer-auth/forgot-password and /reset-password, built on
CustomerPasswordResetToken (Phase 3D) and the Phase 3A/3B/3C foundation.
These tests stay in the default non-production environment throughout —
the credential-independent safety gate never calls send_email at all
there (proven directly by a mocked-and-asserted-not-called test, mirroring
test_phone_otp_auth.py's own proof of the identical pattern) — so no real
email is ever sent by this file.

Run with: pytest tests/test_customer_password_reset.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import verify_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User, UserSession
from app.modules.bookings.models import Booking, Guest
from app.modules.customer_auth.models import CustomerAccount, CustomerPasswordResetToken, CustomerSession
from app.modules.customer_auth.service import hash_reset_token
from app.modules.portal.auth_models import GuestIdentity
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)
REGISTER_URL = "/api/v1/customer-auth/register"
LOGIN_URL = "/api/v1/customer-auth/login"
VERIFY_OTP_URL = "/api/v1/customer-auth/login/verify-otp"
FORGOT_URL = "/api/v1/customer-auth/forgot-password"
RESET_URL = "/api/v1/customer-auth/reset-password"

PASSWORD = "CorrectHorse123!"
NEW_PASSWORD = "ANewPassword456!"


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


def _random_email() -> str:
    return f"reset-{uuid.uuid4().hex[:10]}@example.com"


def _random_phone() -> str:
    return f"9{uuid.uuid4().int % 10**9:09d}"


def _register(email: str, password: str = PASSWORD) -> dict:
    resp = client.post(REGISTER_URL, json={
        "first_name": "Ada", "last_name": "Lovelace", "email": email,
        "phone": _random_phone(), "password": password,
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(email: str, password: str = PASSWORD):
    """Full two-step login by email, returning the final verify-otp
    response (or the step-1 failure response, if step 1 itself failed) —
    these tests only care about the end-to-end outcome."""
    step1 = client.post(LOGIN_URL, json={"channel": "email", "identifier": email, "password": password})
    if step1.status_code != 200:
        return step1
    return client.post(VERIFY_OTP_URL, json={"challenge_id": step1.json()["challenge_id"], "code": step1.json()["dev_otp_code"]})


def _forgot(email: str):
    return client.post(FORGOT_URL, json={"email": email})


def _reset(token: str, new_password: str = NEW_PASSWORD):
    return client.post(RESET_URL, json={"token": token, "new_password": new_password})


# ---------------------------------------------------------------------------
# 1-3: forgot-password basic behavior
# ---------------------------------------------------------------------------

def test_forgot_password_for_existing_customer(db):
    email = _random_email()
    _register(email)
    resp = _forgot(email)
    assert resp.status_code == 200, resp.text
    assert resp.json()["dev_reset_token"]


def test_forgot_password_for_unknown_email():
    resp = _forgot(_random_email())
    assert resp.status_code == 200
    assert resp.json()["dev_reset_token"] is None


def test_existing_and_unknown_email_return_identical_public_response(db):
    email = _random_email()
    _register(email)
    known = _forgot(email)
    unknown = _forgot(_random_email())
    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]


def test_unknown_email_creates_no_reset_token(db):
    before = db.query(CustomerPasswordResetToken).count()
    _forgot(_random_email())
    after = db.query(CustomerPasswordResetToken).count()
    assert after == before


# ---------------------------------------------------------------------------
# 4: email normalization
# ---------------------------------------------------------------------------

def test_forgot_password_email_is_normalized(db):
    raw_email = f"  MixedCase-{uuid.uuid4().hex[:8]}@Example.COM  "
    normalized = raw_email.strip().lower()
    _register(normalized)
    resp = _forgot(raw_email)
    assert resp.status_code == 200
    assert resp.json()["dev_reset_token"]


# ---------------------------------------------------------------------------
# 5-6: token randomness / never stored raw
# ---------------------------------------------------------------------------

def test_reset_tokens_are_cryptographically_random_and_distinct(db):
    email = _random_email()
    _register(email)
    token_a = _forgot(email).json()["dev_reset_token"]
    token_b = _forgot(email).json()["dev_reset_token"]
    assert token_a != token_b
    assert len(token_a) >= 32


def test_raw_token_never_stored_only_a_hash(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    row = db.scalar(select(CustomerPasswordResetToken).where(CustomerPasswordResetToken.token_hash == hash_reset_token(raw_token)))
    assert row is not None
    assert row.token_hash != raw_token
    assert raw_token not in row.token_hash
    assert len(row.token_hash) == 64  # sha256 hex digest


# ---------------------------------------------------------------------------
# 7-9: valid reset changes the password
# ---------------------------------------------------------------------------

def test_valid_token_resets_password(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    resp = _reset(raw_token)
    assert resp.status_code == 200, resp.text

    account = db.scalar(select(CustomerAccount).where(CustomerAccount.email == email))
    assert verify_password(NEW_PASSWORD, account.password_hash) is True


def test_old_password_stops_working_after_reset(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]
    _reset(raw_token)

    assert _login(email, PASSWORD).status_code == 401


def test_new_password_works_after_reset(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]
    _reset(raw_token)

    assert _login(email, NEW_PASSWORD).status_code == 200


# ---------------------------------------------------------------------------
# 10-13: single-use / reuse / expiry / invalid token
# ---------------------------------------------------------------------------

def test_reset_token_becomes_single_use(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    first = _reset(raw_token)
    assert first.status_code == 200


def test_reused_token_is_rejected(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    assert _reset(raw_token).status_code == 200
    second = _reset(raw_token, new_password="AnotherPass789!")
    assert second.status_code == 400


def test_expired_token_is_rejected(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    row = db.scalar(select(CustomerPasswordResetToken).where(CustomerPasswordResetToken.token_hash == hash_reset_token(raw_token)))
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    resp = _reset(raw_token)
    assert resp.status_code == 400


def test_invalid_token_is_rejected():
    resp = _reset("this-token-does-not-exist-at-all")
    assert resp.status_code == 400


def test_new_forgot_password_request_invalidates_previous_token(db):
    email = _random_email()
    _register(email)
    first_token = _forgot(email).json()["dev_reset_token"]
    second_token = _forgot(email).json()["dev_reset_token"]

    assert _reset(first_token).status_code == 400  # superseded, not usable
    assert _reset(second_token).status_code == 200


# ---------------------------------------------------------------------------
# 14: weak new password rejected
# ---------------------------------------------------------------------------

def test_weak_new_password_rejected(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    resp = _reset(raw_token, new_password="alllowercase")
    assert resp.status_code == 422

    # The token must still be usable — a rejected weak-password attempt
    # is a validation failure, not a consumption of the token.
    resp2 = _reset(raw_token, new_password=NEW_PASSWORD)
    assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# 15-16: no session created by reset; existing sessions all revoked
# ---------------------------------------------------------------------------

def test_successful_reset_creates_no_customer_session(db):
    email = _random_email()
    account_id = uuid.UUID(_register(email)["id"])
    raw_token = _forgot(email).json()["dev_reset_token"]

    before = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    _reset(raw_token)
    after = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    assert after == before


def test_successful_reset_revokes_all_existing_sessions(db):
    email = _random_email()
    account_id = uuid.UUID(_register(email)["id"])
    # Two active sessions for this customer.
    _login(email)
    _login(email)
    active_before = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id, CustomerSession.revoked_at.is_(None)).count()
    assert active_before == 2

    raw_token = _forgot(email).json()["dev_reset_token"]
    _reset(raw_token)

    active_after = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id, CustomerSession.revoked_at.is_(None)).count()
    assert active_after == 0


# ---------------------------------------------------------------------------
# 17-18: old JWT rejected after reset; other customers unaffected
# ---------------------------------------------------------------------------

def test_old_jwt_rejected_after_password_reset(db):
    from app.modules.customer_auth.service import get_current_customer
    from fastapi import HTTPException

    email = _random_email()
    _register(email)
    old_token = _login(email).json()["access_token"]

    raw_token = _forgot(email).json()["dev_reset_token"]
    _reset(raw_token)

    with pytest.raises(HTTPException):
        get_current_customer(token=old_token, db=db)


def test_reset_does_not_affect_another_customers_sessions(db):
    email_a, email_b = _random_email(), _random_email()
    _register(email_a)
    _register(email_b)
    token_b = _login(email_b).json()["access_token"]

    raw_token_a = _forgot(email_a).json()["dev_reset_token"]
    _reset(raw_token_a)

    from app.modules.customer_auth.service import get_current_customer

    resolved_b = get_current_customer(token=token_b, db=db)
    assert resolved_b.email == email_b


# ---------------------------------------------------------------------------
# 19-21: security boundary — staff, Guest/GuestIdentity, Booking untouched
# ---------------------------------------------------------------------------

def test_reset_does_not_affect_staff_user_or_session(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    staff_before = db.scalar(select(User).where(User.email == "homestaypdy@gmail.com"))
    staff_session_count_before = db.query(UserSession).filter(UserSession.user_id == staff_before.id, UserSession.revoked_at.is_(None)).count()

    _reset(raw_token)

    staff_after = db.scalar(select(User).where(User.email == "homestaypdy@gmail.com"))
    staff_session_count_after = db.query(UserSession).filter(UserSession.user_id == staff_after.id, UserSession.revoked_at.is_(None)).count()

    assert staff_before.password_hash == staff_after.password_hash
    assert staff_session_count_before == staff_session_count_after


def test_reset_does_not_affect_guest_or_guest_identity(db):
    shared_email = _random_email()
    guest = Guest(name="Existing Guest", phone=f"90{uuid.uuid4().int % 10**8:08d}", email=shared_email)
    db.add(guest)
    db.commit()
    guest_id = guest.id

    _register(shared_email)
    identity_count_before = db.query(GuestIdentity).count()
    raw_token = _forgot(shared_email).json()["dev_reset_token"]
    _reset(raw_token)

    reloaded_guest = db.get(Guest, guest_id)
    assert reloaded_guest.email == shared_email
    assert db.query(GuestIdentity).count() == identity_count_before


def test_reset_does_not_modify_booking(db):
    prop_phone = f"89{uuid.uuid4().int % 10**8:08d}"
    guest = Guest(name="Booking Guest", phone=prop_phone, email=_random_email())
    db.add(guest)
    db.commit()

    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]

    booking_count_before = db.query(Booking).count()
    _reset(raw_token)
    booking_count_after = db.query(Booking).count()
    assert booking_count_after == booking_count_before


# ---------------------------------------------------------------------------
# 22: audit logging leaks nothing
# ---------------------------------------------------------------------------

def test_audit_logs_contain_no_secrets(db):
    email = _random_email()
    _register(email)
    raw_token = _forgot(email).json()["dev_reset_token"]
    token_hash = hash_reset_token(raw_token)
    login_resp = _login(email, PASSWORD)
    old_login_token = login_resp.json()["access_token"] if login_resp.status_code == 200 else None
    _reset(raw_token)
    # Also generate a failure-path row.
    client.post(RESET_URL, json={"token": raw_token, "new_password": "SomeOtherPass1!"})

    rows = db.scalars(
        select(AuditLog).where(AuditLog.action.in_([
            "customer_auth.password_reset_requested",
            "customer_auth.password_reset_success",
            "customer_auth.password_reset_failure",
        ]))
    ).all()
    assert rows
    for row in rows:
        for attr in ("details", "entity_id", "previous_value", "new_value"):
            value = getattr(row, attr, None)
            if value is not None:
                assert raw_token not in value
                assert token_hash not in value
                assert PASSWORD not in value
                assert NEW_PASSWORD not in value
                assert "reset-password?token=" not in value
                if old_login_token:
                    assert old_login_token not in value


def test_dev_mode_never_sends_real_email(db):
    """Defense-in-depth, mirrors test_phone_otp_auth.py's own proof of the
    identical pattern: send_email is patched here specifically to assert
    it is NEVER CALLED in non-production.

    _register happens BEFORE the patch is active (same convention the
    very next test already uses) — registration now also dispatches its
    own, separately tested, welcome email in the background; patching
    only around _forgot keeps this assertion scoped to forgot-password's
    own send behavior, not signup's."""
    email = _random_email()
    _register(email)
    with patch("app.modules.notifications.service.send_email") as mock_send:
        resp = _forgot(email)
        assert resp.status_code == 200
        mock_send.assert_not_called()


def test_production_forgot_password_sends_email_when_configured(monkeypatch, db):
    """Production path IS reachable: send_email is invoked with the
    account's real email, and dev_reset_token is never returned there —
    mirrors test_email_otp_auth.py's identical production-path proof."""
    email = _random_email()
    _register(email)

    monkeypatch.setattr("app.modules.customer_auth.router.settings.environment", "production")
    monkeypatch.setattr("app.modules.customer_auth.router.settings.smtp_host", "smtp.test.example.com")

    with patch("app.modules.notifications.service.send_email") as mock_send:
        resp = _forgot(email)
        assert resp.status_code == 200
        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == email
        assert resp.json()["dev_reset_token"] is None

    row = db.scalar(
        select(CustomerAccount).where(CustomerAccount.email == email)
    )
    log_row = db.query(AuditLog).filter(
        AuditLog.action == "customer_auth.password_reset_requested", AuditLog.entity_id == str(row.id)
    ).order_by(AuditLog.created_at.desc()).first()
    assert log_row is not None


def test_production_forgot_password_fails_closed_when_smtp_not_configured(monkeypatch):
    """Production's not-configured check runs BEFORE the account lookup —
    identical 501 whether the email is known or unknown, so it can never
    be used to enumerate accounts. The response body must stay generic:
    naming SMTP/config state to an anonymous caller is itself a real
    information leak, the same class this endpoint otherwise avoids by
    never confirming/denying a submitted email is registered."""
    monkeypatch.setattr("app.modules.customer_auth.router.settings.environment", "production")
    monkeypatch.setattr("app.modules.customer_auth.router.settings.smtp_host", "")

    resp = _forgot(_random_email())
    assert resp.status_code == 501
    detail = resp.json()["detail"]
    assert "SMTP" not in detail
    assert "email delivery is not configured" not in detail
    assert detail == "Something went wrong. Please try again later."


def test_production_smtp_not_configured_response_identical_across_emails(monkeypatch, db):
    """Same status + same body regardless of which email was submitted
    (registered, unregistered, or malformed-but-schema-valid) while SMTP
    is unavailable — the fix must not introduce any way to distinguish
    accounts via this response."""
    monkeypatch.setattr("app.modules.customer_auth.router.settings.environment", "production")
    monkeypatch.setattr("app.modules.customer_auth.router.settings.smtp_host", "")

    known_email = _random_email()
    _register(known_email)

    known_resp = _forgot(known_email)
    unknown_resp = _forgot(_random_email())

    assert known_resp.status_code == unknown_resp.status_code == 501
    assert known_resp.json() == unknown_resp.json()


def test_notification_log_records_reset_email_delivery(monkeypatch, db):
    from app.modules.notifications.models import NotificationLog

    email = _random_email()
    _register(email)
    monkeypatch.setattr("app.modules.customer_auth.router.settings.environment", "production")
    monkeypatch.setattr("app.modules.customer_auth.router.settings.smtp_host", "smtp.test.example.com")

    with patch("app.modules.notifications.service.send_email"):
        _forgot(email)

    rows = db.scalars(
        select(NotificationLog).where(NotificationLog.event_type == "customer_password_reset", NotificationLog.recipient == email)
    ).all()
    assert any(r.success is True for r in rows)
    # Never carries the reset link/token in any column.
    for r in rows:
        assert "token=" not in (r.error_message or "")


# ---------------------------------------------------------------------------
# 23: rate limiting
# ---------------------------------------------------------------------------

def test_forgot_password_rate_limiting_blocks_after_threshold():
    email = _random_email()
    for _ in range(5):
        resp = _forgot(email)
        assert resp.status_code == 200
    blocked = _forgot(email)
    assert blocked.status_code == 429


def test_reset_password_rate_limiting_blocks_after_threshold():
    for _ in range(10):
        resp = _reset("not-a-real-token")
        assert resp.status_code == 400
    blocked = _reset("not-a-real-token")
    assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# 24-28: regressions — signup, login/logout, guest OTP, staff auth
# ---------------------------------------------------------------------------

def test_existing_signup_still_works(db):
    resp = client.post(REGISTER_URL, json={
        "first_name": "Grace", "last_name": "Hopper", "email": _random_email(),
        "phone": _random_phone(), "password": "AnotherPass1",
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201


def test_existing_login_and_logout_still_work(db):
    email = _random_email()
    _register(email)
    login_resp = _login(email)
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    logout_resp = client.post("/api/v1/customer-auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_resp.status_code == 204


def test_existing_guest_phone_otp_still_works(db):
    phone = f"88{uuid.uuid4().int % 10**8:08d}"
    resp = client.post("/api/v1/portal/auth/phone/request-otp", json={"phone": phone})
    assert resp.status_code == 200
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post("/api/v1/portal/auth/phone/verify-otp", json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200


def test_existing_guest_email_otp_still_works(db):
    guest_email = f"guestotp-{uuid.uuid4().hex[:10]}@example.com"
    resp = client.post("/api/v1/portal/auth/email/request-otp", json={"email": guest_email})
    assert resp.status_code == 200
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post("/api/v1/portal/auth/email/verify-otp", json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200


def test_existing_staff_authentication_still_works():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["access_token"]
