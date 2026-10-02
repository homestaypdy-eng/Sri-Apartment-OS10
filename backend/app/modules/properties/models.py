import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    address: Mapped[str] = mapped_column(String(500), default="")
    gst_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    state_code: Mapped[str] = mapped_column(String(2))  # for CGST/SGST vs IGST logic
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    property_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "homestay", "apartment_block", "villa"
    owner_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    owner_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_maps_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)  # soft-deactivation — see deactivate/reactivate endpoints; never hard-deleted

    units: Mapped[list["Unit"]] = relationship(back_populates="property", cascade="all, delete-orphan")


# Canonical accommodation-type vocabulary — the set of values the admin
# dropdown (Phase 2/4A) should offer. Unit.unit_type stays an unconstrained
# String(50) (no DB CHECK constraint), so this is a suggested list, not an
# enforced one: an existing or future value outside this tuple (e.g. a typo,
# or a genuinely new type someone types via "other") is still accepted and
# stored as-is, never rejected.
UNIT_TYPES = ("apartment", "villa", "suite", "room", "cottage", "resort", "other")

# Unlike UNIT_TYPES above, this IS enforced (Phase 5A) — app.shared.availability
# and every booking-creation path branch on unit.status being exactly one of
# these three, so an unvalidated typo here would silently make a unit
# unbookable (or worse, leave it bookable when it shouldn't be) with no
# error surfaced anywhere. Validated at the app layer (properties/router.py's
# update_unit), not a DB CHECK constraint — same pattern as
# maintenance.models.TICKET_STATUSES/TICKET_PRIORITIES.
UNIT_STATUSES = ("available", "maintenance", "blocked")


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "Apartment 2B"
    unit_type: Mapped[str] = mapped_column(String(50), default="apartment")
    max_occupancy: Mapped[int] = mapped_column(default=2)
    base_rate: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # weekday rate
    weekend_rate: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)  # Fri/Sat nights; falls back to base_rate if unset
    # Long-stay absolute nightly rate: a stay of `long_stay_min_nights` or
    # more is billed at `long_stay_rate` for EVERY night, overriding
    # weekday/weekend/seasonal pricing entirely (see pricing.py's module
    # docstring) — not a percentage discount, unlike LongStayDiscountTier
    # below. NOT NULL with a real default (matches the business's current
    # 30-night / Rs.2,000 policy) so the pricing engine always has a
    # well-defined value to read, same reasoning as base_rate itself.
    long_stay_min_nights: Mapped[int] = mapped_column(default=30)
    long_stay_rate: Mapped[float] = mapped_column(Numeric(10, 2), default=2000)
    status: Mapped[str] = mapped_column(String(20), default="available")  # available/maintenance/blocked
    housekeeping_status: Mapped[str] = mapped_column(String(20), default="clean")  # clean/dirty/inspected
    unit_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    floor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    flat_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amenities: Mapped[str | None] = mapped_column(Text, nullable=True)  # free-text/comma-separated — e.g. "AC, WiFi, Kitchen, Balcony"
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # long-form listing copy, e.g. for the public website
    is_active: Mapped[bool] = mapped_column(default=True)  # soft-deactivation — see deactivate/reactivate endpoints; never hard-deleted

    # Structured unit details (Admin-control phase 2). All nullable with no
    # default value change to existing rows — an existing unit's amenities/
    # description text is untouched; these are additive fields an admin
    # fills in going forward. Website falls back to its own existing
    # hardcoded copy (check-in/out time) or the business-level default
    # (house_rules) whenever a given unit hasn't set its own value yet.
    bedroom_count: Mapped[int | None] = mapped_column(nullable=True)
    bathroom_count: Mapped[int | None] = mapped_column(nullable=True)
    house_rules: Mapped[str | None] = mapped_column(Text, nullable=True)  # per-unit override of BusinessSettings.house_rules
    checkin_time: Mapped[str | None] = mapped_column(String(20), nullable=True)  # e.g. "12:30 PM"; falls back to the site default when unset
    checkout_time: Mapped[str | None] = mapped_column(String(20), nullable=True)

    property: Mapped["Property"] = relationship(back_populates="units")


class SeasonalRate(Base):
    """
    A date-range rate override for a unit (e.g. Diwali week, Christmas/New
    Year). Takes priority over weekday/weekend rates for any night that
    falls within [start_date, end_date] inclusive. Overlapping seasonal
    ranges for the same unit are allowed at the DB level (no constraint
    against it) — the pricing engine resolves overlaps by picking the most
    recently created one, and that resolution rule is documented in
    pricing.py rather than silently picked without a rule.
    """

    __tablename__ = "seasonal_rates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "Diwali 2026"
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate_per_night: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")


class LongStayDiscountTier(Base):
    """
    A percentage discount applied to the pre-discount subtotal once a stay
    reaches min_nights. Multiple tiers per unit are allowed (e.g. 7+ nights
    -> 10%, 30+ nights -> 20%); the pricing engine picks the tier with the
    HIGHEST min_nights that the stay still qualifies for, not the first
    match, so a 35-night stay gets the 30-night tier's discount, not the
    7-night one.

    Distinct from Unit.long_stay_min_nights/long_stay_rate (an absolute
    flat nightly rate, not a percentage) — the two mechanisms never stack;
    the absolute rate takes priority whenever it applies, and these tiers
    only ever apply on top of a stay it did NOT apply to (see pricing.py).
    """

    __tablename__ = "long_stay_discount_tiers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    min_nights: Mapped[int] = mapped_column(nullable=False)
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)

    unit = relationship("Unit")


class PropertyFile(Base):
    """
    A document or photo attached to a property — ownership papers,
    property tax receipts, exterior/interior photos, etc. Separate model
    from GuestDocument/BookingAttachment (same shared pattern) since this
    attaches to a Property, not a Guest or Booking.
    """

    __tablename__ = "property_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("properties.id"), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), default="document")  # document | photo
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    caption: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    property = relationship("Property")


class UnitPhoto(Base):
    """A photo of a unit/apartment (exterior, interior, bedroom, etc.) —
    same shared pattern as PropertyFile/GuestDocument, scoped to one Unit.

    `caption` doubles as the public website's image alt text (see
    PublicUnitPhotoOut/resolveUnitPhotos on the frontend) — there is
    deliberately no separate alt-text field. `sort_order` (lower first) and
    `is_primary` are additive (Admin-control phase 2): both default to
    values that reproduce today's existing behavior for every already-
    uploaded photo (insertion order, no photo marked primary) until an
    admin explicitly reorders or picks a cover photo."""

    __tablename__ = "unit_photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    caption: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_primary: Mapped[bool] = mapped_column(default=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")
