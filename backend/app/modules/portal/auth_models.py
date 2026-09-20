"""
Phase 1 of the guest-auth redesign (see the read-only architecture audit
this implements): the additive database foundation for phone OTP, email
OTP, and Google sign-in, alongside revocable guest sessions.

Deliberately separate from portal/models.py (which holds GuestRequest, an
unrelated concern) and NOT imported by portal/router.py — these models
have no endpoints wired to them yet. The existing magic-link flow
(request-login / verify, GuestRequest, get_current_guest) is completely
untouched by this file.

Three tables, three responsibilities:

  OtpChallenge     — one row per OTP send, either channel. Modeled directly
                      on auth.models.PasswordResetToken: hashed code, never
                      the raw value; a single consumed_at timestamp doubles
                      as both "was this used" and "when" (no separate status
                      column that could drift out of sync with it).

  GuestIdentity     — "this phone / this email / this Google subject is the
                      same person across visits." guest_id is nullable ON
                      PURPOSE: an identity can be fully authenticated before
                      any staff-created Guest record exists. The
                      UniqueConstraint on (channel, external_id) is what
                      actually prevents one phone/email/Google account from
                      ever being represented by two different identity rows
                      — not a constraint on guest_id, which wouldn't stop
                      two rows from separately claiming the same contact
                      point. Nothing in this file auto-populates guest_id;
                      linking is a deliberate, separate action for a later
                      phase, never a side effect of matching an email.

  GuestSession      — modeled directly on auth.models.UserSession: no
                      stored secret at all (not even a hash), because the
                      session's unforgeability comes from the JWT's own
                      HS256 signature, not from this row. The row exists
                      purely so a session can be looked up by its id (used
                      as the token's jti) and checked for revocation. Like
                      UserSession, there is no expires_at column — expiry is
                      enforced by the token's own exp claim, exactly as
                      staff refresh sessions already work; duplicating that
                      into a second column would just be a second place for
                      it to (eventually) disagree with the token.

                      identity_id (not guest_id) is the required link, since
                      a session must be able to exist for an identity that
                      has no linked guest yet. Once an identity is linked,
                      its guest is reached via GuestIdentity.guest_id.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

OTP_CHANNELS = ("phone", "email")
IDENTITY_CHANNELS = ("phone", "email", "google")

DEFAULT_OTP_MAX_ATTEMPTS = 5


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    __table_args__ = (
        CheckConstraint(f"channel IN {OTP_CHANNELS}", name="ck_otp_challenge_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    # Normalized phone (digits, country-code-prepended) or lowercased email
    # — never the raw as-typed string, so the same real-world destination
    # always produces the same identifier. Not itself secret, but never
    # logged/exposed beyond what this row and its owner's request needed.
    identifier: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # sha256 hex digest of the 6-digit code — same principle as
    # PasswordResetToken.token_hash: the raw code exists only in the
    # SMS/email it was sent in, never at rest here.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_OTP_MAX_ATTEMPTS)
    # NULL = still pending/active. Non-null = successfully verified and
    # single-use-consumed — same PasswordResetToken.consumed_at pattern.
    # There is deliberately no separate "status" enum: a timestamp that is
    # either null or set cannot itself disagree with a second status field,
    # because there is no second field.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Abuse/audit metadata only — same field name and shape as
    # UserSession.ip_address, not used for any authentication decision.
    request_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Resend cooldown is deliberately NOT a stored column: it's computed at
    # request time from the most recent created_at for the same identifier
    # (see auth_service.py), the same way UserSession has no redundant
    # "is_active" column — it's derived from revoked_at instead.


class GuestIdentity(Base):
    __tablename__ = "guest_identities"
    __table_args__ = (
        CheckConstraint(f"channel IN {IDENTITY_CHANNELS}", name="ck_guest_identity_channel"),
        # The actual security boundary: one real-world phone/email/Google
        # subject can only ever be ONE row. A constraint on guest_id
        # instead would not prevent two separate rows from each claiming
        # the same phone number.
        UniqueConstraint("channel", "external_id", name="uq_guest_identity_channel_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    # Normalized phone / lowercased email / Google `sub` — the thing this
    # row's owner actually proved control of.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable on purpose — see module docstring. Never set by anything in
    # this file; linking is a separate, later action.
    guest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guests.id"), nullable=True, index=True)
    # Google's claimed email, kept for reference on google-channel rows
    # even though external_id is the `sub`. Not itself a trust boundary —
    # linking policy (see the architecture doc) still applies to it.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # When this channel was first proven controlled (OTP verified / Google
    # ID token validated).
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    # When guest_id was set — separate from verified_at because linking can
    # happen well after first verification (an identity can sit unlinked
    # for a long time before a guest record exists to link it to).
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    guest = relationship("Guest")


class GuestSession(Base):
    """
    Guest-side counterpart to auth.models.UserSession — see that class's
    docstring for the underlying pattern (a DB-backed, revocable record
    referenced by a JWT's jti claim, not a stored secret). identity_id
    (not guest_id) is intentional: see module docstring.
    """

    __tablename__ = "guest_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guest_identities.id"), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    identity = relationship("GuestIdentity")
