"""
Phase 3A: customer account & session data-model foundation for a future
self-service Sign Up / Log In (email+password, later Google — the only
social-login provider this project supports).
Kept entirely separate from BOTH existing auth surfaces:

  - app.modules.auth (internal staff/admin: User/Role/UserSession) — a
    CustomerAccount is never a User, has no role_id, and is never looked
    up by app.shared.rbac.get_current_user/require_role. A customer JWT's
    role claim ("customer" — see core/security.py's
    create_customer_session_token) can never equal "admin"/"staff"/
    "manager", so it can never satisfy any existing RBAC check.

  - app.modules.portal.auth_models (booking-side guest identity/session:
    GuestIdentity/GuestSession) — a CustomerAccount has no booking access
    of its own by construction. Nothing in this file links it to a Guest,
    and no endpoint exists yet (Phase 3B+) that would. Any future linking
    must be a deliberate action (mirroring Phase 2A's admin-only
    GuestIdentity-linking endpoint), never an automatic email match.

Three tables:

  CustomerAccount — one row per self-service registered customer.
                    password_hash uses the SAME argon2 CryptContext as
                    app.core.security.hash_password/verify_password — no
                    second hashing scheme. email is stored ALREADY
                    normalized via app.modules.portal.auth_service.
                    normalize_email (the same helper the OTP flows
                    already use for identifier matching, not a new
                    implementation), and is unique at the database level.
                    No date_of_birth field, per the current requirement.

  CustomerSession — modeled on auth.models.UserSession and portal.
                    auth_models.GuestSession: no stored secret, looked up
                    by its own id (used as the JWT's jti), checked for
                    revocation. UNLIKE those two (which deliberately have
                    no expires_at, relying solely on the JWT's own exp
                    claim — see GuestSession's docstring), this table
                    DOES carry an explicit expires_at: a deliberate extra
                    defense-in-depth layer for this newer, still-unproven
                    auth surface, so a session's own record independently
                    bounds its lifetime regardless of what any given
                    token's exp claim says.

  CustomerPasswordResetToken — Phase 3D. Modeled directly on
                    auth.models.PasswordResetToken (staff): only a
                    SHA-256 hash of the reset token is ever stored, never
                    the raw value, and it is single-use (used_at). A
                    dedicated table, not the staff table — a customer
                    password reset must never be able to touch a staff
                    User row, and vice versa, and the two tables' rows
                    must never be comparable/joinable by anything.

  CustomerLoginOtpChallenge — Phase 3E (public-website login redesign):
                    the second factor for customer login. Login is now
                    two-step — password validated first, then an OTP sent
                    to whichever channel (email or phone) the customer
                    logged in with, and a CustomerSession/JWT is only ever
                    minted after that OTP verifies. Modeled directly on
                    portal.auth_models.OtpChallenge (hashed code via the
                    SAME generate_otp_code/hash_otp_code helpers, single-
                    use via consumed_at, attempt-limited) — reused
                    intentionally rather than a new hashing scheme. A
                    dedicated table, not OtpChallenge itself: this is a
                    CustomerAccount-scoped 2FA step, never a Guest/booking
                    identity proof, and the two must never be joinable.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class CustomerAccount(Base):
    __tablename__ = "customer_accounts"
    __table_args__ = (
        # Partial unique index: enforces uniqueness among rows that DO have
        # a phone, while permitting any number of legacy NULLs (accounts
        # created before this column existed). Declared here too (not only
        # in the additive migration) so a completely fresh database created
        # via create_all() ends up with the identical constraint.
        Index("ix_customer_accounts_phone_unique", "phone", unique=True, postgresql_where=text("phone IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Always the OUTPUT of auth_service.normalize_email — never a second,
    # independent normalization. Unique at the DB level on the already-
    # normalized value, so two case/whitespace variants of the same real
    # address can never both register.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # Phase 3E: always the OUTPUT of portal.auth_service.normalize_phone
    # (the same helper guest-OTP identifier matching already uses — not a
    # second normalization implementation). Nullable at the DB level ONLY
    # so a pre-existing CustomerAccount row from before this column
    # existed is never fabricated a phone number (see the additive
    # migration in db/additive_migrations.py) — the signup schema itself
    # (CustomerRegisterRequest) still requires it for every NEW account.
    # Unique via a partial index (WHERE phone IS NOT NULL) rather than a
    # plain unique column, so multiple legacy NULLs remain legal.
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Argon2 via app.core.security.pwd_context — the exact CryptContext
    # staff passwords already use. Never a raw/plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Timestamps, not booleans: an auditable record of WHEN consent was
    # given, not merely that it was — matters if terms/policy text changes
    # later and past acceptances need to be understood against what they
    # actually agreed to at the time.
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    privacy_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Nullable on purpose: email verification is a Phase 3B+ policy
    # decision, not decided here. The column exists now so adding
    # verification later needs no schema change — only setting this at
    # the right moment.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CustomerSession(Base):
    """Guest/staff-session counterpart for CustomerAccount — see this
    module's docstring for why expires_at is present here even though
    the two older session tables deliberately omit it."""

    __tablename__ = "customer_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_accounts.id"), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer = relationship("CustomerAccount")


class CustomerPasswordResetToken(Base):
    __tablename__ = "customer_password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_accounts.id"), nullable=False, index=True)
    # sha256 hex digest of the raw token — the raw value only ever exists
    # in the reset email link, never at rest here. Same reasoning
    # auth.models.PasswordResetToken already documents: a fast hash is
    # appropriate for a short-lived, expiry-bounded, single-use secret.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL = still pending/usable. Non-null = either actually consumed by
    # a successful reset, or superseded by a newer request for the same
    # customer (at most one valid token per customer at a time — same
    # convention OtpChallenge.consumed_at already uses for "invalidated by
    # a new request", not a separate status column).
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CustomerLoginOtpChallenge(Base):
    """Phase 3E: the second factor for customer login. See this module's
    docstring for why this is a dedicated table rather than reusing
    portal.auth_models.OtpChallenge directly."""

    __tablename__ = "customer_login_otp_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_accounts.id"), nullable=False, index=True)
    # Which channel this specific OTP was sent to — the customer chose it
    # at step 1 (email or phone), and step 2 must be verified against the
    # same channel's code, never the other one.
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    # sha256 hex digest via portal.auth_service.hash_otp_code — the SAME
    # helper the guest OTP flows already use, not a second hashing scheme.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # NULL = still pending. Non-null = verified successfully OR superseded
    # by a newer login attempt for this customer — same single-field
    # convention OtpChallenge.consumed_at and CustomerPasswordResetToken.
    # used_at already use.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CustomerSocialIdentity(Base):
    """Phase 3G: links a verified Google identity to a CustomerAccount.
    Google is the only social-login provider this project supports —
    this table's `provider` column is kept as a plain string rather than
    a single-value enum only because it costs nothing to leave room for a
    possible future provider, never because another provider is currently
    supported. The identity key is (provider, provider_subject) — the provider's own
    stable, immutable user id ("sub" for Google's OIDC id_token) — NEVER
    email. Email addresses can change or be reused, so using email as the
    identity key would let a compromised or later-reused address at the
    provider be silently swapped onto a different real person's customer
    account; the subject cannot.

    A CustomerAccount row is unique per (provider, provider_subject) pair
    at the database level — a given Google identity can be linked to
    exactly one customer, and the reverse lookup ("does an account
    already have Google linked") is the same unique index doing double
    duty. A single CustomerAccount has at most one row here today (one
    Google identity), since Google is the only linkable provider.

    Deliberately NOT created automatically just because an OAuth email
    happens to match an existing CustomerAccount's email — see
    oauth_service.py's resolve_customer_for_oauth for the
    proof-of-ownership linking flow that guards against that exact
    account-takeover vector."""

    __tablename__ = "customer_social_identities"
    __table_args__ = (
        Index("ix_customer_social_identities_provider_subject", "provider", "provider_subject", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_accounts.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # Informational only — captured at the moment of linking for support/
    # audit purposes. NEVER used to look up or match an identity; the
    # (provider, provider_subject) pair above is the only identity key.
    email_at_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
