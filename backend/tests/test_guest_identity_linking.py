"""
Phase 2A of the guest-auth hardening: the staff-assisted first-time guest
identity linking endpoint (POST /api/v1/guests/{guest_id}/link-identity).

Without this endpoint, a GuestIdentity created by phone/email OTP
verification with no matching Guest record (see test_phone_otp_auth.py's
/test_email_otp_auth.py's "unlinked identity" tests) had no way to ever
gain booking access — this is the recovery path.

Run with: pytest tests/test_guest_identity_linking.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.bookings.models import Guest
from app.modules.portal.auth_models import GuestIdentity, OtpChallenge
from app.modules.portal.auth_service import OTP_RESEND_COOLDOWN_SECONDS

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

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


@pytest.fixture(scope="module")
def admin_headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _staff_headers(admin_headers, role: str) -> dict:
    email = f"linktest-{role}-{uuid.uuid4().hex[:8]}@test.com"
    resp = client.post("/api/v1/auth/register", headers=admin_headers, json={"name": f"Link Test {role.title()}", "email": email, "password": "TestPass123!", "role": role})
    assert resp.status_code == 201, resp.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _random_phone() -> str:
    return f"94{uuid.uuid4().int % 10**8:08d}"


def _rewind_cooldown(phone: str) -> None:
    """Test-only helper (same as test_phone_otp_auth.py's): a repeat login
    for the same phone within one test would otherwise hit the real
    60s resend cooldown, so back-date the most recent challenge instead
    of sleeping for a real minute."""
    db = SessionLocal()
    try:
        latest = db.scalar(select(OtpChallenge).where(OtpChallenge.identifier == phone).order_by(OtpChallenge.created_at.desc()))
        if latest is not None:
            latest.created_at = datetime.now(timezone.utc) - timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS + 5)
            db.commit()
    finally:
        db.close()


def _create_unlinked_phone_identity(phone: str) -> str:
    """Runs a real phone-OTP login for a phone with NO matching Guest
    record — verify-otp creates the GuestIdentity row unlinked (guest_id
    is None), exactly like a brand-new guest logging in before staff have
    created their Guest record. Returns the session access_token. Safe to
    call more than once for the same phone in one test (rewinds the
    resend cooldown first)."""
    _rewind_cooldown(phone)
    resp = client.post(PHONE_REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 200, resp.text
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post(PHONE_VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200, verify.text
    return verify.json()["access_token"]


def _link(admin_headers, guest_id, channel: str, external_id: str):
    return client.post(f"/api/v1/guests/{guest_id}/link-identity", headers=admin_headers, json={"channel": channel, "external_id": external_id})


# ---------------------------------------------------------------------------
# Before linking: the identity is authenticated but sees no bookings
# ---------------------------------------------------------------------------

def test_unlinked_identity_cannot_see_bookings_before_linking(db):
    phone = _random_phone()
    token = _create_unlinked_phone_identity(phone)
    resp = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Correct linking: admin links, then the SAME identity's next login
# resolves to the target guest and can see that guest's data
# ---------------------------------------------------------------------------

def test_admin_can_link_unlinked_identity_to_guest(admin_headers, db):
    phone = _random_phone()
    _create_unlinked_phone_identity(phone)

    target_guest = Guest(name="Link Target Guest", phone=_random_phone())
    db.add(target_guest)
    db.commit()

    resp = _link(admin_headers, target_guest.id, "phone", phone)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["guest_id"] == str(target_guest.id)
    assert body["linked_at"] is not None

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "phone", GuestIdentity.external_id == phone))
    assert identity.guest_id == target_guest.id

    audit_rows = db.scalars(select(AuditLog).where(AuditLog.action == "guest_identity.linked").order_by(AuditLog.created_at.desc())).all()
    assert any(r.entity_id == str(identity.id) for r in audit_rows)

    # A fresh login with the now-linked phone must resolve to the target
    # guest and be able to see (their empty) booking list.
    new_token = _create_unlinked_phone_identity(phone)
    me = client.get(ME_URL, headers={"Authorization": f"Bearer {new_token}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(target_guest.id)


# ---------------------------------------------------------------------------
# Cannot link the same identity to two different guests
# ---------------------------------------------------------------------------

def test_link_prevents_double_linking_to_different_guest(admin_headers, db):
    phone = _random_phone()
    _create_unlinked_phone_identity(phone)

    guest_a = Guest(name="Link Guest A", phone=_random_phone())
    guest_b = Guest(name="Link Guest B", phone=_random_phone())
    db.add_all([guest_a, guest_b])
    db.commit()

    first = _link(admin_headers, guest_a.id, "phone", phone)
    assert first.status_code == 200

    second = _link(admin_headers, guest_b.id, "phone", phone)
    assert second.status_code == 409

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "phone", GuestIdentity.external_id == phone))
    assert identity.guest_id == guest_a.id  # unchanged by the rejected second attempt


def test_relinking_to_the_same_guest_is_rejected_not_a_silent_noop(admin_headers, db):
    phone = _random_phone()
    _create_unlinked_phone_identity(phone)
    guest = Guest(name="Link Guest Repeat", phone=_random_phone())
    db.add(guest)
    db.commit()

    first = _link(admin_headers, guest.id, "phone", phone)
    assert first.status_code == 200

    second = _link(admin_headers, guest.id, "phone", phone)
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Not found cases
# ---------------------------------------------------------------------------

def test_link_unknown_identity_returns_404(admin_headers, db):
    guest = Guest(name="Link Guest No Identity", phone=_random_phone())
    db.add(guest)
    db.commit()
    resp = _link(admin_headers, guest.id, "phone", _random_phone())
    assert resp.status_code == 404


def test_link_unknown_guest_returns_404(admin_headers):
    phone = _random_phone()
    _create_unlinked_phone_identity(phone)
    resp = _link(admin_headers, uuid.uuid4(), "phone", phone)
    assert resp.status_code == 404


def test_link_invalid_channel_returns_400(admin_headers, db):
    guest = Guest(name="Link Guest Bad Channel", phone=_random_phone())
    db.add(guest)
    db.commit()
    resp = _link(admin_headers, guest.id, "carrier_pigeon", "n/a")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Role restriction: admin-only, same tier as /guests/merge
# ---------------------------------------------------------------------------

def test_manager_cannot_link_identity(admin_headers, db):
    guest = Guest(name="Link Guest RBAC Manager", phone=_random_phone())
    db.add(guest)
    db.commit()
    phone = _random_phone()
    _create_unlinked_phone_identity(phone)

    manager_headers = _staff_headers(admin_headers, "manager")
    resp = client.post(f"/api/v1/guests/{guest.id}/link-identity", headers=manager_headers, json={"channel": "phone", "external_id": phone})
    assert resp.status_code == 403


def test_staff_cannot_link_identity(admin_headers, db):
    guest = Guest(name="Link Guest RBAC Staff", phone=_random_phone())
    db.add(guest)
    db.commit()
    phone = _random_phone()
    _create_unlinked_phone_identity(phone)

    staff_headers = _staff_headers(admin_headers, "staff")
    resp = client.post(f"/api/v1/guests/{guest.id}/link-identity", headers=staff_headers, json={"channel": "phone", "external_id": phone})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Cross-guest isolation still holds after linking
# ---------------------------------------------------------------------------

def test_cross_guest_isolation_after_linking(admin_headers, db):
    phone_a = _random_phone()
    phone_b = _random_phone()
    _create_unlinked_phone_identity(phone_a)
    _create_unlinked_phone_identity(phone_b)

    guest_a = Guest(name="Link Isolation Guest A", phone=_random_phone())
    guest_b = Guest(name="Link Isolation Guest B", phone=_random_phone())
    db.add_all([guest_a, guest_b])
    db.commit()

    assert _link(admin_headers, guest_a.id, "phone", phone_a).status_code == 200
    assert _link(admin_headers, guest_b.id, "phone", phone_b).status_code == 200

    token_a = _create_unlinked_phone_identity(phone_a)
    token_b = _create_unlinked_phone_identity(phone_b)

    me_a = client.get(ME_URL, headers={"Authorization": f"Bearer {token_a}"})
    me_b = client.get(ME_URL, headers={"Authorization": f"Bearer {token_b}"})
    assert me_a.status_code == 200 and me_a.json()["id"] == str(guest_a.id)
    assert me_b.status_code == 200 and me_b.json()["id"] == str(guest_b.id)
    assert me_a.json()["id"] != me_b.json()["id"]
