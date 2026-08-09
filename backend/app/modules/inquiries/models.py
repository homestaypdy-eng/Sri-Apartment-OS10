import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

INQUIRY_STATUSES = ("draft", "sent", "confirmed", "expired", "cancelled")

# Sales-pipeline stage — deliberately separate from `status` above.
# `status` describes the quotation DOCUMENT's lifecycle (draft/sent/
# confirmed/...); `lead_status` describes where the CLIENT is in the sales
# funnel. A quotation can be `sent` while the lead is still `new` (sent but
# not yet followed up), or `follow_up` while status stays `sent` (no new
# document was issued, just ongoing conversation). Conflating these two
# would make either concept lie in some real scenario.
LEAD_STATUSES = ("new", "contacted", "follow_up", "converted", "lost")

INQUIRY_SOURCES = ("walk_in", "phone", "whatsapp", "ota", "website")
INQUIRY_PRIORITIES = ("low", "medium", "high", "urgent")

COMMUNICATION_CHANNELS = ("whatsapp", "email")


class InquiryCounter(Base):
    """Sequential, financial-year-scoped offer numbering: SRI/26-27/003."""

    __tablename__ = "inquiry_counters"

    financial_year_short: Mapped[str] = mapped_column(String(10), primary_key=True)  # e.g. "26-27"
    last_number: Mapped[int] = mapped_column(default=0)


class Inquiry(Base):
    __tablename__ = "inquiries"
    __table_args__ = (
        CheckConstraint(f"status IN {INQUIRY_STATUSES}", name="ck_inquiry_status"),
        CheckConstraint(f"lead_status IN {LEAD_STATUSES}", name="ck_inquiry_lead_status"),
        CheckConstraint("check_out_date > check_in_date", name="ck_inquiry_checkout_after_checkin"),
        CheckConstraint(f"source IN {INQUIRY_SOURCES}", name="ck_inquiry_source"),
        CheckConstraint(f"priority IN {INQUIRY_PRIORITIES}", name="ck_inquiry_priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    offer_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    offer_date: Mapped[date] = mapped_column(Date, default=lambda: datetime.now(timezone.utc).date())
    valid_upto: Mapped[date] = mapped_column(Date, nullable=False)

    # Client details
    client_name: Mapped[str] = mapped_column(String(150), nullable=False)
    client_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    client_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stay details
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    flat_room_label: Mapped[str] = mapped_column(String(100), nullable=False)  # free text, mirrors template's "Flat/Room"
    num_guests: Mapped[int] = mapped_column(default=1)
    check_in_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_time: Mapped[str] = mapped_column(String(20), default="12:30 PM")
    check_out_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_out_time: Mapped[str] = mapped_column(String(20), default="11:00 AM")
    tariff_type: Mapped[str] = mapped_column(String(50), default="Room Tariff")

    # Price breakdown (mirrors the "Accommodation Charges" block in the template)
    rate_per_night: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    nights: Mapped[int] = mapped_column(nullable=False)
    extra_charges: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    discount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    taxable_value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    gst_rate_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    cgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    sgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    rounded_off: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    grand_total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Payment
    advance_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    balance_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    payment_mode: Mapped[str] = mapped_column(String(50), default="Online / Bank Transfer")
    pricing_source: Mapped[str] = mapped_column(String(10), default="manual")  # "auto" | "manual"
    rate_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string of the daily rate breakdown, when auto-calculated

    status: Mapped[str] = mapped_column(String(20), default="draft")
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    guest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Lead management
    lead_status: Mapped[str] = mapped_column(String(20), default="new")
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    follow_up_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where this enquiry came from — walk-in/phone/whatsapp/ota/website —
    # distinct from Booking.source (which is set only after conversion);
    # this exists on the inquiry itself so lead-source reporting works
    # even for enquiries that never convert.
    source: Mapped[str] = mapped_column(String(20), default="phone")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Only meaningful once lead_status = "lost" — why the business didn't
    # get this booking (price, availability, chose a competitor, etc.),
    # kept separate from follow_up_notes since it's a specific, one-time
    # outcome, not an ongoing note.
    lost_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    unit = relationship("Unit")

    @property
    def is_expired(self) -> bool:
        """Computed, not stored — an inquiry is 'expired' the moment today
        passes valid_upto, without needing a background job to flip a flag."""
        return date.today() > self.valid_upto


class InquiryCommunication(Base):
    """Audit log of quotation sends — every WhatsApp/email send attempt,
    successful or not, so 'was this actually sent' has a real answer."""

    __tablename__ = "inquiry_communications"
    __table_args__ = (CheckConstraint(f"channel IN {COMMUNICATION_CHANNELS}", name="ck_communication_channel"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inquiry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inquiries.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    sent_to: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[bool] = mapped_column(default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class InquiryAttachment(Base):
    """A file attached to an enquiry — ID proof sent ahead, a reference
    photo the client shared, etc. Same shared pattern as
    BookingAttachment/GuestDocument, scoped to one Inquiry."""

    __tablename__ = "inquiry_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inquiry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inquiries.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
