import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class PublicUnitOut(BaseModel):
    id: uuid.UUID
    name: str
    base_rate: float
    max_occupancy: int
    property_name: str


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
    guest_name: str
    guest_phone: str
    guest_email: EmailStr | None = None
    promo_code: str | None = None


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
