"""
Phase 2 of the guest-auth redesign: mobile OTP tests only (no email OTP,
no Google — out of scope for this phase). send_sms is mocked in every
test that reaches request-otp — no real SMS is ever sent, and no SMS
provider credential is required or read.

Run with: pytest tests/test_phone_otp_auth.py -v
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
from app.modules.portal.auth_router import OTP_REQUEST_MAX_PER_IP, OTP_REQUEST_MAX_PER_PHONE
from app.modules.portal.auth_service import OTP_RESEND_COOLDOWN_SECONDS, is_guest_session_active, normalize_phone
from app.shared import rate_limit as rate_limit_module

client = TestClient(app)

PATCH_TARGET = "app.modules.portal.auth_router.send_otp_sms"
REQUEST_URL = "/api/v1/portal/auth/phone/request-otp"
VERIFY_URL = "/api/v1/portal/auth/phone/verify-otp"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clear_rate_limit_state():
    """Rate limiting is in-memory/per-process — clear it around every test
    so tests can't bleed into each other's counters."""
    rate_limit_module._attempts.clear()
    yield
    rate_limit_module._attempts.clear()


def _random_phone() -> str:
    return f"97{uuid.uuid4().int % 10**8:08d}"


def _request_otp(phone: str, mock_send) -> tuple[str, str]:
    """mock_send is still passed/patched by every caller as a defense-in-
    depth guarantee (see test_dev_phone_otp_never_invokes_real_sms_sender),
    but the code itself now comes from dev_otp_code — the credential-
    independent safety gate means send_sms is never actually called in
    this (non-production) test environment, so there's nothing to extract
    from a mock call any more."""
    resp = client.post(REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 200, resp.text
    assert not mock_send.called
    return resp.json()["challenge_id"], resp.json()["dev_otp_code"]


def _wrong_code(real_code: str) -> str:
    return "000000" if real_code != "000000" else "111111"


def _rewind_cooldown(db, identifier: str) -> None:
    """Test-only helper: makes the most recent challenge for an identifier
    look old enough that the resend cooldown no longer applies, so a test
    can issue a second real request without waiting 60 real seconds."""
    latest = db.scalar(
        select(OtpChallenge).where(OtpChallenge.identifier == identifier).order_by(OtpChallenge.created_at.desc())
    )
    latest.created_at = datetime.now(timezone.utc) - timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS + 5)
    db.commit()


# ---------------------------------------------------------------------------
# 1-3: valid request, hashing, SMS called through a mock
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_valid_phone_otp_request(mock_send):
    challenge_id, code = _request_otp(_random_phone(), mock_send)
    assert challenge_id
    assert len(code) == 6 and code.isdigit()


@patch(PATCH_TARGET)
def test_otp_hashed_never_plaintext(mock_send, db):
    challenge_id, code = _request_otp(_random_phone(), mock_send)
    row = db.get(OtpChallenge, uuid.UUID(challenge_id))
    assert row.code_hash != code
    assert code not in row.code_hash
    assert len(row.code_hash) == 64  # sha256 hex digest


@patch(PATCH_TARGET)
def test_sms_service_called_through_mock(mock_send, monkeypatch):
    """Proves the mock correctly intercepts a real call in the one path
    where send_sms IS actually invoked (production) — the credential-
    independent safety gate means it's never called in non-production at
    all, so that's not where this particular property is meaningful."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "production")
    phone = _random_phone()
    resp = client.post(REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 200
    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == normalize_phone(phone)


# ---------------------------------------------------------------------------
# 4-5: identical generic response regardless of Guest match
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_unknown_phone_generic_response(mock_send):
    resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    assert resp.status_code == 200
    assert "challenge_id" in resp.json()


@patch(PATCH_TARGET)
def test_existing_guest_phone_same_generic_response(mock_send, db):
    guest = Guest(name="OTP Existing Guest", phone=_random_phone())
    db.add(guest)
    db.commit()

    resp_unknown = client.post(REQUEST_URL, json={"phone": _random_phone()})
    resp_known = client.post(REQUEST_URL, json={"phone": guest.phone})
    assert resp_unknown.status_code == resp_known.status_code == 200
    assert resp_unknown.json()["message"] == resp_known.json()["message"]


# ---------------------------------------------------------------------------
# 6: expiration
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_otp_expires_correctly(mock_send, db):
    challenge_id, code = _request_otp(_random_phone(), mock_send)
    row = db.get(OtpChallenge, uuid.UUID(challenge_id))
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7-8: attempt counting and max-attempts lockout
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_incorrect_otp_increments_attempts(mock_send, db):
    challenge_id, code = _request_otp(_random_phone(), mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": _wrong_code(code)})
    assert resp.status_code == 401
    row = db.get(OtpChallenge, uuid.UUID(challenge_id))
    assert row.attempt_count == 1


@patch(PATCH_TARGET)
def test_max_attempts_blocks_further_verification(mock_send):
    challenge_id, code = _request_otp(_random_phone(), mock_send)
    wrong = _wrong_code(code)
    for _ in range(5):
        resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": wrong})
        assert resp.status_code == 401
    # Even the CORRECT code must now be refused.
    final = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert final.status_code == 401


# ---------------------------------------------------------------------------
# 9-10: successful auth, single-use
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_correct_otp_authenticates_successfully(mock_send):
    challenge_id, code = _request_otp(_random_phone(), mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]
    assert resp.json()["token_type"] == "bearer"


@patch(PATCH_TARGET)
def test_correct_otp_cannot_be_reused(mock_send):
    challenge_id, code = _request_otp(_random_phone(), mock_send)
    first = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert first.status_code == 200
    second = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert second.status_code == 401


# ---------------------------------------------------------------------------
# 11: previous OTP invalidated by a new request
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_previous_otp_invalidated_by_new_request(mock_send, db):
    phone = _random_phone()
    challenge_id_1, code_1 = _request_otp(phone, mock_send)
    _rewind_cooldown(db, normalize_phone(phone))
    challenge_id_2, code_2 = _request_otp(phone, mock_send)

    stale = client.post(VERIFY_URL, json={"challenge_id": challenge_id_1, "code": code_1})
    assert stale.status_code == 401

    fresh = client.post(VERIFY_URL, json={"challenge_id": challenge_id_2, "code": code_2})
    assert fresh.status_code == 200


# ---------------------------------------------------------------------------
# 12: resend cooldown
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_resend_cooldown_blocks_immediate_resend(mock_send):
    phone = _random_phone()
    first = client.post(REQUEST_URL, json={"phone": phone})
    assert first.status_code == 200
    second = client.post(REQUEST_URL, json={"phone": phone})
    assert second.status_code == 429


# ---------------------------------------------------------------------------
# 13-14: rate limiting by IP and by phone
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_ip_rate_limiting(mock_send):
    for _ in range(OTP_REQUEST_MAX_PER_IP):
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
        assert resp.status_code == 200
    resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    assert resp.status_code == 429


@patch(PATCH_TARGET)
def test_phone_rate_limiting(mock_send, db):
    phone = _random_phone()
    for _ in range(OTP_REQUEST_MAX_PER_PHONE):
        resp = client.post(REQUEST_URL, json={"phone": phone})
        assert resp.status_code == 200
        _rewind_cooldown(db, normalize_phone(phone))  # isolate this from the cooldown check
    resp = client.post(REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# 15: phone normalization for supported Indian formats
# ---------------------------------------------------------------------------

def test_phone_normalization_indian_formats():
    assert normalize_phone("8870237237") == "8870237237"
    assert normalize_phone("+91 88702 37237") == "8870237237"
    assert normalize_phone("918870237237") == "8870237237"
    assert normalize_phone("08870237237") == "8870237237"


# ---------------------------------------------------------------------------
# 16-17: identity linking correctness and single-guest constraint
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_existing_guest_identity_links_correctly(mock_send, db):
    guest = Guest(name="Linked OTP Guest", phone=_random_phone())
    db.add(guest)
    db.commit()

    challenge_id, code = _request_otp(guest.phone, mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(guest.id)


@patch(PATCH_TARGET)
def test_phone_identity_reused_not_duplicated(mock_send, db):
    phone = _random_phone()
    challenge_id_1, code_1 = _request_otp(phone, mock_send)
    resp1 = client.post(VERIFY_URL, json={"challenge_id": challenge_id_1, "code": code_1})
    assert resp1.status_code == 200

    _rewind_cooldown(db, normalize_phone(phone))
    challenge_id_2, code_2 = _request_otp(phone, mock_send)
    resp2 = client.post(VERIFY_URL, json={"challenge_id": challenge_id_2, "code": code_2})
    assert resp2.status_code == 200

    identities = db.scalars(
        select(GuestIdentity).where(GuestIdentity.channel == "phone", GuestIdentity.external_id == normalize_phone(phone))
    ).all()
    assert len(identities) == 1  # same identity reused across both verifications, never duplicated


def test_ambiguous_phone_across_two_guests_is_not_auto_linked(db):
    """Not part of the numbered 23, but directly exercises the required
    'stop and report, never choose one' rule for a phone shared by two
    pre-existing Guest rows — the schema has no uniqueness constraint on
    Guest.phone, so this can genuinely happen with real data."""
    from app.modules.audit.models import AuditLog

    shared_phone = _random_phone()
    db.add_all([Guest(name="Dup A", phone=shared_phone), Guest(name="Dup B", phone=shared_phone)])
    db.commit()

    with patch(PATCH_TARGET) as mock_send:
        challenge_id, code = _request_otp(shared_phone, mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200  # still authenticates — just unlinked

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "phone", GuestIdentity.external_id == normalize_phone(shared_phone)))
    assert identity.guest_id is None  # neither guest chosen

    audit_row = db.scalar(select(AuditLog).where(AuditLog.action == "guest_identity.ambiguous_phone_match"))
    assert audit_row is not None  # ambiguity was reported, not silently dropped


# ---------------------------------------------------------------------------
# 18-19: guest session creation and revocation
# ---------------------------------------------------------------------------

@patch(PATCH_TARGET)
def test_guest_session_created_after_auth(mock_send, db):
    phone = _random_phone()
    challenge_id, code = _request_otp(phone, mock_send)
    resp = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert resp.status_code == 200

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "phone", GuestIdentity.external_id == normalize_phone(phone)))
    sessions = db.scalars(select(GuestSession).where(GuestSession.identity_id == identity.id)).all()
    assert len(sessions) == 1
    assert sessions[0].revoked_at is None


@patch(PATCH_TARGET)
def test_guest_session_can_be_revoked(mock_send, db):
    phone = _random_phone()
    challenge_id, code = _request_otp(phone, mock_send)
    client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == "phone", GuestIdentity.external_id == normalize_phone(phone)))
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
    guest_a = Guest(name="Guest A P2", phone=_random_phone())
    guest_b = Guest(name="Guest B P2", phone=_random_phone())
    db.add_all([guest_a, guest_b])
    db.commit()

    challenge_a, code_a = _request_otp(guest_a.phone, mock_send)
    token_a = client.post(VERIFY_URL, json={"challenge_id": challenge_a, "code": code_a}).json()["access_token"]

    me_a = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token_a}"})
    assert me_a.status_code == 200
    assert me_a.json()["id"] == str(guest_a.id)
    assert me_a.json()["id"] != str(guest_b.id)


