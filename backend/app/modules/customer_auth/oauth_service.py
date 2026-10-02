"""
Phase 3G: pure helpers behind customer-facing Google OAuth login — state/
link-token signing, server-side token exchange + verification, and the
identity-resolution logic that decides whether an OAuth sign-in maps to
an existing CustomerAccount, requires a proof-of-ownership link step, or
creates a new account. See oauth_router.py's module docstring for the
endpoints that call these and the exact provider-console configuration a
real deployment needs.

Google is the only social-login provider this project supports — no
other provider's app credentials, endpoints, or identity handling remain
anywhere in this module or its callers. The identity-resolution
functions below still take a `provider` string rather than hardcoding
"google" throughout, since CustomerSocialIdentity's (provider,
provider_subject) schema is already provider-agnostic and costs nothing
extra to keep that way for a possible future provider — but today,
oauth_router.py only ever calls these with provider="google".

Reuses, deliberately, rather than reimplements:
  - app.core.config.settings.jwt_secret_key/jwt_algorithm for signing the
    short-lived state/link tokens below — the SAME secret every other
    token in this project already uses, not a second signing scheme.
  - app.core.security.hash_password for the unusable random password an
    OAuth-created account gets (see service.py's
    create_customer_account_via_oauth).
  - app.modules.portal.auth_service.normalize_email for the identity
    normalization the manual signup/login flows already use.
"""
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.customer_auth.models import CustomerAccount, CustomerSocialIdentity
from app.modules.portal.auth_service import normalize_email

OAUTH_STATE_TTL_MINUTES = 10
OAUTH_LINK_TOKEN_TTL_MINUTES = 15

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class OAuthVerificationError(Exception):
    """The provider's response could not be trusted (bad token exchange,
    bad id_token signature/audience/issuer, provider error, etc.) —
    callers surface this as one generic failure message, never the
    specific reason, so a caller can't use it to probe this server's
    OAuth configuration."""


def google_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def create_oauth_state(provider: str) -> str:
    """A short-lived, SIGNED CSRF token — proves the callback corresponds
    to a /start this server itself issued within the last
    OAUTH_STATE_TTL_MINUTES. Carries no customer identity."""
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": "oauth_state",
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    }
    return jose_jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_oauth_state(token: str, expected_provider: str) -> bool:
    try:
        payload = jose_jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return False
    return payload.get("purpose") == "oauth_state" and payload.get("provider") == expected_provider


def create_oauth_link_token(provider: str, subject: str, email: str | None) -> str:
    """Issued when an OAuth callback finds a VERIFIED provider email that
    already belongs to an existing CustomerAccount which has never linked
    this provider identity before. Rather than treating "same email" as
    proof of ownership (the classic OAuth account-takeover vector), this
    token is handed back so the customer can complete a NORMAL
    password+OTP login for that account and then present this token to
    POST /oauth/{provider}/confirm-link — proving they actually control
    the account before the identities are ever linked."""
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": "oauth_link",
        "provider": provider,
        "subject": subject,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=OAUTH_LINK_TOKEN_TTL_MINUTES),
    }
    return jose_jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_oauth_link_token(token: str) -> dict | None:
    try:
        payload = jose_jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if payload.get("purpose") != "oauth_link":
        return None
    return payload


def google_auth_redirect_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_google_code(code: str) -> dict:
    """Server-side only — google_client_secret never leaves this
    process. Returns verified profile claims (subject/email/
    email_verified/first_name/last_name) taken from the id_token AFTER
    checking its signature against Google's own published JWKS and its
    audience/issuer/expiry — never a claim trusted unverified."""
    try:
        token_resp = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        id_token = token_resp.json()["id_token"]

        jwks = httpx.get(GOOGLE_JWKS_URL, timeout=15).json()
        unverified_header = jose_jwt.get_unverified_header(id_token)
        key = next((k for k in jwks["keys"] if k["kid"] == unverified_header["kid"]), None)
        if key is None:
            raise OAuthVerificationError("No matching Google signing key")

        claims = jose_jwt.decode(
            id_token, key, algorithms=[unverified_header["alg"]],
            audience=settings.google_client_id, issuer=GOOGLE_ISSUERS,
        )
    except OAuthVerificationError:
        raise
    except Exception as e:
        raise OAuthVerificationError(f"Google token exchange/verification failed: {type(e).__name__}") from e

    given_name = claims.get("given_name")
    full_name = claims.get("name") or "Customer"
    return {
        "subject": claims["sub"],
        "email": claims.get("email"),
        "email_verified": bool(claims.get("email_verified")),
        "first_name": given_name or full_name.split(" ")[0],
        "last_name": claims.get("family_name") or "",
    }


def find_customer_by_social_identity(db: Session, provider: str, subject: str) -> CustomerAccount | None:
    identity = db.scalar(
        select(CustomerSocialIdentity).where(
            CustomerSocialIdentity.provider == provider, CustomerSocialIdentity.provider_subject == subject,
        )
    )
    if identity is None:
        return None
    return db.get(CustomerAccount, identity.customer_id)


def link_social_identity(db: Session, customer_id, provider: str, subject: str, email: str | None) -> None:
    db.add(CustomerSocialIdentity(customer_id=customer_id, provider=provider, provider_subject=subject, email_at_link=email))


class OAuthLoginOutcome:
    """status is one of "logged_in" (an existing linked identity),
    "created" (a brand-new CustomerAccount was just made), or
    "link_required" (a verified email matches an existing, unlinked
    CustomerAccount — customer/account is None; link_token must be
    presented to /confirm-link only after that account's own normal
    login proves ownership)."""

    def __init__(self, customer: CustomerAccount | None, status: str, link_token: str | None = None):
        self.customer = customer
        self.status = status
        self.link_token = link_token


def resolve_customer_for_oauth(
    db: Session, provider: str, subject: str, email: str | None, email_verified: bool, first_name: str, last_name: str,
) -> OAuthLoginOutcome:
    """The core anti-takeover decision: identity (provider, subject) is
    the ONLY thing ever used to recognize a returning customer. Email is
    used only to decide whether a link-confirmation step is required for
    a first-time OAuth sign-in — never to silently authenticate as an
    existing account."""
    existing_by_identity = find_customer_by_social_identity(db, provider, subject)
    if existing_by_identity is not None:
        return OAuthLoginOutcome(existing_by_identity, "logged_in")

    existing_by_email = None
    if email and email_verified:
        existing_by_email = db.scalar(select(CustomerAccount).where(CustomerAccount.email == normalize_email(email)))

    if existing_by_email is not None:
        link_token = create_oauth_link_token(provider, subject, email)
        return OAuthLoginOutcome(None, "link_required", link_token)

    from app.modules.customer_auth.service import create_customer_account_via_oauth

    signup_email = email if (email and email_verified) else f"{provider}-{subject}@oauth.invalid"
    new_account = create_customer_account_via_oauth(
        db, signup_email, first_name, last_name, email_verified=bool(email and email_verified),
    )
    link_social_identity(db, new_account.id, provider, subject, email)
    db.commit()
    db.refresh(new_account)
    return OAuthLoginOutcome(new_account, "created")
