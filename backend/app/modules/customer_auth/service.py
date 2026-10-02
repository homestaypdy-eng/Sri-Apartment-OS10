"""
Phase 3A: customer account/session helper functions and the
get_current_customer dependency. No endpoints yet (Phase 3B+) — these are
the primitives a future signup/login router will call, plus the one
piece (get_current_customer) that is itself the FastAPI dependency a
future customer-facing route would depend on.

Reuses, deliberately, rather than reimplements:
  - app.core.security.hash_password/verify_password (argon2 — the exact
    CryptContext staff passwords already use).
  - app.core.security.create_customer_session_token/decode_token (the
    existing JWT signing/verification infrastructure).
  - app.modules.portal.auth_service.normalize_email (the exact helper the
    OTP flows already use for identifier matching — not a second
    normalization implementation).
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_token, hash_password, verify_password
from app.db.session import get_db
from app.modules.customer_auth.models import CustomerAccount, CustomerLoginOtpChallenge, CustomerSession
from app.modules.customer_auth.phone_service import normalize_phone_e164
from app.modules.portal.auth_service import generate_otp_code, hash_otp_code, normalize_email, otp_expiry

CUSTOMER_SESSION_LIFETIME = timedelta(hours=24)
# Phase 3E: same 5-attempts default OtpChallenge already uses — reused
# value, not re-derived, for the customer-login second factor.
CUSTOMER_LOGIN_OTP_MAX_ATTEMPTS = 5


def hash_reset_token(raw_token: str) -> str:
    """Phase 3D: sha256 hex digest of a raw password-reset token — same
    hashing choice auth/router.py's staff forgot_password already uses
    for PasswordResetToken (a fast hash is appropriate here because the
    token's security comes from expiry + single-use + rate limiting, not
    from resisting offline cracking, the same reasoning OtpChallenge's
    hash_otp_code already documents). A shared function so
    forgot-password (hash to store) and reset-password (hash to look up)
    can't drift into two different hashing schemes."""
    return hashlib.sha256(raw_token.encode()).hexdigest()

_customer_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/customer-auth/login", auto_error=False)


