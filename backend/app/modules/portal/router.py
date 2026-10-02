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
import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import TokenError, create_guest_magic_token, create_guest_session_token, decode_token
from app.db.session import get_db
from app.modules.bookings import document_storage
from app.modules.bookings.models import IDENTITY_DOCUMENT_TYPES, Booking, Guest, GuestDocument
from app.modules.notifications.service import send_email, EmailNotConfiguredError, trigger_guest_request_created, trigger_guest_request_resolved
from app.modules.payments.models import Invoice
from app.modules.payments.service import generate_invoice_pdf_for_booking
from app.modules.portal.auth_service import is_guest_session_active
from app.modules.portal.models import REQUEST_PRIORITIES, REQUEST_STATUSES, REQUEST_TYPES, GuestRequest
from app.modules.portal.schemas import (
    GuestBookingOut,
    GuestFeedbackCreate,
    GuestIdentityDocumentOut,
    GuestIdentityStatusOut,
    GuestIdentitySubmitRequest,
    GuestProfileOut,
    GuestRequestCreate,
    GuestRequestOut,
    GuestRequestStaffCreate,
    GuestRequestStaffUpdate,
    GuestSessionToken,
    RequestLoginRequest,
    RequestLoginResponse,
    VerifyLoginRequest,
)
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.shared.file_upload import EXTENSION_BY_CONTENT_TYPE, read_and_validate_upload
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

    # SESSION REVOCATION ENFORCEMENT (Phase 2A guest auth hardening): a
    # token WITH a jti must resolve to a real, unrevoked GuestSession row —
    # mirrors the identical jti-optional/jti-enforced pattern auth/router.py's
    # /refresh already uses for staff sessions ("a refresh token issued
    # before session-tracking existed has no jti and is treated as still
    # valid; a token WITH a jti must point to a real, unrevoked session").
    # Today only the phone/email OTP flows (auth_router.py/auth_email_
    # router.py) mint a jti; the magic-link flow below still calls
    # create_guest_session_token with no jti, exactly as before this
    # change — so magic-link tokens keep working unmodified and this is
    # purely additive. Fail closed whenever a jti IS present: a malformed
    # jti, a deleted session row, or a revoked one are all rejected the
    # same way, before the 24h JWT expiry would otherwise have allowed it.
    session_id = payload.get("jti")
    if session_id:
        try:
            session_uuid = uuid.UUID(session_id)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(401, "Invalid or expired session")
        if not is_guest_session_active(db, session_uuid):
            raise HTTPException(401, "Invalid or expired session")

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
        # CREDENTIAL-INDEPENDENT SAFETY GATE: settings.environment is checked
        # BEFORE send_email is ever called — not "try sending and see if it
        # fails". The previous "try it, catch EmailNotConfiguredError" design
        # was only safe when SMTP credentials happened to be absent; this
        # endpoint's own real SMTP credentials are routinely configured
        # (shared with quotations/password-reset), so a local/dev/test run
        # of THIS test suite genuinely opened an authenticated SMTP session
        # against Gmail once — see the Phase 1 verification incident write-up.
        # Outside production, send_email is never invoked at all, regardless
        # of whether valid SMTP credentials happen to exist; dev_magic_token
        # (already returned below) is how local/automated testing completes
        # the flow instead. Mirrors the identical fix already applied to
        # auth_router.py/auth_email_router.py's OTP endpoints. Production's
        # email path — including its EmailNotConfiguredError handling — is
        # completely unchanged.
        if settings.environment != "production":
            return RequestLoginResponse(sent=True, channel="email", dev_magic_token=dev_token)
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


# ---------------------------------------------------------------------------
# Identity Verification (guest-facing) — entirely separate from this portal's
# own login (magic-link / OTP), which only ever proves control of a contact
# channel. Nothing here can set identity_status to "verified"; only an
# authorised OS10 admin can, via POST /guests/{id}/identity/verify. Every
# action below is scoped to the caller's OWN linked Guest record
# (guest.id from get_current_guest, never a client-supplied guest_id), so
# there is no path for one guest to act on another's identity record.
# ---------------------------------------------------------------------------

