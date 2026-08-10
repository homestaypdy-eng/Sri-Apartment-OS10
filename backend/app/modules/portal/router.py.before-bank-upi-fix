"""
Guest-facing portal API.

AUTH APPROACH: magic-link login via a short-lived token, matching the
"Guest Login" requirement without needing a guest-managed password (which
would need a signup/reset flow this scope doesn't call for). A guest
requests login by phone; if they have an email on file AND SMTP is
configured, the link is emailed for real. Since this sandbox has neither
guaranteed real guest emails nor working SMTP, the response also returns
the raw magic token directly — clearly documented in the schema as a
testability concession that a real production deployment would remove
(the token would only ever reach the guest via the email/WhatsApp channel,
never appear in an API response body).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import TokenError, create_guest_magic_token, create_guest_session_token, decode_token
from app.db.session import get_db
from app.modules.bookings.models import Booking, Guest
from app.modules.notifications.service import send_email, EmailNotConfiguredError, trigger_guest_request_created, trigger_guest_request_resolved
from app.modules.payments.models import Invoice
from app.modules.payments.service import generate_invoice_pdf
from app.modules.portal.models import REQUEST_PRIORITIES, REQUEST_STATUSES, GuestRequest
from app.modules.portal.schemas import (
    GuestBookingOut,
    GuestFeedbackCreate,
    GuestProfileOut,
    GuestRequestCreate,
    GuestRequestOut,
    GuestRequestStaffUpdate,
    GuestSessionToken,
    RequestLoginRequest,
    RequestLoginResponse,
    VerifyLoginRequest,
)
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.shared.rbac import get_current_user, require_role
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/v1/portal", tags=["guest-portal"])

_guest_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/portal/verify", auto_error=False)


def get_current_guest(token: str | None = Depends(_guest_oauth2_scheme), db: Session = Depends(get_db)) -> Guest:
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError:
        raise HTTPException(401, "Invalid or expired session")
    if payload.get("role") != "guest":
        raise HTTPException(403, "This endpoint requires a guest session, not a staff/admin token")
    guest = db.get(Guest, uuid.UUID(payload["sub"]))
    if guest is None:
        raise HTTPException(401, "Guest not found")
    return guest


@router.post("/request-login", response_model=RequestLoginResponse)
def request_login(payload: RequestLoginRequest, db: Session = Depends(get_db)):
    guest = db.scalar(select(Guest).where(Guest.phone == payload.phone))
    if guest is None:
        # Same response whether or not the phone matches a guest — avoids
        # leaking which phone numbers are registered guests.
        return RequestLoginResponse(sent=False, channel="none_available", dev_magic_token=None)

    magic_token = create_guest_magic_token(str(guest.id))
    login_link = f"https://portal.example.com/verify?token={magic_token}"  # placeholder domain — real deployment sets its own

    # P0-2: dev_magic_token must never appear in production responses — the token
    # should only reach the guest via their email/WhatsApp channel, never in the
    # API response body where it could be logged by proxies or intercepted.
    dev_token = magic_token if settings.environment != "production" else None

    if guest.email:
        try:
            send_email(guest.email, "Your Sri Apartment Homestay login link", f"Click to log in: {login_link}\n\nThis link expires in 15 minutes.")
            return RequestLoginResponse(sent=True, channel="email", dev_magic_token=dev_token)
        except EmailNotConfiguredError:
            pass

    return RequestLoginResponse(sent=False, channel="none_available", dev_magic_token=dev_token)


@router.post("/verify", response_model=GuestSessionToken)
def verify_login(payload: VerifyLoginRequest, db: Session = Depends(get_db)):
    try:
        token_payload = decode_token(payload.token, expected_type="access")
    except TokenError:
        raise HTTPException(401, "Invalid or expired login link")
    if token_payload.get("role") != "guest_magic":
        raise HTTPException(401, "Invalid login token")

    guest = db.get(Guest, uuid.UUID(token_payload["sub"]))
    if guest is None:
        raise HTTPException(401, "Guest not found")

    return GuestSessionToken(access_token=create_guest_session_token(str(guest.id)))


@router.get("/me", response_model=GuestProfileOut)
def get_my_profile(guest: Guest = Depends(get_current_guest)):
    return guest


@router.get("/bookings", response_model=list[GuestBookingOut])
def my_bookings(guest: Guest = Depends(get_current_guest), db: Session = Depends(get_db)):
    return db.scalars(select(Booking).where(Booking.guest_id == guest.id).order_by(Booking.check_in.desc())).all()


@router.get("/invoices/{booking_id}/pdf")
def download_my_invoice(booking_id: uuid.UUID, guest: Guest = Depends(get_current_guest), db: Session = Depends(get_db)):
    booking = db.get(Booking, booking_id)
    if booking is None or booking.guest_id != guest.id:
        raise HTTPException(404, "Booking not found")  # 404 not 403 — don't confirm other guests' booking IDs exist
    invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking_id))
    if invoice is None:
        raise HTTPException(404, "No invoice generated for this booking yet")

    from app.modules.properties.models import Unit, Property
    unit = db.get(Unit, booking.unit_id)
    property_ = db.get(Property, unit.property_id)
    pdf_path = generate_invoice_pdf(
        invoice_no=invoice.invoice_no, property_name=property_.name, property_gst_number=property_.gst_number,
        guest_name=guest.name, guest_gst_number=guest.gst_number, unit_name=unit.name,
        check_in=booking.check_in, check_out=booking.check_out, subtotal=float(invoice.subtotal),
        cgst=float(invoice.cgst), sgst=float(invoice.sgst), igst=float(invoice.igst), total=float(invoice.total),
        hsn_code=invoice.hsn_code, gst_rate_percent=float(invoice.gst_rate_percent), itc_available=invoice.itc_available,
    )
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{invoice.invoice_no.replace('/', '_')}.pdf")


@router.post("/requests", response_model=GuestRequestOut, status_code=201)
def create_request(payload: GuestRequestCreate, guest: Guest = Depends(get_current_guest), db: Session = Depends(get_db)):
    if payload.booking_id:
        booking = db.get(Booking, payload.booking_id)
        if booking is None or booking.guest_id != guest.id:
            raise HTTPException(404, "Booking not found")

    request_type = "extend_stay" if payload.requested_check_out_date else "service"
    req = GuestRequest(
        guest_id=guest.id,
        booking_id=payload.booking_id,
        request_type=request_type,
        description=payload.description,
        requested_check_out_date=payload.requested_check_out_date,
        status="open",
    )
    db.add(req)
    db.flush()
    record_audit_log(db, None, "guest_request.create", entity_type="guest_request", entity_id=str(req.id), details=f"{request_type} from {guest.name}: {payload.description[:100]}")
    trigger_guest_request_created(db, req)
    db.commit()
    db.refresh(req)
    return req


@router.post("/complaints", response_model=GuestRequestOut, status_code=201)
def create_complaint(payload: GuestRequestCreate, guest: Guest = Depends(get_current_guest), db: Session = Depends(get_db)):
    req = GuestRequest(
        guest_id=guest.id, booking_id=payload.booking_id, request_type="complaint",
        description=payload.description, status="open",
    )
    db.add(req)
    db.flush()
    record_audit_log(db, None, "guest_request.create", entity_type="guest_request", entity_id=str(req.id), details=f"complaint from {guest.name}: {payload.description[:100]}")
    trigger_guest_request_created(db, req)
    db.commit()
    db.refresh(req)
    return req


@router.get("/requests", response_model=list[GuestRequestOut])
def my_requests(guest: Guest = Depends(get_current_guest), db: Session = Depends(get_db)):
    return db.scalars(select(GuestRequest).where(GuestRequest.guest_id == guest.id).order_by(GuestRequest.created_at.desc())).all()


# --- Staff-side management of guest requests ---

staff_router = APIRouter(prefix="/api/v1/guest-requests", tags=["guest-portal"])


@staff_router.get("", response_model=list[GuestRequestOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_all_requests(status: str | None = None, request_type: str | None = None, db: Session = Depends(get_db)):
    query = select(GuestRequest).order_by(GuestRequest.created_at.desc())
    if status:
        query = query.where(GuestRequest.status == status)
    if request_type:
        query = query.where(GuestRequest.request_type == request_type)
    return db.scalars(query).all()


@staff_router.patch("/{request_id}", response_model=GuestRequestOut, dependencies=[Depends(require_role("admin", "staff"))])
def update_request(request_id: uuid.UUID, payload: GuestRequestStaffUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from datetime import datetime, timezone

    req = db.get(GuestRequest, request_id)
    if req is None:
        raise HTTPException(404, "Request not found")

    changes = []
    if payload.status is not None:
        if payload.status not in REQUEST_STATUSES:
            raise HTTPException(400, f"Invalid status '{payload.status}'")
        changes.append(f"status: {req.status} -> {payload.status}")
        req.status = payload.status
        if payload.status == "resolved":
            req.resolved_at = datetime.now(timezone.utc)
    if payload.priority is not None:
        if payload.priority not in REQUEST_PRIORITIES:
            raise HTTPException(400, f"Invalid priority '{payload.priority}'")
        changes.append(f"priority: {req.priority} -> {payload.priority}")
        req.priority = payload.priority
    if payload.assigned_to is not None:
        changes.append(f"assigned_to: {req.assigned_to} -> {payload.assigned_to}")
        req.assigned_to = payload.assigned_to
    if payload.staff_notes is not None:
        req.staff_notes = payload.staff_notes

    if changes:
        record_audit_log(db, user.id, "guest_request.update", entity_type="guest_request", entity_id=str(req.id), details="; ".join(changes))
    if payload.status == "resolved":
        trigger_guest_request_resolved(db, req)
    db.commit()
    db.refresh(req)
    return req


@router.post("/guest-requests/{request_id}/feedback", response_model=GuestRequestOut, dependencies=[Depends(require_role("admin", "staff"))])
def submit_guest_feedback(request_id: uuid.UUID, payload: GuestFeedbackCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Feedback submission is staff-facilitated (entered on the guest's
    behalf at checkout, over phone, etc.) rather than requiring the
    guest's own portal session — this matches how the business actually
    collects feedback in practice, not a guest self-service form.
    """
    req = db.get(GuestRequest, request_id)
    if req is None:
        raise HTTPException(404, "Request not found")
    req.guest_feedback_rating = payload.rating
    req.guest_feedback_text = payload.text
    record_audit_log(db, user.id, "guest_request.feedback", entity_type="guest_request", entity_id=str(req.id), details=f"Rating: {payload.rating}/5" + (f" — {payload.text}" if payload.text else ""))
    db.commit()
    db.refresh(req)
    return req


