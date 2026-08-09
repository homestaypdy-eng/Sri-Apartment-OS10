"""
Public-facing endpoints for the direct booking widget.

Flow:
  GET  /api/v1/public/quote                          → server-authoritative price quote
  POST /api/v1/public/holds                          → create hold + Razorpay order
  POST /api/v1/public/holds/{hold_id}/verify-payment → verify signature, convert to booking

No authentication — these are called by anonymous website visitors.
Rate limiting is a known gap (see README), same as the rest of this system.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.bookings.models import Booking, Guest
from app.modules.notifications.service import trigger_booking_confirmation, trigger_booking_confirmation_sms
from app.modules.payment_gateway.models import GatewayOrder
from app.modules.payment_gateway.service import (
    GatewayNotConfiguredError,
    create_order,
    verify_payment_signature,
)
from app.core.config import settings
from app.modules.payments.models import Invoice, Payment
from app.modules.payments.service import calculate_gst, generate_invoice_pdf, next_invoice_number
from app.modules.properties.models import LongStayDiscountTier, Property, SeasonalRate, Unit
from app.modules.properties.pricing import calculate_price
from app.modules.public_booking.models import BookingHold, PromoCode
from app.modules.public_booking.schemas import (
    CreateHoldRequest,
    HoldOut,
    PromoValidateRequest,
    PromoValidateResponse,
    PublicAvailabilityOut,
    PublicBookingConfirmation,
    PublicQuoteOut,
    PublicUnitOut,
    QuoteSummaryOut,
    VerifyPaymentRequest,
)
from app.shared.rbac import require_role

router = APIRouter(prefix="/api/v1/public", tags=["public-booking"])

promo_admin_router = APIRouter(prefix="/api/v1/promo-codes", tags=["public-booking"])

# How long a hold reserves a unit while the guest completes payment.
HOLD_TTL_MINUTES = 15


# ---------------------------------------------------------------------------
# Promo code admin endpoints (unchanged)
# ---------------------------------------------------------------------------

class PromoCodeCreate(BaseModel):
    code: str
    discount_type: str
    value: float
    valid_from: date
    valid_to: date
    max_uses: int | None = None


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
    )


def _check_booking_conflict(db: Session, unit_id: uuid.UUID, check_in: date, check_out: date) -> bool:
    """Returns True if a confirmed/checked_in booking already overlaps these dates."""
    conflict = db.execute(
        text(
            """
            SELECT id FROM bookings
            WHERE unit_id = :unit_id AND status IN ('confirmed', 'checked_in')
              AND daterange(check_in, check_out, '[)') && daterange(:ci, :co, '[)')
            LIMIT 1
            """
        ),
        {"unit_id": str(unit_id), "ci": check_in, "co": check_out},
    ).first()
    return conflict is not None


def _convert_hold_to_booking(db: Session, hold: BookingHold) -> tuple[Booking, Invoice, bool]:
    """
    Core conversion logic — shared by the verify-payment endpoint and the
    webhook backup path. Creates Guest + Booking + Invoice atomically.
    Raises HTTPException on conflict (409) so callers can handle appropriately.
    The hold must already be in 'paid' status before calling this.
    """
    unit = db.get(Unit, hold.unit_id)
    property_ = db.get(Property, unit.property_id)

    guest = db.scalar(select(Guest).where(Guest.phone == hold.guest_phone))
    if guest is None:
        guest = Guest(name=hold.guest_name, phone=hold.guest_phone, email=hold.guest_email)
        db.add(guest)
        db.flush()

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
    )
    invoice.pdf_path = pdf_path

    emailed = trigger_booking_confirmation(db, booking)
    trigger_booking_confirmation_sms(db, booking)

    hold.booking_id = booking.id
    hold.status = "converted"
    hold.updated_at = datetime.now(timezone.utc)

    return booking, invoice, emailed


# ---------------------------------------------------------------------------
# Public unit listing
# ---------------------------------------------------------------------------

@router.get("/units", response_model=list[PublicUnitOut])
def list_public_units(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Unit, Property)
        .join(Property, Unit.property_id == Property.id)
        .where(Unit.status == "available")
    ).all()
    return [
        PublicUnitOut(id=u.id, name=u.name, base_rate=float(u.base_rate), max_occupancy=u.max_occupancy, property_name=p.name)
        for u, p in rows
    ]


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
    return PublicAvailabilityOut(
        unit_id=unit_id, check_in=check_in, check_out=check_out,
        available=available, nights=nights,
        estimated_total=round(float(unit.base_rate) * nights, 2),
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

@router.post("/holds", response_model=HoldOut, status_code=201)
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
        guest_state=None,
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

    # Create the Razorpay order — raises 501 if not configured
    try:
        result = create_order(total, gateway_order.receipt, notes={"hold_id": str(hold.id)})
        gateway_order.gateway_order_id = result.get("id")
    except GatewayNotConfiguredError as e:
        db.rollback()
        raise HTTPException(501, str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(502, f"Razorpay order creation failed: {e}")

    db.commit()
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

@router.post("/holds/{hold_id}/verify-payment", response_model=PublicBookingConfirmation, status_code=201)
def verify_payment(hold_id: uuid.UUID, payload: VerifyPaymentRequest, db: Session = Depends(get_db)):
    """
    Verifies the Razorpay payment signature server-side and, on success,
    converts the BookingHold into a confirmed Booking with GST invoice.
    A browser callback alone is never sufficient — this endpoint is the
    authoritative gate.
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
            confirmation_emailed=None,
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

    # Payment verified — record it and convert the hold
    hold.status = "paid"
    hold.razorpay_payment_id = payload.razorpay_payment_id
    hold.updated_at = datetime.now(timezone.utc)
    if gateway_order:
        gateway_order.status = "paid"
        gateway_order.gateway_payment_id = payload.razorpay_payment_id

    booking, invoice, emailed = _convert_hold_to_booking(db, hold)

    db.commit()
    db.refresh(booking)
    db.refresh(invoice)

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
        confirmation_emailed=emailed,
    )