@router.get("/me/identity", response_model=GuestIdentityStatusOut)
def get_my_identity(guest: Guest = Depends(get_current_guest), db: Session = Depends(get_db)):
    most_recent_doc = db.scalar(
        select(GuestDocument).where(GuestDocument.guest_id == guest.id).order_by(GuestDocument.uploaded_at.desc())
    )
    return GuestIdentityStatusOut(
        status=guest.identity_status,
        document_type=most_recent_doc.document_type if most_recent_doc else None,
        document_id=most_recent_doc.id if most_recent_doc else None,
        verified_at=guest.identity_verified_at,
        # Section 21: a fixed, generic message — never the admin's raw
        # internal rejection reason, which stays staff-only.
        message=(
            "Your identity verification could not be completed. Please submit a valid identity document."
            if guest.identity_status == "rejected" else None
        ),
    )


@router.post("/me/identity/documents", response_model=GuestIdentityDocumentOut, status_code=201)
async def upload_my_identity_document(
    document_type: str,
    file: UploadFile = File(...),
    guest: Guest = Depends(get_current_guest),
    db: Session = Depends(get_db),
):
    """
    Guest-facing counterpart to OS10's POST /guests/{id}/documents — same
    validation (real file-signature check, not just a declared
    Content-Type) and the same private storage backend, reused as-is.
    guest_id always comes from the authenticated session, never a path or
    body parameter, so this can never be used to attach a document to
    someone else's Guest record.
    """
    if document_type not in IDENTITY_DOCUMENT_TYPES:
        raise HTTPException(400, "Invalid document_type")

    allowed_types = ("image/jpeg", "image/png", "image/webp", "application/pdf")
    contents = await read_and_validate_upload(file, allowed_content_types=allowed_types, verify_signature=True)
    ext = EXTENSION_BY_CONTENT_TYPE[file.content_type]
    stored_object: tuple[str, str] | None = None
    try:
        storage_backend, storage_key = await run_in_threadpool(
            document_storage.store_new_document, contents, ext, file.content_type,
        )
        stored_object = (storage_backend, storage_key)
        stored_path = (
            str(document_storage.storage_path(storage_backend, storage_key))
            if storage_backend in {"local", "railway_volume"} else ""
        )
        doc = GuestDocument(
            guest_id=guest.id, document_type=document_type, document_number=None,
            filename=file.filename or storage_key, content_type=file.content_type,
            file_path=stored_path, storage_backend=storage_backend, storage_key=storage_key,
            file_size_bytes=len(contents), sha256_checksum=hashlib.sha256(contents).hexdigest(),
            side="document", uploaded_by=None,  # uploaded by the guest themself, not an OS10 user
        )
        db.add(doc)
        db.flush()
        record_audit_log(db, None, "guest.identity_document_upload", entity_type="guest", entity_id=str(guest.id), details=f"Self-uploaded {document_type} via guest portal: {doc.filename}")
        db.commit()
        db.refresh(doc)
        return doc
    except document_storage.StorageUnavailable:
        db.rollback()
        if stored_object:
            try:
                await run_in_threadpool(document_storage.delete_stored_object, *stored_object)
            except Exception:
                pass
        raise HTTPException(503, "Document storage is unavailable") from None
    except Exception:
        db.rollback()
        if stored_object:
            try:
                await run_in_threadpool(document_storage.delete_stored_object, *stored_object)
            except Exception:
                pass
        raise


@router.post("/me/identity/submit", response_model=GuestIdentityStatusOut)
def submit_my_identity(payload: GuestIdentitySubmitRequest, guest: Guest = Depends(get_current_guest), db: Session = Depends(get_db)):
    """
    Moves the guest's OWN Identity Verification into the OS10 review
    queue. This — and every other endpoint in this file — can never set
    identity_status to "verified"; only POST /guests/{id}/identity/verify
    (admin-only, in bookings/router.py) can. A guest cannot mark
    themselves verified through this or any other API call.
    """
    doc = db.get(GuestDocument, payload.document_id)
    if doc is None or doc.guest_id != guest.id:
        raise HTTPException(404, "Document not found")
    if guest.identity_status in ("submitted", "under_review", "verified"):
        raise HTTPException(409, f"Identity verification is already {guest.identity_status}")

    guest.identity_status = "submitted"
    record_audit_log(db, None, "guest.identity_submit", entity_type="guest", entity_id=str(guest.id), details=f"Submitted for review via guest portal ({doc.document_type})")
    db.commit()
    return GuestIdentityStatusOut(status=guest.identity_status, document_type=doc.document_type, verified_at=None, message=None)


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

    # Return the issued file unchanged whenever it is still available. If a
    # storage restart removed it, regenerate from the invoice record rather
    # than failing the customer's download outright.
    pdf_path = invoice.pdf_path if invoice.pdf_path and os.path.isfile(invoice.pdf_path) else None
    if pdf_path is None:
        pdf_path = generate_invoice_pdf_for_booking(db, booking, invoice)
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


