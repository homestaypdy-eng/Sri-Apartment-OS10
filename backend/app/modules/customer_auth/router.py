"""
Phase 3B/3C/3D/3E/3F: customer registration, two-step (password + OTP)
login, logout, forgot-password, reset-password, and (Phase 3F) the
authenticated profile endpoints a professional account experience needs
(current-customer restore, profile edit, change-password). Built entirely
on the Phase 3A/3E foundation (models.py, service.py) — no new password
hashing, JWT, database-access, rate-limit, audit-logging, or email/SMS-
sending mechanism introduced here; every one of those is the existing
project infrastructure, reused.

Endpoints:
    POST  /api/v1/customer-auth/register          (Phase 3B/3E: +phone)
    POST  /api/v1/customer-auth/login              (Phase 3E: step 1 — password check + OTP dispatch)
    POST  /api/v1/customer-auth/login/verify-otp   (Phase 3E: step 2 — OTP check + session issuance)
    POST  /api/v1/customer-auth/logout             (Phase 3C)
    GET   /api/v1/customer-auth/me                 (Phase 3F: restore the authenticated customer's profile)
    PATCH /api/v1/customer-auth/me                 (Phase 3F: edit first/last name + phone — never email)
    POST  /api/v1/customer-auth/change-password    (Phase 3F: current-password-gated, revokes other sessions)
    POST  /api/v1/customer-auth/forgot-password    (Phase 3D)
    POST  /api/v1/customer-auth/reset-password     (Phase 3D)

/me, PATCH /me, and /change-password all resolve "which customer" ENTIRELY
from the session token via get_current_customer — none of them accept an
id/email from the caller, so none can ever act on another customer's row.

/register deliberately does NOT log the customer in: no CustomerSession
and no JWT are created there. /login/verify-otp is the only place a
customer session is minted — /login itself never creates one, even on a
fully correct password, since the second factor is still outstanding.

/login's and /forgot-password's failure paths are CREDENTIAL-INDEPENDENT
of whether the email/phone exists at all — mirrors auth/router.py's staff
login and forgot_password exactly, the established anti-enumeration
convention this codebase already uses everywhere (forgot-password, guest
OTP request). /login/verify-otp's failure path mirrors portal/
auth_router.py's verify_phone_otp exactly: one generic message for
unknown/wrong-channel/consumed/expired/attempts-exceeded/wrong-code,
constant-time code comparison, attempt_count incremented only on a wrong
code.

/forgot-password and /login (for its OTP dispatch) both apply the
CREDENTIAL-INDEPENDENT SAFETY GATE the OTP endpoints already established
(settings.environment checked BEFORE any send is attempted, never "try it
and see") — a gate staff's own forgot_password does NOT have (it only
checks whether SMTP is *configured*, not whether this is *production*, so
a local run with real SMTP credentials present could otherwise send a
real email; see the forgot_password docstring below for the exact
mechanism this avoids).

None of these endpoints ever touches Guest, GuestIdentity, Booking, User,
or UserSession. Account lookups query ONLY CustomerAccount — never Guest
— so a matching Guest email/phone is never revealed or acted on, and a
successful customer login never creates or touches a GuestSession/
GuestIdentity. Password reset uses its own dedicated
CustomerPasswordResetToken table, never the staff PasswordResetToken
table, so the two trust boundaries can never touch. The login OTP second
factor uses its own dedicated CustomerLoginOtpChallenge table, never
portal's OtpChallenge, for the same reason.
"""
import logging
import hashlib
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.bookings import document_storage
from app.modules.bookings.models import IDENTITY_DOCUMENT_TYPES, Guest, GuestDocument
from app.modules.customer_auth.models import (
    CustomerAccount,
    CustomerLoginOtpChallenge,
    CustomerPasswordResetToken,
    CustomerSession,
)
from app.modules.customer_auth.schemas import (
    CustomerAccountOut,
    CustomerChangePasswordRequest,
    CustomerChangePasswordResponse,
    CustomerForgotPasswordRequest,
    CustomerForgotPasswordResponse,
    CustomerLoginOtpRequestResponse,
    CustomerLoginRequest,
    CustomerLoginResponse,
    CustomerLoginVerifyRequest,
    CustomerRegisterRequest,
    CustomerResetPasswordRequest,
    CustomerResetPasswordResponse,
    CustomerUpdateProfileRequest,
)
from app.modules.customer_auth.phone_service import InvalidPhoneNumberError, normalize_phone_e164
from app.modules.customer_auth.service import (
    create_customer_account,
    create_customer_login_otp_challenge,
    create_customer_session,
    get_current_customer,
    get_current_customer_with_session,
    hash_reset_token,
    revoke_customer_session,
    verify_customer_password,
)
from app.modules.customer_auth.identity_service import resolve_customer_guest
from app.core.security import create_customer_session_token, hash_password
from app.modules.notifications.service import (
    EmailNotConfiguredError,
    dispatch_customer_login_otp_email,
    dispatch_customer_login_otp_sms,
    dispatch_customer_welcome_email,
    send_customer_login_otp_email_now,
    send_customer_password_reset_email,
)
from app.modules.portal.auth_service import OTP_EXPIRY_MINUTES, hash_otp_code, normalize_email
from app.modules.portal.schemas import GuestIdentityDocumentOut, GuestIdentityStatusOut, GuestIdentitySubmitRequest
from app.shared.file_upload import EXTENSION_BY_CONTENT_TYPE, read_and_validate_upload
from app.shared.rate_limit import is_rate_limited, record_attempt, reset

