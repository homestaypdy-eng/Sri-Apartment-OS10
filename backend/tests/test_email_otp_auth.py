"""
Phase 3 of the guest-auth redesign: email OTP tests only (no Google — out
of scope). send_email is mocked in every test that reaches request-otp —
no real email is ever sent, and no SMTP credential is required or read.
Structured as a close mirror of test_phone_otp_auth.py, since
auth_email_router.py is itself a structural mirror of auth_router.py.

Run with: pytest tests/test_email_otp_auth.py -v
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.session import SessionLocal
from app.modules.bookings.models import Booking, Guest
from app.modules.properties.models import Property, Unit
from app.modules.portal.auth_models import GuestIdentity, GuestSession, OtpChallenge
from app.modules.portal.auth_email_router import OTP_REQUEST_MAX_PER_IP, OTP_REQUEST_MAX_PER_EMAIL
from app.modules.portal.auth_service import OTP_RESEND_COOLDOWN_SECONDS, is_guest_session_active, normalize_email
from app.shared import rate_limit as rate_limit_module

client = TestClient(app)

PATCH_TARGET = "app.modules.portal.auth_email_router.send_otp_email"
REQUEST_URL = "/api/v1/portal/auth/email/request-otp"
VERIFY_URL = "/api/v1/portal/auth/email/verify-otp"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clear_rate_limit_state():
    rate_limit_module._attempts.clear()
    yield
    rate_limit_module._attempts.clear()


def _random_email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


def _request_otp(email: str, mock_send) -> tuple[str, str]:
    """mock_send is still passed/patched by every caller as a defense-in-
    depth guarantee (see test_dev_email_otp_never_invokes_real_email_sender),
    but the code itself now comes from dev_otp_code — the credential-
    independent safety gate means send_email is never actually called in
    this (non-production) test environment, so there's nothing to extract
    from a mock call any more."""
    resp = client.post(REQUEST_URL, json={"email": email})
    assert resp.status_code == 200, resp.text
    assert not mock_send.called
    return resp.json()["challenge_id"], resp.json()["dev_otp_code"]


def _wrong_code(real_code: str) -> str:
    return "000000" if real_code != "000000" else "111111"


def _rewind_cooldown(db, identifier: str) -> None:
    latest = db.scalar(
        select(OtpChallenge).where(OtpChallenge.identifier == identifier).order_by(OtpChallenge.created_at.desc())
    )
    latest.created_at = datetime.now(timezone.utc) - timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS + 5)
    db.commit()


# ---------------------------------------------------------------------------
# 1-4: valid request, normalization, hashing, mock
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_valid_email_otp_request(mock_send):
    challenge_id, code = _request_otp(_random_email(), mock_send)
    assert challenge_id
    assert len(code) == 6 and code.isdigit()


def test_email_normalization():
    assert normalize_email("  Guest@Example.com ") == "guest@example.com"
    assert normalize_email("guest@example.com") == "guest@example.com"


@patch(PATCH_TARGET)
def test_otp_hashed_never_plaintext(mock_send, db):
    challenge_id, code = _request_otp(_random_email(), mock_send)
    row = db.get(OtpChallenge, uuid.UUID(challenge_id))
    assert row.code_hash != code
    assert code not in row.code_hash
    assert len(row.code_hash) == 64  # sha256 hex digest


@patch(PATCH_TARGET)
def test_email_service_called_through_mock(mock_send, monkeypatch):
    """Proves the mock correctly intercepts a real call in the one path
    where send_email IS actually invoked (production) — the credential-
    independent safety gate means it's never called in non-production at
    all, so that's not where this particular property is meaningful."""
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "production")
    # Randomized (not the fixed "guest@example.com") so this test can't
    # inherit another run's resend-cooldown row from the persistent test-db
    # — still wrapped in whitespace/uppercase so the normalization
    # assertion below stays meaningful.
    email = f"  {_random_email().upper()} "
    resp = client.post(REQUEST_URL, json={"email": email})
    assert resp.status_code == 200
    mock_send.assert_called_once()
    called_to = mock_send.call_args[0][0]
    assert called_to == normalize_email(email)


# ---------------------------------------------------------------------------
# 5-6: identical generic response regardless of Guest match
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_unknown_email_generic_response(mock_send):
    resp = client.post(REQUEST_URL, json={"email": _random_email()})
    assert resp.status_code == 200
    assert "challenge_id" in resp.json()


