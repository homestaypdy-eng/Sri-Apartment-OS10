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


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "Apartment 2B"
    unit_type: Mapped[str] = mapped_column(String(50), default="apartment")
    max_occupancy: Mapped[int] = mapped_column(default=2)
    base_rate: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # weekday rate
    weekend_rate: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)  # Fri/Sat nights; falls back to base_rate if unset
    status: Mapped[str] = mapped_column(String(20), default="available")  # available/maintenance/blocked
    housekeeping_status: Mapped[str] = mapped_column(String(20), default="clean")  # clean/dirty/inspected
    unit_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    floor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    flat_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amenities: Mapped[str | None] = mapped_column(Text, nullable=True)  # free-text/comma-separated — e.g. "AC, WiFi, Kitchen, Balcony"
    is_active: Mapped[bool] = mapped_column(default=True)  # soft-deactivation — see deactivate/reactivate endpoints; never hard-deleted

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
    same shared pattern as PropertyFile/GuestDocument, scoped to one Unit."""

    __tablename__ = "unit_photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("units.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    caption: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    unit = relationship("Unit")
