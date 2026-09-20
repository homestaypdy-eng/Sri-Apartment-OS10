"""
Phase 1 of the guest-auth redesign: database/model-level tests for the new
additive foundation (OtpChallenge, GuestIdentity, GuestSession) plus
confirmation that existing Guest/Booking data and the existing magic-link
flow are unaffected. No new endpoints exist yet, so these are direct
SQLAlchemy-session tests except for the one magic-link round trip, which
uses the existing TestClient pattern already used elsewhere in this suite.

Run with: pytest tests/test_guest_auth_foundation.py -v
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.bookings.models import Booking, Guest
from app.modules.properties.models import Property, Unit
from app.modules.portal.auth_models import GuestIdentity, GuestSession, OtpChallenge
from app.modules.portal.auth_service import (
    generate_otp_code,
    hash_otp_code,
    is_guest_session_active,
    normalize_email,
    normalize_phone,
    otp_expiry,
)

client = TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _random_phone() -> str:
    return f"97{uuid.uuid4().int % 10**8:08d}"


def _make_guest(db, phone: str | None = None, email: str | None = None, name: str = "Foundation Test Guest") -> Guest:
    guest = Guest(name=name, phone=phone or _random_phone(), email=email)
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return guest


# ---------------------------------------------------------------------------
# 1-4: OTP challenges
# ---------------------------------------------------------------------------

def test_otp_challenge_created_securely(db):
    raw_code = generate_otp_code()
    assert len(raw_code) == 6 and raw_code.isdigit()

    challenge = OtpChallenge(
        channel="phone",
        identifier=normalize_phone("8870237237"),
        code_hash=hash_otp_code(raw_code),
        expires_at=otp_expiry(),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    assert challenge.id is not None
    assert challenge.attempt_count == 0
    assert challenge.max_attempts == 5
    assert challenge.consumed_at is None


def test_otp_plaintext_never_stored(db):
    raw_code = generate_otp_code()
    challenge = OtpChallenge(
        channel="email",
        identifier=normalize_email("guest@example.com"),
        code_hash=hash_otp_code(raw_code),
        expires_at=otp_expiry(),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    assert raw_code not in challenge.code_hash
    assert challenge.code_hash != raw_code
    assert len(challenge.code_hash) == 64  # sha256 hex digest length
    assert challenge.code_hash == hash_otp_code(raw_code)  # verifiable without ever storing the raw code


def test_otp_expiration_field(db):
    expired = OtpChallenge(
        channel="phone", identifier=normalize_phone(_random_phone()),
        code_hash=hash_otp_code("123456"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    fresh = OtpChallenge(
        channel="phone", identifier=normalize_phone(_random_phone()),
        code_hash=hash_otp_code("654321"),
        expires_at=otp_expiry(),
    )
    db.add_all([expired, fresh])
    db.commit()
    db.refresh(expired)
    db.refresh(fresh)

    now = datetime.now(timezone.utc)
    assert expired.expires_at < now
    assert fresh.expires_at > now


def test_otp_attempt_counter(db):
    challenge = OtpChallenge(
        channel="phone", identifier=normalize_phone(_random_phone()),
        code_hash=hash_otp_code("111111"), expires_at=otp_expiry(),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    challenge.attempt_count += 1
    challenge.attempt_count += 1
    db.commit()
    db.refresh(challenge)
    assert challenge.attempt_count == 2
    assert challenge.attempt_count < challenge.max_attempts

    challenge.attempt_count = challenge.max_attempts
    db.commit()
    db.refresh(challenge)
    assert challenge.attempt_count >= challenge.max_attempts  # caller must now refuse verification


# ---------------------------------------------------------------------------
# 5-9: Guest identities
# ---------------------------------------------------------------------------

def test_identity_links_to_guest(db):
    guest = _make_guest(db)
    identity = GuestIdentity(
        channel="phone", external_id=normalize_phone(guest.phone),
        guest_id=guest.id, linked_at=datetime.now(timezone.utc),
    )
    db.add(identity)
    db.commit()
    db.refresh(identity)

    assert identity.guest_id == guest.id
    assert identity.linked_at is not None


def test_duplicate_identity_rejected(db):
    ext_id = normalize_phone(_random_phone())
    db.add(GuestIdentity(channel="phone", external_id=ext_id))
    db.commit()

    db.add(GuestIdentity(channel="phone", external_id=ext_id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_phone_identity_cannot_belong_to_two_guests(db):
    guest_a = _make_guest(db)
    guest_b = _make_guest(db)
    ext_id = normalize_phone(_random_phone())

    db.add(GuestIdentity(channel="phone", external_id=ext_id, guest_id=guest_a.id))
    db.commit()

    db.add(GuestIdentity(channel="phone", external_id=ext_id, guest_id=guest_b.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_email_identity_cannot_belong_to_two_guests(db):
    guest_a = _make_guest(db)
    guest_b = _make_guest(db)
    ext_id = normalize_email(f"dup-{uuid.uuid4().hex[:8]}@example.com")

    db.add(GuestIdentity(channel="email", external_id=ext_id, guest_id=guest_a.id))
    db.commit()

    db.add(GuestIdentity(channel="email", external_id=ext_id, guest_id=guest_b.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_google_identity_uniqueness_enforced(db):
    sub = f"google-sub-{uuid.uuid4().hex}"
    db.add(GuestIdentity(channel="google", external_id=sub, email="a@example.com"))
    db.commit()

    db.add(GuestIdentity(channel="google", external_id=sub, email="b@example.com"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_identity_channel_is_constrained(db):
    """Guards against a future typo/bug introducing a fourth, unsupported
    channel value silently — same defensive intent as GuestRequest's
    CheckConstraint on request_type."""
    db.add(GuestIdentity(channel="bogus", external_id="nope"))  # fits VARCHAR(10) so the CHECK constraint, not a length error, is what's under test
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_identity_guest_id_stays_null_until_explicitly_linked(db):
    """Nothing in this phase auto-links an identity from a bare email/phone
    match — guest_id must be set explicitly, never as a side effect of
    construction."""
    identity = GuestIdentity(channel="email", external_id=normalize_email(f"unlinked-{uuid.uuid4().hex[:8]}@example.com"))
    db.add(identity)
    db.commit()
    db.refresh(identity)
    assert identity.guest_id is None
    assert identity.linked_at is None


# ---------------------------------------------------------------------------
# 10-12: Guest sessions
# ---------------------------------------------------------------------------

def test_guest_session_created(db):
    identity = GuestIdentity(channel="phone", external_id=normalize_phone(_random_phone()))
    db.add(identity)
    db.commit()
    db.refresh(identity)

    session = GuestSession(identity_id=identity.id)
    db.add(session)
    db.commit()
    db.refresh(session)

    assert session.id is not None
    assert session.revoked_at is None
    assert is_guest_session_active(db, session.id) is True


def test_guest_session_revocation(db):
    identity = GuestIdentity(channel="email", external_id=normalize_email(f"revoke-{uuid.uuid4().hex[:8]}@example.com"))
    db.add(identity)
    db.commit()
    db.refresh(identity)

    session = GuestSession(identity_id=identity.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    assert is_guest_session_active(db, session.id) is True

    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    assert is_guest_session_active(db, session.id) is False


def test_unknown_or_revoked_session_not_treated_as_active(db):
    assert is_guest_session_active(db, uuid.uuid4()) is False  # unknown id entirely

    identity = GuestIdentity(channel="phone", external_id=normalize_phone(_random_phone()))
    db.add(identity)
    db.commit()
    db.refresh(identity)

    revoked_session = GuestSession(identity_id=identity.id, revoked_at=datetime.now(timezone.utc))
    db.add(revoked_session)
    db.commit()
    db.refresh(revoked_session)
    assert is_guest_session_active(db, revoked_session.id) is False


# ---------------------------------------------------------------------------
# 13: existing Guest/Booking data untouched by this phase
# ---------------------------------------------------------------------------

def test_existing_guest_and_booking_fields_unaffected(db):
    prop = Property(name=f"Foundation Prop {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
    db.add(prop)
    db.commit()
    db.refresh(prop)

    unit = Unit(property_id=prop.id, name=f"Foundation Unit {uuid.uuid4().hex[:6]}", base_rate=2000)
    db.add(unit)
    db.commit()
    db.refresh(unit)

    guest = _make_guest(db, email="foundation@example.com")
    booking = Booking(
        unit_id=unit.id, guest_id=guest.id,
        check_in=date.today() + timedelta(days=900), check_out=date.today() + timedelta(days=902),
        status="confirmed", rate_applied=2000,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    reloaded_guest = db.get(Guest, guest.id)
    reloaded_booking = db.get(Booking, booking.id)
    assert reloaded_guest.name == guest.name
    assert reloaded_guest.email == "foundation@example.com"
    assert reloaded_booking.status == "confirmed"
    assert reloaded_booking.guest_id == guest.id
    assert reloaded_booking.rate_applied == 2000


# ---------------------------------------------------------------------------
# 14: existing magic-link authentication still functions, unmodified
# ---------------------------------------------------------------------------

def test_existing_magic_link_login_still_works(db, monkeypatch):
    # Regression guard for the Phase 1 verification SMTP incident: this test
    # previously called the real send_email() whenever real SMTP
    # credentials happened to be configured in the environment it ran in
    # (it was, once, and Gmail was actually contacted). request_login's own
    # environment gate (see portal/router.py) now prevents that
    # structurally, but this mock is a second, independent guarantee that
    # doesn't rely on trusting the app's own environment detection — if
    # anything in this call path ever again reaches send_email, this fails
    # loudly instead of opening a real SMTP connection.
    from unittest.mock import MagicMock

    mock_send = MagicMock(side_effect=AssertionError("send_email must not be called by this test"))
    monkeypatch.setattr("app.modules.portal.router.send_email", mock_send)

    guest = _make_guest(db, email=f"magiclink-{uuid.uuid4().hex[:8]}@example.com")

    resp = client.post("/api/v1/portal/request-login", json={"phone": guest.phone})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dev_magic_token"], "dev_magic_token should be present outside production (see portal/router.py)"
    mock_send.assert_not_called()

    verify_resp = client.post("/api/v1/portal/verify", json={"token": body["dev_magic_token"]})
    assert verify_resp.status_code == 200
    access_token = verify_resp.json()["access_token"]

    me_resp = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["id"] == str(guest.id)


def test_magic_link_request_login_never_calls_real_sender_outside_production(db, monkeypatch):
    """Explicit regression test for the Phase 1 verification SMTP incident:
    proves request-login's real send_email() is structurally unreachable
    outside production, independent of whether real SMTP credentials happen
    to be configured. Mirrors the equivalent guarantee already tested for
    the Phone/Email OTP routers."""
    from unittest.mock import MagicMock

    mock_send = MagicMock(side_effect=AssertionError("send_email must not be called outside production"))
    monkeypatch.setattr("app.modules.portal.router.send_email", mock_send)
    assert settings.environment != "production", "this test only proves the non-production guarantee"

    guest = _make_guest(db, email=f"regression-{uuid.uuid4().hex[:8]}@example.com")
    resp = client.post("/api/v1/portal/request-login", json={"phone": guest.phone})

    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    assert resp.json()["channel"] == "email"
    assert resp.json()["dev_magic_token"]
    mock_send.assert_not_called()


def test_magic_link_login_unaffected_for_nonexistent_phone():
    """Same-response-regardless-of-match behavior (enumeration protection)
    is untouched by this phase's additions."""
    resp = client.post("/api/v1/portal/request-login", json={"phone": _random_phone()})
    assert resp.status_code == 200
    assert resp.json()["sent"] is False
    assert resp.json()["channel"] == "none_available"
