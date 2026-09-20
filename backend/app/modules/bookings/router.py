import os
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.bookings.models import BOOKING_STATUSES, Booking, BookingAttachment, Guest, GuestDocument
from app.modules.bookings.schemas import (
    BookingArchiveRequest,
    BookingAttachmentOut,
    BookingCreate,
    BookingOut,
    BookingUpdate,
    GuestCreate,
    GuestDocumentOut,
    GuestHistory,
    GuestMergeRequest,
    GuestMergeResult,
    GuestOut,
    GuestSegment,
    GuestUpdate,
)
from app.modules.finance.models import OTACommission, SecurityDeposit
from app.modules.housekeeping.models import HousekeepingTask
from app.modules.payments.models import Invoice, Payment
from app.modules.portal.auth_models import IDENTITY_CHANNELS, GuestIdentity
from app.modules.portal.auth_schemas import GuestIdentityOut, LinkGuestIdentityRequest
from app.modules.portal.auth_service import normalize_email, normalize_phone
from app.modules.properties.models import Property, Unit
from app.shared.availability import unit_is_available, units_availability
from app.shared.file_upload import read_and_validate_upload
from app.shared.rbac import get_current_user, require_role

ATTACHMENT_STORAGE_DIR = os.environ.get("BOOKING_ATTACHMENT_STORAGE_DIR", "/tmp/sri_apartment_attachments")
os.makedirs(ATTACHMENT_STORAGE_DIR, exist_ok=True)
GUEST_DOCUMENT_STORAGE_DIR = os.environ.get("GUEST_DOCUMENT_STORAGE_DIR", "/tmp/sri_apartment_guest_documents")
os.makedirs(GUEST_DOCUMENT_STORAGE_DIR, exist_ok=True)

router = APIRouter(prefix="/api/v1", tags=["bookings"])

# Allowed status transitions — enforced server-side so the UI can't push a
# booking into an invalid state (e.g. checked_out straight from inquiry).
ALLOWED_TRANSITIONS = {
    "inquiry": {"confirmed", "cancelled"},
    "confirmed": {"checked_in", "cancelled", "no_show"},
    "checked_in": {"checked_out"},
    "checked_out": set(),
    "cancelled": set(),
    "no_show": set(),
}

# Fields that feed directly into GST/total calculation at invoice-generation
# time (see payments/service.py calculate_gst, which uses booking.rate_applied
# and the check_in/check_out-derived night count). Once an Invoice row exists
# for a booking, changing any of these would silently desynchronize the
# invoice from the booking it was generated from — blocked in update_booking.
INVOICE_IMPACTING_FIELDS = {"check_in", "check_out", "unit_id", "rate_applied"}


