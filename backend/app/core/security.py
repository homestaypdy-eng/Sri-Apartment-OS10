from datetime import datetime, timedelta, timezone
from typing import Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def _create_token(subject: str, role: str, expires_delta: timedelta, token_type: Literal["access", "refresh"], jti: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, role: str) -> str:
    return _create_token(user_id, role, timedelta(minutes=settings.access_token_expire_minutes), "access")


def create_guest_magic_token(guest_id: str) -> str:
    """Short-lived (15 min) token embedded in a magic login link."""
    return _create_token(guest_id, "guest_magic", timedelta(minutes=15), "access")


def create_guest_session_token(guest_id: str, jti: str | None = None) -> str:
    """Longer-lived (24h) token issued after a magic link is verified —
    a guest browsing their bookings needs more than 15 minutes.

    `jti` is optional and unused by any current caller (today's magic-link
    flow calls this with just guest_id, unchanged). It exists so the guest-
    auth redesign's Phase 2 can mint a token whose jti matches a
    GuestSession row's id — the same revocable-session pattern
    create_refresh_token already uses for staff — without touching this
    function's existing behavior for existing callers."""
    return _create_token(guest_id, "guest", timedelta(hours=24), "access", jti=jti)


def create_customer_session_token(customer_id: str, jti: str | None = None) -> str:
    """Phase 3A customer-auth foundation: 24h token for a self-service
    CustomerAccount session, same shape as create_guest_session_token but
    with role="customer" — a distinct claim value that ensures this token
    can never satisfy shared.rbac.get_current_user (which looks its
    subject up in the `users` table, where no CustomerAccount row exists)
    or portal.router.get_current_guest (which requires role=="guest"
    exactly). Unlike create_guest_session_token's optional jti (kept
    optional there only for the pre-existing magic-link caller), jti here
    should always be a real CustomerSession id — there is no legacy
    customer token to stay backward-compatible with."""
    return _create_token(customer_id, "customer", timedelta(hours=24), "access", jti=jti)


def create_refresh_token(user_id: str, role: str, jti: str | None = None) -> str:
    return _create_token(user_id, role, timedelta(days=settings.refresh_token_expire_days), "refresh", jti=jti)


class TokenError(Exception):
    pass


def decode_token(token: str, expected_type: Literal["access", "refresh"]) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise TokenError(str(e)) from e

    if payload.get("type") != expected_type:
        raise TokenError(f"Expected {expected_type} token, got {payload.get('type')}")

    return payload