# ---------------------------------------------------------------------------
# 21: existing magic-link authentication is unaffected
# ---------------------------------------------------------------------------

def test_existing_magic_link_still_works(db):
    guest = Guest(name="Magic Link Guest P2", phone=_random_phone(), email=f"p2-{uuid.uuid4().hex[:8]}@example.com")
    db.add(guest)
    db.commit()

    resp = client.post("/api/v1/portal/request-login", json={"phone": guest.phone})
    assert resp.status_code == 200
    token = resp.json()["dev_magic_token"]
    assert token

    verify = client.post("/api/v1/portal/verify", json={"token": token})
    assert verify.status_code == 200


# ---------------------------------------------------------------------------
# 22: existing booking records untouched by this phase
# ---------------------------------------------------------------------------

def test_existing_booking_records_unchanged(db):
    prop = Property(name=f"P2 Prop {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
    db.add(prop)
    db.commit()
    unit = Unit(property_id=prop.id, name=f"P2 Unit {uuid.uuid4().hex[:6]}", base_rate=2500)
    db.add(unit)
    db.commit()
    guest = Guest(name="P2 Booking Guest", phone=_random_phone())
    db.add(guest)
    db.commit()
    booking = Booking(
        unit_id=unit.id, guest_id=guest.id,
        check_in=date.today() + timedelta(days=950), check_out=date.today() + timedelta(days=952),
        status="confirmed", rate_applied=2500,
    )
    db.add(booking)
    db.commit()

    reloaded = db.get(Booking, booking.id)
    assert reloaded.status == "confirmed"
    assert reloaded.rate_applied == 2500
    assert reloaded.guest_id == guest.id


# ---------------------------------------------------------------------------
# 23: no real SMS can be sent, even if a test forgets to mock
# ---------------------------------------------------------------------------

def test_sms_not_configured_still_fails_closed_in_production(monkeypatch):
    """Production's SmsNotConfiguredError handling is untouched — this
    test environment genuinely has no SMS credentials, so send_sms
    (unpatched, actually called in production) still raises it."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "production")
    resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    assert resp.status_code == 501
    assert "not configured" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Credential-independent SMS safety gate + development-only OTP mechanism
# ---------------------------------------------------------------------------

def test_dev_phone_otp_never_invokes_real_sms_sender(monkeypatch):
    """CREDENTIAL-INDEPENDENT SAFETY GATE — the core proof. send_sms is
    patched here specifically so we can assert it is NEVER CALLED in
    non-production, regardless of whether real SMS credentials exist —
    this is what actually prevents the real-send incident (already found
    and fixed for email OTP) from occurring on the phone channel too."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
        assert resp.status_code == 200
        mock_send.assert_not_called()


def test_dev_phone_otp_still_produces_dev_otp_code(monkeypatch):
    """1. Even with send_sms patched (so this holds regardless of local
    credential state), the safe development OTP mechanism still works."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
        assert resp.status_code == 200
        assert resp.json()["dev_otp_code"]
        mock_send.assert_not_called()


def test_dev_phone_otp_verification_works_normally(monkeypatch):
    """Development OTP verification still runs through the exact same
    verify_phone_otp logic — no special-cased dev path there."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
        mock_send.assert_not_called()
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200
    assert verify.json()["access_token"]


def test_production_still_permits_real_sms_delivery(monkeypatch):
    """Production path is completely unchanged: send_sms IS still
    invoked, with the real message content, when environment=='production'."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "production")
    phone = _random_phone()
    with patch(PATCH_TARGET) as mock_send:
        resp = client.post(REQUEST_URL, json={"phone": phone})
        assert resp.status_code == 200
        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == normalize_phone(phone)
        assert resp.json()["dev_otp_code"] is None  # never returned in production


def test_dev_otp_still_stored_only_as_hash(monkeypatch, db):
    """3. Returning the raw code in dev mode does not change what's
    persisted — still only a sha256 hash, same as every other challenge."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET):
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    dev_code = resp.json()["dev_otp_code"]
    row = db.get(OtpChallenge, uuid.UUID(resp.json()["challenge_id"]))
    assert row.code_hash != dev_code
    assert dev_code not in row.code_hash
    assert len(row.code_hash) == 64


def test_dev_otp_code_passes_through_normal_verification(monkeypatch):
    """4. The dev-obtained code is verified through the exact same
    verify-otp logic as any other code — no special-cased dev path there."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET):
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 200
    assert verify.json()["access_token"]


def test_dev_otp_incorrect_code_still_fails(monkeypatch):
    """5. A wrong code is still rejected even for a dev-mode challenge."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET):
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    verify = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": _wrong_code(code)})
    assert verify.status_code == 401


def test_dev_otp_expiration_still_enforced(monkeypatch, db):
    """6. Expiration still applies to a dev-mode challenge."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET):
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    row = db.get(OtpChallenge, uuid.UUID(challenge_id))
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    verify = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert verify.status_code == 401


def test_dev_otp_max_attempts_still_enforced(monkeypatch):
    """7. The attempt limit still applies to a dev-mode challenge."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET):
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    wrong = _wrong_code(code)
    for _ in range(5):
        client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": wrong})
    final = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert final.status_code == 401


def test_dev_otp_single_use_still_enforced(monkeypatch):
    """8. A dev-mode challenge is still single-use."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with patch(PATCH_TARGET):
        resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    challenge_id, code = resp.json()["challenge_id"], resp.json()["dev_otp_code"]
    first = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert first.status_code == 200
    second = client.post(VERIFY_URL, json={"challenge_id": challenge_id, "code": code})
    assert second.status_code == 401


def test_dev_otp_rate_limits_still_enforced(monkeypatch, db):
    """9. Per-IP/per-phone rate limits still apply in dev mode — the dev
    mechanism only changes the RESPONSE body, not any check before it."""
    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    phone = _random_phone()
    with patch(PATCH_TARGET):
        for _ in range(OTP_REQUEST_MAX_PER_PHONE):
            resp = client.post(REQUEST_URL, json={"phone": phone})
            assert resp.status_code == 200
            _rewind_cooldown(db, normalize_phone(phone))  # isolate the rate-limit check from the cooldown check
        resp = client.post(REQUEST_URL, json={"phone": phone})
    assert resp.status_code == 429


def test_dev_otp_value_never_logged(monkeypatch, caplog):
    """10. The raw OTP never appears in application log output."""
    import logging

    monkeypatch.setattr("app.modules.portal.auth_router.settings.environment", "development")
    with caplog.at_level(logging.DEBUG):
        with patch(PATCH_TARGET):
            resp = client.post(REQUEST_URL, json={"phone": _random_phone()})
    dev_code = resp.json()["dev_otp_code"]
    assert dev_code not in caplog.text


def test_magic_link_unaffected_by_dev_otp_mechanism(db):
    """11. The unrelated magic-link flow is unaffected by any of this."""
    guest = Guest(name="Magic Link Guest DevOTP", phone=_random_phone(), email=f"devotp-{uuid.uuid4().hex[:8]}@example.com")
    db.add(guest)
    db.commit()
    resp = client.post("/api/v1/portal/request-login", json={"phone": guest.phone})
    assert resp.status_code == 200
    token = resp.json()["dev_magic_token"]
    assert token
    verify = client.post("/api/v1/portal/verify", json={"token": token})
    assert verify.status_code == 200


def test_guest_and_booking_data_unaffected_by_dev_otp_mechanism(db):
    """12. Existing Guest/Booking data is unaffected by any of this."""
    prop = Property(name=f"DevOTP Prop {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
    db.add(prop)
    db.commit()
    unit = Unit(property_id=prop.id, name=f"DevOTP Unit {uuid.uuid4().hex[:6]}", base_rate=2200)
    db.add(unit)
    db.commit()
    guest = Guest(name="DevOTP Booking Guest", phone=_random_phone())
    db.add(guest)
    db.commit()
    booking = Booking(
        unit_id=unit.id, guest_id=guest.id,
        check_in=date.today() + timedelta(days=960), check_out=date.today() + timedelta(days=962),
        status="confirmed", rate_applied=2200,
    )
    db.add(booking)
    db.commit()

    reloaded = db.get(Booking, booking.id)
    assert reloaded.status == "confirmed"
    assert reloaded.rate_applied == 2200
    assert reloaded.guest_id == guest.id