logger = logging.getLogger("sri_apartment_os")

router = APIRouter(prefix="/api/v1/customer-auth", tags=["customer-auth"])

# Distinct thresholds from OTP-request/staff-login — reuses the exact same
# shared rate-limit mechanism (app.shared.rate_limit) with its own values,
# the same pattern the OTP endpoints already use for their own thresholds.
CUSTOMER_REGISTER_MAX_PER_IP = 5
CUSTOMER_REGISTER_WINDOW_SECONDS = 3600  # 1 hour

CUSTOMER_FORGOT_PASSWORD_MAX_PER_KEY = 5
CUSTOMER_FORGOT_PASSWORD_WINDOW_SECONDS = 3600  # 1 hour
CUSTOMER_RESET_PASSWORD_MAX_PER_IP = 10
CUSTOMER_RESET_PASSWORD_WINDOW_SECONDS = 3600  # 1 hour

# Phase 3F: change-password is authenticated (the caller already holds a
# valid session), so this exists only to blunt a brute-force guess of the
# CURRENT password, not to prevent enumeration — same shared rate-limit
# mechanism, keyed per-customer rather than per-IP since the caller's
# identity is already known from their session.
CUSTOMER_CHANGE_PASSWORD_MAX_PER_CUSTOMER = 5
CUSTOMER_CHANGE_PASSWORD_WINDOW_SECONDS = 3600  # 1 hour

# Same 30-minute value auth/router.py's staff forgot-password already
# uses for PASSWORD_RESET_TOKEN_EXPIRE_MINUTES — this codebase's own
# existing precedent for "short expiration" on this exact kind of token.
CUSTOMER_PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 30

_GENERIC_LOGIN_ERROR = "Incorrect email/phone or password"
_GENERIC_OTP_VERIFY_ERROR = "That code is incorrect or has expired."
_GENERIC_RESET_REQUEST_MESSAGE = "If that email is registered, a password reset link has been sent."

# LOCAL EMAIL OTP TESTING: a short SMTP connect timeout for the real-email
# dev-test path only (see _dispatch_dev_email_otp) — deliberately shorter
# than send_email's default 15s production timeout, so a local test that
# hits an unreachable/slow SMTP server can't run long enough to trip the
# frontend's 12s request timeout (src/lib/api.ts DEFAULT_TIMEOUT_MS).
_DEV_EMAIL_OTP_TEST_SMTP_TIMEOUT_SECONDS = 8


def _dispatch_dev_email_otp(db: Session, account: CustomerAccount, raw_code: str, expiry_minutes: int) -> str:
    """LOCAL-ONLY — only ever called from the non-production branch of
    login_customer below; production's own branch is completely untouched
    and never calls this. Decides and executes the local Email OTP
    testing behavior per settings.customer_auth_email_otp_mode:

      - "dev_code" (default, or any other/unset value): no network
        attempt at all — same no-send behavior this codebase always had
        outside production, before this setting existed.
      - "real_email": sends SYNCHRONOUSLY (unlike production's
        backgrounded, outcome-blind dispatch_customer_login_otp_email) so
        the caller can honestly report what happened — but ONLY when
        account.email exactly matches an entry in
        settings.customer_auth_email_otp_test_allowlist. Any other case
        (mode not "real_email", empty allowlist, or a non-matching
        address) never attempts a send: flipping the mode alone can never
        send to an arbitrary address, only an explicit, non-empty,
        exact-match allowlist entry can.

    Returns one of "dev_code_only", "dev_real_email_not_allowlisted",
    "dev_real_email_sent", "dev_real_email_failed" — see
    CustomerLoginOtpRequestResponse.otp_delivery_status.
    """
    if settings.customer_auth_email_otp_mode != "real_email":
        return "dev_code_only"

    allowlist = {
        entry.strip().lower()
        for entry in settings.customer_auth_email_otp_test_allowlist.split(",")
        if entry.strip()
    }
    if account.email.lower() not in allowlist:
        return "dev_real_email_not_allowlisted"

    try:
        send_customer_login_otp_email_now(
            account, raw_code, expiry_minutes, db,
            timeout=_DEV_EMAIL_OTP_TEST_SMTP_TIMEOUT_SECONDS,
        )
    except Exception as e:
        # Local diagnostic only — never surfaced to the frontend beyond
        # the generic "failed" status; no OTP code, no stack trace, no
        # SMTP credential detail crosses this boundary.
        logger.error(f"LOCAL real-email OTP test send failed for customer_id={account.id}: {type(e).__name__}: {e}")
        return "dev_real_email_failed"
    return "dev_real_email_sent"


