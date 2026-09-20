"""
Phase 3G: customer-facing Google OAuth login. Google is the only
social-login provider this project supports — no other provider's
endpoint, setting, or identity handling remains anywhere in this router
or its callers.

Endpoints:
    GET  /api/v1/customer-auth/oauth/status                  (whether Google is configured)
    GET  /api/v1/customer-auth/oauth/google/start             (redirect to Google's consent screen)
    GET  /api/v1/customer-auth/oauth/google/callback          (Google redirects back here)
    POST /api/v1/customer-auth/oauth/{provider}/confirm-link  (authenticated — completes a pending link)

SECURITY MODEL
==============
- The Google client id/secret live ONLY in this backend's environment —
  see core/config.py's own docstring on these fields. Nothing OAuth-secret
  ever reaches the frontend; the frontend only ever does a full-page
  navigation to /start and reads the resulting session token back from a
  URL FRAGMENT (never a query string) after /callback redirects it home —
  see _callback_redirect_url below.
- /start and /callback are both gated on the SAME credential-independent
  "is this configured" check this codebase already uses for
  SMTP/SMS/Razorpay/Zotel (settings.google_client_id/secret non-empty) —
  an unconfigured Google returns a clear 501, never a fake success.
- The identity key for "is this a returning customer" is ALWAYS
  (provider, provider_subject) — the provider's own immutable user id —
  never email. See oauth_service.resolve_customer_for_oauth's own
  docstring for the account-takeover vector this avoids and the
  proof-of-ownership linking flow used instead of ever auto-linking on a
  matching email.
- CSRF is covered by a signed, short-lived `state` value (created in
  oauth_service.create_oauth_state, verified in the callback) — reuses
  this project's existing JWT secret/algorithm, not a second signing
  scheme.
- No new session/JWT mechanism: a successful OAuth login calls the exact
  same create_customer_session + create_customer_session_token every
  other customer login path already uses.
- None of these endpoints ever creates or touches a Guest, GuestIdentity,
  Booking, or staff User row — same trust-boundary isolation the rest of
  this module already documents.

PROVIDER-CONSOLE CONFIGURATION A REAL DEPLOYMENT MUST SET
==========================================================
Google Cloud Console (console.cloud.google.com > APIs & Services >
Credentials > Create OAuth client ID > Web application):
  - Authorized JavaScript origins: the public website's own origin
    — e.g. http://localhost:5175 for local dev, or
    https://sriapartment.in in production.
  - Authorized redirect URIs: EXACTLY settings.google_redirect_uri
    — e.g. http://localhost:8000/api/v1/customer-auth/oauth/google/callback
    for local dev, or https://api.sriapartment.in/api/v1/customer-auth/
    oauth/google/callback in production. Local dev and production use
    DIFFERENT values of the SAME env var (GOOGLE_REDIRECT_URI) — nothing
    here hardcodes localhost as the only supported callback.
  - Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI in
    this backend's .env (or its container's environment in production),
    and CUSTOMER_PORTAL_BASE_URL to the matching frontend origin
    (http://localhost:5175 for local dev, https://sriapartment.in in
    production) — see _callback_redirect_url/_login_redirect_url below,
    which build every customer-facing redirect from that one setting.

Until GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are set, /start and
/callback return 501 and the frontend shows its existing honest "isn't
set up yet" message (see GET /status and OAuthButtons.tsx) — no code
change is needed once real credentials are added, for any environment.
"""
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_customer_session_token
from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.customer_auth.models import CustomerAccount
from app.modules.customer_auth.oauth_service import (
    OAuthLoginOutcome,
    OAuthVerificationError,
    create_oauth_state,
    exchange_google_code,
    google_auth_redirect_url,
    google_configured,
    link_social_identity,
    resolve_customer_for_oauth,
    verify_oauth_link_token,
    verify_oauth_state,
)
from app.modules.customer_auth.schemas import (
    CustomerOAuthLinkConfirmRequest,
    CustomerOAuthLinkConfirmResponse,
    CustomerOAuthStatusResponse,
)
from app.modules.customer_auth.service import create_customer_session, get_current_customer
from app.modules.portal.auth_service import normalize_email
from app.shared.rate_limit import is_rate_limited, record_attempt

logger = logging.getLogger("sri_apartment_os")

router = APIRouter(prefix="/api/v1/customer-auth/oauth", tags=["customer-auth-oauth"])

OAUTH_CALLBACK_MAX_PER_IP = 20
OAUTH_CALLBACK_WINDOW_SECONDS = 3600


@router.get("/status", response_model=CustomerOAuthStatusResponse)
def oauth_status():
    """Never reveals the client id/secret themselves — only whether
    Google has credentials configured at all, so the frontend can decide
    (before the customer ever clicks) whether the button does a real
    redirect or shows the honest not-configured message."""
    return CustomerOAuthStatusResponse(google=google_configured())


def _login_redirect_url(**params: str) -> str:
    return f"{settings.customer_portal_base_url}/login?{urlencode(params)}"


