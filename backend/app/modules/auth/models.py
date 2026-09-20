import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    # The currently implemented roles are exactly: 'admin', 'staff',
    # 'manager' — these are the only role names require_role(...) checks
    # anywhere in the codebase actually accept. No other role name has any
    # meaning at runtime; do not read this column as a roadmap of roles
    # that exist or are planned.

    permissions: Mapped[list["Permission"]] = relationship(back_populates="role", cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship(back_populates="role")


class Permission(Base):
    """
    RBAC finding I-5: this table is currently DORMANT — it is seeded at
    bootstrap (see app/db/seed.py's ROLE_PERMISSIONS) but no runtime
    authorization code anywhere reads it. Every actual access-control
    decision in this codebase goes through require_role(...) in
    app/shared/rbac.py, which checks a route-declared tuple of role NAMES
    against the current user's role — it never queries this table. Rows
    here (including the admin "*"/"*" wildcard row) are stored data only,
    with no enforcement meaning today. Activating genuine per-resource
    permission checks against this table would be a separate, deliberate
    RBAC project (defining what the wildcard means, backfilling accurate
    rows for every role against the real route matrix, then wiring
    require_role or a successor to actually read it) — not something to
    assume is already happening because this table and its rows exist.
    """

    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("role_id", "resource", "action", name="uq_role_resource_action"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    resource: Mapped[str] = mapped_column(String(50))  # e.g. 'bookings', 'payments', 'staff'
    action: Mapped[str] = mapped_column(String(50))  # e.g. 'create', 'read', 'update', 'delete'

    role: Mapped["Role"] = relationship(back_populates="permissions")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    role: Mapped["Role"] = relationship(back_populates="users")


class PasswordResetToken(Base):
    """
    A real password-reset token: only the SHA-256 hash is stored (never
    the raw token — the same principle as never storing a plaintext
    password), single-use (consumed_at), and short-lived. The raw token
    only ever exists in the reset email link and this row's hash, never
    logged or persisted anywhere else.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # sha256 hex digest
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserSession(Base):
    """
    A real, DB-backed record of each issued refresh token — the missing
    piece that turned "stateless JWT" into an actual, revocable session.
    Each refresh token embeds this row's id as its jti claim; /refresh
    checks the row hasn't been revoked before honoring the token, so
    revoking a session here takes effect immediately, not just at
    natural JWT expiry.
    """

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
