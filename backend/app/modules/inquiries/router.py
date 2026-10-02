import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.notifications.service import trigger_inquiry_created
from app.modules.auth.models import User
from app.modules.bookings.models import Booking, Guest
from app.modules.inquiries.models import INQUIRY_PRIORITIES, INQUIRY_SOURCES, INQUIRY_STATUSES, LEAD_STATUSES, Inquiry, InquiryAttachment, InquiryCommunication
from app.shared.file_upload import read_and_validate_upload
from app.modules.inquiries.schemas import (
    CommunicationOut,
    ConvertToBookingRequest,
    ConvertToBookingResponse,
    InquiryAttachmentOut,
    InquiryCreate,
    InquiryOut,
    InquiryUpdate,
    SendEmailRequest,
    SendEmailResponse,
    SendWhatsAppResponse,
)
from app.modules.inquiries.service import (
    EmailNotConfiguredError,
    build_whatsapp_message,
    build_whatsapp_url,
    calculate_pricing,
    calculate_pricing_from_subtotal,
    generate_quotation_pdf,
    next_offer_no,
    send_quotation_email,
)
from app.modules.payments.models import Payment
from app.modules.properties.models import Unit
from app.modules.properties.router import get_unit_price_quote
from app.modules.settings.router import get_or_create_bank_settings, get_or_create_settings
from app.shared.availability import lock_unit_for_reservation, unit_is_available
from app.shared.occupancy import enforce_unit_max_occupancy
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/inquiries", tags=["inquiries"])


def _map_payment_mode(inquiry_payment_mode: str) -> str:
    """Maps the inquiry's free-text payment_mode to the Payment model's fixed enum."""
    m = inquiry_payment_mode.lower()
    if "upi" in m:
        return "upi"
    if "card" in m:
        return "card"
    if "cash" in m:
        return "cash"
    return "bank_transfer"  # covers "Online / Bank Transfer" and any other default