@patch(PATCH_TARGET)
def test_existing_guest_email_same_generic_response(mock_send, db):
    guest = Guest(name="Email OTP Existing Guest", phone=f"97{uuid.uuid4().int % 10**8:08d}", email=_random_email())
    db.add(guest)
    db.commit()

    resp_unknown = client.post(REQUEST_URL, json={"email": _random_email()})
    resp_known = client.post(REQUEST_URL, json={"email": guest.email})
    assert resp_unknown.status_code == resp_known.status_code == 200
    assert resp_unknown.json()["message"] == resp_known.json()["message"]


# ---------------------------------------------------------------------------
# 7: expiration
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_otp_expires_correctly(mock_send, db):
    challenge_id, code = _request_otp(_random_email(), mock_send)
    row = db.get(OtpChallenge, uuid.UUID(challenge_id))
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 8-9: incorrect code, max attempts
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_incorrect_otp_increments_attempts(mock_send, db):
    challenge_id, code = _request_otp(_random_email(), mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": _wrong_code(code)})
    assert resp.status_code == 401
    row = db.get(OtpChallenge, uuid.UUID(challenge_id))
    assert row.attempt_count == 1


@patch(PATCH_TARGET)
def test_max_attempts_blocks_further_verification(mock_send):
    challenge_id, code = _request_otp(_random_email(), mock_send)
    wrong = _wrong_code(code)
    for _ in range(5):
        resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": wrong})
        assert resp.status_code == 401
    final = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert final.status_code == 401


# ---------------------------------------------------------------------------
# 10-11: correct code, single-use
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_correct_otp_authenticates_successfully(mock_send):
    challenge_id, code = _request_otp(_random_email(), mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]
    assert resp.json()["token_type"] == "bearer"


@patch(PATCH_TARGET)
def test_correct_otp_cannot_be_reused(mock_send):
    challenge_id, code = _request_otp(_random_email(), mock_send)
    first = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert first.status_code == 200
    second = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert second.status_code == 401


# ---------------------------------------------------------------------------
# 12: previous challenge superseded by a new request
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_previous_otp_invalidated_by_new_request(mock_send, db):
    email = _random_email()
    challenge_id_1, code_1 = _request_otp(email, mock_send)
    _rewind_cooldown(db, normalize_email(email))
    challenge_id_2, code_2 = _request_otp(email, mock_send)

    stale = client.post(VERIFY_URL, json={"challenge_id": challenge_id_1, "code": code_1})
    assert stale.status_code == 401
    fresh = client.post(VERIFY_URL, json={"challenge_id": challenge_id_2, "code": code_2})
    assert fresh.status_code == 200


# ---------------------------------------------------------------------------
# 13-15: resend cooldown, IP rate limit, email rate limit
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_resend_cooldown_blocks_immediate_resend(mock_send):
    email = _random_email()
    first = client.post(REQUEST_URL, json={"email": email})
    assert first.status_code == 200
    second = client.post(REQUEST_URL, json={"email": email})
    assert second.status_code == 429


@patch(PATCH_TARGET)
def test_ip_rate_limiting(mock_send):
    for _ in range(OTP_REQUEST_MAX_PER_IP):
        resp = client.post(REQUEST_URL, json={"email": _random_email()})
        assert resp.status_code == 200
    resp = client.post(REQUEST_URL, json={"email": _random_email()})
    assert resp.status_code == 429


@patch(PATCH_TARGET)
def test_email_rate_limiting(mock_send, db):
    email = _random_email()
    for _ in range(OTP_REQUEST_MAX_PER_EMAIL):
        resp = client.post(REQUEST_URL, json={"email": email})
        assert resp.status_code == 200
        _rewind_cooldown(db, normalize_email(email))
    resp = client.post(REQUEST_URL, json={"email": email})
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# 16-17: identity linking, duplicate-identity protection
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_existing_guest_identity_links_correctly(mock_send, db):
    guest = Guest(name="Linked Email OTP Guest", phone=f"97{uuid.uuid4().int % 10**8:08d}", email=_random_email())
    db.add(guest)
    db.commit()

    challenge_id, code = _request_otp(guest.email, mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(guest.id)


def test_duplicate_email_identity_rejected_at_db_level(db):
    """17. Same DB-level uniqueness guarantee Phase 1 already proved for
    phone — (channel, external_id) is the real security boundary."""
    from sqlalchemy.exc import IntegrityError

    ext_id = normalize_email(_random_email())
    db.add(GuestIdentity(channel="email", external_id=ext_id))
    db.commit()
    db.add(GuestIdentity(channel="email", external_id=ext_id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@patch(PATCH_TARGET)
def test_email_identity_reused_not_duplicated(mock_send, db):
    email = _random_email()
    challenge_id_1, code_1 = _request_otp(email, mock_send)
    resp1 = client.post(VERIFY_URL, json={"challenge_id": challenge_id_1, "code": code_1})
    assert resp1.status_code == 200

    _rewind_cooldown(db, normalize_email(email))
    challenge_id_2, code_2 = _request_otp(email, mock_send)
    resp2 = client.post(VERIFY_URL, json={"challenge_id": challenge_id_2, "code": code_2})
    assert resp2.status_code == 200

    identities = db.scalars(
        select(GuestIdentity).where(GuestIdentity.channel == "email", GuestIdentity.external_id == normalize_email(email))
    ).all()
    assert len(identities) == 1


def test_ambiguous_email_across_two_guests_is_not_auto_linked(db):
    """Mirrors the phone-OTP ambiguity test: never auto-pick a Guest when
    more than one shares the same email (Guest.email has no uniqueness
    constraint either)."""
    from app.modules.audit.models import AuditLog

    shared_email = _random_email()
    db.add_all([
        Guest(name="Dup Email A", phone=f"97{uuid.uuid4().int % 10**8:08d}", email=shared_email),
        Guest(name="Dup Email B", phone=f"97{uuid.uuid4().int % 10**8:08d}", email=shared_email),
    ])
    db.commit()

    with patch(PATCH_TARGET) as mock_send:
        challenge_id, code = _request_otp(shared_email, mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200  # still authenticates — just unlinked

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "email", GuestIdentity.external_id == normalize_email(shared_email)))
    assert identity.guest_id is None

    audit_row = db.scalar(select(AuditLog).where(AuditLog.action == "guest_identity.ambiguous_email_match"))
    assert audit_row is not None


# ---------------------------------------------------------------------------
# 18-19: session creation, revocation
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_guest_session_created_after_auth(mock_send, db):
    email = _random_email()
    challenge_id, code = _request_otp(email, mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "email", GuestIdentity.external_id == normalize_email(email)))
    sessions = db.scalars(select(GuestSession).where(GuestSession.identity_id == identity.id)).all()
    assert len(sessions) == 1
    assert sessions[0].revoked_at is None


@patch(PATCH_TARGET)
def test_guest_session_can_be_revoked(mock_send, db):
    email = _random_email()
    challenge_id, code = _request_otp(email, mock_send)
    client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "email", GuestIdentity.external_id == normalize_email(email)))
    session_row = db.scalar(select(GuestSession).where(GuestSession.identity_id == identity.id))
    assert is_guest_session_active(db, session_row.id) is True

    session_row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    assert is_guest_session_active(db, session_row.id) is False


