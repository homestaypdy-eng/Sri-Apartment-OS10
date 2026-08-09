import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

SETTINGS_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class BusinessSettings(Base):
    """
    Singleton table (always exactly one row, fixed ID) holding editable
    business configuration: company details, bank/UPI info, and document
    templates. Invoice/quotation PDF generation reads from here so an
    admin can update these from Settings instead of a code change.

    Seeded with the SAME values that were previously hardcoded in
    payments/service.py and inquiries/service.py, so existing PDF output
    is byte-for-byte unchanged until someone edits Settings — this is an
    additive change, not a behavior change.
    """

    __tablename__ = "business_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=lambda: SETTINGS_SINGLETON_ID)

    company_name: Mapped[str] = mapped_column(String(150), default="SRI APARTMENT HOMESTAY")
    tagline: Mapped[str] = mapped_column(String(200), default="Luxury 2BHK Family Homestay")
    address: Mapped[str] = mapped_column(Text, default="163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010")
    contact_phone: Mapped[str] = mapped_column(String(50), default="+91 88702 37237")
    contact_email: Mapped[str] = mapped_column(String(255), default="homestaypdy@gmail.com")
    gst_number: Mapped[str] = mapped_column(String(20), default="34PACPS5159H1Z5")
    udyam_number: Mapped[str] = mapped_column(String(30), default="UDYAM-PY-03-0032646")
    state_code: Mapped[str] = mapped_column(String(2), default="34")

    bank_name: Mapped[str] = mapped_column(String(100), default="Axis Bank")
    bank_account_name: Mapped[str] = mapped_column(String(150), default="Sri Apartment")
    bank_account_number: Mapped[str] = mapped_column(String(30), default="925020053638481")
    bank_ifsc: Mapped[str] = mapped_column(String(15), default="UTIB0003923")
    bank_branch: Mapped[str] = mapped_column(String(100), default="Puducherry Branch")
    upi_id: Mapped[str] = mapped_column(String(100), default="8870237237@axisbank")

    signatory_name: Mapped[str] = mapped_column(String(150), default="S. Sundaramurthy")
    signatory_title: Mapped[str] = mapped_column(String(100), default="Proprietor")

    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # falls back to bundled asset if null
    qr_code_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    invoice_terms: Mapped[str] = mapped_column(Text, default="")
    quotation_terms: Mapped[str] = mapped_column(
        Text,
        default=(
            "This is a pre-booking offer, valid up to the date above.\n"
            "Rates are subject to change after the validity date.\n"
            "Pre-booking is subject to availability of the room.\n"
            "Advance payment is required to confirm booking.\n"
            "ID proof is mandatory at the time of check-in."
        ),
    )
    house_rules: Mapped[str] = mapped_column(
        Text,
        default=(
            "No smoking inside the property.\n"
            "No parties or loud music allowed.\n"
            "Maintain cleanliness & respect the property."
        ),
    )
    cancellation_policy: Mapped[str] = mapped_column(
        Text,
        default=(
            "Full refund if cancelled 48 hrs before check-in.\n"
            "No refund after that window.\n"
            "Confirm booking by paying the advance amount."
        ),
    )

    default_gst_rate_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)  # override, null = use slab logic

    # Theme — kept deliberately minimal (a single brand accent color used
    # on generated PDFs), not a full web-app theming engine, which would
    # be redesign work well beyond what an internal ops tool needs.
    theme_color: Mapped[str] = mapped_column(String(7), default="#0f172a")  # hex color
    whatsapp_business_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