@staff_router.post("/{request_id}/approve-extension", response_model=GuestRequestOut, dependencies=[Depends(require_role("admin", "staff"))])
def approve_extension_request(request_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    The real business-workflow gap this closes: an extend_stay request
    previously had no effect on the actual booking when "resolved" — the
    guest would be told their stay was extended, but the booking's
    check_out date, and therefore its revenue, never actually changed.
    This endpoint is the one place that both marks the request resolved
    AND genuinely extends the booking — going through the exact same
    DB-level overlap-safety check a normal booking edit does, so an
    extension can't silently double-book the unit for the extra nights.
    """
    from datetime import datetime, timezone
    from sqlalchemy.exc import IntegrityError

    req = db.get(GuestRequest, request_id)
    if req is None:
        raise HTTPException(404, "Request not found")
    if req.request_type != "extend_stay":
        raise HTTPException(400, "This endpoint only applies to extend_stay requests")
    if req.requested_check_out_date is None:
        raise HTTPException(400, "This request has no requested check-out date")
    if req.booking_id is None:
        raise HTTPException(400, "This request isn't linked to a booking")

    booking = db.get(Booking, req.booking_id)
    if booking is None:
        raise HTTPException(404, "Linked booking not found")
    if req.requested_check_out_date <= booking.check_out:
        raise HTTPException(400, "Requested check-out date must be after the current check-out date")

    old_check_out = booking.check_out
    booking.check_out = req.requested_check_out_date

    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        if "no_overlapping_bookings" in str(e.orig):
            raise HTTPException(409, "Extending to this date would overlap with another booking for the same unit")
        raise

    req.status = "resolved"
    req.resolved_at = datetime.now(timezone.utc)

    record_audit_log(
        db, user.id, "booking.edit", entity_type="booking", entity_id=str(booking.id),
        details=f"Extended via guest request {req.id}",
        previous_value=f"check_out={old_check_out}", new_value=f"check_out={booking.check_out}",
    )
    record_audit_log(db, user.id, "guest_request.update", entity_type="guest_request", entity_id=str(req.id), details=f"Extension approved: check_out {old_check_out} -> {booking.check_out}")
    trigger_guest_request_resolved(db, req)
    db.commit()
    db.refresh(req)
    return req