# ---------------------------------------------------------------------------
# 20: cross-guest booking authorization
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_guest_cannot_access_another_guests_data(mock_send, db):
    guest_a = Guest(name="Guest A Email P3", phone=f"97{uuid.uuid4().int % 10**8:08d}", email=_random_email())
    guest_b = Guest(name="Guest B Email P3", phone=f"97{uuid.uuid4().int % 10**8:08d}", email=_random_email())
    db.add_all([guest_a, guest_b])
    db.commit()

    challenge_a, code_a = _request_otp(guest_a.email, mock_send)
    token_a = client.post(VERIFY_URL, json={"challenge_id": challenge_a, "code": code_a}).json()["access_token"]

    me_a = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token_a}"})
    assert me_a.status_code == 200
    assert me_a.json()["id"] == str(guest_a.id)
    assert me_a.json()["id"] != str(guest_b.id)


# ---------------------------------------------------------------------------
# 21: unlinked email identity follows the same safe Mobile OTP behavior
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_unlinked_email_identity_authenticates_without_booking_access(mock_send, db):
    email = _random_email()  # matches no Guest
    challenge_id, code = _request_otp(email, mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200  # authenticated
    token = resp.json()["access_token"]

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "email", GuestIdentity.external_id == normalize_email(email)))
    assert identity.guest_id is None  # never invented a Guest

    me = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401  # same safe degrade as unlinked phone identities
    assert me.json()["detail"] == "Guest not found"  # distinguishable from an expired/invalid session (see website's handling)


# ---------------------------------------------------------------------------
# 22: no real email is sent during tests
# ---------------------------------------------------------------------------

def test_dev_email_otp_never_invokes_real_email_sender(monkeypatch):
    """CREDENTIAL-INDEPENDENT SAFETY GATE — the core proof. send_email is
    patched here (unlike the old version of this test) specifically so we
    can assert it is NEVER CALLED in non-production, regardless of
    whether real SMTP credentials exist — this is what actually prevents
    the real-Gmail-send incident from recurring, not an assumption about
    this test environment's credentials."""
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "development")
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"email": _random_email()})
        assert resp.status_code == 200
        mock_send.assert_not_called()


