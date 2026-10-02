"""
Phase 3B: customer registration endpoint tests.

POST /api/v1/customer-auth/register creates exactly one CustomerAccount
row and nothing else — no CustomerSession, no JWT, no Guest/GuestIdentity/
User/UserSession row is ever touched by this endpoint. These tests prove
both the happy path and every security boundary from the Phase 3B task.

Run with: pytest tests/test_customer_signup.py -v
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.bookings.models import Guest
from app.modules.customer_auth.models import CustomerAccount, CustomerSession
from app.modules.customer_auth.router import CUSTOMER_REGISTER_MAX_PER_IP
from app.modules.portal.auth_models import GuestIdentity
from app.modules.portal.auth_service import normalize_email

client = TestClient(app)
REGISTER_URL = "/api/v1/customer-auth/register"


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
    return f"signup-{uuid.uuid4().hex[:10]}@example.com"


def _random_phone() -> str:
    return f"9{uuid.uuid4().int % 10**9:09d}"


def _valid_payload(**overrides) -> dict:
    payload = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": _random_email(),
        "phone": _random_phone(),
        "password": "CorrectHorse123!",
        "terms_accepted": True,
        "privacy_accepted": True,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1: successful signup
# ---------------------------------------------------------------------------

def test_successful_signup(db):
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == email
    assert body["first_name"] == "Ada"
    assert body["last_name"] == "Lovelace"
    assert body["email_verified_at"] is None
    assert "password" not in body
    assert "password_hash" not in body

    account = db.scalar(select(CustomerAccount).where(CustomerAccount.email == email))
    assert account is not None
    assert account.terms_accepted_at is not None
    assert account.privacy_accepted_at is not None


# ---------------------------------------------------------------------------
# 2-3: first/last name required
# ---------------------------------------------------------------------------

def test_first_name_required():
    resp = client.post(REGISTER_URL, json=_valid_payload(first_name="   "))
    assert resp.status_code == 422


def test_last_name_required():
    resp = client.post(REGISTER_URL, json=_valid_payload(last_name=""))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4: invalid email
# ---------------------------------------------------------------------------

def test_invalid_email_rejected():
    resp = client.post(REGISTER_URL, json=_valid_payload(email="not-an-email"))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4b: phone required and validated (Phase 3E)
# ---------------------------------------------------------------------------

def test_phone_required():
    payload = _valid_payload()
    del payload["phone"]
    resp = client.post(REGISTER_URL, json=payload)
    assert resp.status_code == 422


def test_invalid_phone_rejected():
    resp = client.post(REGISTER_URL, json=_valid_payload(phone="123"))
    assert resp.status_code == 422


def test_phone_is_returned_and_normalized(db):
    """International E.164 normalization (customer_auth.phone_service) —
    deliberately NOT the bare 10-digit Guest/booking convention."""
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email, phone="+91 98765 43210"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["phone"] == "+919876543210"


def test_duplicate_phone_returns_409(db):
    phone = _random_phone()
    first = client.post(REGISTER_URL, json=_valid_payload(phone=phone))
    assert first.status_code == 201
    stored_phone = first.json()["phone"]

    second = client.post(REGISTER_URL, json=_valid_payload(phone=phone))
    assert second.status_code == 409

    count = db.query(CustomerAccount).filter(CustomerAccount.phone == stored_phone).count()
    assert count == 1


def test_duplicate_phone_detected_across_different_formatting_of_the_same_number(db):
    """"+91 98765 43210", "91-98765-43210", and "098765 43210" must all
    resolve to the SAME normalized number and therefore collide — this is
    exactly the "prevent duplicate accounts from different formatting"
    requirement, not merely "the identical string twice"."""
    bare = _random_phone()
    formatted_with_country_code = f"+91 {bare[:5]} {bare[5:]}"
    formatted_with_leading_zero = f"0{bare}"

    first = client.post(REGISTER_URL, json=_valid_payload(phone=formatted_with_country_code))
    assert first.status_code == 201, first.text
    assert first.json()["phone"] == f"+91{bare}"

    second = client.post(REGISTER_URL, json=_valid_payload(phone=formatted_with_leading_zero))
    assert second.status_code == 409, second.text


# ---------------------------------------------------------------------------
# 4c: worldwide phone numbers (not just India) — E.164 support
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_phone,expected_e164",
    [
        ("+33 6 12 34 56 78", "+33612345678"),  # France
        ("+44 20 7946 0958", "+442079460958"),  # United Kingdom
        ("+1 415-555-0132", "+14155550132"),  # United States
        ("+61 4 1234 5678", "+61412345678"),  # Australia
        ("+91 91234 56780", "+919123456780"),  # India — must keep working (distinct literal from other tests' own fixed number, to avoid a cross-test 409 in the shared test DB)
    ],
)
def test_international_phone_numbers_accepted_and_normalized_to_e164(raw_phone, expected_e164):
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email, phone=raw_phone))
    assert resp.status_code == 201, resp.text
    assert resp.json()["phone"] == expected_e164


def test_phone_missing_country_code_falls_back_to_india_default_region():
    """A bare national-format number with no explicit country code (the
    shape every pre-existing Indian customer's number is already in) must
    still work — this project's default/assumed region, not a forced
    requirement to always type a country code."""
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email, phone="9876543211"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["phone"] == "+919876543211"


def test_implausible_phone_number_rejected_even_with_a_country_code():
    """A country code alone doesn't make a garbage number valid —
    phonenumbers' own length/pattern validation for that region still
    applies."""
    resp = client.post(REGISTER_URL, json=_valid_payload(phone="+1 123"))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5: email normalization
# ---------------------------------------------------------------------------

def test_email_is_normalized(db):
    raw = f"  MixedCase-{uuid.uuid4().hex[:8]}@Example.COM  "
    resp = client.post(REGISTER_URL, json=_valid_payload(email=raw))
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == normalize_email(raw)


# ---------------------------------------------------------------------------
# 6-7: Argon2 hashing, plaintext never stored
# ---------------------------------------------------------------------------

def test_password_is_hashed_and_plaintext_never_stored(db):
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email, password="CorrectHorse123!"))
    assert resp.status_code == 201
    account = db.scalar(select(CustomerAccount).where(CustomerAccount.email == email))
    assert account.password_hash != "CorrectHorse123!"
    assert account.password_hash.startswith("$argon2")
    assert "CorrectHorse123!" not in account.password_hash


def test_weak_password_rejected():
    resp = client.post(REGISTER_URL, json=_valid_payload(password="alllowercase"))
    assert resp.status_code == 422
    resp2 = client.post(REGISTER_URL, json=_valid_payload(password="short1"))
    assert resp2.status_code == 422


# ---------------------------------------------------------------------------
# 8-9: terms / privacy must be accepted
# ---------------------------------------------------------------------------

def test_terms_must_be_accepted(db):
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email, terms_accepted=False))
    assert resp.status_code == 400
    assert db.scalar(select(CustomerAccount).where(CustomerAccount.email == email)) is None


def test_privacy_must_be_accepted(db):
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email, privacy_accepted=False))
    assert resp.status_code == 400
    assert db.scalar(select(CustomerAccount).where(CustomerAccount.email == email)) is None


# ---------------------------------------------------------------------------
# 10-11: duplicate normalized email -> 409, case/whitespace insensitive
# ---------------------------------------------------------------------------

def test_duplicate_normalized_email_returns_409(db):
    email = _random_email()
    first = client.post(REGISTER_URL, json=_valid_payload(email=email))
    assert first.status_code == 201

    second = client.post(REGISTER_URL, json=_valid_payload(email=email))
    assert second.status_code == 409

    count = db.query(CustomerAccount).filter(CustomerAccount.email == email).count()
    assert count == 1


def test_duplicate_check_is_case_and_whitespace_insensitive(db):
    base = f"casecheck-{uuid.uuid4().hex[:8]}@example.com"
    first = client.post(REGISTER_URL, json=_valid_payload(email=base))
    assert first.status_code == 201

    second = client.post(REGISTER_URL, json=_valid_payload(email=f"  {base.upper()}  "))
    assert second.status_code == 409

    count = db.query(CustomerAccount).filter(CustomerAccount.email == base).count()
    assert count == 1


# ---------------------------------------------------------------------------
# 12-14: no automatic linking / no Guest / GuestIdentity / booking access
# ---------------------------------------------------------------------------

def test_matching_guest_email_does_not_link_customer_account(db):
    shared_email = _random_email()
    guest = Guest(name="Existing Guest", phone=f"93{uuid.uuid4().int % 10**8:08d}", email=shared_email)
    db.add(guest)
    db.commit()
    guest_id_before = guest.id

    resp = client.post(REGISTER_URL, json=_valid_payload(email=shared_email))
    assert resp.status_code == 201, resp.text

    # The Guest row must be completely untouched.
    reloaded_guest = db.get(Guest, guest_id_before)
    assert reloaded_guest.email == shared_email
    assert reloaded_guest.name == "Existing Guest"

    # The explicit link is nullable and signup never guesses an existing
    # Guest from matching email/name alone.
    account = db.query(CustomerAccount).filter(CustomerAccount.email == shared_email).one()
    assert account.guest_id is None


def test_signup_creates_no_guest_identity(db):
    before = db.query(GuestIdentity).count()
    client.post(REGISTER_URL, json=_valid_payload())
    after = db.query(GuestIdentity).count()
    assert after == before


def test_signup_grants_no_booking_access(db):
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email))
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" not in body
    assert "bookings" not in body


# ---------------------------------------------------------------------------
# 15-16: no session, no JWT on signup
# ---------------------------------------------------------------------------

def test_signup_does_not_create_a_customer_session(db):
    before = db.query(CustomerSession).count()
    resp = client.post(REGISTER_URL, json=_valid_payload())
    assert resp.status_code == 201
    after = db.query(CustomerSession).count()
    assert after == before


def test_signup_response_contains_no_jwt(db):
    resp = client.post(REGISTER_URL, json=_valid_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" not in body
    assert "token" not in body
    assert "jwt" not in body


# ---------------------------------------------------------------------------
# 17: audit logging never contains the password or its hash
# ---------------------------------------------------------------------------

def test_audit_log_never_contains_password_or_hash(db):
    email = _random_email()
    password = "CorrectHorse123!"
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email, password=password))
    assert resp.status_code == 201

    account = db.scalar(select(CustomerAccount).where(CustomerAccount.email == email))
    real_hash = account.password_hash

    # Also attempt a duplicate signup to generate the failure-path audit row too.
    client.post(REGISTER_URL, json=_valid_payload(email=email, password=password))

    rows = db.scalars(
        select(AuditLog).where(AuditLog.action.in_(["customer_auth.signup_success", "customer_auth.signup_failure"]))
    ).all()
    assert rows
    for row in rows:
        for attr in ("details", "entity_id", "previous_value", "new_value"):
            value = getattr(row, attr, None)
            if value is not None:
                assert password not in value
                assert real_hash not in value


# ---------------------------------------------------------------------------
# 18: rate limiting
# ---------------------------------------------------------------------------

def test_rate_limiting_blocks_after_threshold():
    for _ in range(CUSTOMER_REGISTER_MAX_PER_IP):
        resp = client.post(REGISTER_URL, json=_valid_payload())
        assert resp.status_code == 201, resp.text

    blocked = client.post(REGISTER_URL, json=_valid_payload())
    assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# 19: CustomerAccount stays separate from staff User
# ---------------------------------------------------------------------------

def test_customer_account_separate_from_staff_user_table(db):
    email = _random_email()
    resp = client.post(REGISTER_URL, json=_valid_payload(email=email))
    assert resp.status_code == 201

    assert db.scalar(select(User).where(User.email == email)) is None
    assert CustomerAccount.__tablename__ != User.__tablename__
    assert not hasattr(User, "terms_accepted_at")


# ---------------------------------------------------------------------------
# 20: signup welcome email — dispatched via BackgroundTask, best-effort,
# never blocks or fails signup. TestClient runs BackgroundTasks inline
# before client.post() returns (same assumption test_public_booking_
# payment_gate.py's dispatch_booking_notifications tests already rely on),
# so these assertions run immediately after the request, no waiting needed.
# ---------------------------------------------------------------------------

WELCOME_SEND_PATH = "app.modules.notifications.service.send_email"


def test_successful_signup_triggers_one_welcome_email(db):
    email = _random_email()
    with patch(WELCOME_SEND_PATH) as mock_send:
        resp = client.post(REGISTER_URL, json=_valid_payload(email=email))
        assert resp.status_code == 201, resp.text
        mock_send.assert_called_once()


def test_welcome_email_addressed_to_new_customer():
    email = _random_email()
    with patch(WELCOME_SEND_PATH) as mock_send:
        resp = client.post(REGISTER_URL, json=_valid_payload(email=email))
        assert resp.status_code == 201
        to_email = mock_send.call_args[0][0]
        assert to_email == email


def test_welcome_email_contains_customer_name():
    with patch(WELCOME_SEND_PATH) as mock_send:
        resp = client.post(REGISTER_URL, json=_valid_payload(first_name="Grace", last_name="Hopper"))
        assert resp.status_code == 201
        args, kwargs = mock_send.call_args
        body = args[2]
        html_body = kwargs.get("html_body") or ""
        assert "Grace Hopper" in body
        assert "Grace Hopper" in html_body


def test_welcome_email_subject_is_exact():
    with patch(WELCOME_SEND_PATH) as mock_send:
        resp = client.post(REGISTER_URL, json=_valid_payload())
        assert resp.status_code == 201
        subject = mock_send.call_args[0][1]
        assert subject == "Welcome to Sri Apartment Homestay"


def test_welcome_email_login_url_is_production_domain():
    with patch(WELCOME_SEND_PATH) as mock_send:
        resp = client.post(REGISTER_URL, json=_valid_payload())
        assert resp.status_code == 201
        args, kwargs = mock_send.call_args
        body = args[2]
        html_body = kwargs.get("html_body") or ""
        assert "https://sriapartment.in/login" in body
        assert "https://sriapartment.in/login" in html_body
        assert "localhost" not in html_body


def test_welcome_email_contains_logo_reference():
    with patch(WELCOME_SEND_PATH) as mock_send:
        resp = client.post(REGISTER_URL, json=_valid_payload())
        assert resp.status_code == 201
        args, kwargs = mock_send.call_args
        html_body = kwargs.get("html_body") or ""
        inline_images = kwargs.get("inline_images") or {}
        assert "cid:logo" in html_body
        assert "logo" in inline_images


def test_failed_welcome_email_delivery_does_not_roll_back_account(db):
    email = _random_email()
    with patch(WELCOME_SEND_PATH, side_effect=OSError("smtp unreachable")):
        resp = client.post(REGISTER_URL, json=_valid_payload(email=email))
        assert resp.status_code == 201, resp.text

    account = db.scalar(select(CustomerAccount).where(CustomerAccount.email == email))
    assert account is not None


def test_failed_welcome_email_delivery_does_not_expose_smtp_error_to_customer():
    email = _random_email()
    with patch(WELCOME_SEND_PATH, side_effect=OSError("smtp unreachable")):
        resp = client.post(REGISTER_URL, json=_valid_payload(email=email))
        assert resp.status_code == 201, resp.text
        body_text = resp.text.lower()
        assert "smtp" not in body_text
        assert "unreachable" not in body_text
        body = resp.json()
        assert "detail" not in body  # a real CustomerAccountOut, not an error response
        assert body["email"] == email


def test_duplicate_signup_does_not_send_welcome_email():
    email = _random_email()
    with patch(WELCOME_SEND_PATH) as mock_send:
        first = client.post(REGISTER_URL, json=_valid_payload(email=email))
        assert first.status_code == 201
        mock_send.assert_called_once()

        mock_send.reset_mock()
        second = client.post(REGISTER_URL, json=_valid_payload(email=email))
        assert second.status_code == 409
        mock_send.assert_not_called()
