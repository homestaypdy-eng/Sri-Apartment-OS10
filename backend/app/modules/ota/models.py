import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

OTA_CHANNELS = ("booking_com", "airbnb", "agoda")
SYNC_TYPES = ("reservation_import", "availability_sync", "rate_sync", "booking_update", "cancellation_sync")
SYNC_STATUSES = ("success", "failed", "partial")


class OTAChannelConfig(Base):
    """
    One row per OTA channel. Real Booking.com/Airbnb/Agoda integration
    requires becoming an approved connectivity partner with each platform
    (a business/legal process, not just an API key) — this system cannot
    self-provision that, so `is_live` stays false regardless of any
    credentials configured, until that partnership actually exists.
    """

    __tablename__ = "ota_channel_configs"
    __table_args__ = (CheckConstraint(f"channel IN {OTA_CHANNELS}", name="ck_ota_channel"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class OTAListingMapping(Base):
    """Maps a local Unit to its external listing ID on a given OTA channel."""

    __tablename__ = "ota_listing_mappings"
    __table_args__ = (UniqueConstraint("channel", "unit_id", name="uq_ota_mapping_channel_unit"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    external_listing_id: Mapped[str] = mapped_column(String(150), nullable=False)

    unit = relationship("Unit")


class OTASyncLog(Base):
    __tablename__ = "ota_sync_logs"
    __table_args__ = (
        CheckConstraint(f"sync_type IN {SYNC_TYPES}", name="ck_ota_sync_type"),
        CheckConstraint(f"status IN {SYNC_STATUSES}", name="ck_ota_sync_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    records_affected: Mapped[int] = mapped_column(default=0)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
