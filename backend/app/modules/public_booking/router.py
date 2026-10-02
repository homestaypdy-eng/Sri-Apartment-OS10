"""
Public-facing endpoints for the direct booking widget.

Flow:
  GET  /api/v1/public/quote                          → server-authoritative price quote
  POST /api/v1/public/holds                          → create hold + Razorpay order
  POST /api/v1/public/holds/{hold_id}/verify-payment → verify signature, convert to booking

No authentication — these are called by anonymous website visitors.
The hold-creation and payment-verification mutations use shared PostgreSQL
rate-limit counters; business-level booking and payment checks remain separate.
"""
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.bookings.models import Booking, Guest
from app.modules.notifications.models import NotificationLog
from app.modules.notifications.service import dispatch_booking_notifications
from app.modules.payment_gateway.models import GatewayOrder
from app.modules.payment_gateway.service import (
    GatewayNotConfiguredError,
    create_order,
    verify_provider_payment,
    verify_payment_signature,
)
from app.core.config import settings
from app.modules.payments.models import Invoice, Payment
from app.modules.payments.service import calculate_gst, generate_invoice_pdf, next_invoice_number
from app.modules.properties.models import LongStayDiscountTier, Property, SeasonalRate, Unit, UnitPhoto
from app.modules.settings.router import get_or_create_bank_settings, get_or_create_settings
from app.modules.properties.pricing import calculate_price
from app.shared.availability import lock_unit_for_reservation, unit_is_available, units_availability
from app.shared.occupancy import enforce_unit_max_occupancy
from app.modules.public_booking.models import BookingHold, PromoCode
from app.modules.public_booking.schemas import (
    CreateHoldRequest,
    HoldOut,
    PromoValidateRequest,
    PromoValidateResponse,
    PublicAvailabilityOut,
    PublicBookingConfirmation,
    PublicBusinessInfoOut,
    PublicQuoteOut,
    PublicUnitOut,
    PublicUnitPhotoOut,
    QuoteSummaryOut,
    VerifyPaymentRequest,
)
from app.shared.rbac import require_role
from app.shared.database_rate_limit import public_rate_limit

router = APIRouter(prefix="/api/v1/public", tags=["public-booking"])
logger = logging.getLogger("sri_apartment_os")

promo_admin_router = APIRouter(prefix="/api/v1/promo-codes", tags=["public-booking"])

# How long a hold reserves a unit while the guest completes payment.
HOLD_TTL_MINUTES = 15


# ---------------------------------------------------------------------------
# Promo code admin endpoints (unchanged)
# ---------------------------------------------------------------------------

class PromoCodeCreate(BaseModel):
    code: str
    discount_type: str
    # gt=0 matches the same "a discount can't be zero/negative" rule this
    # codebase already enforces for the analogous LongStayDiscountTier
    # (properties/schemas.py) — a negative value would silently INCREASE
    # the guest's total (compute_discount subtracts it from the subtotal),
    # and this field was previously unvalidated at both the app and DB
    # layer. The percent<=100 bound (only meaningful for discount_type=
    # "percent") is enforced below via a field validator, since it depends
    # on discount_type.
    value: float = Field(gt=0)
    valid_from: date
    valid_to: date
    max_uses: int | None = Field(default=None, gt=0)

    @field_validator("value")
    @classmethod
    def percent_value_within_range(cls, v, info):
        if info.data.get("discount_type") == "percent" and v > 100:
            raise ValueError("value must be at most 100 when discount_type is 'percent'")
        return v

    @field_validator("valid_to")
    @classmethod
    def valid_to_after_valid_from(cls, v, info):
        start = info.data.get("valid_from")
        if start and v < start:
            raise ValueError("valid_to must be on or after valid_from")
        return v


class PromoCodeOut(BaseModel):
    id: uuid.UUID
    code: str
    discount_type: str
    value: float
    valid_from: date
    valid_to: date
    max_uses: int | None
    used_count: int
    active: bool

    class Config:
        from_attributes = True