def _callback_redirect_url(token: str) -> str:
    # The session token travels in the URL FRAGMENT, never the query
    # string — a fragment is never sent to any server on the browser's
    # subsequent navigation and never appears in this backend's, or any
    # proxy's, access logs; only client-side JS on the destination page
    # can read it (see the public website's OAuthCallbackPage).
    return f"{settings.customer_portal_base_url}/oauth/callback#token={token}"


@router.get("/google/start")
def google_start():
    if not google_configured():
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google sign-in is not configured. Contact your administrator.")
    return RedirectResponse(google_auth_redirect_url(create_oauth_state("google")))


def _finish_oauth(db: Session, outcome: OAuthLoginOutcome, provider: str, ip: str) -> RedirectResponse:
    if outcome.status == "link_required":
        record_audit_log(
            db, None, "customer_auth.oauth_link_required",
            entity_type="customer_account", entity_id=None,
            details=f"{provider} OAuth matched an existing account's email but is not yet linked",
            ip_address=ip,
        )
        db.commit()
        return RedirectResponse(_login_redirect_url(oauth_link_required=provider, oauth_link_token=outcome.link_token or ""))

    account = outcome.customer
    assert account is not None  # only "link_required" ever leaves customer unset
    session_row = create_customer_session(db, account.id, ip_address=ip, user_agent=None)
    access_token = create_customer_session_token(str(account.id), jti=str(session_row.id))

    action = "customer_auth.oauth_signup_success" if outcome.status == "created" else "customer_auth.oauth_login_success"
    record_audit_log(
        db, None, action, entity_type="customer_account", entity_id=str(account.id),
        details=f"Customer authenticated via {provider} OAuth", ip_address=ip,
    )
    db.commit()

    return RedirectResponse(_callback_redirect_url(access_token))


@router.get("/google/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    ip = request.client.host if request.client else "unknown"
    if not google_configured():
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google sign-in is not configured.")

    rate_limit_key = f"customer-oauth-callback:google:{ip}"
    if is_rate_limited(rate_limit_key, max_attempts=OAUTH_CALLBACK_MAX_PER_IP, window_seconds=OAUTH_CALLBACK_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please try again later.")
    record_attempt(rate_limit_key)

    if error or not code or not state or not verify_oauth_state(state, "google"):
        record_audit_log(
            db, None, "customer_auth.oauth_failure", entity_type="customer_account", entity_id=None,
            details="Google OAuth callback failed state/param validation", ip_address=ip,
        )
        db.commit()
        return RedirectResponse(_login_redirect_url(oauth_error="google"))

    try:
        profile = exchange_google_code(code)
    except OAuthVerificationError as e:
        logger.error(f"Google OAuth verification failed: {e}")
        record_audit_log(
            db, None, "customer_auth.oauth_failure", entity_type="customer_account", entity_id=None,
            details="Google token exchange/verification failed", ip_address=ip,
        )
        db.commit()
        return RedirectResponse(_login_redirect_url(oauth_error="google"))

    outcome = resolve_customer_for_oauth(
        db, "google", profile["subject"], profile["email"], profile["email_verified"], profile["first_name"], profile["last_name"],
    )
    return _finish_oauth(db, outcome, "google", ip)


@router.post("/{provider}/confirm-link", response_model=CustomerOAuthLinkConfirmResponse)
def confirm_link(
    provider: str,
    payload: CustomerOAuthLinkConfirmRequest,
    request: Request,
    customer: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Completes a pending social-identity link. The caller must already
    hold a normal, fully-authenticated CustomerSession (password + OTP)
    for the account being linked — get_current_customer enforces that —
    which is the proof-of-ownership step resolve_customer_for_oauth's
    "link_required" outcome exists to require, rather than ever linking
    on an email match alone. Google is the only supported provider —
    kept as a path parameter (rather than hardcoding "google" here) so
    any unknown or no-longer-supported provider name fails with a clear
    404 instead of silently matching."""
    ip = request.client.host if request.client else "unknown"
    if provider != "google":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider")

    claims = verify_oauth_link_token(payload.link_token)
    if claims is None or claims.get("provider") != provider:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link request is invalid or has expired. Please try Continue with Google again.")

    # Defense in depth: the link token must have been issued for THIS
    # customer's own email, never linkable onto a different account than
    # the one the OAuth callback actually matched.
    claimed_email = claims.get("email")
    if claimed_email and normalize_email(claimed_email) != normalize_email(customer.email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link request doesn't match your account.")

    link_social_identity(db, customer.id, provider, claims["subject"], claimed_email)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This social account is already linked to a different customer.")

    record_audit_log(
        db, None, "customer_auth.oauth_link_confirmed", entity_type="customer_account", entity_id=str(customer.id),
        details=f"{provider} identity linked after proof-of-ownership login", ip_address=ip,
    )
    db.commit()

    return CustomerOAuthLinkConfirmResponse(message=f"Your {provider.capitalize()} account has been linked.")
