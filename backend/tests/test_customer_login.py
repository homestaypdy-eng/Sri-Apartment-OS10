"""
Phase 3E: customer two-step (password + OTP) login/logout tests.

POST /api/v1/customer-auth/login (step 1: password check + OTP dispatch),
POST /api/v1/customer-auth/login/verify-otp (step 2: OTP check + session
issuance), and /logout — built on the Phase 3A/3E foundation
(CustomerSession, CustomerLoginOtpChallenge, get_current_customer/
_with_session) and the Phase 3B/3E registration endpoint (now requiring
phone). These tests prove the happy path (both email and phone channels)
plus every security boundary from the Phase 3E task: password-required
(no OTP-only login), anti-enumeration, OTP correctness/expiry/attempt-
limits/single-use, session/JWT correctness, cross-customer isolation, and
that staff/guest auth and existing signup remain completely unaffected.

Run with: pytest tests/test_customer_login.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import decode_token
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.bookings.models import Guest
from app.modules.customer_auth.models import CustomerAccount, CustomerLoginOtpChallenge, CustomerSession
from app.modules.portal.auth_models import GuestIdentity
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)
REGISTER_URL = "/api/v1/customer-auth/register"
LOGIN_URL = "/api/v1/customer-auth/login"
VERIFY_OTP_URL = "/api/v1/customer-auth/login/verify-otp"
LOGOUT_URL = "/api/v1/customer-auth/logout"


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
    return f"login-{uuid.uuid4().hex[:10]}@example.com"


def _random_phone() -> str:
    return f"9{uuid.uuid4().int % 10**9:09d}"


PASSWORD = "CorrectHorse123!"


def _register(email: str, phone: str | None = None, password: str = PASSWORD) -> dict:
    resp = client.post(REGISTER_URL, json={
        "first_name": "Ada", "last_name": "Lovelace", "email": email,
        "phone": phone or _random_phone(), "password": password,
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login_step1(channel: str, identifier: str, password: str = PASSWORD):
    return client.post(LOGIN_URL, json={"channel": channel, "identifier": identifier, "password": password})


def _verify_otp(challenge_id: str, code: str):
    return client.post(VERIFY_OTP_URL, json={"challenge_id": challenge_id, "code": code})


def _login(email: str, password: str = PASSWORD):
    """Full two-step login by email, returning the final verify-otp
    response — most tests only care about the end-to-end outcome."""
    step1 = _login_step1("email", email, password)
    if step1.status_code != 200:
        return step1
    return _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"])


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1-4: successful login (email + phone channels), email normalization
# ---------------------------------------------------------------------------

def test_successful_login_via_email(db):
    email = _random_email()
    _register(email)
    resp = _login(email)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["customer"]["email"] == email
    assert "password" not in body["customer"]
    assert "password_hash" not in body["customer"]


def test_successful_login_via_phone(db):
    email = _random_email()
    phone = _random_phone()
    _register(email, phone=phone)
    step1 = _login_step1("phone", phone)
    assert step1.status_code == 200, step1.text
    verify = _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"])
    assert verify.status_code == 200, verify.text
    assert verify.json()["customer"]["email"] == email


def test_successful_login_via_international_phone_number(db):
    """Worldwide mobile login: a French number registered with its
    country code must be able to log back in typed EITHER exactly as
    registered, or with different (but equivalent) formatting — both
    normalize to the same E.164 identifier this project actually stores."""
    email = _random_email()
    resp = client.post(REGISTER_URL, json={
        "first_name": "Amelie", "last_name": "Poulain", "email": email,
        "phone": "+33 6 98 76 54 32", "password": PASSWORD,
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["phone"] == "+33698765432"

    step1 = _login_step1("phone", "0033-6-98-76-54-32")
    assert step1.status_code == 200, step1.text
    verify = _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"])
    assert verify.status_code == 200, verify.text
    assert verify.json()["customer"]["email"] == email


def test_same_password_works_for_both_email_and_phone_login(db):
    """There is exactly ONE password_hash per CustomerAccount — this proves
    the identical password authenticates the SAME account via either
    channel, with no second/separate password for phone login."""
    email = _random_email()
    phone = _random_phone()
    account_id = uuid.UUID(_register(email, phone=phone)["id"])

    via_email = _login(email)
    assert via_email.status_code == 200, via_email.text
    assert via_email.json()["customer"]["id"] == str(account_id)

    step1 = _login_step1("phone", phone)
    assert step1.status_code == 200, step1.text
    via_phone = _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"])
    assert via_phone.status_code == 200, via_phone.text
    assert via_phone.json()["customer"]["id"] == str(account_id)


def test_phone_login_for_account_without_a_phone_on_file_is_rejected_generically(db):
    """Reproduces a real scenario: a CustomerAccount created before the
    Phase 3E phone column existed has phone=NULL (never fabricated — see
    CustomerAccount.phone's own docstring). Attempting phone-channel login
    for such an account must fail with the SAME generic anti-enumeration
    error as any other unknown identifier — never a 500, never a message
    that reveals the account exists but has no phone on file."""
    from app.core.security import hash_password
    from app.modules.customer_auth.models import CustomerAccount as _CustomerAccount

    now = datetime.now(timezone.utc)
    legacy_account = _CustomerAccount(
        first_name="Legacy", last_name="NoPhone", email=_random_email(),
        phone=None, password_hash=hash_password(PASSWORD),
        terms_accepted_at=now, privacy_accepted_at=now,
    )
    db.add(legacy_account)
    db.commit()

    resp = _login_step1("phone", _random_phone())
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email/phone or password"


def test_account_resolvable_by_either_normalized_email_or_phone(db):
    """Direct model-layer proof (no login/password involved) that a single
    CustomerAccount row is reachable by EITHER identifier, resolving to
    the identical id — exactly the invariant a real account gains the
    moment a phone number is added to it alongside its existing email."""
    from app.modules.customer_auth.phone_service import normalize_phone_e164
    from app.modules.portal.auth_service import normalize_email

    email = _random_email()
    phone = _random_phone()
    account_id = uuid.UUID(_register(email, phone=phone)["id"])

    by_email = db.scalar(select(CustomerAccount).where(CustomerAccount.email == normalize_email(email)))
    by_phone = db.scalar(select(CustomerAccount).where(CustomerAccount.phone == normalize_phone_e164(phone)))
    assert by_email is not None
    assert by_phone is not None
    assert by_email.id == by_phone.id == account_id
    assert by_email.password_hash == by_phone.password_hash


def test_login_email_is_normalized(db):
    raw_email = f"  MixedCase-{uuid.uuid4().hex[:8]}@Example.COM  "
    _register(raw_email.strip().lower())
    resp = _login(raw_email)
    assert resp.status_code == 200, resp.text


def test_correct_password_logs_in(db):
    email = _random_email()
    _register(email)
    assert _login(email, PASSWORD).status_code == 200


# ---------------------------------------------------------------------------
# 5-7: step 1 requires the correct password — no OTP-only login
# ---------------------------------------------------------------------------

def test_step1_incorrect_password_rejected(db):
    email = _random_email()
    _register(email)
    resp = _login_step1("email", email, "WrongPassword1")
    assert resp.status_code == 401


def test_step1_unknown_identifier_rejected():
    resp = _login_step1("email", _random_email(), "SomePassword1")
    assert resp.status_code == 401


def test_step1_unknown_identifier_and_wrong_password_give_identical_response(db):
    email = _random_email()
    _register(email)

    wrong_password_resp = _login_step1("email", email, "WrongPassword1")
    unknown_identifier_resp = _login_step1("email", _random_email(), "WrongPassword1")

    assert wrong_password_resp.status_code == unknown_identifier_resp.status_code == 401
    assert wrong_password_resp.json() == unknown_identifier_resp.json()


# ---------------------------------------------------------------------------
# 8-10: session/challenge creation behavior — no session until OTP verified
# ---------------------------------------------------------------------------

def test_failed_step1_creates_no_session_and_no_challenge(db):
    email = _random_email()
    _register(email)
    sessions_before = db.query(CustomerSession).count()
    challenges_before = db.query(CustomerLoginOtpChallenge).count()
    _login_step1("email", email, "WrongPassword1")
    assert db.query(CustomerSession).count() == sessions_before
    assert db.query(CustomerLoginOtpChallenge).count() == challenges_before


def test_successful_step1_creates_challenge_but_no_session(db):
    email = _random_email()
    account_id = uuid.UUID(_register(email)["id"])
    sessions_before = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    resp = _login_step1("email", email)
    assert resp.status_code == 200
    assert resp.json()["challenge_id"]
    sessions_after = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    assert sessions_after == sessions_before  # step 1 alone never creates a session


def test_successful_verify_creates_exactly_one_session(db):
    email = _random_email()
    account_id = uuid.UUID(_register(email)["id"])
    before = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    resp = _login(email)
    assert resp.status_code == 200
    after = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    assert after == before + 1


# ---------------------------------------------------------------------------
# 11-16: OTP correctness — wrong code, expired, max attempts, single-use
# ---------------------------------------------------------------------------

def test_wrong_otp_code_rejected(db):
    email = _random_email()
    _register(email)
    step1 = _login_step1("email", email)
    resp = _verify_otp(step1.json()["challenge_id"], "000000")
    assert resp.status_code == 401


def test_wrong_otp_code_does_not_create_session(db):
    email = _random_email()
    account_id = uuid.UUID(_register(email)["id"])
    step1 = _login_step1("email", email)
    before = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    _verify_otp(step1.json()["challenge_id"], "000000")
    after = db.query(CustomerSession).filter(CustomerSession.customer_id == account_id).count()
    assert after == before


def test_expired_otp_rejected(db):
    email = _random_email()
    _register(email)
    step1 = _login_step1("email", email)
    challenge = db.get(CustomerLoginOtpChallenge, uuid.UUID(step1.json()["challenge_id"]))
    challenge.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    resp = _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"])
    assert resp.status_code == 401


def test_otp_max_attempts_exceeded(db):
    email = _random_email()
    _register(email)
    step1 = _login_step1("email", email)
    challenge_id = step1.json()["challenge_id"]
    for _ in range(5):
        resp = _verify_otp(challenge_id, "000000")
        assert resp.status_code == 401
    # Even the correct code now fails — attempts are exhausted.
    exhausted = _verify_otp(challenge_id, step1.json()["dev_otp_code"])
    assert exhausted.status_code == 401


def test_otp_is_single_use(db):
    email = _random_email()
    _register(email)
    step1 = _login_step1("email", email)
    first = _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"])
    assert first.status_code == 200
    second = _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"])
    assert second.status_code == 401


def test_unknown_challenge_id_rejected():
    resp = _verify_otp(str(uuid.uuid4()), "123456")
    assert resp.status_code == 401


def test_new_login_invalidates_previous_challenge(db):
    email = _random_email()
    _register(email)
    first_step1 = _login_step1("email", email)
    second_step1 = _login_step1("email", email)
    # The first challenge was invalidated the moment the second was issued.
    stale = _verify_otp(first_step1.json()["challenge_id"], first_step1.json()["dev_otp_code"])
    assert stale.status_code == 401
    fresh = _verify_otp(second_step1.json()["challenge_id"], second_step1.json()["dev_otp_code"])
    assert fresh.status_code == 200


# ---------------------------------------------------------------------------
# 17-19: JWT shape
# ---------------------------------------------------------------------------

def test_jwt_has_role_customer_and_type_access(db):
    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    payload = decode_token(token, expected_type="access")
    assert payload["role"] == "customer"
    assert payload["type"] == "access"


def test_jwt_contains_jti_matching_a_customer_session(db):
    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    payload = decode_token(token, expected_type="access")
    jti = payload["jti"]
    assert jti
    session_row = db.get(CustomerSession, uuid.UUID(jti))
    assert session_row is not None
    assert session_row.revoked_at is None


# ---------------------------------------------------------------------------
# 20-21: password/OTP never returned
# ---------------------------------------------------------------------------

def test_password_hash_never_returned(db):
    email = _random_email()
    _register(email)
    body = _login(email).json()
    assert "password_hash" not in body
    assert "password_hash" not in body["customer"]


def test_plaintext_password_never_returned(db):
    email = _random_email()
    _register(email)
    step1 = _login_step1("email", email)
    resp_text = _verify_otp(step1.json()["challenge_id"], step1.json()["dev_otp_code"]).text
    assert PASSWORD not in resp_text


# ---------------------------------------------------------------------------
# 22-24: token works with get_current_customer; rejected by staff/guest
# ---------------------------------------------------------------------------

def test_customer_jwt_works_with_get_current_customer_dependency(db):
    from app.modules.customer_auth.service import get_current_customer

    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    resolved = get_current_customer(token=token, db=db)
    assert resolved.email == email


def test_customer_token_cannot_authenticate_as_staff(db):
    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    resp = client.get("/api/v1/auth/sessions", headers=_auth(token))
    assert resp.status_code == 401


def test_customer_token_cannot_authenticate_as_guest(db):
    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    resp = client.get("/api/v1/portal/me", headers=_auth(token))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 25-26: cross-customer isolation
# ---------------------------------------------------------------------------

def test_customer_a_cannot_access_customer_b_session(db):
    from app.modules.customer_auth.service import get_current_customer

    email_a, email_b = _random_email(), _random_email()
    _register(email_a)
    _register(email_b)
    token_a = _login(email_a).json()["access_token"]
    token_b = _login(email_b).json()["access_token"]

    resolved_a = get_current_customer(token=token_a, db=db)
    resolved_b = get_current_customer(token=token_b, db=db)
    assert resolved_a.email == email_a
    assert resolved_b.email == email_b
    assert resolved_a.id != resolved_b.id


def test_customer_a_cannot_revoke_customer_b_session(db):
    email_a, email_b = _random_email(), _random_email()
    _register(email_a)
    _register(email_b)
    token_a = _login(email_a).json()["access_token"]
    token_b = _login(email_b).json()["access_token"]

    # A logs out (revokes only A's own session).
    assert client.post(LOGOUT_URL, headers=_auth(token_a)).status_code == 204

    # B's session must be completely untouched — A's logout could not
    # have affected it, since logout only ever resolves and revokes the
    # caller's own jti.
    payload_b = decode_token(token_b, expected_type="access")
    session_b = db.get(CustomerSession, uuid.UUID(payload_b["jti"]))
    assert session_b.revoked_at is None

    from app.modules.customer_auth.service import get_current_customer

    resolved_b = get_current_customer(token=token_b, db=db)
    assert resolved_b.email == email_b


# ---------------------------------------------------------------------------
# 27-29: logout behavior
# ---------------------------------------------------------------------------

def test_logout_revokes_the_correct_session(db):
    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    payload = decode_token(token, expected_type="access")
    session_id = uuid.UUID(payload["jti"])

    resp = client.post(LOGOUT_URL, headers=_auth(token))
    assert resp.status_code == 204

    session_row = db.get(CustomerSession, session_id)
    assert session_row.revoked_at is not None


def test_revoked_jwt_rejected_after_logout(db):
    from app.modules.customer_auth.service import get_current_customer
    from fastapi import HTTPException

    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    client.post(LOGOUT_URL, headers=_auth(token))

    with pytest.raises(HTTPException):
        get_current_customer(token=token, db=db)

    # And a second logout call with the same (now-revoked) token fails
    # safely — no crash, a clean 401, not a duplicate revoke attempt.
    resp = client.post(LOGOUT_URL, headers=_auth(token))
    assert resp.status_code == 401


def test_logout_does_not_affect_another_customers_session(db):
    email_a, email_b = _random_email(), _random_email()
    _register(email_a)
    _register(email_b)
    token_a = _login(email_a).json()["access_token"]
    token_b = _login(email_b).json()["access_token"]

    client.post(LOGOUT_URL, headers=_auth(token_a))

    payload_b = decode_token(token_b, expected_type="access")
    session_b = db.get(CustomerSession, uuid.UUID(payload_b["jti"]))
    assert session_b.revoked_at is None


# ---------------------------------------------------------------------------
# 30: audit logging never contains credentials or OTP codes
# ---------------------------------------------------------------------------

def test_audit_logs_contain_no_credentials_or_otp(db):
    email = _random_email()
    _register(email)
    step1 = _login_step1("email", email)
    otp_code = step1.json()["dev_otp_code"]
    verify_resp = _verify_otp(step1.json()["challenge_id"], otp_code)
    token = verify_resp.json()["access_token"]
    _login_step1("email", email, "WrongPassword1")  # generate a failure row too
    _verify_otp(step1.json()["challenge_id"], "000000")  # and an OTP failure row
    client.post(LOGOUT_URL, headers=_auth(token))

    rows = db.scalars(
        select(AuditLog).where(AuditLog.action.in_([
            "customer_auth.login_success", "customer_auth.login_failure",
            "customer_auth.login_otp_requested", "customer_auth.login_otp_verify_failure",
            "customer_auth.logout",
        ]))
    ).all()
    assert rows
    for row in rows:
        for attr in ("details", "entity_id", "previous_value", "new_value"):
            value = getattr(row, attr, None)
            if value is not None:
                assert PASSWORD not in value
                assert "WrongPassword1" not in value
                assert otp_code not in value
                assert token not in value


# ---------------------------------------------------------------------------
# 31: rate limiting
# ---------------------------------------------------------------------------

def test_login_rate_limiting_blocks_after_threshold(db):
    email = _random_email()
    _register(email)
    # Staff-login's default threshold (is_rate_limited with no override) is
    # 5 attempts / 5 minutes — reused as-is here (see router.py).
    for _ in range(5):
        resp = _login_step1("email", email, "WrongPassword1")
        assert resp.status_code == 401
    blocked = _login_step1("email", email, "WrongPassword1")
    assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# 32-34: no auto-linking, no GuestIdentity, no booking access
# ---------------------------------------------------------------------------

def test_matching_guest_email_does_not_link_on_login(db):
    shared_email = _random_email()
    guest = Guest(name="Existing Guest", phone=f"92{uuid.uuid4().int % 10**8:08d}", email=shared_email)
    db.add(guest)
    db.commit()

    _register(shared_email)
    resp = _login(shared_email)
    assert resp.status_code == 200

    reloaded_guest = db.get(Guest, guest.id)
    assert reloaded_guest.email == shared_email  # untouched
    assert not hasattr(CustomerAccount, "guest_id")


def test_login_creates_no_guest_identity(db):
    email = _random_email()
    _register(email)
    before = db.query(GuestIdentity).count()
    _login(email)
    after = db.query(GuestIdentity).count()
    assert after == before


def test_login_grants_no_automatic_booking_access(db):
    email = _random_email()
    _register(email)
    token = _login(email).json()["access_token"]
    resp = client.get("/api/v1/portal/bookings", headers=_auth(token))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 35-37: regressions — signup, guest OTP, staff auth all still work
# ---------------------------------------------------------------------------

def test_existing_signup_still_works(db):
    email = _random_email()
    resp = client.post(REGISTER_URL, json={
        "first_name": "Grace", "last_name": "Hopper", "email": email,
        "phone": _random_phone(), "password": "AnotherPass1",
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert resp.status_code == 201


def test_existing_guest_otp_authentication_still_works(db):
    phone = f"91{uuid.uuid4().int % 10**8:08d}"
    resp = client.post("/api/v1/portal/auth/phone/request-otp", json={"phone": phone})
    assert resp.status_code == 200
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post("/api/v1/portal/auth/phone/verify-otp", json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200
    assert verify.json()["access_token"]


def test_existing_staff_authentication_still_works():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["access_token"]
