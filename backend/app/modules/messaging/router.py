import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.bookings.models import Booking, Guest
from app.modules.inquiries.models import Inquiry
from app.modules.messaging import service
from app.modules.messaging.models import Conversation, ConversationMessage
from app.modules.messaging.schemas import (
    BookingDetailsOut,
    ConfirmationPreviewOut,
    ConversationDetailOut,
    ConversationSummaryOut,
    DisplayStatusOut,
    MessageCreate,
    MessageOut,
)
from app.modules.properties.models import Unit
from app.modules.public_booking.models import BookingHold
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/messages", tags=["messaging"])

# Every messaging endpoint is readable by admin/manager/staff alike — this
# is an internal operations tool, not a guest-facing one; no role here sees
# anything a Booking/Guest/Inquiry endpoint wouldn't already show them.
STAFF_ROLES = ("admin", "manager", "staff")

FILTERS = (
    "all", "unread", "enquiry", "booking_request", "payment_pending",
    "upcoming_checkin", "confirmed", "checked_in", "checked_out", "cancelled",
)


def _load_linked(db: Session, convo: Conversation) -> tuple[Booking | None, BookingHold | None, Inquiry | None, Guest | None]:
    booking = db.get(Booking, convo.booking_id) if convo.booking_id else None
    hold = db.get(BookingHold, convo.hold_id) if convo.hold_id else None
    inquiry = db.get(Inquiry, convo.inquiry_id) if convo.inquiry_id else None
    guest = db.get(Guest, convo.guest_id) if convo.guest_id else None
    return booking, hold, inquiry, guest


def _summary(db: Session, convo: Conversation) -> ConversationSummaryOut:
    booking, hold, inquiry, guest = _load_linked(db, convo)

    payment_status = None
    unit_name = None
    booking_reference = None
    if booking is not None:
        payment_status = service.payment_summary(db, booking)["payment_status"]
        unit = db.get(Unit, booking.unit_id) if booking.unit_id else None
        unit_name = unit.name if unit else None
        booking_reference = service.booking_reference(booking)
        guest = guest or booking.guest
    elif hold is not None:
        unit = db.get(Unit, hold.unit_id) if hold.unit_id else None
        unit_name = unit.name if unit else None
    elif inquiry is not None:
        unit_name = inquiry.flat_room_label

    display_status = service.derive_display_status(convo, booking, hold, inquiry, payment_status)
    stay = service.stay_fields(booking, hold)

    last_message = db.scalar(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == convo.id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(1)
    )

    guest_name = guest.name if guest else (hold.guest_name if hold else (inquiry.client_name if inquiry else None))
    guest_phone = guest.phone if guest else (hold.guest_phone if hold else (inquiry.client_phone if inquiry else None))
    guest_email = guest.email if guest else (hold.guest_email if hold else (inquiry.client_email if inquiry else None))

    return ConversationSummaryOut(
        id=convo.id,
        guest_name=guest_name,
        guest_phone=guest_phone,
        guest_email=guest_email,
        unit_name=unit_name,
        unit_code=None,
        check_in=stay["check_in"],
        check_out=stay["check_out"],
        nights=(stay["check_out"] - stay["check_in"]).days if stay["check_in"] and stay["check_out"] else None,
        guests=stay["guests"],
        booking_reference=booking_reference,
        display_status=DisplayStatusOut(**display_status),
        last_message_preview=(last_message.body[:140] if last_message else None),
        last_message_at=convo.last_message_at,
        unread_count=service.unread_count(db, convo.id),
        created_at=convo.created_at,
    )


def _matches_filter(summary: ConversationSummaryOut, filter_key: str) -> bool:
    label = summary.display_status.label
    if filter_key == "all":
        return True
    if filter_key == "unread":
        return summary.unread_count > 0
    if filter_key == "enquiry":
        return label == "ENQUIRY"
    if filter_key == "booking_request":
        return label == "BOOKING REQUEST"
    if filter_key == "payment_pending":
        return label == "PAYMENT PENDING"
    if filter_key == "upcoming_checkin":
        return label == "UPCOMING CHECK-IN"
    if filter_key == "confirmed":
        return label == "CONFIRMED"
    if filter_key == "checked_in":
        return label == "CHECKED-IN"
    if filter_key == "checked_out":
        return label == "CHECKED-OUT"
    if filter_key == "cancelled":
        return label == "CANCELLED"
    return True


def _matches_search(summary: ConversationSummaryOut, q: str) -> bool:
    q = q.strip().lower()
    if not q:
        return True
    haystack = " ".join(
        str(v) for v in (
            summary.guest_name, summary.guest_phone, summary.guest_email,
            summary.unit_name, summary.booking_reference,
        ) if v
    ).lower()
    return q in haystack


