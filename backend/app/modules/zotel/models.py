"""
Zotel channel-manager integration — data model.

Kept entirely separate from app/modules/ota/ (OTAChannelConfig/
OTAListingMapping/OTASyncLog): that module models a DIRECT partnership
with an OTA platform itself (Booking.com/Airbnb/Agoda/MMT), gated by
OTA_CHANNELS with its own CHECK constraint and its own "is_live requires
a real partnership" semantics. Zotel is architecturally different — a
single third-party channel-manager SaaS product OS10 talks to over one
documented REST API, which itself aggregates whichever OTAs the hotel has
connected to Zotel. Reusing OTAListingMapping would have meant either
widening OTA_CHANNELS's CHECK constraint to include a non-OTA value, or
bypassing the existing create_mapping() endpoint's explicit channel
validation — either weakens an existing, already-shipped feature's
guarantees for the sake of this one. A parallel, equally small model set
keeps both integrations independently correct.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

ZOTEL_SYNC_TYPES = ("connectivity_test", "bookings_import", "reconciliation")
ZOTEL_SYNC_STATUSES = ("success", "partial", "failed")
ZOTEL_RECORD_OUTCOMES = ("imported", "updated", "skipped", "unmapped", "conflicted", "failed")


class ZotelRoomMapping(Base):
    """
    Explicit, admin-configured mapping from a Zotel room_type_id (a UUID,
    per the Zotel External API docs — "prefer a stable external identifier"
    rather than display name) to an OS10 Unit. Never auto-created: a Zotel
    room type with no row here is reported as unmapped, not guessed.
    """

    __tablename__ = "zotel_room_mappings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), unique=True, nullable=False)
    # Zotel's room_type_id is itself a UUID per the docs, but stored as
    # text here (not a Postgres UUID column) since it's an opaque external
    # identifier we only ever compare for equality, never join against
    # locally-generated UUIDs.
    zotel_room_type_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    zotel_room_type_name: Mapped[str | None] = mapped_column(String(150), nullable=True)  # cached label only, never the join key
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")


class ZotelSyncLog(Base):
    """One row per sync run (connectivity test, bookings import, or a
    reconciliation pass) — the per-run summary. Per-reservation detail
    lives in ZotelSyncRecord, linked via sync_log_id."""

    __tablename__ = "zotel_sync_logs"
    __table_args__ = (
        CheckConstraint(f"sync_type IN {ZOTEL_SYNC_TYPES}", name="ck_zotel_sync_type"),
        CheckConstraint(f"status IN {ZOTEL_SYNC_STATUSES}", name="ck_zotel_sync_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # safe, secret-free summary
    records_fetched: Mapped[int] = mapped_column(default=0)
    records_imported: Mapped[int] = mapped_column(default=0)
    records_updated: Mapped[int] = mapped_column(default=0)
    records_skipped: Mapped[int] = mapped_column(default=0)
    records_unmapped: Mapped[int] = mapped_column(default=0)
    records_conflicted: Mapped[int] = mapped_column(default=0)
    records_failed: Mapped[int] = mapped_column(default=0)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ZotelSyncRecord(Base):
    """Per-reservation outcome of one bookings_import/reconciliation run.
    external_reservation_id is Zotel's transaction_id — always recorded,
    even for a failed/unmapped/conflicted outcome, since that id is the
    only safe way to find this reservation again later without guessing
    from customer name/phone/dates."""

    __tablename__ = "zotel_sync_records"
    __table_args__ = (CheckConstraint(f"outcome IN {ZOTEL_RECORD_OUTCOMES}", name="ck_zotel_record_outcome"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_log_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("zotel_sync_logs.id"), nullable=False)
    external_reservation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    # Never customer PII beyond what's already on the linked Booking/Guest
    # row — just what happened and why, e.g. "unmapped room_type_id
    # a1b2...", "conflicts with existing confirmed booking <id>".
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sync_log = relationship("ZotelSyncLog")
    booking = relationship("Booking")
