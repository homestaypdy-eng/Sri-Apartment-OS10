import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class AuditLog(Base):
    """
    Records who did what to which entity. Hooked into a targeted set of
    security/finance-sensitive actions (settings changes, role changes,
    user activation, payroll, refunds) rather than every single endpoint —
    logging literally everything would need middleware-level interception
    with per-route field redaction, a bigger change than this pass scopes.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "settings.update", "user.role_change"
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # free-form human-readable summary
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # structured "before" state, when the action is a modification
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # structured "after" state
    ip_address: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User")


class LoginEvent(Base):
    """Session/login history — every login attempt, successful or not."""

    __tablename__ = "login_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    email_attempted: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_address: Mapped[str | None] = mapped_column(String(50), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