@promo_admin_router.post("", response_model=PromoCodeOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_promo_code(payload: PromoCodeCreate, db: Session = Depends(get_db)):
    if payload.discount_type not in ("percent", "flat"):
        raise HTTPException(400, "discount_type must be 'percent' or 'flat'")
    if db.scalar(select(PromoCode).where(PromoCode.code == payload.code.upper())):
        raise HTTPException(409, f"Promo code '{payload.code}' already exists")
    promo = PromoCode(**{**payload.model_dump(), "code": payload.code.upper()})
    db.add(promo)
    db.commit()
    db.refresh(promo)
    return promo


@promo_admin_router.get("", response_model=list[PromoCodeOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_promo_codes(db: Session = Depends(get_db)):
    return db.scalars(select(PromoCode)).all()


class PromoCodeUpdate(BaseModel):
    """Partial update — covers edit, activate/deactivate (`active`), and
    changing expiry (`valid_to`) through one endpoint. `code` itself is
    immutable after creation (create a new code instead of renaming one
    that may already be in use/redeemed)."""

    discount_type: str | None = None
    value: float | None = Field(default=None, gt=0)
    valid_from: date | None = None
    valid_to: date | None = None
    max_uses: int | None = Field(default=None, gt=0)
    active: bool | None = None


@promo_admin_router.patch("/{promo_id}", response_model=PromoCodeOut, dependencies=[Depends(require_role("admin"))])
def update_promo_code(promo_id: uuid.UUID, payload: PromoCodeUpdate, db: Session = Depends(get_db)):
    promo = db.get(PromoCode, promo_id)
    if promo is None:
        raise HTTPException(404, "Promo code not found")
    changes = payload.model_dump(exclude_unset=True)
    if "discount_type" in changes and changes["discount_type"] not in ("percent", "flat"):
        raise HTTPException(400, "discount_type must be 'percent' or 'flat'")
    # Validated against the EFFECTIVE post-merge state, not just the
    # fields actually sent — a request that only changes `value` on an
    # existing discount_type="percent" promo must still be bounded, and a
    # request that only changes `discount_type` to "percent" must still
    # respect the existing value.
    effective_discount_type = changes.get("discount_type", promo.discount_type)
    effective_value = changes.get("value", float(promo.value))
    if effective_discount_type == "percent" and effective_value > 100:
        raise HTTPException(400, "value must be at most 100 when discount_type is 'percent'")
    effective_valid_from = changes.get("valid_from", promo.valid_from)
    effective_valid_to = changes.get("valid_to", promo.valid_to)
    if effective_valid_to < effective_valid_from:
        raise HTTPException(400, "valid_to must be on or after valid_from")
    for field, value in changes.items():
        setattr(promo, field, value)
    db.commit()
    db.refresh(promo)
    return promo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_full_price_quote(db: Session, unit: Unit, check_in: date, check_out: date):
    """Run the full pricing engine for a unit+dates, same as admin price-quote endpoint."""
    seasonal = [
        {
            "id": s.id,
            "name": s.name,
            "start_date": s.start_date,
            "end_date": s.end_date,
            "rate_per_night": float(s.rate_per_night),
            "created_at": s.created_at,
        }
        for s in db.scalars(select(SeasonalRate).where(SeasonalRate.unit_id == unit.id)).all()
    ]
    tiers = [
        {"min_nights": t.min_nights, "discount_percent": float(t.discount_percent)}
        for t in db.scalars(select(LongStayDiscountTier).where(LongStayDiscountTier.unit_id == unit.id)).all()
    ]
    return calculate_price(
        check_in=check_in,
        check_out=check_out,
        base_rate=float(unit.base_rate),
        weekend_rate=float(unit.weekend_rate) if unit.weekend_rate is not None else None,
        seasonal_rates=seasonal,
        discount_tiers=tiers,
        long_stay_min_nights=unit.long_stay_min_nights,
        long_stay_rate=float(unit.long_stay_rate),
    )


def _check_booking_conflict(db: Session, unit_id: uuid.UUID, check_in: date, check_out: date) -> bool:
    """Returns True if the unit is unavailable for these dates — an
    overlapping confirmed/checked_in booking OR an overlapping active
    hold (another guest already mid-payment). See app/shared/availability.py
    for why both tables have to be checked, not just bookings."""
    return not unit_is_available(db, unit_id, check_in, check_out)


def _convert_hold_to_booking(db: Session, hold: BookingHold) -> tuple[Booking, Invoice]:
    """
    Core conversion logic — shared by the verify-payment endpoint and the
    webhook backup path. Creates Guest + Booking + Invoice + Payment
    atomically. Raises HTTPException on conflict (409) so callers can
    handle appropriately. The hold must already be in 'paid' status before
    calling this.

    Deliberately does ONLY database work — no notification sending. It used
    to also call every trigger_* function in-line before returning, which
    meant the browser waited on up to 4 real, sequential SMTP sends before
    ever getting an HTTP response; in this dev environment that was
    directly observed blocking for the full 15s smtplib timeout on one
    send, well past the website's 12s client-side timeout, causing the
    guest's browser to show a timeout even though the booking had already
    committed successfully (see dispatch_booking_notifications' docstring
    for the full root-cause writeup). Callers are responsible for
    scheduling app.modules.notifications.service.dispatch_booking_notifications
    as a background task once they've committed.

    Because this function is only ever invoked once per BookingHold (the
    browser path and the webhook backup path both gate on hold.status
    before calling it, and a converted hold is never re-converted — see
    verify_payment()'s idempotent replay branch and the webhook's own
    hold.status == "pending_payment" check), callers scheduling
    dispatch_booking_notifications after calling this also only ever do so
    once per BookingHold — no separate duplicate-call protection needed
    here (dispatch_booking_notifications' own per-recipient idempotency
    guard is defense-in-depth on top of that, same as before).

    Phase 5A: unit.status is checked at hold CREATION time (create_hold)
    but nothing previously re-checked it here at CONVERSION time — so an
    admin marking a unit "maintenance"/"blocked" while a guest's payment
    was already in flight (anywhere within the hold's TTL) would still
    let that payment convert into a confirmed Booking. Re-checked
    immediately before creating the Booking below, mirroring the exact
    rollback-then-409 pattern the pre-existing overlap-conflict branch
    (the no_overlapping_bookings IntegrityError handler further down)
    already uses for the equivalent "payment succeeded but conversion
    can't proceed" situation — same non-partial-state guarantee, same
    caller-facing contract, nothing new invented.
    """
    unit = db.get(Unit, hold.unit_id)
    property_ = db.get(Property, unit.property_id)

    if unit.status != "available":
        db.rollback()
        raise HTTPException(
            409,
            f"This unit is no longer available for booking (status: {unit.status}). "
            "Your payment was received; please contact us for a refund.",
        )

    # Recheck at payment conversion too: the hold may have been created by an
    # older client or before a unit's configured capacity changed.
    enforce_unit_max_occupancy(unit, hold.adults, 0)

    guest = db.scalar(select(Guest).where(Guest.phone == hold.guest_phone))
    if guest is None:
        guest = Guest(
            name=hold.guest_name, phone=hold.guest_phone, email=hold.guest_email, state_code=hold.guest_state,
            address=hold.guest_address, gst_number=hold.guest_gstin,
        )
        db.add(guest)
        db.flush()
    else:
        # Only ever set from a real submitted value — never overwrite an
        # existing guest's known (or unknown) state/address/GSTIN with NULL
        # just because this particular hold didn't collect one.
        if hold.guest_state is not None:
            guest.state_code = hold.guest_state
        if hold.guest_address:
            guest.address = hold.guest_address
        if hold.guest_gstin:
            guest.gst_number = hold.guest_gstin

    effective_rate = (float(hold.subtotal) - float(hold.discount_amount)) / hold.nights if hold.nights else 0

    booking = Booking(
        unit_id=hold.unit_id,
        guest_id=guest.id,
        check_in=hold.check_in,
        check_out=hold.check_out,
        status="confirmed",
        source="website",
        rate_applied=round(effective_rate, 2),
        promo_code_used=hold.promo_code_used,
        adults=hold.adults,
    )
    db.add(booking)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        if "no_overlapping_bookings" in str(e.orig):
            raise HTTPException(409, "A confirmed booking already exists for these dates — payment will be refunded")
        raise

    gst_data = {
        "cgst": float(hold.cgst),
        "sgst": float(hold.sgst),
        "igst": float(hold.igst),
        "total": float(hold.total),
        "gst_rate_percent": float(hold.gst_rate_percent),
        "itc_available": float(hold.gst_rate_percent) >= 18.0,
    }
    invoice_no = next_invoice_number(db)
    invoice = Invoice(
        booking_id=booking.id,
        invoice_no=invoice_no,
        subtotal=round(float(hold.subtotal) - float(hold.discount_amount), 2),
        cgst=gst_data["cgst"],
        sgst=gst_data["sgst"],
        igst=gst_data["igst"],
        total=gst_data["total"],
        gst_rate_percent=gst_data["gst_rate_percent"],
        itc_available=gst_data["itc_available"],
    )
    db.add(invoice)
    db.flush()

    # P0-1: Payment record — without this, payments/booking reconciliation reports
    # show ₹0 collected for all web bookings despite money actually having moved.
    payment = Payment(
        booking_id=booking.id,
        amount=float(hold.total),
        mode="razorpay",
        reference_no=hold.razorpay_payment_id,
        status="completed",
    )
    db.add(payment)
    db.flush()

    # Same bank/UPI + signatory source as the OS10-generated invoice
    # (payments/router.py) and the quotation PDF — BankSettings/
    # BusinessSettings, not a second/duplicated config surface.
    # Keep lazy singleton creation inside this conversion transaction. The
    # settings helpers normally commit for standalone GET endpoints; that
    # would otherwise commit a partially converted booking if rows were
    # missing during the first public payment.
    business_settings = get_or_create_settings(db, commit_if_created=False)
    bank_settings = get_or_create_bank_settings(db, commit_if_created=False)
    bank_details = {
        "bank_name": bank_settings.bank_name,
        "bank_account_name": bank_settings.bank_account_name,
        "bank_account_number": bank_settings.bank_account_number,
        "bank_ifsc": bank_settings.bank_ifsc,
        "bank_branch": bank_settings.bank_branch,
        "upi_id": bank_settings.upi_id,
        "signatory_name": business_settings.signatory_name,
        "signatory_title": business_settings.signatory_title,
    }

    pdf_path = generate_invoice_pdf(
        invoice_no=invoice_no,
        property_name=property_.name,
        property_gst_number=property_.gst_number,
        guest_name=guest.name,
        guest_gst_number=guest.gst_number,
        unit_name=unit.name,
        check_in=booking.check_in,
        check_out=booking.check_out,
        subtotal=invoice.subtotal,
        cgst=invoice.cgst,
        sgst=invoice.sgst,
        igst=invoice.igst,
        total=invoice.total,
        hsn_code=invoice.hsn_code,
        gst_rate_percent=gst_data["gst_rate_percent"],
        itc_available=gst_data["itc_available"],
        bank_details=bank_details,
        qr_path=business_settings.qr_code_path,
        # Paid in full via the payment gateway before a booking is ever
        # created on this path — reflect that instead of the "0 received"
        # default, which would misleadingly show a fully-paid web booking
        # as unpaid on its own invoice.
        payment_received=float(payment.amount),
        balance_due=round(float(invoice.total) - float(payment.amount), 2),
        guest_phone=guest.phone, guest_email=guest.email, guest_address=guest.address,
        num_guests=booking.adults + booking.children, rate_per_night=float(booking.rate_applied),
        payment_mode=payment.mode,
    )
    invoice.pdf_path = pdf_path

    hold.booking_id = booking.id
    hold.status = "converted"
    hold.updated_at = datetime.now(timezone.utc)

    return booking, invoice


# ---------------------------------------------------------------------------
# Public unit listing
# ---------------------------------------------------------------------------

@router.get("/units", response_model=list[PublicUnitOut])
def list_public_units(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Unit, Property)
        .join(Property, Unit.property_id == Property.id)
        # is_active (soft-deactivation) and status (available/maintenance/
        # blocked) are two independent flags — a unit must satisfy both to
        # be a genuinely bookable public listing. Previously only `status`
        # was checked, so a deactivated-but-still-"available"-status unit
        # stayed publicly visible (Phase 5 audit finding).
        .where(Unit.status == "available", Unit.is_active.is_(True))
    ).all()
    return [
        PublicUnitOut(
            id=u.id, name=u.name, unit_type=u.unit_type, base_rate=float(u.base_rate),
            max_occupancy=u.max_occupancy, property_name=p.name, property_type=p.property_type,
            description=u.description, amenities=u.amenities,
            bedroom_count=u.bedroom_count, bathroom_count=u.bathroom_count,
            house_rules=u.house_rules, checkin_time=u.checkin_time, checkout_time=u.checkout_time,
        )
        for u, p in rows
    ]


# ---------------------------------------------------------------------------
# Public business contact info — read-only, no auth. Single source of truth
# for the website's footer/contact/location pages, which previously kept
# their own separate hardcoded/env-var copy of this same information.
# Deliberately excludes GST/UDYAM/bank/UPI (admin/staff-only elsewhere).
# ---------------------------------------------------------------------------

@router.get("/business-info", response_model=PublicBusinessInfoOut)
def get_public_business_info(db: Session = Depends(get_db)):
    s = get_or_create_settings(db)
    return PublicBusinessInfoOut(
        company_name=s.company_name,
        address=s.address,
        contact_phone=s.contact_phone,
        contact_email=s.contact_email,
        whatsapp_business_number=s.whatsapp_business_number,
        house_rules=s.house_rules,
    )


# ---------------------------------------------------------------------------
# Public unit photos — read-only, no auth. Reuses the existing UnitPhoto
# rows/files created via the admin upload endpoint (properties/router.py);
# no separate storage or duplicate upload path.
# ---------------------------------------------------------------------------

@router.get("/units/{unit_id}/photos", response_model=list[PublicUnitPhotoOut])
def list_public_unit_photos(unit_id: uuid.UUID, db: Session = Depends(get_db)):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")

    photos = db.scalars(
        select(UnitPhoto).where(UnitPhoto.unit_id == unit_id).order_by(UnitPhoto.is_primary.desc(), UnitPhoto.sort_order.asc(), UnitPhoto.uploaded_at.asc())
    ).all()
    return [
        PublicUnitPhotoOut(id=p.id, caption=p.caption, url=f"/api/v1/public/units/{unit_id}/photos/{p.id}/image")
        for p in photos
    ]


@router.get("/units/{unit_id}/photos/{photo_id}/image")
def get_public_unit_photo_image(unit_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db)):
    record = db.get(UnitPhoto, photo_id)
    if record is None or record.unit_id != unit_id:
        raise HTTPException(404, "Photo not found")
    if not os.path.exists(record.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(record.file_path, media_type=record.content_type, filename=record.filename)


# ---------------------------------------------------------------------------
# Availability check (unit status now authoritative)
# ---------------------------------------------------------------------------

@router.get("/availability", response_model=PublicAvailabilityOut)
def check_public_availability(
    unit_id: uuid.UUID = Query(...),
    check_in: date = Query(...),
    check_out: date = Query(...),
    db: Session = Depends(get_db),
):
    if check_out <= check_in:
        raise HTTPException(400, "check_out must be after check_in")
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")

    nights = (check_out - check_in).days

    # Unit must be operationally available — maintenance/blocked units are not bookable
    if unit.status != "available":
        return PublicAvailabilityOut(
            unit_id=unit_id, check_in=check_in, check_out=check_out,
            available=False, nights=nights, estimated_total=0.0,
        )

    available = not _check_booking_conflict(db, unit_id, check_in, check_out)
    # Same pricing engine as /public/quote and create_hold (via
    # _get_full_price_quote) — this used to be a naive base_rate*nights
    # estimate that silently disagreed with the real quote whenever a
    # weekend rate, seasonal rate, or long-stay discount applied to these
    # dates. subtotal_after_discount (pre-promo, promo isn't known at the
    # search-results stage) is exactly what /public/quote shows as
    # subtotal_after_discount before a promo code is applied.
    quote = _get_full_price_quote(db, unit, check_in, check_out)
    return PublicAvailabilityOut(
        unit_id=unit_id, check_in=check_in, check_out=check_out,
        available=available, nights=nights,
        estimated_total=quote.subtotal_after_discount,
    )


# ---------------------------------------------------------------------------
# Promo code validation
# ---------------------------------------------------------------------------

@router.post("/promo/validate", response_model=PromoValidateResponse)
def validate_promo(payload: PromoValidateRequest, db: Session = Depends(get_db)):
    promo = db.scalar(select(PromoCode).where(PromoCode.code == payload.code.upper()))
    if promo is None:
        return PromoValidateResponse(valid=False, discount_amount=0, reason="Promo code not found")
    if not promo.is_valid_today():
        return PromoValidateResponse(valid=False, discount_amount=0, reason="Promo code is expired, inactive, or fully used")
    return PromoValidateResponse(valid=True, discount_amount=promo.compute_discount(payload.subtotal))


# ---------------------------------------------------------------------------
# Server-authoritative price quote
# ---------------------------------------------------------------------------

@router.get("/quote", response_model=PublicQuoteOut)
def public_quote(
    unit_id: uuid.UUID = Query(...),
    check_in: date = Query(...),
    check_out: date = Query(...),
    promo_code: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Returns a complete, server-computed price breakdown for the given unit and
    dates. Uses the full pricing engine (seasonal rates, weekend rates,
    long-stay discounts). No client-supplied price is accepted.
    """
    if check_out <= check_in:
        raise HTTPException(400, "check_out must be after check_in")
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    property_ = db.get(Property, unit.property_id)

    unit_available = unit.status == "available" and not _check_booking_conflict(db, unit_id, check_in, check_out)

    quote = _get_full_price_quote(db, unit, check_in, check_out)

    promo_discount = 0.0
    if promo_code:
        promo = db.scalar(select(PromoCode).where(PromoCode.code == promo_code.upper()))
        if promo is None or not promo.is_valid_today():
            raise HTTPException(400, "Invalid or expired promo code")
        promo_discount = promo.compute_discount(quote.subtotal_after_discount)

    subtotal_after_promo = round(quote.subtotal_after_discount - promo_discount, 2)
    effective_rate = subtotal_after_promo / quote.nights if quote.nights else 0.0

    gst = calculate_gst(
        subtotal=subtotal_after_promo,
        tariff_per_night=effective_rate,
        guest_state=None,           # unknown at quote time → IGST (conservative)
        property_state=property_.state_code,
    )

    return PublicQuoteOut(
        unit_id=unit_id,
        unit_name=unit.name,
        unit_type=unit.unit_type,
        check_in=check_in,
        check_out=check_out,
        nights=quote.nights,
        daily_breakdown=[
            {"the_date": d.the_date, "rate": d.rate, "source": d.source, "seasonal_name": d.seasonal_name}
            for d in quote.daily_breakdown
        ],
        subtotal_before_discount=quote.subtotal_before_discount,
        long_stay_discount_percent=quote.long_stay_discount_percent,
        long_stay_discount_amount=quote.long_stay_discount_amount,
        promo_discount_amount=promo_discount,
        subtotal_after_discount=subtotal_after_promo,
        gst_rate_percent=gst["gst_rate_percent"],
        cgst=gst["cgst"],
        sgst=gst["sgst"],
        igst=gst["igst"],
        total=gst["total"],
        advance_amount=gst["total"],  # advance_amount = total
        currency="INR",
        available=unit_available,
    )


# ---------------------------------------------------------------------------
# Hold creation
# ---------------------------------------------------------------------------

@router.post(
    "/holds",
    response_model=HoldOut,
    status_code=201,
    dependencies=[Depends(public_rate_limit("create_hold", max_requests=10))],
)
def create_hold(payload: CreateHoldRequest, db: Session = Depends(get_db)):
    """
    Reserves a unit for HOLD_TTL_MINUTES and creates a Razorpay order.
    The hold must be converted to a confirmed Booking via verify-payment
    before it expires. No booking exists until payment is verified.
    """
    if payload.check_out <= payload.check_in:
        raise HTTPException(400, "check_out must be after check_in")

    unit = db.get(Unit, payload.unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")

    # Unit status is authoritative — maintenance/blocked units cannot be held
    if unit.status != "available":
        raise HTTPException(409, f"Unit is not available for booking (status: {unit.status})")

    # --- Phase 1: short, locked reservation transaction -------------------
    # Closes the booking-vs-active-hold cross-table race: everything from
    # here through this phase's own db.commit() below happens under a
    # unit-scoped advisory lock, so a concurrent create_booking() (or
    # another create_hold()/verify_payment()) for this same unit cannot
    # interleave its own availability check with this one's reservation
    # write. Deliberately acquired BEFORE the external Razorpay call further
    # down (Phase 2) and released at this phase's commit — the lock must
    # never be held across a slow/unavailable external dependency. See
    # lock_unit_for_reservation()'s own docstring for the full reasoning.
    lock_unit_for_reservation(db, payload.unit_id)
    db.refresh(unit)
    enforce_unit_max_occupancy(unit, payload.adults, 0)

    property_ = db.get(Property, unit.property_id)

    # Check existing confirmed bookings for overlap
    if _check_booking_conflict(db, payload.unit_id, payload.check_in, payload.check_out):
        raise HTTPException(409, "This unit is not available for the selected dates")

    # Server-authoritative pricing — client cannot influence any amount
    quote = _get_full_price_quote(db, unit, payload.check_in, payload.check_out)

    promo_discount = 0.0
    promo_code_str = None
    if payload.promo_code:
        promo = db.scalar(select(PromoCode).where(PromoCode.code == payload.promo_code.upper()))
        if promo is None or not promo.is_valid_today():
            raise HTTPException(400, "Invalid or expired promo code")
        promo_discount = promo.compute_discount(quote.subtotal_after_discount)
        promo.used_count += 1
        promo_code_str = promo.code

    subtotal_after_promo = round(quote.subtotal_after_discount - promo_discount, 2)
    effective_rate = subtotal_after_promo / quote.nights if quote.nights else 0.0

    gst = calculate_gst(
        subtotal=subtotal_after_promo,
        tariff_per_night=effective_rate,
        guest_state=payload.guest_state,
        property_state=property_.state_code,
    )

    total = gst["total"]
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=HOLD_TTL_MINUTES)

    # Create GatewayOrder first so we have its PK for the FK on BookingHold
    gateway_order = GatewayOrder(
        amount=total,
        receipt=f"hold_{uuid.uuid4().hex[:12]}",
        status="created",
    )
    db.add(gateway_order)
    db.flush()  # get gateway_order.id

    # P0-4: Expire ghost holds for this unit whose TTL has elapsed but were
    # never explicitly cleaned up (e.g. browser closed mid-payment). Without
    # this, they block new holds via the EXCLUDE constraint indefinitely.
    db.execute(
        text(
            "UPDATE booking_holds SET status='expired', updated_at=now() "
            "WHERE unit_id=:uid AND status='pending_payment' AND expires_at < now()"
        ),
        {"uid": str(payload.unit_id)},
    )

    # Create the hold — DB EXCLUDE fires here on simultaneous duplicate
    hold = BookingHold(
        unit_id=payload.unit_id,
        guest_name=payload.guest_name,
        guest_phone=payload.guest_phone,
        guest_email=payload.guest_email,
        guest_state=payload.guest_state,
        adults=payload.adults,
        guest_address=payload.guest_address,
        guest_gstin=payload.guest_gstin,
        check_in=payload.check_in,
        check_out=payload.check_out,
        nights=quote.nights,
        subtotal=quote.subtotal_before_discount,
        discount_amount=round(quote.long_stay_discount_amount + promo_discount, 2),
        promo_code_used=promo_code_str,
        cgst=gst["cgst"],
        sgst=gst["sgst"],
        igst=gst["igst"],
        gst_rate_percent=gst["gst_rate_percent"],
        total=total,
        advance_amount=total,
        status="pending_payment",
        gateway_order_id=gateway_order.id,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    db.add(hold)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        if "no_overlapping_holds" in str(e.orig):
            raise HTTPException(409, "This unit is no longer available for the selected dates")
        raise

    # Link GatewayOrder back to the hold
    gateway_order.hold_id = hold.id

    # Phase 1 ends here: the unit is now safely, atomically reserved — this
    # commit is what makes the hold visible (and correctly blocking) to
    # every other transaction, and releases the advisory lock acquired
    # above. From this point on, no lock is needed: a real, committed row
    # is enough for unit_is_available() and the EXCLUDE constraints to do
    # their job, exactly as they already do for same-table races.
    db.commit()
    db.refresh(hold)
    db.refresh(gateway_order)

    # --- Phase 2: unlocked — the external Razorpay call ---------------
    # Deliberately outside the lock and outside Phase 1's transaction: a
    # slow or unavailable Razorpay must never block another guest's
    # reservation attempt for this same unit. Failure here cancels the
    # already-committed hold explicitly (below) rather than relying on a
    # rollback, since there is nothing left in this transaction to roll
    # back — Phase 1 already committed.
    try:
        result = create_order(total, gateway_order.receipt, notes={"hold_id": str(hold.id)})
        gateway_order.gateway_order_id = result.get("id")
        gateway_order.status = "created"
        db.commit()
    except GatewayNotConfiguredError as e:
        hold.status = "cancelled"
        gateway_order.status = "failed"
        db.commit()
        raise HTTPException(501, str(e))
    except Exception as e:
        hold.status = "cancelled"
        gateway_order.status = "failed"
        db.commit()
        raise HTTPException(502, f"Razorpay order creation failed: {e}")

    db.refresh(hold)
    db.refresh(gateway_order)

    return HoldOut(
        hold_id=hold.id,
        razorpay_order_id=gateway_order.gateway_order_id,
        razorpay_key_id=settings.razorpay_key_id or None,
        amount=float(hold.advance_amount),
        currency=hold.currency,
        expires_at=hold.expires_at,
        quote_summary=QuoteSummaryOut(
            nights=hold.nights,
            subtotal=float(hold.subtotal),
            discount_amount=float(hold.discount_amount),
            total=float(hold.total),
        ),
    )


# ---------------------------------------------------------------------------
# Payment verification → booking conversion
# ---------------------------------------------------------------------------

@router.post(
    "/holds/{hold_id}/verify-payment",
    response_model=PublicBookingConfirmation,
    status_code=201,
    dependencies=[Depends(public_rate_limit("verify_payment", max_requests=30))],
)
def verify_payment(hold_id: uuid.UUID, payload: VerifyPaymentRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Verifies the Razorpay payment signature server-side and, on success,
    converts the BookingHold into a confirmed Booking with GST invoice.
    A browser callback alone is never sufficient — this endpoint is the
    authoritative gate.

    Notification sending is scheduled as a background task (see
    dispatch_booking_notifications) rather than awaited here, so
    confirmation_emailed/confirmation_sms_sent/confirmation_whatsapp_sent
    are always None on this fast path — the real outcome becomes available
    on a replay call (below) once dispatch has completed.
    """
    # P1-2: SELECT FOR UPDATE prevents two concurrent verify-payment calls from
    # both reading pending_payment and both attempting a conversion.
    hold = db.scalar(select(BookingHold).where(BookingHold.id == hold_id).with_for_update())
    if hold is None:
        raise HTTPException(404, "Booking hold not found")

    if hold.status == "converted":
        # P1-1: Idempotent — a network retry after a successful payment must not
        # return 409. Return the same confirmation as the original call.
        booking = db.get(Booking, hold.booking_id) if hold.booking_id else None
        if booking is None:
            raise HTTPException(409, "Hold is converted but the linked booking could not be found")
        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking.id))
        # A refresh/retry of this page must show what actually happened on
        # the original call, not a blank/unknown placeholder — look up the
        # real outcome from the notification log instead of guessing.
        emailed = db.scalar(
            select(NotificationLog.success).where(
                NotificationLog.booking_id == booking.id,
                NotificationLog.event_type == "booking_confirmation",
                NotificationLog.channel == "email",
            ).order_by(NotificationLog.created_at.desc()).limit(1)
        )
        sms_sent = db.scalar(
            select(NotificationLog.success).where(
                NotificationLog.booking_id == booking.id,
                NotificationLog.event_type == "booking_confirmation",
                NotificationLog.channel == "sms",
            ).order_by(NotificationLog.created_at.desc()).limit(1)
        )
        whatsapp_sent = db.scalar(
            select(NotificationLog.success).where(
                NotificationLog.booking_id == booking.id,
                NotificationLog.event_type == "booking_confirmation",
                NotificationLog.channel == "whatsapp",
            ).order_by(NotificationLog.created_at.desc()).limit(1)
        )
        return PublicBookingConfirmation(
            booking_id=booking.id,
            guest_id=booking.guest_id,
            check_in=booking.check_in,
            check_out=booking.check_out,
            nights=hold.nights,
            subtotal=float(hold.subtotal),
            discount_amount=float(hold.discount_amount),
            total=float(hold.total),
            invoice_id=invoice.id if invoice else None,
            invoice_no=invoice.invoice_no if invoice else None,
            confirmation_emailed=emailed,
            confirmation_sms_sent=sms_sent,
            confirmation_whatsapp_sent=whatsapp_sent,
        )
    if hold.status in ("expired", "cancelled"):
        raise HTTPException(409, f"This hold is {hold.status} and cannot be confirmed")
    if hold.status != "pending_payment":
        raise HTTPException(409, f"Unexpected hold status: {hold.status}")

    # TTL check — lazy expiry
    if datetime.now(timezone.utc) > hold.expires_at:
        hold.status = "expired"
        hold.updated_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(410, "This booking hold has expired. Please start a new booking.")

    # Razorpay order ID must match what we issued
    gateway_order = db.get(GatewayOrder, hold.gateway_order_id) if hold.gateway_order_id else None
    stored_razorpay_order_id = gateway_order.gateway_order_id if gateway_order else None

    if stored_razorpay_order_id != payload.razorpay_order_id:
        raise HTTPException(400, "razorpay_order_id does not match the order created for this hold")

    # Cryptographic signature verification — the authoritative payment confirmation
    try:
        valid = verify_payment_signature(
            razorpay_order_id=payload.razorpay_order_id,
            razorpay_payment_id=payload.razorpay_payment_id,
            razorpay_signature=payload.razorpay_signature,
        )
    except GatewayNotConfiguredError as e:
        raise HTTPException(501, str(e))

    if not valid:
        raise HTTPException(400, "Invalid Razorpay payment signature — payment could not be verified")

    # The Checkout HMAC proves the submitted order/payment IDs are paired,
    # but only Razorpay's canonical payment resource establishes captured
    # status, amount, and currency. Do not mutate local payment state if the
    # provider is unavailable; a later browser retry or signed webhook can
    # safely complete the conversion.
    if (
        gateway_order is None
        or not gateway_order.gateway_order_id
        or gateway_order.hold_id != hold.id
        or round(float(gateway_order.amount), 2) != round(float(hold.advance_amount), 2)
        or gateway_order.currency.upper() != hold.currency.upper()
    ):
        raise HTTPException(409, "The local payment order is unavailable")
    try:
        provider_verified = verify_provider_payment(
            payload.razorpay_payment_id,
            order_id=stored_razorpay_order_id,
            amount_rupees=float(gateway_order.amount),
            currency=gateway_order.currency,
        )
    except GatewayNotConfiguredError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.warning("Razorpay payment lookup failed for hold %s: %s", hold.id, e)
        raise HTTPException(503, "Payment status could not be confirmed with Razorpay; retry safely")
    if not provider_verified:
        raise HTTPException(409, "Razorpay payment is not captured for the expected amount and currency")

    # Provider lookup is external network I/O, so do not hold the unit-scoped
    # advisory lock across it. The hold row remains locked to serialize
    # competing conversions; now acquire the shared reservation lock and
    # keep it through the final expiry check, booking insert, and commit.
    lock_unit_for_reservation(db, hold.unit_id)

    # Provider lookup is network I/O. The hold may cross its deadline while
    # that request is in flight; never convert based only on the earlier TTL
    # check. Record the verified charge against the order for reconciliation,
    # release the expired hold, and avoid claiming inventory retroactively.
    now = datetime.now(timezone.utc)
    if now > hold.expires_at:
        hold.status = "expired"
        hold.razorpay_payment_id = payload.razorpay_payment_id
        hold.updated_at = now
        gateway_order.status = "paid"
        gateway_order.gateway_payment_id = payload.razorpay_payment_id
        db.commit()
        raise HTTPException(409, "Payment was captured after the booking hold expired and requires reconciliation")

    # Payment verified — record it and convert the hold
    hold.status = "paid"
    hold.razorpay_payment_id = payload.razorpay_payment_id
    hold.updated_at = datetime.now(timezone.utc)
    if gateway_order:
        gateway_order.status = "paid"
        gateway_order.gateway_payment_id = payload.razorpay_payment_id

    booking, invoice = _convert_hold_to_booking(db, hold)

    db.commit()
    db.refresh(booking)
    db.refresh(invoice)

    # Scheduled to run AFTER this HTTP response has been sent (FastAPI
    # BackgroundTasks) — see dispatch_booking_notifications' docstring for
    # why sending it inline here was the actual cause of the payment ->
    # booking-confirmation timeout bug.
    background_tasks.add_task(dispatch_booking_notifications, booking.id)

    return PublicBookingConfirmation(
        booking_id=booking.id,
        guest_id=booking.guest_id,
        check_in=booking.check_in,
        check_out=booking.check_out,
        nights=hold.nights,
        subtotal=float(hold.subtotal),
        discount_amount=float(hold.discount_amount),
        total=float(hold.total),
        invoice_id=invoice.id,
        invoice_no=invoice.invoice_no,
        # Not yet known — dispatch just started in the background. A
        # replay of this same call (see the "already converted" branch
        # above) reports the real outcome once it completes.
        confirmation_emailed=None,
        confirmation_sms_sent=None,
        confirmation_whatsapp_sent=None,
    )