def create_customer_account(
    db: Session,
    first_name: str,
    last_name: str,
    email: str,
    phone: str,
    password: str,
    terms_accepted_at: datetime | None = None,
    privacy_accepted_at: datetime | None = None,
) -> CustomerAccount:
    """Creates and commits a new CustomerAccount. Uniqueness of the
    normalized email/phone is enforced by the database's own constraints
    (a unique index on email, a partial unique index on phone) — this
    function does not pre-check for a duplicate itself (a signup endpoint
    should do that check for a clean 409, the same convention
    auth/router.py's register_user already uses for staff — this
    foundation only guarantees the constraints actually exist at the
    database level).

    Re-normalizes both email and phone here as a safety net (they're
    already normalized by the schema validators before the router ever
    calls this function) — normalize_email/normalize_phone_e164 are both
    idempotent, so calling them again on an already-normalized value is a
    harmless no-op, the same defensive-redundancy this function has
    always applied to email.

    terms_accepted_at/privacy_accepted_at default to "now" — a future
    signup endpoint should instead pass the exact moment the request was
    received, if that distinction ever matters."""
    now = datetime.now(timezone.utc)
    account = CustomerAccount(
        first_name=first_name,
        last_name=last_name,
        email=normalize_email(email),
        phone=normalize_phone_e164(phone),
        password_hash=hash_password(password),
        terms_accepted_at=terms_accepted_at or now,
        privacy_accepted_at=privacy_accepted_at or now,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def create_customer_account_via_oauth(
    db: Session, email: str, first_name: str, last_name: str, *, email_verified: bool = False,
) -> CustomerAccount:
    """Phase 3G: creates a CustomerAccount for a brand-new Google
    sign-in — no phone (nullable at the DB level, same as any pre-Phase-
    3E legacy account — see CustomerAccount.phone's own docstring) and a
    randomly generated, never-revealed, never-logged password hash, so
    password-based login stays impossible for this account until the
    customer deliberately sets a real password via the existing forgot-
    password flow. Does NOT create a CustomerSocialIdentity row itself —
    the caller (oauth_service.resolve_customer_for_oauth) does that in
    the same transaction, since this function only knows how to create
    the account, not which provider identity it's for."""
    now = datetime.now(timezone.utc)
    account = CustomerAccount(
        first_name=first_name,
        last_name=last_name,
        email=normalize_email(email),
        phone=None,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        email_verified_at=now if email_verified else None,
        terms_accepted_at=now,
        privacy_accepted_at=now,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def verify_customer_password(account: CustomerAccount, plain_password: str) -> bool:
    return verify_password(plain_password, account.password_hash)


def create_customer_session(
    db: Session,
    customer_id,
    ip_address: str | None = None,
    user_agent: str | None = None,
    lifetime: timedelta = CUSTOMER_SESSION_LIFETIME,
) -> CustomerSession:
    session_row = CustomerSession(
        customer_id=customer_id,
        ip_address=ip_address,
        user_agent=user_agent,
        expires_at=datetime.now(timezone.utc) + lifetime,
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    return session_row


def create_customer_login_otp_challenge(db: Session, customer_id, channel: str) -> tuple[CustomerLoginOtpChallenge, str]:
    """Phase 3E: the second factor for customer login. Invalidates any
    previous unused login-OTP challenge for this customer first — same "at
    most one valid at a time" convention OtpChallenge and
    CustomerPasswordResetToken already use — then generates and stores a
    new one. Returns (challenge, raw_code); the caller is responsible for
    actually sending raw_code (or exposing it as dev_otp_code outside
    production) and never persisting it anywhere else.

    Reuses generate_otp_code/hash_otp_code/otp_expiry from
    portal.auth_service unchanged — the same primitives the guest OTP
    flows already use, not a second implementation."""
    now = datetime.now(timezone.utc)
    still_active = db.scalars(
        select(CustomerLoginOtpChallenge).where(
            CustomerLoginOtpChallenge.customer_id == customer_id,
            CustomerLoginOtpChallenge.consumed_at.is_(None),
        )
    ).all()
    for old in still_active:
        old.consumed_at = now

    raw_code = generate_otp_code()
    challenge = CustomerLoginOtpChallenge(
        customer_id=customer_id,
        channel=channel,
        code_hash=hash_otp_code(raw_code),
        expires_at=otp_expiry(),
        max_attempts=CUSTOMER_LOGIN_OTP_MAX_ATTEMPTS,
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge, raw_code


def is_customer_session_active(db: Session, session_id) -> bool:
    """Mirrors portal.auth_service.is_guest_session_active, plus an
    explicit expires_at check — see CustomerSession's own docstring for
    why this table (unlike GuestSession/UserSession) carries that column
    at all: an extra, independent bound on this newer auth surface's
    session lifetime, on top of the JWT's own exp claim."""
    session_row = db.get(CustomerSession, session_id)
    if session_row is None:
        return False
    if session_row.revoked_at is not None:
        return False
    if session_row.expires_at < datetime.now(timezone.utc):
        return False
    return True


def revoke_customer_session(db: Session, session_id) -> None:
    session_row = db.get(CustomerSession, session_id)
    if session_row is not None and session_row.revoked_at is None:
        session_row.revoked_at = datetime.now(timezone.utc)
        db.commit()


def _resolve_customer_session(token: str | None, db: Session) -> tuple[CustomerAccount, uuid.UUID]:
    """Shared decode+validate logic behind get_current_customer and (Phase
    3C) logout, which additionally needs to know WHICH session to revoke,
    not just who the caller is. Identical checks/error responses as
    get_current_customer always had — this is a pure internal refactor,
    not a behavior change: role must be "customer", jti is REQUIRED
    (there is no pre-Phase-3A customer token to stay compatible with), and
    the session must still be active."""
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError:
        raise HTTPException(401, "Invalid or expired session")
    if payload.get("role") != "customer":
        raise HTTPException(403, "This endpoint requires a customer session, not a staff/admin or guest token")

    session_id = payload.get("jti")
    if not session_id:
        raise HTTPException(401, "Invalid or expired session")
    try:
        session_uuid = uuid.UUID(session_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(401, "Invalid or expired session")
    if not is_customer_session_active(db, session_uuid):
        raise HTTPException(401, "Invalid or expired session")

    customer = db.get(CustomerAccount, uuid.UUID(payload["sub"]))
    if customer is None:
        raise HTTPException(401, "Customer account not found")
    return customer, session_uuid


def get_current_customer(
    token: str | None = Depends(_customer_oauth2_scheme), db: Session = Depends(get_db)
) -> CustomerAccount:
    """FastAPI dependency for a customer-facing route. Mirrors
    portal.router.get_current_guest's shape exactly, with two deliberate
    differences: the role check is "customer" (never "guest" or any
    staff role), and jti is REQUIRED rather than optional — there is no
    pre-Phase-3A customer token to stay backward-compatible with, so
    every customer token is expected to carry one from day one and a
    token without it fails closed rather than being waved through.

    Does NOT update CustomerSession.last_used_at on every call — matching
    the existing session pattern exactly: neither get_current_user
    (staff) nor get_current_guest touches last_used_at on ordinary
    authenticated requests either (UserSession.last_used_at is only
    touched by /auth/refresh, not by get_current_user), so adding a write
    here would be a new pattern, not a reused one, and an unnecessary
    database write on every request."""
    customer, _session_id = _resolve_customer_session(token, db)
    return customer


def get_current_customer_with_session(
    token: str | None = Depends(_customer_oauth2_scheme), db: Session = Depends(get_db)
) -> tuple[CustomerAccount, uuid.UUID]:
    """Same checks as get_current_customer, but also returns the session
    id — used by POST /customer-auth/logout, which must revoke exactly
    the caller's own session, never inferred any other way."""
    return _resolve_customer_session(token, db)