@router.post("", response_model=InquiryOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def create_inquiry(payload: InquiryCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.source not in INQUIRY_SOURCES:
        raise HTTPException(400, f"Invalid source '{payload.source}'. Must be one of {INQUIRY_SOURCES}")
    if payload.priority not in INQUIRY_PRIORITIES:
        raise HTTPException(400, f"Invalid priority '{payload.priority}'. Must be one of {INQUIRY_PRIORITIES}")
    nights = (payload.check_out_date - payload.check_in_date).days
    unit = db.get(Unit, payload.unit_id) if payload.unit_id else None

    pricing_source = "manual"
    rate_breakdown_json = None
    effective_rate_per_night = payload.rate_per_night

    if unit is not None and payload.rate_per_night is None:
        # No manual rate given and a real unit is linked -> auto-calculate
        # from the unit's weekday/weekend/seasonal rates + long-stay discount.
        quote = get_unit_price_quote(db, unit, payload.check_in_date, payload.check_out_date)
        pricing = calculate_pricing_from_subtotal(quote.subtotal_after_discount, quote.average_nightly_rate, payload.extra_charges, payload.discount)
        effective_rate_per_night = quote.average_nightly_rate
        pricing_source = "auto"
        rate_breakdown_json = json.dumps(
            {
                "daily": [{"date": d.the_date.isoformat(), "rate": d.rate, "source": d.source, "seasonal_name": d.seasonal_name} for d in quote.daily_breakdown],
                "subtotal_before_discount": quote.subtotal_before_discount,
                "long_stay_discount_percent": quote.long_stay_discount_percent,
                "long_stay_discount_amount": quote.long_stay_discount_amount,
                "subtotal_after_discount": quote.subtotal_after_discount,
            }
        )
    elif unit is not None and payload.rate_per_night is not None:
        # A real unit is linked but staff typed a rate manually anyway ->
        # this is an override of what the system would have calculated.
        # Log it (what the engine WOULD have charged vs what was actually
        # used) so a manager can see overrides happened and why, rather
        # than the override silently looking identical to an auto quote.
        quote = get_unit_price_quote(db, unit, payload.check_in_date, payload.check_out_date)
        pricing = calculate_pricing(payload.rate_per_night, nights, payload.extra_charges, payload.discount)
        record_audit_log(
            db, user.id, "inquiry.manual_rate_override", entity_type="unit", entity_id=str(unit.id),
            details=(
                f"Auto-calculated average rate would have been Rs.{quote.average_nightly_rate}/night "
                f"(subtotal Rs.{quote.subtotal_after_discount}); manual rate of Rs.{payload.rate_per_night}/night "
                f"used instead. Reason: {payload.override_reason or 'not given'}"
            ),
        )
        pricing_source = "manual"
    else:
        # No unit linked at all (ad-hoc quote) -> rate_per_night is required
        if payload.rate_per_night is None:
            raise HTTPException(400, "rate_per_night is required when no unit_id is linked")
        pricing = calculate_pricing(payload.rate_per_night, nights, payload.extra_charges, payload.discount)

    if payload.advance_amount > pricing["grand_total"]:
        raise HTTPException(
            400,
            f"Advance amount ({payload.advance_amount}) cannot exceed the grand total ({pricing['grand_total']})",
        )

    balance = round(pricing["grand_total"] - payload.advance_amount, 2)

    inquiry = Inquiry(
        offer_no=next_offer_no(db),
        valid_upto=payload.valid_upto or payload.check_in_date,
        client_name=payload.client_name,
        client_phone=payload.client_phone,
        client_email=payload.client_email,
        client_address=payload.client_address,
        unit_id=payload.unit_id,
        flat_room_label=payload.flat_room_label,
        num_guests=payload.num_guests,
        check_in_date=payload.check_in_date,
        check_in_time=payload.check_in_time,
        check_out_date=payload.check_out_date,
        check_out_time=payload.check_out_time,
        tariff_type=payload.tariff_type,
        rate_per_night=effective_rate_per_night,
        nights=nights,
        extra_charges=payload.extra_charges,
        discount=payload.discount,
        taxable_value=pricing["taxable_value"],
        gst_rate_percent=pricing["gst_rate_percent"],
        cgst=pricing["cgst"],
        sgst=pricing["sgst"],
        total_amount=pricing["total_amount"],
        rounded_off=pricing["rounded_off"],
        grand_total=pricing["grand_total"],
        advance_amount=payload.advance_amount,
        balance_amount=balance,
        payment_mode=payload.payment_mode,
        status="draft",
        lead_status="new",
        source=payload.source,
        priority=payload.priority,
        assigned_to=payload.assigned_to,
        created_by=user.id,
        pricing_source=pricing_source,
        rate_breakdown=rate_breakdown_json,
    )
    db.add(inquiry)
    db.flush()
    record_audit_log(db, user.id, "inquiry.create", entity_type="inquiry", entity_id=str(inquiry.id), details=f"{inquiry.offer_no} for {payload.client_name} ({payload.source})")
    trigger_inquiry_created(db, inquiry)
    db.commit()
    db.refresh(inquiry)
    return inquiry


@router.get("", response_model=list[InquiryOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_inquiries(
    status: str | None = None,
    lead_status: str | None = None,
    expired: bool | None = None,
    source: str | None = None,
    priority: str | None = None,
    assigned_to: uuid.UUID | None = None,
    search: str | None = Query(None, description="Matches client name or phone"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(Inquiry).order_by(Inquiry.created_at.desc())
    if status:
        query = query.where(Inquiry.status == status)
    if lead_status:
        query = query.where(Inquiry.lead_status == lead_status)
    if source:
        query = query.where(Inquiry.source == source)
    if priority:
        query = query.where(Inquiry.priority == priority)
    if assigned_to:
        query = query.where(Inquiry.assigned_to == assigned_to)
    if search:
        query = query.where((Inquiry.client_name.ilike(f"%{search}%")) | (Inquiry.client_phone.ilike(f"%{search}%")))
    query = query.offset(offset).limit(limit)
    inquiries = db.scalars(query).all()
    if expired is not None:
        inquiries = [i for i in inquiries if i.is_expired == expired]
    return inquiries


# NOTE: these two routes MUST be registered before GET /{inquiry_id} below —
# both are single path-segment GETs under this router, and FastAPI/Starlette
# matches routes in registration order, so "/expiring" would otherwise be
# swallowed by "/{inquiry_id}" (with inquiry_id="expiring") and fail UUID
# validation instead of ever reaching this handler.
@router.get("/follow-ups/due", response_model=list[InquiryOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_follow_ups_due(as_of: date | None = Query(None), db: Session = Depends(get_db)):
    """
    Leads with a follow-up date on or before `as_of` (default: today) that
    haven't already reached a terminal state. This is a pull-based reminder
    list, not a push notification — see the README for why.
    """
    cutoff = as_of or date.today()
    query = select(Inquiry).where(
        Inquiry.next_follow_up_date <= cutoff,
        Inquiry.next_follow_up_date.isnot(None),
        Inquiry.lead_status.notin_(("converted", "lost")),
    ).order_by(Inquiry.next_follow_up_date.asc())
    return db.scalars(query).all()


@router.get("/expiring", response_model=list[InquiryOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_expiring_soon(within_days: int = Query(3, ge=0), db: Session = Depends(get_db)):
    """Quotations whose validity ends within the next N days and haven't converted or been lost."""
    today = date.today()
    cutoff = today + timedelta(days=within_days)
    query = select(Inquiry).where(
        Inquiry.valid_upto >= today,
        Inquiry.valid_upto <= cutoff,
        Inquiry.lead_status.notin_(("converted", "lost")),
    ).order_by(Inquiry.valid_upto.asc())
    return db.scalars(query).all()


@router.get("/{inquiry_id}", response_model=InquiryOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_inquiry(inquiry_id: uuid.UUID, db: Session = Depends(get_db)):
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")
    return inquiry


@router.patch("/{inquiry_id}", response_model=InquiryOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_inquiry(inquiry_id: uuid.UUID, payload: InquiryUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")

    provided = payload.model_dump(exclude_unset=True)

    # Fields safe to change even after conversion — CRM/pipeline metadata
    # only, nothing that affects the guest's paperwork or money already
    # collected. Everything else is locked the moment a real Booking +
    # Payment exist, so a confirmed booking's terms can't quietly drift
    # from what the guest actually agreed to.
    always_allowed = {"lead_status", "next_follow_up_date", "follow_up_notes", "priority", "assigned_to", "lost_reason"}
    locked_fields_present = set(provided.keys()) - always_allowed
    if inquiry.booking_id is not None and locked_fields_present:
        raise HTTPException(
            409,
            "This inquiry has already been converted to a confirmed booking and its details are locked. "
            "Changes to the stay, rate, or client details now belong on the Booking/Payment records instead.",
        )

    if "status" in provided:
        if payload.status not in INQUIRY_STATUSES:
            raise HTTPException(400, f"Invalid status '{payload.status}'")
        inquiry.status = payload.status

    if "lead_status" in provided:
        if payload.lead_status not in LEAD_STATUSES:
            raise HTTPException(400, f"Invalid lead_status '{payload.lead_status}'. Must be one of {LEAD_STATUSES}")
        inquiry.lead_status = payload.lead_status

    if "priority" in provided:
        if payload.priority not in INQUIRY_PRIORITIES:
            raise HTTPException(400, f"Invalid priority '{payload.priority}'. Must be one of {INQUIRY_PRIORITIES}")
        inquiry.priority = payload.priority

    if "assigned_to" in provided:
        inquiry.assigned_to = payload.assigned_to

    if "lost_reason" in provided:
        inquiry.lost_reason = payload.lost_reason

    if "next_follow_up_date" in provided:
        inquiry.next_follow_up_date = payload.next_follow_up_date

    if "follow_up_notes" in provided:
        inquiry.follow_up_notes = payload.follow_up_notes

    # Simple direct fields (no recalculation needed)
    for field in ("client_name", "client_phone", "client_email", "client_address", "flat_room_label",
                  "num_guests", "check_in_time", "check_out_time", "tariff_type", "valid_upto", "payment_mode"):
        if field in provided:
            setattr(inquiry, field, provided[field])

    # Fields that require re-running the pricing engine if any of them changed
    pricing_fields = {"unit_id", "check_in_date", "check_out_date", "rate_per_night", "extra_charges", "discount"}
    if pricing_fields & set(provided.keys()):
        new_unit_id = provided.get("unit_id", inquiry.unit_id)
        new_check_in = provided.get("check_in_date", inquiry.check_in_date)
        new_check_out = provided.get("check_out_date", inquiry.check_out_date)
        new_extra_charges = provided.get("extra_charges", float(inquiry.extra_charges))
        new_discount = provided.get("discount", float(inquiry.discount))
        manual_rate = provided.get("rate_per_night", None)

        if new_check_out <= new_check_in:
            raise HTTPException(400, "check_out_date must be after check_in_date")
        nights = (new_check_out - new_check_in).days
        unit = db.get(Unit, new_unit_id) if new_unit_id else None

        pricing_source = "manual"
        rate_breakdown_json = inquiry.rate_breakdown
        effective_rate_per_night = manual_rate

        if unit is not None and manual_rate is None:
            quote = get_unit_price_quote(db, unit, new_check_in, new_check_out)
            pricing = calculate_pricing_from_subtotal(quote.subtotal_after_discount, quote.average_nightly_rate, new_extra_charges, new_discount)
            effective_rate_per_night = quote.average_nightly_rate
            pricing_source = "auto"
            rate_breakdown_json = json.dumps({
                "daily": [{"date": d.the_date.isoformat(), "rate": d.rate, "source": d.source, "seasonal_name": d.seasonal_name} for d in quote.daily_breakdown],
                "subtotal_before_discount": quote.subtotal_before_discount,
                "long_stay_discount_percent": quote.long_stay_discount_percent,
                "long_stay_discount_amount": quote.long_stay_discount_amount,
                "subtotal_after_discount": quote.subtotal_after_discount,
            })
        elif unit is not None and manual_rate is not None:
            quote = get_unit_price_quote(db, unit, new_check_in, new_check_out)
            pricing = calculate_pricing(manual_rate, nights, new_extra_charges, new_discount)
            record_audit_log(
                db, user.id, "inquiry.manual_rate_override", entity_type="unit", entity_id=str(unit.id),
                details=(
                    f"[Edit] Auto-calculated average rate would have been Rs.{quote.average_nightly_rate}/night "
                    f"(subtotal Rs.{quote.subtotal_after_discount}); manual rate of Rs.{manual_rate}/night used instead. "
                    f"Reason: {provided.get('override_reason') or 'not given'}"
                ),
            )
            pricing_source = "manual"
            rate_breakdown_json = None
        else:
            if manual_rate is None:
                raise HTTPException(400, "rate_per_night is required when no unit_id is linked")
            pricing = calculate_pricing(manual_rate, nights, new_extra_charges, new_discount)
            pricing_source = "manual"
            rate_breakdown_json = None

        effective_advance = provided.get("advance_amount", float(inquiry.advance_amount))
        if effective_advance > pricing["grand_total"]:
            raise HTTPException(
                400,
                f"The advance amount ({effective_advance}) would exceed the recalculated "
                f"grand total ({pricing['grand_total']}) — adjust the advance amount or the rate/discount.",
            )

        inquiry.unit_id = new_unit_id
        inquiry.check_in_date = new_check_in
        inquiry.check_out_date = new_check_out
        inquiry.nights = nights
        inquiry.rate_per_night = effective_rate_per_night
        inquiry.extra_charges = new_extra_charges
        inquiry.discount = new_discount
        inquiry.taxable_value = pricing["taxable_value"]
        inquiry.gst_rate_percent = pricing["gst_rate_percent"]
        inquiry.cgst = pricing["cgst"]
        inquiry.sgst = pricing["sgst"]
        inquiry.total_amount = pricing["total_amount"]
        inquiry.rounded_off = pricing["rounded_off"]
        inquiry.grand_total = pricing["grand_total"]
        inquiry.balance_amount = round(pricing["grand_total"] - float(inquiry.advance_amount), 2)
        inquiry.pricing_source = pricing_source
        inquiry.rate_breakdown = rate_breakdown_json

    if "advance_amount" in provided:
        if payload.advance_amount > float(inquiry.grand_total):
            raise HTTPException(
                400,
                f"Advance amount ({payload.advance_amount}) cannot exceed the grand total ({inquiry.grand_total})",
            )
        inquiry.advance_amount = payload.advance_amount
        inquiry.balance_amount = round(float(inquiry.grand_total) - payload.advance_amount, 2)

    db.commit()
    db.refresh(inquiry)
    return inquiry


@router.get("/{inquiry_id}/pdf", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def download_quotation_pdf(inquiry_id: uuid.UUID, db: Session = Depends(get_db)):
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")

    business_settings = get_or_create_settings(db)
    filepath = generate_quotation_pdf(inquiry, business_settings=business_settings, bank_settings=get_or_create_bank_settings(db))
    inquiry.pdf_path = filepath
    db.commit()

    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=f"{inquiry.offer_no.replace('/', '_')}_quotation.pdf",
    )


@router.post(
    "/{inquiry_id}/convert-to-booking",
    response_model=ConvertToBookingResponse,
    status_code=201,
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def convert_to_booking(
    inquiry_id: uuid.UUID,
    payload: ConvertToBookingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")

    if inquiry.booking_id is not None:
        raise HTTPException(409, f"This inquiry was already converted to booking {inquiry.booking_id}")

    # The whole point of this endpoint: only convert once an advance has
    # actually been recorded, per the real operational requirement — not
    # just because a quotation exists.
    if float(inquiry.advance_amount) <= 0:
        raise HTTPException(
            400,
            "Advance payment must be recorded on this inquiry before it can be converted to a booking "
            "(update advance_amount via PATCH first)",
        )

    unit_id = payload.unit_id or inquiry.unit_id
    if unit_id is None:
        raise HTTPException(
            400,
            "This inquiry has no linked unit — provide unit_id in the request body to convert it",
        )
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")

    # P1-3: Unit status is authoritative — maintenance/blocked units cannot be
    # booked even via inquiry conversion. The admin must mark the unit available
    # first, the same guard that protects the direct booking and public hold flows.
    if unit.status != "available":
        raise HTTPException(
            409,
            f"Unit is not available for booking (status: {unit.status}). "
            "Change the unit status to 'available' before converting this inquiry.",
        )

    # Inquiry conversion is another booking writer. Serialize it with public
    # holds, staff bookings, edits, and Zotel imports before checking both
    # booking and hold tables; table-local exclusion constraints alone do
    # not protect the cross-table race.
    lock_unit_for_reservation(db, unit_id)
    db.refresh(unit)
    enforce_unit_max_occupancy(unit, inquiry.num_guests, 0)
    if not unit_is_available(db, unit_id, inquiry.check_in_date, inquiry.check_out_date):
        raise HTTPException(409, "This unit is already booked or held for overlapping dates")

    # Reuse an existing guest by phone number rather than creating a
    # duplicate for a repeat client — phone is the most reliable natural key
    # available on the inquiry (email is optional).
    guest = db.scalar(select(Guest).where(Guest.phone == inquiry.client_phone))
    guest_reused = guest is not None
    if guest is None:
        guest = Guest(
            name=inquiry.client_name,
            phone=inquiry.client_phone,
            email=inquiry.client_email,
        )
        db.add(guest)
        db.flush()  # get guest.id before creating the booking

    booking = Booking(
        unit_id=unit_id,
        guest_id=guest.id,
        check_in=inquiry.check_in_date,
        check_out=inquiry.check_out_date,
        status="confirmed",
        source="direct",
        rate_applied=inquiry.rate_per_night,
        adults=inquiry.num_guests,
        extra_guest_charge=inquiry.extra_charges,
        created_by=user.id,
    )
    db.add(booking)
    try:
        db.flush()  # surfaces the overlap-safe EXCLUDE constraint here, before we commit anything else
    except IntegrityError as e:
        db.rollback()
        if "no_overlapping_bookings" in str(e.orig):
            raise HTTPException(
                409,
                "This unit is already booked for an overlapping date range — cannot convert this inquiry",
            )
        raise

    payment = Payment(
        booking_id=booking.id,
        amount=inquiry.advance_amount,
        mode=_map_payment_mode(inquiry.payment_mode),
        status="completed",
        reference_no=f"Advance for {inquiry.offer_no}",
        created_by=user.id,
    )
    db.add(payment)

    inquiry.booking_id = booking.id
    inquiry.guest_id = guest.id
    inquiry.status = "confirmed"
    inquiry.lead_status = "converted"

    record_audit_log(
        db, user.id, "inquiry.convert_to_booking", entity_type="inquiry", entity_id=str(inquiry.id),
        details=f"Converted {inquiry.offer_no} to booking {booking.id} for {guest.name}, advance Rs.{inquiry.advance_amount}"
        + (" (reused existing guest)" if guest_reused else " (created new guest)"),
    )

    db.commit()
    db.refresh(inquiry)
    db.refresh(booking)
    db.refresh(payment)

    return ConvertToBookingResponse(
        inquiry=inquiry,
        booking_id=booking.id,
        guest_id=guest.id,
        payment_id=payment.id,
        guest_reused=guest_reused,
    )


def _bump_lead_after_contact(inquiry: Inquiry) -> None:
    """First contact moves a lead from 'new' to 'contacted' automatically;
    later sends (e.g. a follow-up resend) don't regress an already-further-
    along lead_status like 'follow_up' or 'converted' back to 'contacted'."""
    if inquiry.lead_status == "new":
        inquiry.lead_status = "contacted"
    inquiry.last_contacted_at = datetime.now(timezone.utc)


@router.post(
    "/{inquiry_id}/send/whatsapp",
    response_model=SendWhatsAppResponse,
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def send_whatsapp(inquiry_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")

    url = build_whatsapp_url(inquiry)
    message = build_whatsapp_message(inquiry)

    db.add(
        InquiryCommunication(
            inquiry_id=inquiry.id,
            channel="whatsapp",
            sent_to=inquiry.client_phone,
            success=True,
            sent_by=user.id,
        )
    )
    _bump_lead_after_contact(inquiry)
    if inquiry.status == "draft":
        inquiry.status = "sent"
    db.commit()

    return SendWhatsAppResponse(whatsapp_url=url, message_preview=message)


@router.post(
    "/{inquiry_id}/send/email",
    response_model=SendEmailResponse,
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def send_email(
    inquiry_id: uuid.UUID,
    payload: SendEmailRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")

    to_email = payload.to_email or inquiry.client_email
    if not to_email:
        raise HTTPException(400, "No email address on file for this inquiry — provide to_email in the request")

    pdf_path = generate_quotation_pdf(inquiry, business_settings=get_or_create_settings(db), bank_settings=get_or_create_bank_settings(db))
    inquiry.pdf_path = pdf_path

    try:
        send_quotation_email(inquiry, pdf_path, to_email)
    except EmailNotConfiguredError as e:
        db.add(
            InquiryCommunication(
                inquiry_id=inquiry.id, channel="email", sent_to=to_email, success=False, error_message=str(e), sent_by=user.id
            )
        )
        db.commit()
        raise HTTPException(501, str(e))
    except Exception as e:  # SMTP failures, auth errors, etc — surfaced, not swallowed
        db.add(
            InquiryCommunication(
                inquiry_id=inquiry.id, channel="email", sent_to=to_email, success=False, error_message=str(e), sent_by=user.id
            )
        )
        db.commit()
        raise HTTPException(502, f"Failed to send email: {e}")

    db.add(InquiryCommunication(inquiry_id=inquiry.id, channel="email", sent_to=to_email, success=True, sent_by=user.id))
    _bump_lead_after_contact(inquiry)
    if inquiry.status == "draft":
        inquiry.status = "sent"
    db.commit()

    return SendEmailResponse(sent=True, to_email=to_email)


@router.get(
    "/{inquiry_id}/communications",
    response_model=list[CommunicationOut],
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def list_communications(inquiry_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(
        select(InquiryCommunication)
        .where(InquiryCommunication.inquiry_id == inquiry_id)
        .order_by(InquiryCommunication.sent_at.desc())
    ).all()


@router.get("/{inquiry_id}/timeline", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_inquiry_timeline(inquiry_id: uuid.UUID, db: Session = Depends(get_db)):
    """Every audit-logged action against this specific inquiry, oldest
    first — same pattern as the Booking/Housekeeping/Maintenance
    timelines, filtered to entity_type='inquiry'."""
    from app.modules.audit.models import AuditLog

    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")

    logs = db.scalars(
        select(AuditLog).where(AuditLog.entity_type == "inquiry", AuditLog.entity_id == str(inquiry_id)).order_by(AuditLog.created_at.asc())
    ).all()
    user_ids = {log.user_id for log in logs if log.user_id}
    users_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}

    events = []
    for log in logs:
        user = users_by_id.get(log.user_id) if log.user_id else None
        events.append({
            "action": log.action,
            "user_name": user.name if user else "System",
            "details": log.details,
            "created_at": log.created_at,
        })
    return {"inquiry_id": inquiry_id, "events": events}


INQUIRY_ATTACHMENT_STORAGE_DIR = os.environ.get("INQUIRY_ATTACHMENT_STORAGE_DIR", "/tmp/sri_apartment_inquiry_attachments")
os.makedirs(INQUIRY_ATTACHMENT_STORAGE_DIR, exist_ok=True)


@router.post("/{inquiry_id}/attachments", response_model=InquiryAttachmentOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
async def upload_inquiry_attachment(inquiry_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, "Inquiry not found")

    contents = await read_and_validate_upload(file)
    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{inquiry_id}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(INQUIRY_ATTACHMENT_STORAGE_DIR, stored_name)
    with open(stored_path, "wb") as f:
        f.write(contents)

    record = InquiryAttachment(
        inquiry_id=inquiry_id, filename=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream", file_path=stored_path, uploaded_by=user.id,
    )
    db.add(record)
    db.flush()
    record_audit_log(db, user.id, "inquiry.attachment_upload", entity_type="inquiry", entity_id=str(inquiry_id), details=record.filename)
    db.commit()
    db.refresh(record)
    return record


@router.get("/{inquiry_id}/attachments", response_model=list[InquiryAttachmentOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_inquiry_attachments(inquiry_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(InquiryAttachment).where(InquiryAttachment.inquiry_id == inquiry_id)).all()


@router.get("/{inquiry_id}/attachments/{attachment_id}/download", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def download_inquiry_attachment(inquiry_id: uuid.UUID, attachment_id: uuid.UUID, db: Session = Depends(get_db)):
    record = db.get(InquiryAttachment, attachment_id)
    if record is None or record.inquiry_id != inquiry_id:
        raise HTTPException(404, "Attachment not found")
    if not os.path.exists(record.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(record.file_path, media_type=record.content_type, filename=record.filename)


@router.delete("/{inquiry_id}/attachments/{attachment_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def delete_inquiry_attachment(inquiry_id: uuid.UUID, attachment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    record = db.get(InquiryAttachment, attachment_id)
    if record is None or record.inquiry_id != inquiry_id:
        raise HTTPException(404, "Attachment not found")
    record_audit_log(db, user.id, "inquiry.attachment_delete", entity_type="inquiry", entity_id=str(inquiry_id), details=f"Deleted: {record.filename}")
    if os.path.exists(record.file_path):
        os.remove(record.file_path)
    db.delete(record)
    db.commit()
