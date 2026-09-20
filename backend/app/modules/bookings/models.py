import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import DDL
from sqlalchemy.event import listens_for

from app.db.session import Base


class Guest(Base):
    __tablename__ = "guests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    id_proof_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # e.g. "passport", "aadhaar", "driving_license"
    id_proof_number: Mapped[str | None] = mapped_column(String(50), nullable=True)  # the actual document number
    id_proof_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # scanned copy/photo, if uploaded
    gst_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    is_vip: Mapped[bool] = mapped_column(default=False)
    is_blacklisted: Mapped[bool] = mapped_column(default=False)
    # Personal details
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(60), nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Contact
    whatsapp_number: Mapped[str | None] = mapped_column(String(20), nullable=True)  # null = same as phone
    # Emergency contact
    emergency_contact_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Photo + notes
    photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # staff-only notes about this guest, distinct from any one booking's internal_notes
    # A guest merged into another is marked here rather than deleted, so
    # nothing that references the old guest_id (bookings, payments,
    # invoices — all financial history) ever breaks; see merge endpoint.
    merged_into_guest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guests.id"), nullable=True)


class GuestDocument(Base):
    """
    A specific identity document for a guest — Aadhaar/PAN/Passport/Driving
    Licence, each potentially with a front image, back image, and/or a
    PDF. Separate from Guest.id_proof_type/number/url (which remains as
    the single "primary ID used for checkout paperwork" field, unchanged,
    so nothing that already reads it breaks) — this is for a guest who
    has multiple documents on file, which the single field can't represent.
    """

    __tablename__ = "guest_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guests.id"), nullable=False)
    document_type: Mapped[str] = mapped_column(String(30), nullable=False)  # aadhaar/pan/passport/driving_license/other
    document_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    side: Mapped[str] = mapped_column(String(10), default="front")  # front/back/document (for a single-file PDF)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    guest = relationship("Guest", foreign_keys=[guest_id])


BOOKING_STATUSES = ("inquiry", "confirmed", "checked_in", "checked_out", "cancelled", "no_show")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("check_out > check_in", name="ck_checkout_after_checkin"),
        CheckConstraint(f"status IN {BOOKING_STATUSES}", name="ck_booking_status"),
        # Overlap protection is added via raw DDL below (EXCLUDE USING gist),
        # since SQLAlchemy's ORM layer doesn't have first-class support for
        # exclusion constraints. This is the actual double-booking guard —
        # enforced by Postgres itself, not application code.
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    guest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guests.id"), nullable=False)
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    source: Mapped[str] = mapped_column(String(30), default="direct")  # direct/walk_in/phone/whatsapp/ota_booking_com/...
    rate_applied: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    extra_guest_charge: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    security_deposit: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Bumped on every actual field change via ORM `onupdate` — this is the
    # optimistic-concurrency token: the edit form sends back the value it
    # loaded as `expected_updated_at`, and update_booking() 409s if it no
    # longer matches, instead of silently overwriting a change made by
    # someone else in the meantime.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    promo_code_used: Mapped[str | None] = mapped_column(String(30), nullable=True)  # set only by the public booking engine
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # for Outstanding Payments tracking; null = no due date set
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # staff-only notes, never shown to the guest
    special_requests: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the guest asked for (early check-in, extra bed, etc.)
    adults: Mapped[int] = mapped_column(default=1)
    children: Mapped[int] = mapped_column(default=0)
    extra_bed: Mapped[bool] = mapped_column(default=False)
    arrival_time: Mapped[str | None] = mapped_column(String(10), nullable=True)  # free-text "HH:MM", not a strict time column — guests give approximate arrival windows, not exact times
    departure_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Archive (soft-delete): an administrative "remove from the active list"
    # action, distinct from Cancel (a business status transition) and from
    # the existing hard DELETE (which stays blocked whenever any financial
    # record references the booking — see delete_booking). Archiving is
    # always allowed and always reversible, so it's the safe path for
    # getting a booking with real history out of the active list.
    is_archived: Mapped[bool] = mapped_column(default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Zotel channel-manager sync identity — Zotel's transaction_id for a
    # booking imported from Zotel (see app/modules/zotel/service.py).
    # Null for every direct/walk-in/phone/website booking. Unique when
    # set (see additive_migrations.py) so the same reservation fetched
    # on a later sync is found and reconciled, never re-created.
    external_reservation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    unit = relationship("Unit")
    guest = relationship("Guest")


# --- Overlap-safe booking constraint (the core anti-double-booking guard) ---
# A GENERATED daterange column + a GiST EXCLUDE constraint means Postgres
# physically rejects two overlapping *active* bookings for the same unit,
# regardless of what the application layer does. This is applied as raw DDL
# because SQLAlchemy doesn't model EXCLUDE constraints natively.
_add_stay_range_and_exclude = DDL(
    """
    ALTER TABLE bookings ADD COLUMN IF NOT EXISTS stay_range daterange
        GENERATED ALWAYS AS (daterange(check_in, check_out, '[)')) STORED;

    ALTER TABLE bookings ADD CONSTRAINT no_overlapping_bookings
        EXCLUDE USING gist (
            unit_id WITH =,
            stay_range WITH &&
        ) WHERE (status IN ('confirmed', 'checked_in'));

    -- Reporting queries filter/aggregate heavily on check_in (and, for
    -- per-unit performance, unit_id + check_in) over date ranges — this
    -- index is what keeps those aggregate queries off a full table scan
    -- as booking volume grows.
    CREATE INDEX IF NOT EXISTS ix_bookings_check_in ON bookings (check_in);
    CREATE INDEX IF NOT EXISTS ix_bookings_unit_check_in ON bookings (unit_id, check_in);
    """
)


@listens_for(Booking.__table__, "after_create")
def _apply_overlap_constraint(target, connection, **kw):
    connection.execute(_add_stay_range_and_exclude)


class BookingAttachment(Base):
    """
    A file attached to a booking (ID proof, signed agreement, etc).
    Stored on local disk in this environment, same pattern as invoice/
    quotation PDFs — NOT durable in a container redeploy without a
    persistent volume, same caveat documented for those. A real production
    deployment should point this at S3 or equivalent object storage
    instead; this is a genuine, working local-storage implementation, not
    a stub, but it inherits that one known limitation.
    """

    __tablename__ = "booking_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    booking = relationship("Booking")