@staff_router.post("", response_model=GuestRequestOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def staff_create_request(payload: GuestRequestStaffCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Staff-side counterpart to POST /portal/requests — same GuestRequest
    table/workflow, but for a need staff notice or a guest reports over
    phone/in person rather than through their own guest-portal session.
    guest_id is derived from the booking (not accepted directly), so this
    can't be used to attach a request to an arbitrary guest_id.
    """
    booking = db.get(Booking, payload.booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    if payload.request_type not in REQUEST_TYPES:
        raise HTTPException(400, f"Invalid request_type '{payload.request_type}'")
    if payload.priority not in REQUEST_PRIORITIES:
        raise HTTPException(400, f"Invalid priority '{payload.priority}'")
    if payload.assigned_to is not None and db.get(User, payload.assigned_to) is None:
        raise HTTPException(404, "Assigned user not found")

    req = GuestRequest(
        guest_id=booking.guest_id,
        booking_id=booking.id,
        request_type=payload.request_type,
        description=payload.description,
        priority=payload.priority,
        assigned_to=payload.assigned_to,
        status="open",
    )
    db.add(req)
    db.flush()
    record_audit_log(db, user.id, "guest_request.create", entity_type="guest_request", entity_id=str(req.id), details=f"{payload.request_type} logged by staff for booking {booking.id}: {payload.description[:100]}")
    trigger_guest_request_created(db, req)
    db.commit()
    db.refresh(req)
    return req


@staff_router.get("", response_model=list[GuestRequestOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_all_requests(status: str | None = None, request_type: str | None = None, db: Session = Depends(get_db)):
    query = select(GuestRequest).order_by(GuestRequest.created_at.desc())
    if status:
        query = query.where(GuestRequest.status == status)
    if request_type:
        query = query.where(GuestRequest.request_type == request_type)
    return db.scalars(query).all()


@staff_router.patch("/{request_id}", response_model=GuestRequestOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
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
    # model_fields_set (not `is not None`) distinguishes "field omitted"
    # from an explicit `assigned_to: null` — the latter now clears the
    # assignment. The existence check prevents the previous unhandled
    # ForeignKeyViolation/500 on a bad user id, matching the check
    # housekeeping/maintenance already had.
    if "assigned_to" in payload.model_fields_set:
        new_assignee = payload.assigned_to
        if new_assignee is not None and db.get(User, new_assignee) is None:
            raise HTTPException(404, "Assigned user not found")
        changes.append(f"assigned_to: {req.assigned_to} -> {new_assignee}")
        req.assigned_to = new_assignee
    if payload.staff_notes is not None:
        req.staff_notes = payload.staff_notes

    if changes:
        record_audit_log(db, user.id, "guest_request.update", entity_type="guest_request", entity_id=str(req.id), details="; ".join(changes))
    if payload.status == "resolved":
        trigger_guest_request_resolved(db, req)
    db.commit()
    db.refresh(req)
    return req


@router.post("/guest-requests/{request_id}/feedback", response_model=GuestRequestOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
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


@staff_router.post("/{request_id}/approve-extension", response_model=GuestRequestOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
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

    # with_for_update locks this request (and, below, its booking) for the
    # rest of the transaction — same idiom as Phase 2's InventoryItem/
    # MaintenanceTicket/SecurityDeposit locks — so two near-simultaneous
    # approval clicks on the same request can't both pass the
    # not-already-resolved check below.
    req = db.get(GuestRequest, request_id, with_for_update=True)
    if req is None:
        raise HTTPException(404, "Request not found")
    if req.status == "resolved":
        raise HTTPException(409, "This extension request has already been resolved.")
    if req.request_type != "extend_stay":
        raise HTTPException(400, "This endpoint only applies to extend_stay requests")
    if req.requested_check_out_date is None:
        raise HTTPException(400, "This request has no requested check-out date")
    if req.booking_id is None:
        raise HTTPException(400, "This request isn't linked to a booking")

    booking = db.get(Booking, req.booking_id, with_for_update=True)
    if booking is None:
        raise HTTPException(404, "Linked booking not found")
    if booking.status not in ("confirmed", "checked_in"):
        # Same terminal/active distinction bookings/router.py's
        # ALLOWED_TRANSITIONS already encodes (checked_out/cancelled/no_show
        # are all terminal) — extending a stay only makes sense while it's
        # still active or upcoming.
        raise HTTPException(409, f"Cannot extend a booking with status '{booking.status}'.")
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