@router.get("/conversations", response_model=list[ConversationSummaryOut], dependencies=[Depends(require_role(*STAFF_ROLES))])
def list_conversations(
    filter: str = Query("all", pattern="^(" + "|".join(FILTERS) + ")$"),
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    service.ensure_conversations_exist(db)
    conversations = db.scalars(select(Conversation).order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())).all()
    summaries = [_summary(db, c) for c in conversations]
    filtered = [s for s in summaries if _matches_filter(s, filter) and _matches_search(s, q)]
    return filtered[:limit]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut, dependencies=[Depends(require_role(*STAFF_ROLES))])
def get_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    summary = _summary(db, convo)
    messages = db.scalars(
        select(ConversationMessage).where(ConversationMessage.conversation_id == convo.id).order_by(ConversationMessage.created_at.asc())
    ).all()
    return ConversationDetailOut(
        **summary.model_dump(),
        booking_id=convo.booking_id, hold_id=convo.hold_id, inquiry_id=convo.inquiry_id, guest_id=convo.guest_id,
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut, status_code=201, dependencies=[Depends(require_role(*STAFF_ROLES))])
def post_message(conversation_id: uuid.UUID, payload: MessageCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    now = datetime.now(timezone.utc)
    message = ConversationMessage(
        conversation_id=convo.id,
        sender_type=payload.sender_type,
        sender_id=user.id if payload.sender_type == "staff" else None,
        sender_name=user.name if payload.sender_type == "staff" else None,
        body=payload.body,
        message_type=payload.message_type,
        created_at=now,
        # A staff-authored message has no "unread" concept — it's read_at
        # is set immediately since there's no recipient-side inbox state to
        # track. A guest message (even one staff is manually transcribing
        # after receiving it over phone/WhatsApp — see MessageCreate) must
        # stay genuinely unread: that unread badge is the whole point of
        # logging it — it's what tells staff (this one or another) there's
        # a guest message still needing a reply.
        read_at=now if payload.sender_type == "staff" else None,
    )
    db.add(message)
    convo.last_message_at = now
    convo.updated_at = now
    db.commit()
    db.refresh(message)
    return message


@router.post("/conversations/{conversation_id}/read", status_code=204, dependencies=[Depends(require_role(*STAFF_ROLES))])
def mark_read(conversation_id: uuid.UUID, db: Session = Depends(get_db)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    now = datetime.now(timezone.utc)
    unread = db.scalars(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == convo.id,
            ConversationMessage.sender_type == "guest",
            ConversationMessage.read_at.is_(None),
        )
    ).all()
    for m in unread:
        m.read_at = now
    db.commit()


@router.post("/conversations/{conversation_id}/unread", status_code=204, dependencies=[Depends(require_role(*STAFF_ROLES))])
def mark_unread(conversation_id: uuid.UUID, db: Session = Depends(get_db)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    latest_guest_message = db.scalar(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == convo.id, ConversationMessage.sender_type == "guest")
        .order_by(ConversationMessage.created_at.desc())
        .limit(1)
    )
    if latest_guest_message is not None:
        latest_guest_message.read_at = None
        db.commit()


@router.get("/conversations/{conversation_id}/booking-details", response_model=BookingDetailsOut, dependencies=[Depends(require_role(*STAFF_ROLES))])
def booking_details(conversation_id: uuid.UUID, db: Session = Depends(get_db)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    if convo.booking_id is None:
        raise HTTPException(404, "No booking linked to this conversation")
    booking = db.get(Booking, convo.booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    unit = db.get(Unit, booking.unit_id) if booking.unit_id else None
    guest = booking.guest
    payments = service.payment_summary(db, booking)
    display_status = service.derive_display_status(convo, booking, None, None, payments["payment_status"])

    return BookingDetailsOut(
        booking_reference=service.booking_reference(booking),
        booking_id=booking.id,
        guest_name=guest.name if guest else "-",
        guest_phone=guest.phone if guest else None,
        guest_email=guest.email if guest else None,
        unit_name=unit.name if unit else None,
        check_in=booking.check_in,
        check_out=booking.check_out,
        checkin_time=payments["checkin_time"],
        checkout_time=payments["checkout_time"],
        nights=payments["nights"],
        guests=booking.adults + booking.children,
        booking_status=booking.status,
        display_status=DisplayStatusOut(**display_status),
        payment_status=payments["payment_status"],
        total_amount=payments["total_amount"],
        paid_amount=payments["paid_amount"],
        balance_due=payments["balance_due"],
    )


@router.post("/conversations/{conversation_id}/confirmation-preview", response_model=ConfirmationPreviewOut, dependencies=[Depends(require_role(*STAFF_ROLES))])
def confirmation_preview(conversation_id: uuid.UUID, db: Session = Depends(get_db)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    if convo.booking_id is None:
        raise HTTPException(404, "No booking linked to this conversation — nothing to preview yet")
    booking = db.get(Booking, convo.booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    rendered = service.payment_summary(db, booking)["rendered"]
    return ConfirmationPreviewOut(subject=rendered["subject"], body=rendered["body"], html_body=rendered["html_body"])