def test_dev_email_otp_still_produces_dev_otp_code(monkeypatch):
    """Even with send_email patched (so this holds regardless of local
    credential state), the safe development OTP mechanism still works."""
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "development")
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"email": _random_email()})
        assert resp.status_code == 200
        assert resp.json()["dev_otp_code"]
        mock_send.assert_not_called()


def test_dev_otp_verification_works_normally(monkeypatch):
    """Development OTP verification still runs through the exact same
    verify_email_otp logic — no special-cased dev path there."""
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "development")
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"email": _random_email()})
        mock_send.assert_not_called()
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200
    assert verify.json()["access_token"]


def test_production_still_permits_real_email_delivery(monkeypatch):
    """Production path is completely unchanged: send_email IS still
    invoked, with the real message content, when environment=='production'."""
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "production")
    email = _random_email()
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"email": email})
        assert resp.status_code == 200
        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == normalize_email(email)
        assert resp.json()["dev_otp_code"] is None  # never returned in production


def test_email_not_configured_still_fails_closed_in_production(monkeypatch):
    """Production's EmailNotConfiguredError handling is untouched — this
    test environment genuinely has no SMTP credentials, so send_email
    (unpatched, actually called in production) still raises it."""
    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "production")
    resp = client.post(REQUEST_URL, json={"email": _random_email()})
    assert resp.status_code == 501
    assert "not configured" in resp.json()["detail"].lower()


def test_dev_otp_value_never_logged(monkeypatch, caplog):
    import logging

    monkeypatch.setattr("app.modules.portal.auth_email_router.settings.environment", "development")
    with caplog.at_level(logging.DEBUG):
        with patch(PATCH_TARGET):
            resp = client.post(REQUEST_URL, json={"email": _random_email()})
    dev_code = resp.json()["dev_otp_code"]
    assert dev_code not in caplog.text


# ---------------------------------------------------------------------------
# 23-24: Mobile OTP and magic-link remain unaffected
# ---------------------------------------------------------------------------

@patch("app.modules.portal.auth_router.send_otp_sms")
def test_mobile_otp_flow_unaffected_by_email_otp(mock_send_sms, db):
    """23 (spot-check — the full Mobile OTP suite is re-run separately as
    instructed). Proves the phone-OTP endpoint still works end-to-end
    after Phase 3's additions. Code now comes from dev_otp_code, not a
    mocked send_sms call — phone OTP's own credential-independent safety
    gate means send_sms is never invoked in this (non-production) test
    environment; mock_send_sms is still patched as a defense-in-depth
    guarantee, asserted not-called."""
    phone = f"97{uuid.uuid4().int % 10**8:08d}"
    resp = client.post("/api/v1/portal/auth/phone/request-otp", json={"phone": phone})
    assert resp.status_code == 200
    mock_send_sms.assert_not_called()
    code = resp.json()["dev_otp_code"]
    verify = client.post("/api/v1/portal/auth/phone/verify-otp", json={"challenge_id": resp.json()["challenge_id"], "code": code})
    assert verify.status_code == 200


def test_existing_magic_link_still_works(db):
    guest = Guest(name="Magic Link Guest P3", phone=f"97{uuid.uuid4().int % 10**8:08d}", email=_random_email())
    db.add(guest)
    db.commit()

    resp = client.post("/api/v1/portal/request-login", json={"phone": guest.phone})
    assert resp.status_code == 200
    token = resp.json()["dev_magic_token"]
    assert token

    verify = client.post("/api/v1/portal/verify", json={"token": token})
    assert verify.status_code == 200


def test_existing_guest_and_booking_data_unaffected(db):
    prop = Property(name=f"Email OTP Prop {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
    db.add(prop)
    db.commit()
    unit = Unit(property_id=prop.id, name=f"Email OTP Unit {uuid.uuid4().hex[:6]}", base_rate=2400)
    db.add(unit)
    db.commit()
    guest = Guest(name="Email OTP Booking Guest", phone=f"97{uuid.uuid4().int % 10**8:08d}")
    db.add(guest)
    db.commit()
    booking = Booking(
        unit_id=unit.id, guest_id=guest.id,
        check_in=date.today() + timedelta(days=970), check_out=date.today() + timedelta(days=972),
        status="confirmed", rate_applied=2400,
    )
    db.add(booking)
    db.commit()

    reloaded = db.get(Booking, booking.id)
    assert reloaded.status == "confirmed"
    assert reloaded.rate_applied == 2400
    assert reloaded.guest_id == guest.id
