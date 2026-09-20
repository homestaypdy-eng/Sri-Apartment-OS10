"""
Phase 3 of the guest-auth redesign: email OTP only (no Google — see the
architecture audit and Phase 1's auth_models.py for the full plan). A
separate router, separately mounted in main.py, deliberately structured
as a near-mirror of Phase 2's auth_router.py (mobile OTP) rather than a
shared/refactored abstraction — this keeps every line of the mobile-OTP
implementation (and the live magic-link endpoints in portal/router.py)
completely untouched by this phase, at the cost of some duplication. See
auth_router.py's own module docstring for the design reasoning behind
each pattern reused here (unlinked identities, ambiguous-match handling,
the dev_otp_code response field) — it applies identically to email, just
substituting Guest.email for Guest.phone and send_email for send_sms.

Endpoints:
    POST /api/v1/portal/auth/email/request-otp
    POST /api/v1/portal/auth/email/verify-otp

SAFETY GATE — one deliberate divergence from auth_router.py: request_email_
otp decides whether to attempt real delivery from settings.environment
ALONE, checked before send_email is ever called — not from whether
send_email happens to raise EmailNotConfiguredError. The phone-OTP router
still uses the "try it, catch not-configured" pattern, which is safe only
when SMS credentials happen to be absent; SMTP credentials on a real
deployment are routinely present (shared with quotations/password-reset),
so the same pattern for email would let a local/dev/test run actually
contact Gmail with real credentials. See request_email_otp's inline
comment for the exact mechanism.
"""
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_guest_session_token
from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.bookings.models import Guest
from app.modules.notifications.service import EmailNotConfiguredError, send_otp_email
from app.modules.portal.auth_models import GuestIdentity, GuestSession, OtpChallenge
from app.modules.portal.auth_schemas import EmailOtpRequest, EmailOtpRequestResponse, EmailOtpVerifyRequest
from app.modules.portal.auth_service import (
    OTP_EXPIRY_MINUTES,
    OTP_RESEND_COOLDOWN_SECONDS,
    generate_otp_code,
    hash_otp_code,
    normalize_email,
    otp_expiry,
    record_otp_audit,
)
from app.modules.portal.schemas import GuestSessionToken
from app.shared.rate_limit import is_rate_limited, record_attempt

router = APIRouter(prefix="/api/v1/portal/auth/email", tags=["guest-auth-email"])

# Same values as auth_router.py's phone-channel constants, kept as separate
# named constants (not shared) so each channel's policy can be tuned
# independently without touching the other.
OTP_REQUEST_MAX_PER_IP = 10
OTP_REQUEST_MAX_PER_EMAIL = 5
OTP_REQUEST_WINDOW_SECONDS = 3600  # 1 hour

_GENERIC_VERIFY_ERROR = "That code is incorrect or has expired."


