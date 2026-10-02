import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class PublicUnitOut(BaseModel):
    id: uuid.UUID
    name: str
    unit_type: str
    base_rate: float
    max_occupancy: int
    property_name: str
    property_type: str | None = None
    description: str | None = None
    amenities: str | None = None
    bedroom_count: int | None = None
    bathroom_count: int | None = None
    # Per-unit override; None means "this unit hasn't set one" — the
    # website falls back to its own existing default copy / the business-
    # level house_rules (PublicBusinessInfoOut) respectively.
    house_rules: str | None = None
    checkin_time: str | None = None
    checkout_time: str | None = None


class PublicUnitPhotoOut(BaseModel):
    id: uuid.UUID
    caption: str | None
    url: str


class PublicBusinessInfoOut(BaseModel):
    """Business contact fields safe to publish on the public website —
    deliberately excludes GST/UDYAM/bank/UPI and any other internal-only
    BusinessSettings field. Single source of truth for the website's
    footer/contact/location pages, replacing what used to be separately
    hardcoded/env-var content there."""

    company_name: str
    address: str
    contact_phone: str
    contact_email: str
    whatsapp_business_number: str | None = None
    house_rules: str | None = None


class PublicAvailabilityOut(BaseModel):
    unit_id: uuid.UUID
    check_in: date
    check_out: date
    available: bool
    nights: int
    estimated_total: float


class PromoValidateRequest(BaseModel):
    code: str
    subtotal: float = Field(gt=0)


class PromoValidateResponse(BaseModel):
    valid: bool
    discount_amount: float
    reason: str | None = None


# --- Quote ---

class DayRateOut(BaseModel):
    the_date: date
    rate: float
    source: str
    seasonal_name: str | None = None


class PublicQuoteOut(BaseModel):
    unit_id: uuid.UUID
    unit_name: str
    unit_type: str
    check_in: date
    check_out: date
    nights: int
    daily_breakdown: list[DayRateOut]
    subtotal_before_discount: float
    long_stay_discount_percent: float
    long_stay_discount_amount: float
    promo_discount_amount: float
    subtotal_after_discount: float
    gst_rate_percent: float
    cgst: float
    sgst: float
    igst: float
    total: float
    advance_amount: float
    currency: str
    available: bool


# --- Hold creation ---

class CreateHoldRequest(BaseModel):
    unit_id: uuid.UUID
    check_in: date
    check_out: date
    # WEBSITE-04A: this is an anonymous, internet-facing endpoint (unlike
    # the admin-side GuestCreate schema, which is behind auth) — bound the
    # input size so an anonymous submission can't write an arbitrarily
    # large name/phone into a real guest record. Limits match the
    # BookingHold.guest_name/guest_phone column sizes (models.py) exactly,
    # so an oversized submission is rejected with a clean 422 here rather
    # than a raw DB truncation error.
    guest_name: str = Field(min_length=1, max_length=150)
    guest_phone: str = Field(min_length=6, max_length=20)
    # Required (Correction 5): the public website's own guest-details step
    # now requires a valid email before checkout can proceed — the booking
    # confirmation is sent here, so a hold with no way to reach the guest
    # by email is no longer a valid submission. EmailStr already rejects a
    # malformed address; required (no `| None`) rejects a missing one.
    guest_email: EmailStr
    # GST place-of-supply: optional 2-letter Indian state/UT GST code. Not
    # collected at quote time (state isn't known yet then) — only here, at
    # hold creation. Unset -> existing "unknown -> IGST" behavior, unchanged.
    guest_state: str | None = Field(default=None, min_length=2, max_length=2)
    promo_code: str | None = None
    # Total guest count from the website's own guest selector — a single
    # number (the site has no adult/child distinction anywhere), copied to
    # Booking.adults on conversion. Same default/bound as the admin-side
    # BookingCreate.adults for consistency.
    adults: int = Field(default=1, ge=1)
    # Free-text billing address, combined by the website into one
    # formatted string (Address Line 1/2, city, state, country, PIN) —
    # mirrors Guest.address's own single-field shape, so no new structured
    # columns are needed. Applied to Guest.address on conversion.
    guest_address: str | None = Field(default=None, max_length=500)
    # Optional GSTIN, applied to Guest.gst_number on conversion — already
    # rendered on invoices via the existing guest_gst_number parameter.
    guest_gstin: str | None = Field(default=None, max_length=20)


class QuoteSummaryOut(BaseModel):
    nights: int
    subtotal: float
    discount_amount: float
    total: float


class HoldOut(BaseModel):
    hold_id: uuid.UUID
    razorpay_order_id: str | None   # null only if Razorpay not configured (501 path)
    razorpay_key_id: str | None     # null when not configured; client needs this to initialise the Razorpay checkout SDK
    amount: float
    currency: str
    expires_at: datetime
    quote_summary: QuoteSummaryOut


# --- Payment verification ---

class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str


# Reused as the response for verify-payment (same shape as the old PublicBookingConfirmation)
class PublicBookingConfirmation(BaseModel):
    booking_id: uuid.UUID
    guest_id: uuid.UUID
    check_in: date
    check_out: date
    nights: int
    subtotal: float
    discount_amount: float
    total: float
    invoice_id: uuid.UUID | None
    invoice_no: str | None
    confirmation_emailed: bool | None
    # None here means "nothing to send to" (guest has no phone on file),
    # matching confirmation_emailed's own None-vs-bool convention.
    confirmation_sms_sent: bool | None
    # Notification-audit addition: real automatic WhatsApp send attempt via
    # send_whatsapp() (Meta WhatsApp Business Cloud API) — see
    # notifications/service.py::trigger_booking_confirmation_whatsapp. No
    # provider is configured anywhere in this project as of this change, so
    # this currently reads False whenever the guest has a phone on file
    # (an honest "attempted, not configured" outcome) and None only when
    # there's no phone at all — it is NEVER fabricated as True.
    confirmation_whatsapp_sent: bool | None
