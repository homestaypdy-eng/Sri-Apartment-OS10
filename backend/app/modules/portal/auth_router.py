"""
Phase 2 of the guest-auth redesign: mobile OTP only (no email OTP, no
Google — see the architecture audit and Phase 1's auth_models.py for the
full plan). A separate router, separately mounted in main.py, so the live
magic-link endpoints in portal/router.py need zero changes for this phase.

Endpoints:
    POST /api/v1/portal/auth/phone/request-otp
    POST /api/v1/portal/auth/phone/verify-otp

Design notes (see also auth_models.py's module docstring):

- request-otp never looks up Guest at all — unlike the magic-link flow,
  which only emails a link when a matching Guest with an email exists,
  phone OTP must work for a brand-new phone with no Guest record yet (the
  whole point of this redesign). Because the code path never branches on
  Guest existence, there's nothing to leak — every call gets the exact
  same generic response, by construction, not by careful wording.

- verify-otp auto-links to an existing Guest on an exact phone match. This
  mirrors what today's live magic-link flow already trusts: OTP
  verification IS proof of phone control, the same bar the current system
  already accepts for guest-session access. It is NOT the same as the
  Google-email case in the architecture doc (which needs an extra
  confirmation step, since a third party's claim about an email isn't
  proof of controlling it) — phone OTP verification already IS that proof.

- If a phone matches MORE than one existing Guest row (the schema has no
  uniqueness constraint on Guest.phone), this endpoint does not guess:
  the identity is created unlinked and the ambiguity is written to the
  audit log for staff review — see verify_phone_otp below.

- DEVELOPMENT-ONLY TEST MECHANISM: request_phone_otp's response includes
  dev_otp_code (PhoneOtpRequestResponse), the plaintext OTP just
  generated, whenever settings.environment != "production" — the exact
  same convention already used for portal/router.py's dev_magic_token,
  reusing the same accepted risk model rather than inventing a new one.
  In production this field is always None. It does NOT change what's
  persisted (still only code_hash, never the raw code) or which checks
  verify-otp runs (expiry/attempts/single-use/rate limits all still apply
  exactly as in production) — it only additionally reveals, in the
  response body, a code that already exists in memory for this request
  regardless.

- SAFETY GATE: outside production, send_sms is never called at all —
  the decision is settings.environment alone, checked before any attempt,
  not "try sending and see if it fails". This is deliberately
  credential-independent: it stays true even if valid SMS credentials
  happen to be configured locally. See request_phone_otp's inline comment
  for the exact mechanism (mirrors auth_email_router.py's identical fix).
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
from app.modules.notifications.service import SmsNotConfiguredError, send_otp_sms
from app.modules.portal.auth_models import GuestIdentity, GuestSession, OtpChallenge
from app.modules.portal.auth_schemas import PhoneOtpRequest, PhoneOtpRequestResponse, PhoneOtpVerifyRequest
from app.modules.portal.auth_service import (
    OTP_EXPIRY_MINUTES,
    OTP_RESEND_COOLDOWN_SECONDS,
    generate_otp_code,
    hash_otp_code,
    normalize_phone,
    otp_expiry,
    record_otp_audit,
)
from app.modules.portal.schemas import GuestSessionToken
from app.shared.rate_limit import is_rate_limited, record_attempt

router = APIRouter(prefix="/api/v1/portal/auth/phone", tags=["guest-auth-phone"])

# Distinct from shared.rate_limit's own 5/5min staff-login default — a
# guest requesting their own OTP legitimately needs more headroom than a
# staff member retrying a password, but still bounded.
OTP_REQUEST_MAX_PER_IP = 10
OTP_REQUEST_MAX_PER_PHONE = 5
OTP_REQUEST_WINDOW_SECONDS = 3600  # 1 hour

_GENERIC_VERIFY_ERROR = "That code is incorrect or has expired."


@router.post("/request-otp", response_model=PhoneOtpRequestResponse)
def request_phone_otp(payload: PhoneOtpRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    identifier = normalize_phone(payload.phone)

    ip_key = f"phone-otp-request:ip:{ip}"
    phone_key = f"phone-otp-request:phone:{identifier}"

    if is_rate_limited(ip_key, max_attempts=OTP_REQUEST_MAX_PER_IP, window_seconds=OTP_REQUEST_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please try again later.")
    if is_rate_limited(phone_key, max_attempts=OTP_REQUEST_MAX_PER_PHONE, window_seconds=OTP_REQUEST_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests for this number. Please try again later.")

    most_recent = db.scalar(
        select(OtpChallenge)
        .where(OtpChallenge.channel == "phone", OtpChallenge.identifier == identifier)
        .order_by(OtpChallenge.created_at.desc())
        .limit(1)
    )
    if most_recent is not None and (datetime.now(timezone.utc) - most_recent.created_at).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Please wait before requesting another code.")

    record_attempt(ip_key)
    record_attempt(phone_key)

    raw_code = generate_otp_code()
    message = f"Your Sri Apartment Homestay verification code is {raw_code}. It expires in {OTP_EXPIRY_MINUTES} minutes. Do not share this code."

    # CREDENTIAL-INDEPENDENT SAFETY GATE (Guest Phone OTP only): the check
    # here is settings.environment, decided BEFORE send_sms is ever called
    # — not "try sending and see if it fails". The previous "try it, catch
    # SmsNotConfiguredError" design was only safe when SMS credentials
    # happened to be absent; the equivalent design for email OTP genuinely
    # sent a real, authenticated SMTP session from a local run once real
    # SMTP credentials turned out to be configured (see
    # auth_email_router.py's identical fix for the incident and reasoning).
    # Outside production, send_sms is never invoked at all, regardless of
    # whether valid SMS credentials happen to exist; the dev_otp_code path
    # below is how local/automated testing completes the flow instead.
    # Production's SMS path — including its SmsNotConfiguredError handling
    # — is completely unchanged.
    if settings.environment == "production":
        try:
            send_otp_sms(identifier, message, db)
        except SmsNotConfiguredError:
            # Same convention as forgot_password's SMTP check: a server-side
            # "not configured" state is identical for every caller regardless
            # of which phone was submitted, so it's safe to surface directly
            # rather than folding it into the generic success response.
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED,
                "SMS delivery is not configured. Contact your administrator.",
            )

    # The SMS actually went out (production only) or delivery was never
    # attempted (non-production, by design — see above) — either way, now,
    # and only now, invalidate whatever OTP was previously in flight for
    # this number, so at most one code is ever valid at a time.
    still_active = db.scalars(
        select(OtpChallenge).where(
            OtpChallenge.channel == "phone",
            OtpChallenge.identifier == identifier,
            OtpChallenge.consumed_at.is_(None),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for old in still_active:
        old.consumed_at = now

    challenge = OtpChallenge(
        channel="phone",
        identifier=identifier,
        code_hash=hash_otp_code(raw_code),
        expires_at=otp_expiry(),
        request_ip=ip,
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    record_otp_audit(db, "phone", "request", challenge.id, f"Phone OTP requested for {identifier}", ip)

    # Dev/testability-only — see PhoneOtpRequestResponse.dev_otp_code and
    # the module docstring. raw_code is never logged and never persisted
    # anywhere; this is its only appearance outside the SMS it was (or, in
    # the skipped-delivery dev case above, would have been) sent in.
    dev_code = raw_code if settings.environment != "production" else None
    return PhoneOtpRequestResponse(challenge_id=challenge.id, dev_otp_code=dev_code)


@router.post("/verify-otp", response_model=GuestSessionToken)
def verify_phone_otp(payload: PhoneOtpVerifyRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"

    challenge = db.get(OtpChallenge, payload.challenge_id)
    if challenge is None or challenge.channel != "phone":
        record_otp_audit(db, "phone", "verify_failure", payload.challenge_id, "Unknown or wrong-channel challenge_id", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)
    if challenge.consumed_at is not None:
        record_otp_audit(db, "phone", "verify_failure", challenge.id, "Challenge already consumed", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)
    if challenge.expires_at < datetime.now(timezone.utc):
        record_otp_audit(db, "phone", "verify_failure", challenge.id, "Challenge expired", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)
    if challenge.attempt_count >= challenge.max_attempts:
        record_otp_audit(db, "phone", "verify_failure", challenge.id, "Max verification attempts exceeded", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)

    # Constant-time comparison — both sides are always a 64-char sha256 hex
    # digest, so this never leaks code length/content through timing.
    if not secrets.compare_digest(hash_otp_code(payload.code), challenge.code_hash):
        challenge.attempt_count += 1
        record_otp_audit(db, "phone", "verify_failure", challenge.id, "Incorrect code", ip)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_VERIFY_ERROR)

    challenge.consumed_at = datetime.now(timezone.utc)

    identity = db.scalar(
        select(GuestIdentity).where(GuestIdentity.channel == "phone", GuestIdentity.external_id == challenge.identifier)
    )
    if identity is None:
        matching_guests = db.scalars(select(Guest).where(Guest.phone == challenge.identifier)).all()
        if len(matching_guests) == 1:
            identity = GuestIdentity(
                channel="phone", external_id=challenge.identifier,
                guest_id=matching_guests[0].id, linked_at=datetime.now(timezone.utc),
            )
        elif len(matching_guests) == 0:
            identity = GuestIdentity(channel="phone", external_id=challenge.identifier)
        else:
            # More than one existing Guest shares this phone number. Never
            # guess — leave the identity unlinked (same as a brand-new
            # guest) and record the ambiguity for staff to resolve.
            identity = GuestIdentity(channel="phone", external_id=challenge.identifier)
            record_audit_log(
                db, None, "guest_identity.ambiguous_phone_match",
                entity_type="guest_identity", entity_id=challenge.identifier,
                details=f"{len(matching_guests)} existing Guest records share phone {challenge.identifier}; not auto-linked.",
                ip_address=ip,
            )
        db.add(identity)
        db.flush()  # need identity.id for the session row below

    session_row = GuestSession(identity_id=identity.id, ip_address=ip, user_agent=request.headers.get("user-agent"))
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    db.refresh(identity)

    record_otp_audit(db, "phone", "verify_success", challenge.id, f"Phone OTP verified for {challenge.identifier}; session {session_row.id}", ip)

    # Same token shape the live magic-link flow already issues (sub =
    # Guest.id) when a guest is linked, so it works against the existing,
    # unmodified /me and /bookings endpoints with no changes there. When
    # unlinked, sub is the identity's own id — get_current_guest's
    # db.get(Guest, ...) lookup correctly finds nothing and 401s, which is
    # safe (no data leak) even though there's no "no bookings yet"
    # dashboard support yet — that's later-phase website/API work.
    subject = str(identity.guest_id) if identity.guest_id else str(identity.id)
    access_token = create_guest_session_token(subject, jti=str(session_row.id))

    return GuestSessionToken(access_token=access_token)
