"""
Pure helper functions for the OTP/Google guest-auth redesign, plus (Phase
2A) small guest-auth-event logging helpers. `is_guest_session_active` was
originally written in Phase 1 as unwired foundation; portal/router.py's
get_current_guest now calls it for every guest token that carries a jti
(both OTP flows) — see that function's own comment for the exact
enforcement rule and why magic-link tokens (still jti-less) are unaffected.

Follows the same primitives the codebase already uses elsewhere rather
than introducing new ones: `secrets` for randomness (see
auth/router.py's forgot_password, which generates its raw reset token the
same way), hashlib.sha256 for at-rest hashing of a short-lived, rate-
limited secret (the same reasoning PasswordResetToken already documents:
a fast hash is appropriate here because the secret's security comes from
expiry + attempt limits + rate limiting, not from resisting offline
cracking — unlike a password, which is why THAT uses argon2 instead).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.portal.auth_models import GuestSession

OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60


def generate_otp_code() -> str:
    """Cryptographically random N-digit code — not TOTP, since delivery is
    push (SMS/email), not an authenticator app that verifies offline."""
    return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))


def hash_otp_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def otp_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)


def normalize_phone(raw_phone: str) -> str:
    """
    Canonical identifier for OTP/identity matching: bare 10-digit form, no
    country code. This is deliberately the OPPOSITE direction of the
    WhatsApp-link helper (_format_whatsapp_phone in inquiries/service.py)
    and send_sms's own internal formatting, which both PREPEND the country
    code — those exist for outbound delivery to a gateway API that requires
    it. Identity matching has a different target: it must agree with how
    Guest.phone is actually stored in this system today, which (confirmed
    against real data) is bare digits, no country code — the same
    convention the existing magic-link flow's exact-match lookup already
    relies on. "+91 88702 37237", "918870237237", and "08870237237" all
    converge to "8870237237", same as the already-stored "8870237237".
    """
    digits = "".join(c for c in raw_phone if c.isdigit())
    if len(digits) == 12 and digits.startswith(settings.default_country_code):
        return digits[len(settings.default_country_code):]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


def normalize_email(raw_email: str) -> str:
    return raw_email.strip().lower()


def is_guest_session_active(db: Session, session_id) -> bool:
    """
    Called by portal/router.py's get_current_guest for every guest token
    that carries a jti, to decide whether the presented guest session is
    still honored — mirrors how staff refresh tokens are checked against
    user_sessions in auth/router.py's /refresh. A missing (deleted, or
    never-existed/malformed-id) session is treated the same as a revoked
    one: not active.
    """
    session = db.get(GuestSession, session_id)
    return session is not None and session.revoked_at is None


def record_otp_audit(db: Session, channel: str, event: str, entity_id, details: str, ip_address: str | None) -> None:
    """
    Phase 2A guest OTP audit logging: a thin convenience wrapper around
    audit.schemas.record_audit_log for OTP request/verify events. Guest
    OTP flows have no staff `user_id` to attach the entry to (guests
    aren't rows in the `users` table AuditLog.user_id hard-FKs to) — this
    always passes user_id=None, the same convention the existing
    ambiguous-phone/email-match audit calls in auth_router.py/
    auth_email_router.py already use. Commits immediately, matching how
    auth/router.py's staff login already records an attempt right at the
    point of success/failure rather than deferring to a later, conditional
    commit elsewhere in the same request — so an event that leads to an
    exception being raised right after is still durably logged.

    `entity_id` should be the OtpChallenge id when one exists (request,
    verify success, and any verify failure where a challenge row was
    actually found), or the raw caller-supplied challenge_id string
    otherwise (e.g. an unknown/garbage challenge_id on verify) — still
    useful forensically (challenge-id enumeration attempts) without
    inventing a fake row to point at.

    NEVER pass an OTP code, its hash, or any credential as `details` —
    callers must only describe channel/outcome/identifier, exactly like
    the existing ambiguous-match audit entries already do.
    """
    from app.modules.audit.schemas import record_audit_log  # local import: same reason record_audit_log's own actor_role lookup does this — avoid a hard import-time dependency

    record_audit_log(
        db, None, f"guest_otp.{channel}_{event}",
        entity_type="otp_challenge", entity_id=str(entity_id) if entity_id is not None else None,
        details=details, ip_address=ip_address,
    )
    db.commit()
