import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

SETTINGS_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
BANK_SETTINGS_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


class BusinessSettings(Base):
    """
    Singleton table (always exactly one row, fixed ID) holding editable
    business configuration: company details and document templates.
    Invoice/quotation PDF generation reads from here so an admin can
    update these from Settings instead of a code change.

    Seeded with the SAME values that were previously hardcoded in
    payments/service.py and inquiries/service.py, so existing PDF output
    is byte-for-byte unchanged until someone edits Settings — this is an
    additive change, not a behavior change.

    RBAC Phase A: bank/UPI fields used to live on this table, readable by
    admin+staff via GET /api/v1/settings. See BankSettings below — they
    were split into their own admin-only table so a non-admin role (Manager
    included) can read the rest of this settings resource without ever
    receiving the real bank account number, IFSC, or UPI ID.
    """

    __tablename__ = "business_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=lambda: SETTINGS_SINGLETON_ID)

    company_name: Mapped[str] = mapped_column(String(150), default="SRI APARTMENT HOMESTAY")
    tagline: Mapped[str] = mapped_column(String(200), default="Comfortable Stays in the Heart of Puducherry")
    address: Mapped[str] = mapped_column(Text, default="163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010")
    contact_phone: Mapped[str] = mapped_column(String(50), default="+91 88702 37237")
    contact_email: Mapped[str] = mapped_column(String(255), default="homestaypdy@gmail.com")
    gst_number: Mapped[str] = mapped_column(String(20), default="34PACPS5159H1Z5")
    udyam_number: Mapped[str] = mapped_column(String(30), default="UDYAM-PY-03-0032646")
    state_code: Mapped[str] = mapped_column(String(2), default="34")

    signatory_name: Mapped[str] = mapped_column(String(150), default="S. Sundaramurthy")
    signatory_title: Mapped[str] = mapped_column(String(100), default="Proprietor")

    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # falls back to bundled asset if null
    qr_code_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    invoice_terms: Mapped[str] = mapped_column(Text, default="")
    quotation_terms: Mapped[str] = mapped_column(
        Text,
        default=(
            "This is a pre-booking offer, valid up to the date shown above.\n"
            "Rates are subject to validity & availability.\n"
            "Booking is confirmed only after receipt of required advance payment.\n"
            "Check-in: 12:30 PM  |  Check-out: 11:00 AM.\n"
            "Early check-in / late check-out subject to availability & charges.\n"
            "Valid government-issued ID proof is mandatory at check-in."
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
            "Cancellation requests must be submitted through WhatsApp or email.\n"
            "Cancellation more than 48 hours before scheduled check-in: eligible for a 100% refund.\n"
            "Cancellation exactly 48 hours before or within 48 hours of scheduled check-in: eligible for a 50% refund; the remaining 50% is retained as a late-cancellation charge.\n"
            "Approved refunds are initiated promptly to the original payment method. Posting time varies by payment provider, bank, card issuer, or UPI provider."
        ),
    )

    default_gst_rate_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)  # override, null = use slab logic

    # Theme — kept deliberately minimal (a single brand accent color used
    # on generated PDFs), not a full web-app theming engine, which would
    # be redesign work well beyond what an internal ops tool needs.
    theme_color: Mapped[str] = mapped_column(String(7), default="#0f172a")  # hex color
    # Real default matches this business's actual primary WhatsApp number
    # (same convention as contact_email's own default above) — a fresh
    # install/fresh database starts with the real, known owner contact
    # rather than an empty field an admin has to remember to fill in.
    # The existing dev database's row was already set to this same number
    # via the settings UI/API before this default existed; a column
    # default never overwrites an already-set value.
    whatsapp_business_number: Mapped[str | None] = mapped_column(String(20), nullable=True, default="+918870237237")
    # Booking-notification audit: the business runs two owner contacts, and
    # every owner-facing booking notification (email, and WhatsApp once a
    # provider exists) must reach both. contact_email/whatsapp_business_number
    # above stay the PRIMARY contact (unchanged meaning, unchanged callers);
    # these are the SECONDARY, additive and nullable so a fresh install
    # without a second contact isn't forced to have one.
    secondary_contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True, default="sriapartmentpondy@gmail.com")
    secondary_whatsapp_number: Mapped[str | None] = mapped_column(String(20), nullable=True, default="+919361946138")

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class BankSettings(Base):
    """
    RBAC Phase A: the business's own receiving bank/UPI details, split out
    of BusinessSettings into their own singleton table specifically so
    they can be gated admin-only (GET and PUT) independently of the rest
    of business settings, which stays readable by admin+staff (and, once
    wired in a later phase, Manager). This is the direct fix for the audit
    finding that "staff" — and by extension any future Manager role that
    reused the same check — could read the real bank account number, IFSC,
    and UPI ID via the general settings endpoint.

    Same singleton pattern as BusinessSettings (fixed id, always exactly
    one row). The Phase A split itself carried forward whatever values
    were already live at the time (see additive_migrations.py's
    migrate_legacy_bank_settings) rather than resetting anything; the
    column defaults below are only the fresh-install starting point and
    were separately updated during the Bank Account Migration (Axis →
    IDFC FIRST Bank) to reflect the current, authoritative account.

    NOT a secret store: these are the business's own bank account details
    for receiving guest payments (the same kind of information that
    already appears on every invoice PDF), not a login credential or API
    key. It's sensitive *business* data, gated the same way any other
    admin-only financial record in this system already is.
    """

    __tablename__ = "bank_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=lambda: BANK_SETTINGS_SINGLETON_ID)

    # Bank Account Migration: updated to the current, authoritative bank
    # details (replacing the previous bank's, now retired) — this is only
    # the fresh-install default (a brand new database with no bank_settings
    # row yet); the live/local development database's actual row was
    # updated separately via PUT /api/v1/settings/bank, not by changing
    # this default.
    bank_name: Mapped[str] = mapped_column(String(100), default="IDFC FIRST Bank")
    bank_account_name: Mapped[str] = mapped_column(String(150), default="SRI APARTMENT")
    bank_account_number: Mapped[str] = mapped_column(String(30), default="10304118357")
    bank_ifsc: Mapped[str] = mapped_column(String(15), default="IDFB0080134")
    bank_branch: Mapped[str] = mapped_column(String(100), default="Pondicherry")
    upi_id: Mapped[str] = mapped_column(String(100), default="sriapartment@idfcbank")

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