@router.post("/request-otp", response_model=EmailOtpRequestResponse)
def request_email_otp(payload: EmailOtpRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    identifier = normalize_email(payload.email)

    ip_key = f"email-otp-request:ip:{ip}"
    email_key = f"email-otp-request:email:{identifier}"

    if is_rate_limited(ip_key, max_attempts=OTP_REQUEST_MAX_PER_IP, window_seconds=OTP_REQUEST_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please try again later.")
    if is_rate_limited(email_key, max_attempts=OTP_REQUEST_MAX_PER_EMAIL, window_seconds=OTP_REQUEST_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests for this email. Please try again later.")

    most_recent = db.scalar(
        select(OtpChallenge)
        .where(OtpChallenge.channel == "email", OtpChallenge.identifier == identifier)
        .order_by(OtpChallenge.created_at.desc())
        .limit(1)
    )
    if most_recent is not None and (datetime.now(timezone.utc) - most_recent.created_at).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Please wait before requesting another code.")

    record_attempt(ip_key)
    record_attempt(email_key)

    raw_code = generate_otp_code()
    subject = "Your Sri Apartment Homestay verification code"
    body = (
        f"Your Sri Apartment Homestay verification code is {raw_code}. "
        f"It expires in {OTP_EXPIRY_MINUTES} minutes.\n\n"
        "If you didn't request this, you can safely ignore this email."
    )

    # CREDENTIAL-INDEPENDENT SAFETY GATE (Guest Email OTP only): the check
    # here is settings.environment, decided BEFORE send_email is ever
    # called — not "try sending and see if it fails". This matters because
    # this project's SMTP credentials are frequently configured for real
    # (they're shared with quotations/password-reset), so a local/dev/test
    # run cannot rely on EmailNotConfiguredError to keep it from actually
    # contacting Gmail — that earlier design genuinely sent a real,
    # authenticated SMTP session from a local run. Outside production, the
    # real sender is never invoked at all, regardless of whether valid
    # credentials happen to exist; the dev_otp_code path below is how
    # local/automated testing completes the flow instead. Production's
    # email path — including its EmailNotConfiguredError handling — is
    # completely unchanged.
    if settings.environment == "production":
        try:
            send_otp_email(identifier, subject, body, db)
        except EmailNotConfiguredError:
            # Same convention as forgot_password's SMTP check and the
            # phone-OTP endpoint's SmsNotConfiguredError handling: a
            # server-side "not configured" state is identical for every
            # caller regardless of which email was submitted, so it's safe
            # to surface directly rather than folding it into the generic
            # success response.
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED,
                "Email delivery is not configured. Contact your administrator.",
            )

    # The email actually went out (production only) or delivery was never
    # attempted (non-production, by design — see above) — either way, now,
    # and only now, invalidate whatever OTP was previously in flight for
    # this email, so at most one code is ever valid at a time.
    still_active = db.scalars(
        select(OtpChallenge).where(
            OtpChallenge.channel == "email",
            OtpChallenge.identifier == identifier,
            OtpChallenge.consumed_at.is_(None),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for old in still_active:
        old.consumed_at = now

    challenge = OtpChallenge(
        channel="email",
        identifier=identifier,
        code_hash=hash_otp_code(raw_code),
        expires_at=otp_expiry(),
        request_ip=ip,
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    record_otp_audit(db, "email", "request", challenge.id, f"Email OTP requested for {identifier}", ip)

    # Dev/testability-only — see EmailOtpRequestResponse.dev_otp_code.
    # raw_code is never logged and never persisted anywhere; this is its
    # only appearance outside the email it was (or, in the skipped-
    # delivery dev case above, would have been) sent in.
    dev_code = raw_code if settings.environment != "production" else None
    return EmailOtpRequestResponse(challenge_id=challenge.id, dev_otp_code=dev_code)


@router.post("/verify-otp", response_model=GuestSessionToken)
def verify_email_otp(payload: EmailOtpVerifyRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"

    challenge = db.get(OtpChallenge, payload.challenge_id)
    if challenge is None or challenge.channel != "email":
        record_otp_audit(db, "email", "verify_failure", payload.challenge_id, "Unknown or wrong-channel challenge_id", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)
    if challenge.consumed_at is not None:
        record_otp_audit(db, "email", "verify_failure", challenge.id, "Challenge already consumed", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)
    if challenge.expires_at < datetime.now(timezone.utc):
        record_otp_audit(db, "email", "verify_failure", challenge.id, "Challenge expired", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)
    if challenge.attempt_count >= challenge.max_attempts:
        record_otp_audit(db, "email", "verify_failure", challenge.id, "Max verification attempts exceeded", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)

    # Constant-time comparison — both sides are always a 64-char sha256 hex
    # digest, so this never leaks code length/content through timing.
    if not secrets.compare_digest(hash_otp_code(payload.code), challenge.code_hash):
        challenge.attempt_count += 1
        record_otp_audit(db, "email", "verify_failure", challenge.id, "Incorrect code", ip)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)

    challenge.consumed_at = datetime.now(timezone.utc)

    identity = db.scalar(
        select(GuestIdentity).where(GuestIdentity.channel == "email", GuestIdentity.external_id == challenge.identifier)
    )
    if identity is None:
        matching_guests = db.scalars(select(Guest).where(Guest.email == challenge.identifier)).all()
        if len(matching_guests) == 1:
            identity = GuestIdentity(
                channel="email", external_id=challenge.identifier,
                guest_id=matching_guests[0].id, linked_at=datetime.now(timezone.utc),
            )
        elif len(matching_guests) == 0:
            identity = GuestIdentity(channel="email", external_id=challenge.identifier)
        else:
            # More than one existing Guest shares this email address. Never
            # guess — leave the identity unlinked (same as a brand-new
            # guest) and record the ambiguity for staff to resolve. Same
            # rule as auth_router.py's phone-match handling.
            identity = GuestIdentity(channel="email", external_id=challenge.identifier)
            record_audit_log(
                db, None, "guest_identity.ambiguous_email_match",
                entity_type="guest_identity", entity_id=challenge.identifier,
                details=f"{len(matching_guests)} existing Guest records share email {challenge.identifier}; not auto-linked.",
                ip_address=ip,
            )
        db.add(identity)
        db.flush()  # need identity.id for the session row below

    session_row = GuestSession(identity_id=identity.id, ip_address=ip, user_agent=request.headers.get("user-agent"))
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    db.refresh(identity)

    record_otp_audit(db, "email", "verify_success", challenge.id, f"Email OTP verified for {challenge.identifier}; session {session_row.id}", ip)

    # Same token shape the live magic-link flow and phone-OTP already
    # issue (sub = Guest.id) when a guest is linked, so it works against
    # the existing, unmodified /me and /bookings endpoints with no changes
    # there. When unlinked, sub is the identity's own id — see
    # auth_router.py's identical comment for why this degrades safely.
    subject = str(identity.guest_id) if identity.guest_id else str(identity.id)
    access_token = create_guest_session_token(subject, jti=str(session_row.id))

    return GuestSessionToken(access_token=access_token)
