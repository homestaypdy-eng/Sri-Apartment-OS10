"""
Phase 2A of the guest-auth hardening: proves get_current_guest actually
enforces GuestSession revocation now, instead of merely modeling it
(is_guest_session_active existed since Phase 1 but was never called from
any request path before this phase — see auth_service.py's docstring).

send_sms/send_email are never reached here (dev-mode safety gate — see
test_phone_otp_auth.py/test_email_otp_auth.py for that proof), so no
mocking is needed to keep this file free of any real SMS/email send.

Run with: pytest tests/test_guest_session_revocation.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_guest_session_token
from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Guest
from app.modules.portal.auth_models import GuestIdentity, GuestSession

client = TestClient(app)

PHONE_REQUEST_URL = "/api/v1/portal/auth/phone/request-otp"
PHONE_VERIFY_URL = "/api/v1/portal/auth/phone/verify-otp"
ME_URL = "/api/v1/portal/me"


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
    return f"96{uuid.uuid4().int % 10**8:08d}"


def _login_via_phone_otp(phone: str) -> str:
    """Full phone-OTP login for an existing Guest with this phone —
    exercises the real request/verify endpoints exactly as a guest would,
    so the returned access_token's jti is a real GuestSession row."""
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 200, resp.text
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post(PHONE_VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200, verify.text
    return verify.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1a: an active guest session works
# ---------------------------------------------------------------------------

def test_active_guest_session_allows_access(db):
    phone = _random_phone()
    guest = Guest(name="Session Revocation Guest A", phone=phone)
    db.add(guest)
    db.commit()

    token = _login_via_phone_otp(phone)
    resp = client.get(ME_URL, headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == str(guest.id)


# ---------------------------------------------------------------------------
# 1b: a revoked guest session is rejected immediately, before JWT expiry
# ---------------------------------------------------------------------------

def test_revoked_guest_session_is_rejected(db):
    phone = _random_phone()
    guest = Guest(name="Session Revocation Guest B", phone=phone)
    db.add(guest)
    db.commit()

    token = _login_via_phone_otp(phone)

    # Sanity: works before revocation.
    assert client.get(ME_URL, headers=_auth(token)).status_code == 200

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.guest_id == guest.id))
    session_row = db.scalar(select(GuestSession).where(GuestSession.identity_id == identity.id))
    session_row.revoked_at = datetime.now(timezone.utc)
    db.commit()

    # Same JWT, still well within its 24h expiry — must now be rejected
    # purely because the underlying session was revoked.
    resp = client.get(ME_URL, headers=_auth(token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 1c: an expired JWT, and a jti pointing at a missing/malformed session,
# are both rejected
# ---------------------------------------------------------------------------

def test_expired_jwt_is_rejected():
    now = datetime.now(timezone.utc)
    expired_payload = {
        "sub": str(uuid.uuid4()), "role": "guest", "type": "access",
        "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1),
    }
    expired_token = jwt.encode(expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    resp = client.get(ME_URL, headers=_auth(expired_token))
    assert resp.status_code == 401


def test_jti_pointing_at_nonexistent_session_is_rejected():
    """A well-formed jti that simply doesn't correspond to any GuestSession
    row (e.g. one that was deleted) must fail closed, not be waved through."""
    token = create_guest_session_token(str(uuid.uuid4()), jti=str(uuid.uuid4()))
    resp = client.get(ME_URL, headers=_auth(token))
    assert resp.status_code == 401


def test_malformed_jti_is_rejected():
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(uuid.uuid4()), "role": "guest", "type": "access",
        "iat": now, "exp": now + timedelta(hours=1), "jti": "not-a-uuid",
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    resp = client.get(ME_URL, headers=_auth(token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 1d: an unrelated guest's token cannot access another guest's data, and
# revoking one guest's session never affects another guest's session
# ---------------------------------------------------------------------------

def test_unrelated_guest_token_cannot_access_another_guests_data(db):
    guest_a = Guest(name="Session Revocation Guest C", phone=_random_phone())
    guest_b = Guest(name="Session Revocation Guest D", phone=_random_phone())
    db.add_all([guest_a, guest_b])
    db.commit()

    token_a = _login_via_phone_otp(guest_a.phone)
    token_b = _login_via_phone_otp(guest_b.phone)

    me_a = client.get(ME_URL, headers=_auth(token_a))
    me_b = client.get(ME_URL, headers=_auth(token_b))
    assert me_a.status_code == 200 and me_a.json()["id"] == str(guest_a.id)
    assert me_b.status_code == 200 and me_b.json()["id"] == str(guest_b.id)
    assert me_a.json()["id"] != me_b.json()["id"]

    # Revoking guest A's session must not touch guest B's.
    identity_a = db.scalar(select(GuestIdentity).where(GuestIdentity.guest_id == guest_a.id))
    session_a = db.scalar(select(GuestSession).where(GuestSession.identity_id == identity_a.id))
    session_a.revoked_at = datetime.now(timezone.utc)
    db.commit()

    assert client.get(ME_URL, headers=_auth(token_a)).status_code == 401
    assert client.get(ME_URL, headers=_auth(token_b)).status_code == 200


# ---------------------------------------------------------------------------
# Backward compatibility: the magic-link flow still mints jti-less tokens
# and must keep working exactly as before this change (no regression).
# ---------------------------------------------------------------------------

def test_magic_link_token_without_jti_still_works(db):
    guest = Guest(name="Magic Link Revocation Regression", phone=_random_phone(), email=f"mlr-{uuid.uuid4().hex[:8]}@example.com")
    db.add(guest)
    db.commit()

    resp = client.post("/api/v1/portal/request-login", json={"phone": guest.phone})
    assert resp.status_code == 200
    magic_token = resp.json()["dev_magic_token"]

    verify = client.post("/api/v1/portal/verify", json={"token": magic_token})
    assert verify.status_code == 200
    session_token = verify.json()["access_token"]

    me = client.get(ME_URL, headers=_auth(session_token))
    assert me.status_code == 200
    assert me.json()["id"] == str(guest.id)
