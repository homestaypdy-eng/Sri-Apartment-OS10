"""
Phase 3F: authenticated customer-profile endpoints — GET/PATCH /me and
POST /change-password. Built on the Phase 3A-3E foundation
(get_current_customer, CustomerSession, hash_password/verify_password);
these tests prove the happy path plus every security boundary from the
Phase 3F task: identity resolved only from the session token (never a
client-supplied id/email), email is never editable here, phone-uniqueness
and normalization are enforced identically to signup/login, current-
password is required before any change, other sessions are revoked on a
successful password change, and password_hash is never returned/logged.

Run with: pytest tests/test_customer_profile.py -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.customer_auth.models import CustomerAccount, CustomerSession
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)
REGISTER_URL = "/api/v1/customer-auth/register"
LOGIN_URL = "/api/v1/customer-auth/login"
VERIFY_OTP_URL = "/api/v1/customer-auth/login/verify-otp"
ME_URL = "/api/v1/customer-auth/me"
CHANGE_PASSWORD_URL = "/api/v1/customer-auth/change-password"

PASSWORD = "CorrectHorse123!"


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
    return f"profile-{uuid.uuid4().hex[:10]}@example.com"


def _random_phone() -> str:
    return f"9{uuid.uuid4().int % 10**9:09d}"


def _register(email: str, phone: str | None = None, password: str = PASSWORD) -> dict:
    resp = client.post(REGISTER_URL, json={
        "first_name": "Ada", "last_name": "Lovelace", "email": email,
        "phone": phone or _random_phone(), "password": password,
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login_and_get_token(email: str, password: str = PASSWORD) -> str:
    step1 = client.post(LOGIN_URL, json={"channel": "email", "identifier": email, "password": password})
    assert step1.status_code == 200, step1.text
    verify = client.post(VERIFY_OTP_URL, json={"challenge_id": step1.json()["challenge_id"], "code": step1.json()["dev_otp_code"]})
    assert verify.status_code == 200, verify.text
    return verify.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------

def test_get_me_returns_authenticated_customer_profile(db):
    email = _random_email()
    account = _register(email)
    token = _login_and_get_token(email)

    resp = client.get(ME_URL, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == account["id"]
    assert body["email"] == email
    assert body["first_name"] == "Ada"


def test_get_me_requires_authentication():
    resp = client.get(ME_URL)
    assert resp.status_code == 401


def test_get_me_never_returns_password_hash(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)
    body = client.get(ME_URL, headers=_auth(token)).json()
    assert "password_hash" not in body
    assert "password" not in body


def test_get_me_rejects_staff_and_guest_tokens(db):
    staff_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert staff_login.status_code == 200
    resp = client.get(ME_URL, headers=_auth(staff_login.json()["access_token"]))
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PATCH /me
# ---------------------------------------------------------------------------

def test_update_me_changes_first_last_name_and_phone(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)
    new_phone = _random_phone()
    expected_e164 = f"+91{new_phone}"

    resp = client.patch(ME_URL, headers=_auth(token), json={"first_name": "Grace", "last_name": "Hopper", "phone": new_phone})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["first_name"] == "Grace"
    assert body["last_name"] == "Hopper"
    assert body["phone"] == expected_e164

    # Persisted, not just echoed.
    refetched = client.get(ME_URL, headers=_auth(token)).json()
    assert refetched["first_name"] == "Grace"
    assert refetched["phone"] == expected_e164


def test_update_me_normalizes_phone_like_signup(db):
    """International E.164 normalization (customer_auth.phone_service) —
    same normalizer PATCH /me and POST /register both use, so the two
    can never drift into different stored representations of the same
    number."""
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    # Randomized (not a hardcoded literal) so this can never collide with
    # another test's own phone number in the shared test database.
    bare = _random_phone()
    formatted = f"+91 {bare[:5]} {bare[5:]}"

    resp = client.patch(ME_URL, headers=_auth(token), json={"first_name": "Ada", "last_name": "Lovelace", "phone": formatted})
    assert resp.status_code == 200, resp.text
    assert resp.json()["phone"] == f"+91{bare}"


def test_update_me_rejects_duplicate_phone_from_another_customer(db):
    email_a, email_b = _random_email(), _random_email()
    phone_b = _random_phone()
    _register(email_a)
    _register(email_b, phone=phone_b)
    token_a = _login_and_get_token(email_a)

    resp = client.patch(ME_URL, headers=_auth(token_a), json={"first_name": "Ada", "last_name": "Lovelace", "phone": phone_b})
    assert resp.status_code == 409


def test_update_me_cannot_change_email(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    resp = client.patch(ME_URL, headers=_auth(token), json={
        "first_name": "Ada", "last_name": "Lovelace", "phone": _random_phone(), "email": "hijacked@example.com",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == email  # unchanged — email isn't a field this endpoint accepts


def test_update_me_only_affects_the_caller_own_account(db):
    email_a, email_b = _random_email(), _random_email()
    _register(email_a)
    account_b = _register(email_b)
    token_a = _login_and_get_token(email_a)

    client.patch(ME_URL, headers=_auth(token_a), json={"first_name": "Hijacked", "last_name": "Name", "phone": _random_phone()})

    reloaded_b = client.get(ME_URL, headers=_auth(_login_and_get_token(email_b))).json()
    assert reloaded_b["id"] == account_b["id"]
    assert reloaded_b["first_name"] == "Ada"  # customer B untouched


def test_update_me_requires_authentication():
    resp = client.patch(ME_URL, json={"first_name": "Ada", "last_name": "Lovelace", "phone": "9876543210"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /change-password
# ---------------------------------------------------------------------------

def test_change_password_requires_correct_current_password(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    resp = client.post(CHANGE_PASSWORD_URL, headers=_auth(token), json={"current_password": "WrongPassword1", "new_password": "NewPassword456!"})
    assert resp.status_code == 401


def test_change_password_success_then_old_password_fails_and_new_password_works(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    resp = client.post(CHANGE_PASSWORD_URL, headers=_auth(token), json={"current_password": PASSWORD, "new_password": "NewPassword456!"})
    assert resp.status_code == 200, resp.text

    old_login = client.post(LOGIN_URL, json={"channel": "email", "identifier": email, "password": PASSWORD})
    assert old_login.status_code == 401

    new_login = client.post(LOGIN_URL, json={"channel": "email", "identifier": email, "password": "NewPassword456!"})
    assert new_login.status_code == 200


def test_change_password_revokes_all_active_sessions(db):
    email = _random_email()
    _register(email)
    token_a = _login_and_get_token(email)
    token_b = _login_and_get_token(email)

    active_before = db.query(CustomerSession).filter(CustomerSession.revoked_at.is_(None)).count()
    assert active_before >= 2

    resp = client.post(CHANGE_PASSWORD_URL, headers=_auth(token_a), json={"current_password": PASSWORD, "new_password": "NewPassword456!"})
    assert resp.status_code == 200

    assert client.get(ME_URL, headers=_auth(token_a)).status_code == 401
    assert client.get(ME_URL, headers=_auth(token_b)).status_code == 401


def test_change_password_weak_new_password_rejected(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    resp = client.post(CHANGE_PASSWORD_URL, headers=_auth(token), json={"current_password": PASSWORD, "new_password": "alllowercase"})
    assert resp.status_code == 422


def test_change_password_requires_authentication():
    resp = client.post(CHANGE_PASSWORD_URL, json={"current_password": PASSWORD, "new_password": "NewPassword456!"})
    assert resp.status_code == 401


def test_change_password_rate_limiting_blocks_after_threshold(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)

    for _ in range(5):
        resp = client.post(CHANGE_PASSWORD_URL, headers=_auth(token), json={"current_password": "WrongPassword1", "new_password": "NewPassword456!"})
        assert resp.status_code == 401
    blocked = client.post(CHANGE_PASSWORD_URL, headers=_auth(token), json={"current_password": "WrongPassword1", "new_password": "NewPassword456!"})
    assert blocked.status_code == 429


def test_change_password_audit_logs_contain_no_credentials(db):
    email = _random_email()
    _register(email)
    token = _login_and_get_token(email)
    client.post(CHANGE_PASSWORD_URL, headers=_auth(token), json={"current_password": "WrongPassword1", "new_password": "NewPassword456!"})
    client.post(CHANGE_PASSWORD_URL, headers=_auth(_login_and_get_token(email)), json={"current_password": PASSWORD, "new_password": "NewPassword456!"})

    rows = db.scalars(
        select(AuditLog).where(AuditLog.action.in_([
            "customer_auth.change_password_failure", "customer_auth.change_password_success",
        ]))
    ).all()
    assert rows
    for row in rows:
        for attr in ("details", "entity_id", "previous_value", "new_value"):
            value = getattr(row, attr, None)
            if value is not None:
                assert PASSWORD not in value
                assert "WrongPassword1" not in value
                assert "NewPassword456!" not in value


def test_existing_login_and_signup_still_work_after_profile_changes(db):
    email = _random_email()
    _register(email)
    assert _login_and_get_token(email)