@router.post("/register", response_model=CustomerAccountOut, status_code=status.HTTP_201_CREATED)
def register_customer(payload: CustomerRegisterRequest, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    ip_key = f"customer-register:ip:{ip}"

    if is_rate_limited(ip_key, max_attempts=CUSTOMER_REGISTER_MAX_PER_IP, window_seconds=CUSTOMER_REGISTER_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many registration attempts. Please try again later.")

    if not payload.terms_accepted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You must accept the Terms & Conditions to create an account.")
    if not payload.privacy_accepted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You must accept the Privacy Policy to create an account.")

    record_attempt(ip_key)

    normalized = normalize_email(payload.email)
    normalized_phone = normalize_phone_e164(payload.phone)

    existing = db.scalar(select(CustomerAccount).where(CustomerAccount.email == normalized))
    if existing is not None:
        # Logged for abuse/enumeration-attempt visibility — never reveals
        # anything about Guest data, only that a CustomerAccount signup
        # was attempted against an email that already has one.
        record_audit_log(
            db, None, "customer_auth.signup_failure",
            entity_type="customer_account", entity_id=None,
            details=f"Duplicate email signup attempt: {normalized}",
            ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")

    existing_phone = db.scalar(select(CustomerAccount).where(CustomerAccount.phone == normalized_phone))
    if existing_phone is not None:
        # Same convention as the duplicate-email check above — never
        # reveals anything about Guest data, only that a CustomerAccount
        # signup was attempted against a phone number that already has one.
        record_audit_log(
            db, None, "customer_auth.signup_failure",
            entity_type="customer_account", entity_id=None,
            details=f"Duplicate phone signup attempt: {normalized_phone}",
            ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this phone number already exists.")

    now = datetime.now(timezone.utc)
    account = create_customer_account(
        db,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=normalized,
        phone=normalized_phone,
        password=payload.password,
        terms_accepted_at=now,
        privacy_accepted_at=now,
    )

    record_audit_log(
        db, None, "customer_auth.signup_success",
        entity_type="customer_account", entity_id=str(account.id),
        details=f"Customer account created: {normalized}",
        ip_address=ip,
    )
    db.commit()

    # Scheduled as a BackgroundTask (runs only AFTER this response is
    # sent) — same fix as the payment->booking-confirmation timeout bug:
    # a slow/unreachable SMTP server must never delay or fail signup. The
    # account above is already committed, so nothing here can undo it;
    # see dispatch_customer_welcome_email's own docstring for the full
    # best-effort/no-duplicate reasoning.
    background_tasks.add_task(dispatch_customer_welcome_email, account.id)

    return account


@router.post("/login", response_model=CustomerLoginOtpRequestResponse)
def login_customer(payload: CustomerLoginRequest, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Phase 3E step 1: verify identifier + password, and if correct, dispatch
    a one-time code to that SAME channel (email→email, phone→SMS) rather
    than issuing a session. No CustomerSession/JWT is ever created here —
    only /login/verify-otp does that, and only after the code is confirmed.
    """
    ip = request.client.host if request.client else "unknown"
    if payload.channel == "email":
        identifier = normalize_email(payload.identifier)
        account = db.scalar(select(CustomerAccount).where(CustomerAccount.email == identifier))
    else:
        try:
            identifier = normalize_phone_e164(payload.identifier)
        except InvalidPhoneNumberError:
            # An unparsable phone identifier is treated exactly like an
            # unknown one — same credential-independent, anti-enumeration
            # generic failure below, never a distinct "that's not a valid
            # phone number" response that would leak input validity to an
            # unauthenticated caller.
            identifier = payload.identifier
            account = None
        else:
            account = db.scalar(select(CustomerAccount).where(CustomerAccount.phone == identifier))

    # Single combined key, same shape as auth/router.py's staff login
    # (f"{ip}:{identifier}") — the most directly analogous existing case
    # for a password-based login endpoint, reusing its exact rate-limit
    # key style and default thresholds (5 attempts / 5 minutes).
    rate_limit_key = f"customer-login:{ip}:{payload.channel}:{identifier}"

    if is_rate_limited(rate_limit_key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Please try again later.")

    # CREDENTIAL-INDEPENDENT OF WHETHER THE IDENTIFIER EXISTS: an unknown
    # email/phone and a wrong password produce the exact same response,
    # same status code, same rate-limit key recorded against — nothing
    # here lets a caller distinguish the two cases, mirroring the staff
    # login's own `if user is None or not verify_password(...)` shape
    # exactly.
    if account is None or not verify_customer_password(account, payload.password):
        record_attempt(rate_limit_key)
        record_audit_log(
            db, None, "customer_auth.login_failure",
            entity_type="customer_account", entity_id=(str(account.id) if account else None),
            details=f"Failed customer login attempt for {payload.channel}:{identifier}",
            ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_LOGIN_ERROR)

    reset(rate_limit_key)

    challenge, raw_code = create_customer_login_otp_challenge(db, account.id, payload.channel)

    # CREDENTIAL-INDEPENDENT: the check here is settings.environment,
    # decided BEFORE any send is ever attempted — not "try sending and see
    # if it fails". Outside production, no send is attempted at all,
    # regardless of whether valid SMTP/SMS credentials happen to exist;
    # dev_otp_code below is how local/automated testing completes the flow
    # instead. Same mechanism portal/auth_router.py's request_phone_otp and
    # auth_email_router.py's request_otp already established.
    #
    # The actual send is scheduled as a FastAPI BackgroundTask (runs only
    # AFTER this response is sent) rather than performed inline — a real
    # production incident (login OTP delivery failing with OSError:
    # [Errno 101] Network is unreachable, synchronously, inside this
    # request) showed a slow/unreachable SMTP server could delay this
    # response past the frontend's 12s client timeout, exactly the class
    # of bug already fixed for booking confirmations
    # (dispatch_booking_notifications). Because delivery now happens after
    # the response, this endpoint can no longer report "delivery is not
    # configured" back to the caller the way it used to — a failure is
    # only ever visible as a NotificationLog row (dispatch_customer_login_
    # otp_email/sms's own contract, see their docstrings), never to the
    # customer, which is a strictly stronger form of the same
    # anti-disclosure principle already applied to forgot-password's own
    # 501 message.
    # otp_delivery_status tells the caller exactly what happened (see
    # CustomerLoginOtpRequestResponse) — "sent" is the PRODUCTION-ONLY
    # branch's value below, completely unchanged from what this response
    # always meant before the field existed. Nothing about the production
    # branch's own logic changes here.
    otp_delivery_status = "sent"
    if settings.environment == "production":
        if payload.channel == "email":
            # Subject/body/HTML are built inside dispatch_customer_login_
            # otp_email itself now (see its own docstring) — only the raw
            # code and expiry are passed through, never a pre-formatted
            # message, so content-building lives in one place.
            background_tasks.add_task(dispatch_customer_login_otp_email, account.id, raw_code, OTP_EXPIRY_MINUTES)
        else:
            message = (
                f"Your Sri Apartment Homestay login code is {raw_code}. "
                f"It expires in {OTP_EXPIRY_MINUTES} minutes. Do not share this code."
            )
            background_tasks.add_task(dispatch_customer_login_otp_sms, account.id, message)
    else:
        # LOCAL EMAIL OTP TESTING — never reached in production; the
        # branch above is untouched by any of this. Phone/SMS is
        # unchanged outside production (still never sent, same as
        # before): only email gets the dev_code/real_email modes, via
        # _dispatch_dev_email_otp below.
        if payload.channel == "email":
            otp_delivery_status = _dispatch_dev_email_otp(db, account, raw_code, OTP_EXPIRY_MINUTES)
        else:
            otp_delivery_status = "dev_code_only"

    record_audit_log(
        db, None, "customer_auth.login_otp_requested",
        entity_type="customer_account", entity_id=str(account.id),
        details=f"Login OTP dispatched via {payload.channel} for customer {account.id}",
        ip_address=ip,
    )
    db.commit()

    # Dev/testability-only — see CustomerLoginOtpRequestResponse.
    # dev_otp_code. raw_code is never logged and never persisted anywhere;
    # this is its only appearance outside the email/SMS it was (or, in the
    # non-production case, would have been) sent in. Populated regardless
    # of otp_delivery_status (even dev_real_email_sent) so the flow is
    # never a dead end and test-automation's existing contract (every
    # non-production response carries a usable code) doesn't change.
    dev_code = raw_code if settings.environment != "production" else None
    message = (
        "We've sent a verification code to your email."
        if payload.channel == "email"
        else "We've sent a verification code to your mobile number."
    )
    return CustomerLoginOtpRequestResponse(
        challenge_id=challenge.id, message=message, dev_otp_code=dev_code,
        otp_delivery_status=otp_delivery_status,
    )


@router.post("/login/verify-otp", response_model=CustomerLoginResponse)
def verify_customer_login_otp(payload: CustomerLoginVerifyRequest, request: Request, db: Session = Depends(get_db)):
    """
    Phase 3E step 2: the only place a CustomerSession/JWT is minted.
    Mirrors portal/auth_router.py's verify_phone_otp exactly — one
    generic failure message for every failure mode, constant-time code
    comparison, attempt_count incremented only on a wrong code.
    """
    ip = request.client.host if request.client else "unknown"

    challenge = db.get(CustomerLoginOtpChallenge, payload.challenge_id)
    if challenge is None:
        record_audit_log(
            db, None, "customer_auth.login_otp_verify_failure",
            entity_type="customer_login_otp_challenge", entity_id=str(payload.challenge_id),
            details="Unknown challenge_id", ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_OTP_VERIFY_ERROR)
    if challenge.consumed_at is not None:
        record_audit_log(
            db, None, "customer_auth.login_otp_verify_failure",
            entity_type="customer_login_otp_challenge", entity_id=str(challenge.id),
            details="Challenge already consumed", ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_OTP_VERIFY_ERROR)
    if challenge.expires_at < datetime.now(timezone.utc):
        record_audit_log(
            db, None, "customer_auth.login_otp_verify_failure",
            entity_type="customer_login_otp_challenge", entity_id=str(challenge.id),
            details="Challenge expired", ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_OTP_VERIFY_ERROR)
    if challenge.attempt_count >= challenge.max_attempts:
        record_audit_log(
            db, None, "customer_auth.login_otp_verify_failure",
            entity_type="customer_login_otp_challenge", entity_id=str(challenge.id),
            details="Max verification attempts exceeded", ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_OTP_VERIFY_ERROR)

    # Constant-time comparison — both sides are always a 64-char sha256 hex
    # digest, so this never leaks code length/content through timing.
    if not secrets.compare_digest(hash_otp_code(payload.code), challenge.code_hash):
        challenge.attempt_count += 1
        record_audit_log(
            db, None, "customer_auth.login_otp_verify_failure",
            entity_type="customer_login_otp_challenge", entity_id=str(challenge.id),
            details="Incorrect code", ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_OTP_VERIFY_ERROR)

    challenge.consumed_at = datetime.now(timezone.utc)

    account = db.get(CustomerAccount, challenge.customer_id)
    if account is None:
        # The customer account was somehow removed between step 1 and step
        # 2 — fails the same generic way as every other case above, never
        # a distinct message that would confirm the challenge itself was
        # otherwise valid.
        record_audit_log(
            db, None, "customer_auth.login_otp_verify_failure",
            entity_type="customer_login_otp_challenge", entity_id=str(challenge.id),
            details="Customer account no longer exists", ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _GENERIC_OTP_VERIFY_ERROR)

    verified_at = datetime.now(timezone.utc)
    if challenge.channel == "email":
        account.email_verified_at = verified_at
    elif challenge.channel == "phone":
        account.phone_verified_at = verified_at

    session_row = create_customer_session(db, account.id, ip_address=ip, user_agent=request.headers.get("user-agent"))
    access_token = create_customer_session_token(str(account.id), jti=str(session_row.id))

    record_audit_log(
        db, None, "customer_auth.login_success",
        entity_type="customer_account", entity_id=str(account.id),
        details=f"Customer login succeeded via {challenge.channel} OTP",
        ip_address=ip,
    )
    db.commit()

    return CustomerLoginResponse(access_token=access_token, customer=CustomerAccountOut.model_validate(account))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_customer(
    request: Request,
    resolved: tuple[CustomerAccount, uuid.UUID] = Depends(get_current_customer_with_session),
    db: Session = Depends(get_db),
):
    """Revokes exactly the caller's own current session (identified by
    the JWT's own jti) — never any other session, of this customer or any
    other. Calling this again with the same (now-revoked) token fails
    safely: get_current_customer_with_session itself already rejects a
    revoked session with 401 before this body would ever run again, so
    there is no double-revoke path to guard against here."""
    customer, session_id = resolved
    ip = request.client.host if request.client else "unknown"

    revoke_customer_session(db, session_id)

    record_audit_log(
        db, None, "customer_auth.logout",
        entity_type="customer_account", entity_id=str(customer.id),
        details=f"Customer session {session_id} revoked via logout",
        ip_address=ip,
    )
    db.commit()


@router.get("/me", response_model=CustomerAccountOut)
def get_me(customer: CustomerAccount = Depends(get_current_customer)):
    """Phase 3F: lets the frontend restore the authenticated customer's
    profile (name/email/phone) after a page reload, when only the token
    survives in localStorage and the in-memory customer object from the
    login response is gone. Identity comes ENTIRELY from the session
    token via get_current_customer — there is no id/email parameter to
    accept from the caller, so this can never return another customer's
    data. Read-only; never touches password_hash (CustomerAccountOut has
    no such field at all)."""
    return customer


def _linked_identity_guest(db: Session, customer: CustomerAccount) -> Guest:
    if customer.guest_id is None:
        raise HTTPException(409, "Set up your identity profile before uploading a document")
    guest = db.get(Guest, customer.guest_id)
    if guest is None:
        raise HTTPException(409, "Your guest profile needs staff assistance")
    if guest.merged_into_guest_id is not None:
        canonical = db.get(Guest, guest.merged_into_guest_id)
        if canonical is None or canonical.merged_into_guest_id is not None:
            raise HTTPException(409, "Your guest profile needs staff assistance")
        customer.guest_id = canonical.id
        return canonical
    return guest


def _customer_identity_status(db: Session, guest: Guest) -> GuestIdentityStatusOut:
    document = db.scalar(
        select(GuestDocument).where(GuestDocument.guest_id == guest.id).order_by(GuestDocument.uploaded_at.desc())
    )
    return GuestIdentityStatusOut(
        status=guest.identity_status,
        document_type=document.document_type if document else None,
        document_id=document.id if document else None,
        verified_at=guest.identity_verified_at,
        message=(
            "Your identity verification could not be completed. Please submit a valid identity document."
            if guest.identity_status == "rejected" else None
        ),
    )


@router.post("/me/identity/profile", response_model=GuestIdentityStatusOut)
def initialize_my_identity_profile(
    customer: CustomerAccount = Depends(get_current_customer), db: Session = Depends(get_db),
):
    """Resolve one Main Guest for the signed-in customer without accepting a
    guest_id from the browser. Verified OTP/Google contacts may match an
    existing Guest only when the combined result is unambiguous; otherwise
    a new profile is created server-side or the request fails closed.
    """
    try:
        resolution = resolve_customer_guest(db, customer)
        if resolution.created:
            record_audit_log(
                db, None, "customer_auth.identity_guest_created",
                entity_type="customer_account", entity_id=str(customer.id),
                details="Created a Main Guest profile for customer identity verification",
            )
        elif resolution.linked_existing:
            record_audit_log(
                db, None, "customer_auth.identity_guest_linked",
                entity_type="customer_account", entity_id=str(customer.id),
                details="Linked a Main Guest profile using verified customer contact evidence",
            )
        db.commit()
        db.refresh(resolution.guest)
        return _customer_identity_status(db, resolution.guest)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "We could not safely link this guest profile. Please contact Sri Apartment for assistance") from None


@router.get("/me/identity", response_model=GuestIdentityStatusOut)
def get_my_identity_status(
    customer: CustomerAccount = Depends(get_current_customer), db: Session = Depends(get_db),
):
    guest = _linked_identity_guest(db, customer)
    return _customer_identity_status(db, guest)


@router.post("/me/identity/documents", response_model=GuestIdentityDocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_my_identity_document(
    document_type: str,
    file: UploadFile = File(...),
    customer: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    guest = _linked_identity_guest(db, customer)
    if document_type not in IDENTITY_DOCUMENT_TYPES:
        raise HTTPException(400, "Invalid document_type")

    allowed_types = ("image/jpeg", "image/png", "image/webp", "application/pdf")
    contents = await read_and_validate_upload(file, allowed_content_types=allowed_types, verify_signature=True)
    extension = EXTENSION_BY_CONTENT_TYPE[file.content_type]
    stored_object: tuple[str, str] | None = None
    try:
        storage_backend, storage_key = await run_in_threadpool(
            document_storage.store_new_document, contents, extension, file.content_type,
        )
        stored_object = (storage_backend, storage_key)
        stored_path = (
            str(document_storage.storage_path(storage_backend, storage_key))
            if storage_backend in {"local", "railway_volume"} else ""
        )
        document = GuestDocument(
            guest_id=guest.id,
            document_type=document_type,
            document_number=None,
            filename=file.filename or storage_key,
            content_type=file.content_type,
            file_path=stored_path,
            storage_backend=storage_backend,
            storage_key=storage_key,
            file_size_bytes=len(contents),
            sha256_checksum=hashlib.sha256(contents).hexdigest(),
            side="document",
            uploaded_by=None,
        )
        db.add(document)
        db.flush()
        record_audit_log(
            db, None, "guest.identity_document_upload", entity_type="guest", entity_id=str(guest.id),
            details=f"Customer self-uploaded {document_type} via customer account: {document.filename}",
        )
        db.commit()
        db.refresh(document)
        return document
    except document_storage.StorageUnavailable:
        db.rollback()
        if stored_object:
            try:
                await run_in_threadpool(document_storage.delete_stored_object, *stored_object)
            except Exception:
                pass
        raise HTTPException(503, "Document storage is unavailable") from None
    except Exception:
        db.rollback()
        if stored_object:
            try:
                await run_in_threadpool(document_storage.delete_stored_object, *stored_object)
            except Exception:
                pass
        raise


@router.post("/me/identity/submit", response_model=GuestIdentityStatusOut)
def submit_my_identity_document(
    payload: GuestIdentitySubmitRequest,
    customer: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    guest = _linked_identity_guest(db, customer)
    document = db.get(GuestDocument, payload.document_id)
    if document is None or document.guest_id != guest.id:
        raise HTTPException(404, "Document not found")
    if guest.identity_status in ("submitted", "under_review", "verified"):
        raise HTTPException(409, f"Identity verification is already {guest.identity_status}")

    guest.identity_status = "submitted"
    record_audit_log(
        db, None, "guest.identity_submit", entity_type="guest", entity_id=str(guest.id),
        details=f"Submitted for review through customer account ({document.document_type})",
    )
    db.commit()
    db.refresh(guest)
    return _customer_identity_status(db, guest)


@router.patch("/me", response_model=CustomerAccountOut)
def update_me(
    payload: CustomerUpdateProfileRequest,
    request: Request,
    customer: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Phase 3F: first name / last name / phone only — see
    CustomerUpdateProfileRequest's own docstring for why email is
    deliberately excluded. `customer` is resolved from the session token,
    the same as every other authenticated endpoint here, so this can only
    ever modify the caller's own row."""
    ip = request.client.host if request.client else "unknown"
    normalized_phone = payload.phone

    if normalized_phone != customer.phone:
        conflict = db.scalar(
            select(CustomerAccount).where(CustomerAccount.phone == normalized_phone, CustomerAccount.id != customer.id)
        )
        if conflict is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this phone number already exists.")

    phone_changed = normalized_phone != customer.phone
    customer.first_name = payload.first_name
    customer.last_name = payload.last_name
    customer.phone = normalized_phone
    if phone_changed:
        # A profile edit is not proof of control over a new number. Require
        # a later successful phone OTP before that number can link a Guest.
        customer.phone_verified_at = None

    record_audit_log(
        db, None, "customer_auth.profile_updated",
        entity_type="customer_account", entity_id=str(customer.id),
        details="Customer updated their profile (name/phone)",
        ip_address=ip,
    )
    db.commit()
    db.refresh(customer)
    return customer


@router.post("/change-password", response_model=CustomerChangePasswordResponse)
def change_password(
    payload: CustomerChangePasswordRequest,
    request: Request,
    customer: CustomerAccount = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Phase 3F: requires the CURRENT password (not just an active
    session) before setting a new one — the same bar a change-password
    flow should always clear, since a stolen/left-open session token
    alone shouldn't be enough to lock the real owner out. On success,
    every active CustomerSession is revoked — same principle (and same
    mechanism) as reset_password already uses below: a password change is
    a real security event, so any other logged-in device/browser must
    re-authenticate with the new password rather than silently keep
    working. No new hashing scheme: hash_password/verify_password are the
    exact same Argon2 CryptContext every other customer/staff password
    already uses."""
    ip = request.client.host if request.client else "unknown"
    rate_limit_key = f"customer-change-password:{customer.id}"

    if is_rate_limited(rate_limit_key, max_attempts=CUSTOMER_CHANGE_PASSWORD_MAX_PER_CUSTOMER, window_seconds=CUSTOMER_CHANGE_PASSWORD_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please try again later.")

    if not verify_customer_password(customer, payload.current_password):
        record_attempt(rate_limit_key)
        record_audit_log(
            db, None, "customer_auth.change_password_failure",
            entity_type="customer_account", entity_id=str(customer.id),
            details="Incorrect current password provided", ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect.")

    reset(rate_limit_key)

    customer.password_hash = hash_password(payload.new_password)

    active_sessions = db.scalars(
        select(CustomerSession).where(CustomerSession.customer_id == customer.id, CustomerSession.revoked_at.is_(None))
    ).all()
    now = datetime.now(timezone.utc)
    for session_row in active_sessions:
        session_row.revoked_at = now

    record_audit_log(
        db, None, "customer_auth.change_password_success",
        entity_type="customer_account", entity_id=str(customer.id),
        details=f"Password changed; {len(active_sessions)} session(s) revoked",
        ip_address=ip,
    )
    db.commit()

    return CustomerChangePasswordResponse(message="Password changed. Please log in again.")


@router.post("/forgot-password", response_model=CustomerForgotPasswordResponse)
def forgot_customer_password(payload: CustomerForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """
    Always returns the same generic message regardless of whether the
    email belongs to a CustomerAccount — same reasoning auth/router.py's
    staff forgot_password already documents: confirming "that email
    isn't registered" to an unauthenticated caller is itself a real
    information leak.

    The one exception is SMTP being globally unconfigured IN PRODUCTION:
    that's a server-side state, identical for every caller regardless of
    which email was submitted, so it's checked first — before the account
    lookup — exactly like staff's forgot_password checks it first, for
    the same reason (it can never depend on whether the submitted email
    exists). Unlike staff's version, this check is gated on
    settings.environment == "production" too: outside production, a
    missing/present SMTP config is irrelevant because no send is ever
    attempted at all (see the CREDENTIAL-INDEPENDENT SAFETY GATE below) —
    this is the fix the OTP endpoints already applied to this exact
    pattern (see auth_router.py's own docstring for the incident this
    prevents) that staff's own forgot_password still lacks.
    """
    ip = request.client.host if request.client else "unknown"
    normalized = normalize_email(payload.email)
    ip_key = f"customer-forgot-password:ip:{ip}"
    email_key = f"customer-forgot-password:email:{normalized}"

    if is_rate_limited(ip_key, max_attempts=CUSTOMER_FORGOT_PASSWORD_MAX_PER_KEY, window_seconds=CUSTOMER_FORGOT_PASSWORD_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please try again later.")
    if is_rate_limited(email_key, max_attempts=CUSTOMER_FORGOT_PASSWORD_MAX_PER_KEY, window_seconds=CUSTOMER_FORGOT_PASSWORD_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests for this email. Please try again later.")

    if settings.environment == "production" and not settings.smtp_host:
        # 501 status is diagnostic (ops/monitoring can alert on it); the
        # message itself must stay generic — naming SMTP/config state here
        # would hand an anonymous caller a fact about backend internals,
        # the same class of leak this whole endpoint otherwise avoids by
        # never confirming/denying a submitted email is registered.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Something went wrong. Please try again later.")

    record_attempt(ip_key)
    record_attempt(email_key)

    generic_response = CustomerForgotPasswordResponse(message=_GENERIC_RESET_REQUEST_MESSAGE)

    account = db.scalar(select(CustomerAccount).where(CustomerAccount.email == normalized))
    if account is None:
        # Never create a token, never attempt any send, for an unknown
        # email — nothing beyond this point ever runs in that case.
        record_audit_log(
            db, None, "customer_auth.password_reset_requested",
            entity_type="customer_account", entity_id=None,
            details=f"Password reset requested for unknown email {normalized}",
            ip_address=ip,
        )
        db.commit()
        return generic_response

    # At most one valid reset token per customer at a time — same
    # "invalidate whatever was previously in flight" convention the OTP
    # request handlers already use for OtpChallenge.
    now = datetime.now(timezone.utc)
    still_active = db.scalars(
        select(CustomerPasswordResetToken).where(
            CustomerPasswordResetToken.customer_id == account.id,
            CustomerPasswordResetToken.used_at.is_(None),
        )
    ).all()
    for old in still_active:
        old.used_at = now

    raw_token = secrets.token_urlsafe(32)
    reset_row = CustomerPasswordResetToken(
        customer_id=account.id,
        token_hash=hash_reset_token(raw_token),
        expires_at=now + timedelta(minutes=CUSTOMER_PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
    )
    db.add(reset_row)
    db.commit()

    # No public /reset-password page exists yet (a separate future
    # website-UI phase) — this generates the URL for that future route
    # against the configured local dev origin, never an invented
    # production domain (see config.py's customer_portal_base_url).
    reset_url = f"{settings.customer_portal_base_url}/reset-password?token={raw_token}"
    subject = "Reset your Sri Apartment account password"
    body = (
        f"We received a request to reset your Sri Apartment account password.\n\n"
        f"Use this link to choose a new password (expires in {CUSTOMER_PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes):\n"
        f"{reset_url}\n\nIf you didn't request this, you can safely ignore this email."
    )

    # CREDENTIAL-INDEPENDENT SAFETY GATE: the check here is
    # settings.environment, decided BEFORE send_customer_password_reset_
    # email is ever called — not "try sending and see if it fails".
    # Outside production, no send is attempted at all, regardless of
    # whether valid SMTP credentials happen to exist; dev_reset_token
    # below is how local/automated testing completes the flow instead.
    # SMTP is guaranteed configured here (checked above, before the
    # account lookup, so it can never depend on which email was
    # submitted) — any failure now is a per-request delivery problem, not
    # a global config problem, so — same convention as staff's
    # forgot_password — it stays best-effort and silent to the caller.
    if settings.environment == "production":
        try:
            send_customer_password_reset_email(account.email, subject, body, db)
        except (EmailNotConfiguredError, smtplib.SMTPException, OSError, TimeoutError) as e:
            logger.error(f"Customer password reset email delivery failed for customer_id={account.id}: {type(e).__name__}: {e}")

    # Dev/testability-only — see CustomerForgotPasswordResponse.
    # dev_reset_token. raw_token is never logged and never persisted
    # anywhere; this is its only appearance outside the email it was (or,
    # in the non-production case, would have been) sent in.
    dev_token = raw_token if settings.environment != "production" else None

    record_audit_log(
        db, None, "customer_auth.password_reset_requested",
        entity_type="customer_account", entity_id=str(account.id),
        details=f"Password reset requested for {normalized}",
        ip_address=ip,
    )
    db.commit()

    return CustomerForgotPasswordResponse(message=_GENERIC_RESET_REQUEST_MESSAGE, dev_reset_token=dev_token)


@router.post("/reset-password", response_model=CustomerResetPasswordResponse)
def reset_customer_password(payload: CustomerResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    rate_limit_key = f"customer-reset-password:ip:{ip}"

    if is_rate_limited(rate_limit_key, max_attempts=CUSTOMER_RESET_PASSWORD_MAX_PER_IP, window_seconds=CUSTOMER_RESET_PASSWORD_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please try again later.")
    record_attempt(rate_limit_key)

    token_hash = hash_reset_token(payload.token)
    reset_row = db.scalar(select(CustomerPasswordResetToken).where(CustomerPasswordResetToken.token_hash == token_hash))

    if reset_row is None or reset_row.used_at is not None or reset_row.expires_at < datetime.now(timezone.utc):
        record_audit_log(
            db, None, "customer_auth.password_reset_failure",
            entity_type="customer_account", entity_id=(str(reset_row.customer_id) if reset_row else None),
            details="Invalid, expired, or already-used reset token presented",
            ip_address=ip,
        )
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    account = db.get(CustomerAccount, reset_row.customer_id)
    if account is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    account.password_hash = hash_password(payload.new_password)
    reset_row.used_at = datetime.now(timezone.utc)

    # A password reset is a real security event — every existing session
    # must stop working immediately, same principle (and same pattern) as
    # staff's own reset_password forcing a logout everywhere.
    active_sessions = db.scalars(
        select(CustomerSession).where(CustomerSession.customer_id == account.id, CustomerSession.revoked_at.is_(None))
    ).all()
    now = datetime.now(timezone.utc)
    for session_row in active_sessions:
        session_row.revoked_at = now

    record_audit_log(
        db, None, "customer_auth.password_reset_success",
        entity_type="customer_account", entity_id=str(account.id),
        details=f"Password reset completed; {len(active_sessions)} session(s) revoked",
        ip_address=ip,
    )
    db.commit()

    return CustomerResetPasswordResponse(message="Password has been reset. Please log in with your new password.")