@router.post("/guests", response_model=GuestOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def create_guest(payload: GuestCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    guest = Guest(**payload.model_dump())
    db.add(guest)
    db.flush()
    record_audit_log(db, user.id, "guest.create", entity_type="guest", entity_id=str(guest.id), details=f"{payload.name} ({payload.phone})")
    db.commit()
    db.refresh(guest)
    return guest


@router.patch("/guests/{guest_id}", response_model=GuestOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_guest(guest_id: uuid.UUID, payload: GuestUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    guest = db.get(Guest, guest_id)
    if guest is None:
        raise HTTPException(404, "Guest not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update were provided")
    for field, value in changes.items():
        setattr(guest, field, value)
    record_audit_log(db, user.id, "guest.update", entity_type="guest", entity_id=str(guest.id), details=f"Updated fields: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(guest)
    return guest


@router.post("/guests/{guest_id}/documents", response_model=GuestDocumentOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
async def upload_guest_document(
    guest_id: uuid.UUID,
    document_type: str,
    document_number: str | None = None,
    side: str = "front",
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    guest = db.get(Guest, guest_id)
    if guest is None:
        raise HTTPException(404, "Guest not found")
    if document_type not in ("aadhaar", "pan", "passport", "driving_license", "other"):
        raise HTTPException(400, "Invalid document_type")

    contents = await read_and_validate_upload(file)
    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{guest_id}_{document_type}_{side}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(GUEST_DOCUMENT_STORAGE_DIR, stored_name)
    with open(stored_path, "wb") as f:
        f.write(contents)

    doc = GuestDocument(
        guest_id=guest_id, document_type=document_type, document_number=document_number,
        filename=file.filename or stored_name, content_type=file.content_type or "application/octet-stream",
        file_path=stored_path, side=side, uploaded_by=user.id,
    )
    db.add(doc)
    record_audit_log(db, user.id, "guest.document_upload", entity_type="guest", entity_id=str(guest_id), details=f"{document_type} ({side}): {doc.filename}")
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/guests/{guest_id}/documents", response_model=list[GuestDocumentOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_guest_documents(guest_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(GuestDocument).where(GuestDocument.guest_id == guest_id)).all()


@router.get("/guests/{guest_id}/documents/{document_id}/download", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def download_guest_document(guest_id: uuid.UUID, document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = db.get(GuestDocument, document_id)
    if doc is None or doc.guest_id != guest_id:
        raise HTTPException(404, "Document not found")
    if not os.path.exists(doc.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(doc.file_path, media_type=doc.content_type, filename=doc.filename)


@router.delete("/guests/{guest_id}/documents/{document_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def delete_guest_document(guest_id: uuid.UUID, document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = db.get(GuestDocument, document_id)
    if doc is None or doc.guest_id != guest_id:
        raise HTTPException(404, "Document not found")
    record_audit_log(db, user.id, "guest.document_delete", entity_type="guest", entity_id=str(guest_id), details=f"Deleted: {doc.filename}")
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    db.delete(doc)
    db.commit()


@router.post("/guests/merge", response_model=GuestMergeResult, dependencies=[Depends(require_role("admin"))])
def merge_guests(payload: GuestMergeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Merges a duplicate guest record into a primary one. Does NOT delete
    the duplicate's row (which would either cascade-fail against its own
    real booking/payment history, or silently orphan it) — instead,
    re-points every booking from the duplicate to the primary, and marks
    the duplicate as merged via merged_into_guest_id so it's excluded from
    normal guest search/listing but its own history stays intact and
    traceable.
    """
    if payload.primary_guest_id == payload.duplicate_guest_id:
        raise HTTPException(400, "primary_guest_id and duplicate_guest_id must be different")

    primary = db.get(Guest, payload.primary_guest_id)
    duplicate = db.get(Guest, payload.duplicate_guest_id)
    if primary is None or duplicate is None:
        raise HTTPException(404, "One or both guests not found")
    if duplicate.merged_into_guest_id is not None:
        raise HTTPException(409, "This guest has already been merged into another record")

    bookings = db.scalars(select(Booking).where(Booking.guest_id == payload.duplicate_guest_id)).all()
    for b in bookings:
        b.guest_id = payload.primary_guest_id

    duplicate.merged_into_guest_id = payload.primary_guest_id
    record_audit_log(
        db, user.id, "guest.merge", entity_type="guest", entity_id=str(payload.duplicate_guest_id),
        details=f"Merged into {payload.primary_guest_id}, {len(bookings)} booking(s) reassigned",
    )
    db.commit()
    return {"primary_guest_id": payload.primary_guest_id, "duplicate_guest_id": payload.duplicate_guest_id, "bookings_reassigned": len(bookings)}


@router.post("/guests/{guest_id}/link-identity", response_model=GuestIdentityOut, dependencies=[Depends(require_role("admin"))])
def link_guest_identity(guest_id: uuid.UUID, payload: LinkGuestIdentityRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Phase 2A guest auth hardening: the staff-assisted path for a guest who
    OTP-verified (phone or email) before any Guest record existed to
    auto-link to. Without this, an unlinked GuestIdentity has no way to
    ever gain booking access — get_current_guest correctly 401s it
    forever (see auth_router.py/auth_email_router.py's verify-otp: "no
    data leak even though there's no recovery path yet — that's later-
    phase work"). This endpoint IS that later-phase work.

    admin-only, same restriction tier as /guests/merge just above — both
    endpoints change which guest a piece of identity/booking access
    resolves to, the most sensitive class of guest-data operation in this
    router (staff/manager can create and edit guests, but not repoint who
    "owns" existing access).

    Deliberately takes channel + external_id (what a guest would read out
    over a support call), not a raw identity_id — there's no admin UI
    listing GuestIdentity rows by UUID for staff to look one up from.
    """
    if payload.channel not in IDENTITY_CHANNELS:
        raise HTTPException(400, f"Unknown channel '{payload.channel}'")

    guest = db.get(Guest, guest_id)
    if guest is None:
        raise HTTPException(404, "Guest not found")

    if payload.channel == "phone":
        normalized = normalize_phone(payload.external_id)
    elif payload.channel == "email":
        normalized = normalize_email(payload.external_id)
    else:
        normalized = payload.external_id.strip()

    identity = db.scalar(select(GuestIdentity).where(GuestIdentity.channel == payload.channel, GuestIdentity.external_id == normalized))
    if identity is None:
        raise HTTPException(404, "No guest identity found for this channel/identifier — the guest may not have completed OTP verification yet")

    # Prevent linking the same identity to multiple guests: a currently-
    # unlinked identity may be linked; one already linked to ANY guest
    # (including this same one) is rejected outright rather than silently
    # re-linked, so this action is never accidentally a no-op or a quiet
    # transfer of access from one guest to another.
    if identity.guest_id is not None:
        if identity.guest_id == guest_id:
            raise HTTPException(409, "This identity is already linked to this guest")
        raise HTTPException(409, "This identity is already linked to a different guest")

    identity.guest_id = guest_id
    identity.linked_at = datetime.now(timezone.utc)

    record_audit_log(
        db, user.id, "guest_identity.linked",
        entity_type="guest_identity", entity_id=str(identity.id),
        details=f"Linked {identity.channel} identity ({normalized}) to guest {guest.name} ({guest.id})",
    )
    db.commit()
    db.refresh(identity)
    return identity


@router.get("/guests", response_model=list[GuestOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_guests(
    search: str | None = Query(None, description="Matches name, phone, or email"),
    ids: str | None = Query(None, description="Comma-separated guest IDs — returns exactly these guests, ignoring pagination/sort. Used when a page needs to resolve names for a specific, already-known set of guests (e.g. Guest Requests) without risking the default page-size window silently excluding some of them."),
    include_merged: bool = Query(False, description="Include guests that have been merged into another record"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if ids:
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        return db.scalars(select(Guest).where(Guest.id.in_(id_list))).all()

    query = select(Guest)
    if not include_merged:
        query = query.where(Guest.merged_into_guest_id.is_(None))
    if search:
        query = query.where(
            (Guest.name.ilike(f"%{search}%")) | (Guest.phone.ilike(f"%{search}%")) | (Guest.email.ilike(f"%{search}%"))
        )
    query = query.order_by(Guest.name).offset(offset).limit(limit)
    return db.scalars(query).all()


_GUEST_SEGMENT_SORT_KEYS = ("name", "total_bookings", "total_spent", "last_stay", "segment", "source")


@router.get("/guests/segments", response_model=list[GuestSegment], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_guest_segments(
    search: str | None = Query(None, description="Matches name, phone, or email"),
    ids: str | None = Query(None, description="Comma-separated guest IDs — returns exactly these guests' segmentation stats, ignoring filter/sort/pagination, same convention as GET /guests"),
    include_merged: bool = Query(False),
    segment: str | None = Query(None, description="Filter: 'new' or 'repeat'"),
    source: str | None = Query(None, description="Filter: exact match on latest_source"),
    sort_by: str = Query("name", description="name | total_bookings | total_spent | last_stay | segment | source"),
    sort_dir: str = Query("asc", description="asc | desc"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Batched guest-list segmentation: total_bookings/is_repeat_guest/
    total_spent reuse the exact same definitions as get_guest_history()
    below (ALL bookings regardless of status; total_spent = payments
    actually received) — deliberately NOT reimplemented, so the list and
    the per-guest detail view can never disagree about the same guest.

    Unlike GET /guests, which paginates in SQL before doing any work, this
    endpoint must aggregate across the full search/ids-matched guest set
    BEFORE sorting/filtering/paginating, since sorting by an aggregate
    (e.g. total_spent) is only possible once every matching guest's stats
    are known. It stays N+1-free (exactly 3 queries total: guests, then
    one IN-query for their bookings, one IN-query for those bookings'
    payments — the same batching pattern already used by the "guests"
    report export in export_report() below), but the candidate guest set
    itself is bounded only by `search`/`ids`, not by `limit`/`offset` — an
    intentional tradeoff for this product's guest-list scale, matching
    every other guest export/report in this module.
    """
    if sort_by not in _GUEST_SEGMENT_SORT_KEYS:
        raise HTTPException(400, f"Invalid sort_by '{sort_by}'. Must be one of {_GUEST_SEGMENT_SORT_KEYS}")
    if sort_dir not in ("asc", "desc"):
        raise HTTPException(400, "sort_dir must be 'asc' or 'desc'")
    if segment is not None and segment not in ("new", "repeat"):
        raise HTTPException(400, "segment must be 'new' or 'repeat'")

    if ids:
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        guests = db.scalars(select(Guest).where(Guest.id.in_(id_list))).all()
    else:
        query = select(Guest)
        if not include_merged:
            query = query.where(Guest.merged_into_guest_id.is_(None))
        if search:
            query = query.where(
                (Guest.name.ilike(f"%{search}%")) | (Guest.phone.ilike(f"%{search}%")) | (Guest.email.ilike(f"%{search}%"))
            )
        guests = db.scalars(query).all()

    guest_ids = [g.id for g in guests]
    bookings_by_guest: dict = defaultdict(list)
    if guest_ids:
        for b in db.scalars(select(Booking).where(Booking.guest_id.in_(guest_ids))).all():
            bookings_by_guest[b.guest_id].append(b)
    booking_ids = [b.id for blist in bookings_by_guest.values() for b in blist]
    spend_by_booking: dict = defaultdict(float)
    if booking_ids:
        for p in db.scalars(select(Payment).where(Payment.booking_id.in_(booking_ids))).all():
            spend_by_booking[p.booking_id] += float(p.amount)

    rows = []
    for g in guests:
        g_bookings = bookings_by_guest.get(g.id, [])
        total_bookings = len(g_bookings)
        is_repeat = total_bookings > 1  # same rule as GuestHistory.is_repeat_guest
        total_spent = round(sum(spend_by_booking.get(b.id, 0.0) for b in g_bookings), 2)
        latest = max(g_bookings, key=lambda b: b.check_in) if g_bookings else None
        rows.append({
            "guest_id": g.id, "name": g.name, "phone": g.phone, "email": g.email,
            "is_vip": g.is_vip, "is_blacklisted": g.is_blacklisted,
            "is_repeat_guest": is_repeat, "total_bookings": total_bookings, "total_spent": total_spent,
            "latest_source": latest.source if latest else None,
            "last_stay": latest.check_in if latest else None,
        })

    if ids:
        # Same convention as GET /guests: an explicit id set is returned
        # exactly as-is, ignoring filter/sort/pagination.
        return rows

    if segment == "new":
        rows = [r for r in rows if not r["is_repeat_guest"]]
    elif segment == "repeat":
        rows = [r for r in rows if r["is_repeat_guest"]]
    if source:
        rows = [r for r in rows if r["latest_source"] == source]

    reverse = sort_dir == "desc"
    if sort_by == "name":
        rows.sort(key=lambda r: r["name"].lower(), reverse=reverse)
    elif sort_by == "total_bookings":
        rows.sort(key=lambda r: r["total_bookings"], reverse=reverse)
    elif sort_by == "total_spent":
        rows.sort(key=lambda r: r["total_spent"], reverse=reverse)
    elif sort_by == "last_stay":
        rows.sort(key=lambda r: (r["last_stay"] is not None, r["last_stay"]), reverse=reverse)
    elif sort_by == "segment":
        rows.sort(key=lambda r: r["is_repeat_guest"], reverse=reverse)
    elif sort_by == "source":
        rows.sort(key=lambda r: (r["latest_source"] or ""), reverse=reverse)

    return rows[offset: offset + limit]


@router.get("/guests/{guest_id}/history", response_model=GuestHistory, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_guest_history(guest_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    A single consolidated view of everything tied to one guest — every
    booking (with what they paid and their invoice number if one exists),
    and every service request/complaint they've raised. Reads from
    existing tables only; nothing new is recorded here.
    """
    from app.modules.payments.models import Invoice
    from app.modules.portal.models import GuestRequest

    guest = db.get(Guest, guest_id)
    if guest is None:
        raise HTTPException(404, "Guest not found")

    bookings = db.scalars(select(Booking).where(Booking.guest_id == guest_id).order_by(Booking.check_in.desc())).all()
    booking_lines = []
    total_spent = 0.0

    if bookings:
        booking_ids = [b.id for b in bookings]
        unit_ids = {b.unit_id for b in bookings}
        units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()}

        payments_by_booking: dict = defaultdict(float)
        for p in db.scalars(select(Payment).where(Payment.booking_id.in_(booking_ids))).all():
            payments_by_booking[p.booking_id] += float(p.amount)

        invoices_by_booking = {i.booking_id: i for i in db.scalars(select(Invoice).where(Invoice.booking_id.in_(booking_ids))).all()}

        for b in bookings:
            unit = units_by_id.get(b.unit_id)
            paid = payments_by_booking.get(b.id, 0.0)
            invoice = invoices_by_booking.get(b.id)
            total_spent += paid
            booking_lines.append({
                "booking_id": b.id,
                "unit_name": unit.name if unit else "—",
                "check_in": b.check_in,
                "check_out": b.check_out,
                "status": b.status,
                "rate_applied": float(b.rate_applied),
                "total_paid": round(paid, 2),
                "invoice_no": invoice.invoice_no if invoice else None,
            })

    requests = db.scalars(select(GuestRequest).where(GuestRequest.guest_id == guest_id).order_by(GuestRequest.created_at.desc())).all()
    request_lines = [{"request_type": r.request_type, "description": r.description, "status": r.status, "created_at": r.created_at} for r in requests]

    return {
        "guest": guest,
        "total_bookings": len(bookings),
        "is_repeat_guest": len(bookings) > 1,
        "total_spent": round(total_spent, 2),
        "bookings": booking_lines,
        "requests": request_lines,
    }


@router.get(
    "/units/{unit_id}/availability",
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def check_availability(
    unit_id: uuid.UUID,
    check_in: date = Query(...),
    check_out: date = Query(...),
    db: Session = Depends(get_db),
):
    if check_out <= check_in:
        raise HTTPException(400, "check_out must be after check_in")

    # Unit status is authoritative — maintenance/blocked units are not available
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if unit.status != "available":
        return {"unit_id": unit_id, "check_in": check_in, "check_out": check_out, "available": False}
    if not unit.is_active or not unit.property.is_active:
        return {"unit_id": unit_id, "check_in": check_in, "check_out": check_out, "available": False}

    # Same shared, hold-aware check the public website uses (see
    # app/shared/availability.py) — a unit another guest is actively
    # paying for on the website must show unavailable here too, not just
    # a unit already confirmed-booked.
    available = unit_is_available(db, unit_id, check_in, check_out)

    return {"unit_id": unit_id, "check_in": check_in, "check_out": check_out, "available": available}


@router.get(
    "/units/availability",
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def check_all_units_availability(
    check_in: date = Query(...),
    check_out: date = Query(...),
    db: Session = Depends(get_db),
):
    """
    Batch counterpart to /units/{unit_id}/availability — for the "staff
    enters dates, sees which units are actually bookable" enquiry flow.
    One query for candidate units + one pair of queries for conflicts
    (see units_availability), not one round-trip per unit.
    """
    if check_out <= check_in:
        raise HTTPException(400, "check_out must be after check_in")

    units = db.scalars(select(Unit).where(Unit.status == "available", Unit.is_active.is_(True))).all()
    active_property_ids = {p.id for p in db.scalars(select(Property).where(Property.is_active.is_(True))).all()}
    candidate_units = [u for u in units if u.property_id in active_property_ids]

    availability = units_availability(db, [u.id for u in candidate_units], check_in, check_out)

    return [
        {"unit_id": u.id, "name": u.name, "unit_code": u.unit_code, "available": availability[u.id]}
        for u in candidate_units
    ]


@router.post(
    "/bookings",
    response_model=BookingOut,
    status_code=201,
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def create_booking(payload: BookingCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, payload.unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if unit.status != "available":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Unit is not available for booking (status: {unit.status}). "
            "Change the unit status to 'available' first.",
        )
    if not unit.is_active or not unit.property.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This unit (or its property) has been deactivated and is not available for new bookings")
    # The bookings.no_overlapping_bookings EXCLUDE constraint below only
    # compares against OTHER bookings — it has no way to see a website
    # guest's active payment hold on this same unit/range, since holds
    # live in a different table. Without this pre-check, staff could
    # confirm a manual booking here while a website payment for the same
    # dates is already in flight, and that guest's payment would then
    # fail to convert to a booking *after* their card was already charged.
    if not unit_is_available(db, payload.unit_id, payload.check_in, payload.check_out):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This unit is already booked, or a website payment for these dates is in progress, for an overlapping date range",
        )

    guest = db.get(Guest, payload.guest_id)
    if guest is None:
        raise HTTPException(404, "Guest not found")
    if guest.is_blacklisted:
        raise HTTPException(403, "Guest is blacklisted")

    booking = Booking(
        unit_id=payload.unit_id,
        guest_id=payload.guest_id,
        check_in=payload.check_in,
        check_out=payload.check_out,
        rate_applied=payload.rate_applied or float(unit.base_rate),
        source=payload.source,
        internal_notes=payload.internal_notes,
        special_requests=payload.special_requests,
        adults=payload.adults,
        children=payload.children,
        extra_bed=payload.extra_bed,
        arrival_time=payload.arrival_time,
        departure_time=payload.departure_time,
        status="confirmed",
    )
    db.add(booking)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        # This is the DB-level EXCLUDE constraint firing — the real
        # double-booking guard, not just the pre-flight availability check.
        if "no_overlapping_bookings" in str(e.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This unit is already booked for an overlapping date range",
            )
        raise
    record_audit_log(db, user.id, "booking.create", entity_type="booking", entity_id=str(booking.id), details=f"unit={payload.unit_id}, guest={payload.guest_id}, {payload.check_in}->{payload.check_out}")
    db.commit()
    db.refresh(booking)
    return booking


@router.get("/bookings", response_model=list[BookingOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_bookings(
    status: str | None = None,
    unit_id: uuid.UUID | None = None,
    guest_id: uuid.UUID | None = None,
    search: str | None = Query(None, description="Matches guest name or phone"),
    include_archived: bool = Query(False, description="Include archived bookings. Default excludes them, same as before archiving existed."),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Was previously a completely unbounded `SELECT *` with no filters —
    fine with a handful of test bookings, a real production-scale problem
    once a real business has been running for months. limit/offset default
    to values that don't change behavior for any reasonably-sized dataset
    (200 rows), so existing frontend calls with no params keep working
    exactly as before; they just now have an actual ceiling instead of an
    unbounded one.
    """
    query = select(Booking)
    if not include_archived:
        query = query.where(Booking.is_archived.is_(False))
    if status:
        query = query.where(Booking.status == status)
    if unit_id:
        query = query.where(Booking.unit_id == unit_id)
    if guest_id:
        query = query.where(Booking.guest_id == guest_id)
    if search:
        query = query.join(Guest, Booking.guest_id == Guest.id).where(
            (Guest.name.ilike(f"%{search}%")) | (Guest.phone.ilike(f"%{search}%"))
        )
    query = query.order_by(Booking.check_in.desc()).offset(offset).limit(limit)
    return db.scalars(query).all()


@router.patch("/bookings/{booking_id}/status", response_model=BookingOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_booking_status(booking_id: uuid.UUID, new_status: str = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    if new_status not in BOOKING_STATUSES:
        raise HTTPException(400, f"Invalid status '{new_status}'")

    allowed_next = ALLOWED_TRANSITIONS.get(booking.status, set())
    if new_status not in allowed_next:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot transition booking from '{booking.status}' to '{new_status}'. "
            f"Allowed next states: {sorted(allowed_next) or 'none (terminal state)'}",
        )

    old_status = booking.status
    booking.status = new_status

    if new_status == "checked_out":
        # Checkout automatically raises a cleaning task and marks the room
        # dirty — this is the real operational link between bookings and
        # housekeeping, not just two modules that happen to share a database.
        unit = db.get(Unit, booking.unit_id)
        unit.housekeeping_status = "dirty"
        db.add(
            HousekeepingTask(
                unit_id=booking.unit_id,
                task_type="checkout_clean",
                status="pending",
                notes=f"Auto-created on checkout of booking {booking.id}",
            )
        )

    record_audit_log(db, user.id, "booking.status_change", entity_type="booking", entity_id=str(booking.id), details=f"{old_status} -> {new_status}")
    db.commit()
    db.refresh(booking)
    return booking


@router.patch("/bookings/{booking_id}", response_model=BookingOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_booking(booking_id: uuid.UUID, payload: BookingUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Full reservation edit — guest, unit, dates, or rate. Changing dates or
    unit re-runs the exact same DB-level overlap-safety check (the EXCLUDE
    constraint) that a brand-new booking goes through; there's no separate,
    weaker edit path that could silently create a double-booking. Every
    change is recorded to the audit trail with a before/after summary.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    if payload.expected_updated_at is not None:
        # Optimistic concurrency: if what the editor loaded is no longer
        # what's in the database, someone else changed this booking in the
        # meantime — fail the edit rather than silently overwriting it.
        # Exact comparison (not a tolerance window) — the frontend echoes
        # back the identical ISO timestamp string it was given, which
        # round-trips through JSON/pydantic/psycopg without precision loss,
        # and two genuinely distinct edits can legitimately land within the
        # same second of each other.
        if booking.updated_at != payload.expected_updated_at:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This booking was changed by someone else since you opened it. Reload it and try again.",
            )

    changes = payload.model_dump(exclude_unset=True, exclude={"reason", "expected_updated_at"})
    if not changes:
        raise HTTPException(400, "No fields to update were provided")

    invoice_impacting = INVOICE_IMPACTING_FIELDS & changes.keys()
    if invoice_impacting:
        existing_invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking_id))
        if existing_invoice is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"This booking already has an issued invoice ({existing_invoice.invoice_no}) — "
                f"{', '.join(sorted(invoice_impacting))} cannot be changed since that would desynchronize "
                "the invoice from the booking. Void or credit-note the existing invoice first if this "
                "change is genuinely needed.",
            )

    if "guest_id" in changes:
        guest = db.get(Guest, changes["guest_id"])
        if guest is None:
            raise HTTPException(404, "Guest not found")
        if guest.is_blacklisted:
            raise HTTPException(403, "Guest is blacklisted")
    if "unit_id" in changes and db.get(Unit, changes["unit_id"]) is None:
        raise HTTPException(404, "Unit not found")

    new_check_in = changes.get("check_in", booking.check_in)
    new_check_out = changes.get("check_out", booking.check_out)
    if new_check_out <= new_check_in:
        raise HTTPException(400, "check_out must be after check_in")

    before_summary = f"unit={booking.unit_id}, guest={booking.guest_id}, {booking.check_in}->{booking.check_out}, rate={booking.rate_applied}"

    for field, value in changes.items():
        setattr(booking, field, value)

    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        if "no_overlapping_bookings" in str(e.orig):
            raise HTTPException(status.HTTP_409_CONFLICT, "This change would overlap with another booking for the same unit")
        raise

    after_summary = f"unit={booking.unit_id}, guest={booking.guest_id}, {booking.check_in}->{booking.check_out}, rate={booking.rate_applied}"
    record_audit_log(
        db, user.id, "booking.edit", entity_type="booking", entity_id=str(booking.id),
        details=f"Reason: {payload.reason or 'not given'}",
        previous_value=before_summary,
        new_value=after_summary,
    )
    db.commit()
    db.refresh(booking)
    return booking


@router.delete("/bookings/{booking_id}", status_code=204, dependencies=[Depends(require_role("admin"))])
def delete_booking(booking_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Genuinely deletes a booking — but only if no financial record
    (Payment, Invoice, SecurityDeposit, OTACommission) references it yet.
    A booking a guest has actually paid against must never silently
    disappear; use Cancel (status transition) for that instead. This is a
    real constraint enforced in code, not just documented — attempting to
    delete a paid booking returns 409 with an explicit explanation rather
    than cascading the delete through financial records.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    blockers = []
    if db.scalar(select(Payment).where(Payment.booking_id == booking_id)) is not None:
        blockers.append("payment")
    if db.scalar(select(Invoice).where(Invoice.booking_id == booking_id)) is not None:
        blockers.append("invoice")
    if db.scalar(select(SecurityDeposit).where(SecurityDeposit.booking_id == booking_id)) is not None:
        blockers.append("security deposit")
    if db.scalar(select(OTACommission).where(OTACommission.booking_id == booking_id)) is not None:
        blockers.append("OTA commission record")

    if blockers:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot delete this booking — it has a linked {', '.join(blockers)}. "
            f"Cancel the booking instead (status transition) to preserve the financial record.",
        )

    record_audit_log(
        db, user.id, "booking.delete", entity_type="booking", entity_id=str(booking.id),
        details=f"unit={booking.unit_id}, guest={booking.guest_id}, {booking.check_in}->{booking.check_out}, status was {booking.status}",
    )
    db.query(BookingAttachment).filter(BookingAttachment.booking_id == booking_id).delete()
    db.delete(booking)
    db.commit()


@router.post("/bookings/{booking_id}/archive", response_model=BookingOut, dependencies=[Depends(require_role("admin"))])
def archive_booking(booking_id: uuid.UUID, payload: BookingArchiveRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Administrative "remove from the active list" action — distinct from
    Cancel (a business status transition, see update_booking_status) and
    from the hard DELETE above (blocked whenever any financial record
    exists). Archiving never touches status, payments, invoices, GST, or
    audit history, and is always reversible via /restore. Repeating an
    archive request on an already-archived booking is a safe no-op — it
    returns the current state rather than erroring or double-logging.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    if booking.is_archived:
        return booking

    booking.is_archived = True
    booking.archived_at = datetime.now(timezone.utc)
    booking.archived_by = user.id
    booking.archive_reason = payload.reason
    record_audit_log(
        db, user.id, "booking.archive", entity_type="booking", entity_id=str(booking.id),
        details=f"Reason: {payload.reason or 'not given'} (status was {booking.status})",
    )
    db.commit()
    db.refresh(booking)
    return booking


@router.post("/bookings/{booking_id}/restore", response_model=BookingOut, dependencies=[Depends(require_role("admin"))])
def restore_booking(booking_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reverses archive_booking. Same idempotency rule: restoring a booking
    that isn't archived is a safe no-op, not an error."""
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    if not booking.is_archived:
        return booking

    booking.is_archived = False
    booking.archived_at = None
    booking.archived_by = None
    booking.archive_reason = None
    record_audit_log(db, user.id, "booking.restore", entity_type="booking", entity_id=str(booking.id), details="Restored from archive")
    db.commit()
    db.refresh(booking)
    return booking


@router.post("/bookings/{booking_id}/attachments", response_model=BookingAttachmentOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
async def upload_attachment(booking_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{booking_id}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(ATTACHMENT_STORAGE_DIR, stored_name)
    contents = await read_and_validate_upload(file)
    with open(stored_path, "wb") as f:
        f.write(contents)

    attachment = BookingAttachment(
        booking_id=booking_id,
        filename=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream",
        file_path=stored_path,
        uploaded_by=user.id,
    )
    db.add(attachment)
    record_audit_log(db, user.id, "booking.attachment_upload", entity_type="booking", entity_id=str(booking_id), details=f"Uploaded: {attachment.filename}")
    db.commit()
    db.refresh(attachment)
    return attachment


@router.get("/bookings/{booking_id}/attachments", response_model=list[BookingAttachmentOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_attachments(booking_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(BookingAttachment).where(BookingAttachment.booking_id == booking_id)).all()


@router.get("/bookings/{booking_id}/attachments/{attachment_id}/download", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def download_attachment(booking_id: uuid.UUID, attachment_id: uuid.UUID, db: Session = Depends(get_db)):
    attachment = db.get(BookingAttachment, attachment_id)
    if attachment is None or attachment.booking_id != booking_id:
        raise HTTPException(404, "Attachment not found")
    if not os.path.exists(attachment.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(attachment.file_path, media_type=attachment.content_type, filename=attachment.filename)


@router.delete("/bookings/{booking_id}/attachments/{attachment_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def delete_attachment(booking_id: uuid.UUID, attachment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    attachment = db.get(BookingAttachment, attachment_id)
    if attachment is None or attachment.booking_id != booking_id:
        raise HTTPException(404, "Attachment not found")
    record_audit_log(db, user.id, "booking.attachment_delete", entity_type="booking", entity_id=str(booking_id), details=f"Deleted: {attachment.filename}")
    if os.path.exists(attachment.file_path):
        os.remove(attachment.file_path)
    db.delete(attachment)
    db.commit()


@router.get("/bookings/{booking_id}/timeline", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_booking_timeline(booking_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Every audit-logged action against this specific booking, oldest
    first — creation, edits, status changes, attachment uploads/deletes,
    each with who did it, when, and (for edits) the structured previous/
    new value. This is a thin, purpose-specific view over the same
    AuditLog table /audit/logs reads from, filtered to one entity so
    staff can see a booking's history without needing admin-only access
    to the full audit log.
    """
    from app.modules.audit.models import AuditLog

    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    logs = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "booking", AuditLog.entity_id == str(booking_id))
        .order_by(AuditLog.created_at.asc())
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
            "previous_value": log.previous_value,
            "new_value": log.new_value,
            "created_at": log.created_at,
        })
    return {"booking_id": booking_id, "events": events}
