"""
Phase 3A: customer account & session data-model foundation tests.

No signup/login endpoints exist yet (Phase 3B+) — these tests exercise
the foundation directly: the CustomerAccount/CustomerSession models, the
service-layer helpers, the JWT wrapper, and the get_current_customer
dependency (called directly as a plain function, the same technique
used to test any FastAPI dependency in isolation before a route uses it).

Two tests (15, 16) do use the live app via TestClient, but only to prove
a customer token is REJECTED by the existing staff and guest endpoints —
no new endpoint is exercised, and no existing staff/guest behavior is
touched.

Run with: pytest tests/test_customer_auth_foundation.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.security import create_customer_session_token, decode_token
from app.db.session import SessionLocal
from app.main import app
from app.modules.customer_auth.models import CustomerAccount, CustomerSession
from app.modules.customer_auth.service import (
    create_customer_account,
    create_customer_session,
    get_current_customer,
    is_customer_session_active,
    revoke_customer_session,
    verify_customer_password,
)

client = TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _random_email() -> str:
    return f"customer-{uuid.uuid4().hex[:10]}@example.com"


def _random_phone() -> str:
    return f"9{uuid.uuid4().int % 10**9:09d}"


def _make_account(db, email: str | None = None, phone: str | None = None, password: str = "CorrectHorse123!") -> CustomerAccount:
    return create_customer_account(
        db,
        first_name="Ada",
        last_name="Lovelace",
        email=email or _random_email(),
        phone=phone or _random_phone(),
        password=password,
    )


# ---------------------------------------------------------------------------
# 1: CustomerAccount creation
# ---------------------------------------------------------------------------

def test_customer_account_creation(db):
    email = _random_email()
    account = _make_account(db, email=email)
    assert account.id is not None
    assert account.first_name == "Ada"
    assert account.last_name == "Lovelace"
    assert account.email == email
    assert account.created_at is not None
    assert account.updated_at is not None


# ---------------------------------------------------------------------------
# 2: Email normalization
# ---------------------------------------------------------------------------

def test_email_is_normalized_on_creation(db):
    raw = f"  MixedCase-{uuid.uuid4().hex[:8]}@Example.COM  "
    from app.modules.portal.auth_service import normalize_email

    account = _make_account(db, email=raw)
    assert account.email == normalize_email(raw)
    assert account.email == raw.strip().lower()


# ---------------------------------------------------------------------------
# 3: Duplicate normalized email rejection
# ---------------------------------------------------------------------------

def test_duplicate_normalized_email_rejected(db):
    base = f"dupe-{uuid.uuid4().hex[:8]}@example.com"
    _make_account(db, email=base)

    with pytest.raises(IntegrityError):
        # Different case/whitespace, same LOGICAL address once normalized.
        create_customer_account(db, first_name="Ada", last_name="Lovelace", email=f"  {base.upper()}  ", phone=_random_phone(), password="AnotherPass123!")
    db.rollback()


# ---------------------------------------------------------------------------
# 4-6: Argon2 hashing and password verification
# ---------------------------------------------------------------------------

def test_password_is_argon2_hashed_never_plaintext(db):
    account = _make_account(db, password="CorrectHorse123!")
    assert account.password_hash != "CorrectHorse123!"
    assert account.password_hash.startswith("$argon2")


def test_correct_password_verifies(db):
    account = _make_account(db, password="CorrectHorse123!")
    assert verify_customer_password(account, "CorrectHorse123!") is True


def test_incorrect_password_rejected(db):
    account = _make_account(db, password="CorrectHorse123!")
    assert verify_customer_password(account, "WrongPassword!") is False


# ---------------------------------------------------------------------------
# 7-8: Terms / privacy acceptance timestamps
# ---------------------------------------------------------------------------

def test_terms_accepted_at_recorded(db):
    account = _make_account(db)
    assert account.terms_accepted_at is not None
    assert isinstance(account.terms_accepted_at, datetime)


def test_privacy_accepted_at_recorded(db):
    account = _make_account(db)
    assert account.privacy_accepted_at is not None
    assert isinstance(account.privacy_accepted_at, datetime)


def test_no_date_of_birth_field_exists():
    assert not hasattr(CustomerAccount, "date_of_birth")
    assert not hasattr(CustomerAccount, "dob")


# ---------------------------------------------------------------------------
# 9: CustomerSession creation
# ---------------------------------------------------------------------------

def test_customer_session_creation(db):
    account = _make_account(db)
    session_row = create_customer_session(db, account.id)
    assert session_row.id is not None
    assert session_row.customer_id == account.id
    assert session_row.revoked_at is None
    assert session_row.expires_at > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 10-11: JWT contains correct role/type and jti
# ---------------------------------------------------------------------------

def test_customer_jwt_has_correct_role_and_type(db):
    account = _make_account(db)
    session_row = create_customer_session(db, account.id)
    token = create_customer_session_token(str(account.id), jti=str(session_row.id))

    payload = decode_token(token, expected_type="access")
    assert payload["role"] == "customer"
    assert payload["type"] == "access"
    assert payload["sub"] == str(account.id)


def test_customer_jwt_contains_jti(db):
    account = _make_account(db)
    session_row = create_customer_session(db, account.id)
    token = create_customer_session_token(str(account.id), jti=str(session_row.id))

    payload = decode_token(token, expected_type="access")
    assert payload["jti"] == str(session_row.id)


# ---------------------------------------------------------------------------
# 12-14: session active / revoked / expired
# ---------------------------------------------------------------------------

def test_active_customer_session_is_accepted(db):
    account = _make_account(db)
    session_row = create_customer_session(db, account.id)
    assert is_customer_session_active(db, session_row.id) is True


def test_revoked_customer_session_is_rejected(db):
    account = _make_account(db)
    session_row = create_customer_session(db, account.id)
    revoke_customer_session(db, session_row.id)
    assert is_customer_session_active(db, session_row.id) is False


def test_expired_customer_session_is_rejected(db):
    account = _make_account(db)
    session_row = create_customer_session(db, account.id, lifetime=timedelta(seconds=-1))
    assert is_customer_session_active(db, session_row.id) is False


def test_missing_customer_session_is_rejected(db):
    assert is_customer_session_active(db, uuid.uuid4()) is False


# ---------------------------------------------------------------------------
# 15-16: a customer token must never be accepted as a staff or guest token
# ---------------------------------------------------------------------------

def _customer_token(db) -> str:
    account = _make_account(db)
    session_row = create_customer_session(db, account.id)
    return create_customer_session_token(str(account.id), jti=str(session_row.id))


def test_customer_token_rejected_by_staff_endpoint(db):
    token = _customer_token(db)
    resp = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_customer_token_rejected_by_guest_endpoint(db):
    token = _customer_token(db)
    resp = client.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 17: no automatic booking access
# ---------------------------------------------------------------------------

def test_customer_account_has_no_automatic_booking_access(db):
    token = _customer_token(db)
    resp = client.get("/api/v1/portal/bookings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    # Structural guarantee, not just a behavioral one: nothing on
    # CustomerAccount references a Guest at all.
    assert not hasattr(CustomerAccount, "guest_id")


# ---------------------------------------------------------------------------
# 18: cross-customer session isolation
# ---------------------------------------------------------------------------

def test_cross_customer_session_isolation(db):
    account_a = _make_account(db)
    account_b = _make_account(db)
    session_a = create_customer_session(db, account_a.id)
    session_b = create_customer_session(db, account_b.id)
    token_a = create_customer_session_token(str(account_a.id), jti=str(session_a.id))
    token_b = create_customer_session_token(str(account_b.id), jti=str(session_b.id))

    resolved_a = get_current_customer(token=token_a, db=db)
    resolved_b = get_current_customer(token=token_b, db=db)
    assert resolved_a.id == account_a.id
    assert resolved_b.id == account_b.id
    assert resolved_a.id != resolved_b.id

    # Revoking A's session must not affect B's.
    revoke_customer_session(db, session_a.id)
    with pytest.raises(HTTPException):
        get_current_customer(token=token_a, db=db)
    still_b = get_current_customer(token=token_b, db=db)
    assert still_b.id == account_b.id
