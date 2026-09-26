"""
Business logic for the OS10 Group Inbox: conversation provisioning, the
booking-lifecycle status derivation, and booking-details/confirmation
assembly. Kept separate from router.py so the derivation rules (the part
most likely to need adjusting later) aren't tangled up with HTTP concerns.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.bookings.models import Booking, Guest
from app.modules.inquiries.models import INQUIRY_STATUSES, Inquiry
from app.modules.messaging.models import Conversation, ConversationMessage
from app.modules.notifications.service import render_booking_confirmation_message
from app.modules.public_booking.models import BookingHold

DEFAULT_CHECKIN_TIME = "12:30 PM"
DEFAULT_CHECKOUT_TIME = "11:00 AM"

# Open/actionable enquiry states — a quotation that's already confirmed,
# expired or cancelled has moved past "enquiry" for inbox purposes even
# though the Inquiry row itself never disappears.
OPEN_INQUIRY_STATUSES = ("draft", "sent")
OPEN_HOLD_STATUSES = ("pending_payment", "paid")


def booking_reference(booking: Booking) -> str:
    """
    Short, human-readable display reference (e.g. "SA-2026-1F3A9C") shown
    everywhere in place of the raw UUID — computed, not stored. Deliberately
    NOT a new sequential-counter column: that would need a migration, a
    backfill for every pre-existing booking, and allocation-race handling
    for a purely cosmetic label, which is unnecessary complexity for what
    this needs to do (see the read-only inspection report). Derived
    directly from the booking's own immutable id + creation year, so it
    requires no schema change, is available for every booking including
    historical ones, and cannot collide in practice (16.7M combinations
    per year against this business's real volume). Booking.id itself is
    never changed or replaced.
    """
    year = booking.created_at.year if booking.created_at else date.today().year
    return f"SA-{year}-{str(booking.id)[:6].upper()}"


def derive_display_status(
    conversation: Conversation, booking: Booking | None, hold: BookingHold | None, inquiry: Inquiry | None,
    payment_status: str | None = None,
) -> dict:
    """
    The single place the UI's status badge is computed. Booking.status
    stays the sole authoritative booking-lifecycle field; BookingHold and
    Payment stay authoritative for their own concerns. This function only
    TRANSLATES those existing, real values into one of the display labels
    you asked for — it never stores a competing status anywhere.

    Priority: a linked Booking always wins (it's the most advanced stage
    the guest has reached), then a BookingHold (payment in progress, no
    Booking yet), then an Inquiry, else a generic new-enquiry default.
    """
    if booking is not None:
        if booking.status == "cancelled":
            return {"label": "CANCELLED", "tone": "red"}
        if booking.status == "checked_out":
            return {"label": "CHECKED-OUT", "tone": "slate"}
        if booking.status == "checked_in":
            return {"label": "CHECKED-IN", "tone": "cyan"}
        if booking.status == "no_show":
            return {"label": "NO-SHOW", "tone": "slate"}
        if booking.status == "inquiry":
            return {"label": "ENQUIRY", "tone": "amber"}
        # status == "confirmed" from here on.
        if payment_status is not None and payment_status not in ("PAID",):
            # A confirmed booking with an outstanding balance — real,
            # derived from actual Payment rows (see booking_details()).
            return {"label": "PAYMENT PENDING", "tone": "orange"}
        if booking.check_in >= date.today():
            return {"label": "UPCOMING CHECK-IN", "tone": "blue"}
        return {"label": "CONFIRMED", "tone": "green"}

    if hold is not None:
        if hold.status in ("expired", "cancelled"):
            return {"label": "CANCELLED", "tone": "red"}
        # pending_payment or the brief "paid-but-not-yet-converted" window.
        return {"label": "BOOKING REQUEST", "tone": "orange"}

    if inquiry is not None:
        if inquiry.status == "cancelled":
            return {"label": "CANCELLED", "tone": "red"}
        return {"label": "ENQUIRY", "tone": "amber"}

    return {"label": "ENQUIRY", "tone": "amber"}


def payment_summary(db: Session, booking: Booking) -> dict:
    """Total/paid/balance/payment-status — delegates entirely to the exact
    same computation the real guest confirmation and invoice use, so the
    Booking Details panel can never show a number the guest wasn't already
    told (see render_booking_confirmation_message -> ctx)."""
    rendered = render_booking_confirmation_message(db, booking)
    ctx = rendered["ctx"]
    return {
        "total_amount": ctx["total_amount"],
        "paid_amount": ctx["payment_received"],
        "balance_due": ctx["balance_due"],
        "payment_status": ctx["payment_status"],
        "checkin_time": ctx["checkin_time"],
        "checkout_time": ctx["checkout_time"],
        "nights": ctx["nights"],
        "rendered": rendered,
    }


def _touch(conversation: Conversation, when: datetime) -> None:
    conversation.last_message_at = when
    conversation.updated_at = when


def get_or_create_for_booking(db: Session, booking: Booking) -> Conversation:
    convo = db.scalar(select(Conversation).where(Conversation.booking_id == booking.id))
    if convo is not None:
        return convo
    convo = Conversation(booking_id=booking.id, guest_id=booking.guest_id, status="open")
    db.add(convo)
    db.flush()
    return convo


def get_or_create_for_hold(db: Session, hold: BookingHold) -> Conversation:
    convo = db.scalar(select(Conversation).where(Conversation.hold_id == hold.id))
    if convo is not None:
        return convo
    # BookingHold has no guest_id (guest info is inline, pre-conversion) —
    # opportunistically link an existing Guest by phone so the same person's
    # conversations stay connected once their hold converts to a Booking;
    # never create a new Guest row here (that's Booking-conversion's job).
    guest = db.scalar(select(Guest).where(Guest.phone == hold.guest_phone))
    convo = Conversation(hold_id=hold.id, guest_id=guest.id if guest else None, status="open")
    db.add(convo)
    db.flush()
    return convo


def get_or_create_for_inquiry(db: Session, inquiry: Inquiry) -> Conversation:
    convo = db.scalar(select(Conversation).where(Conversation.inquiry_id == inquiry.id))
    if convo is not None:
        return convo
    convo = Conversation(inquiry_id=inquiry.id, guest_id=inquiry.guest_id, status="open")
    db.add(convo)
    db.flush()
    return convo


def ensure_conversations_exist(db: Session) -> None:
    """
    Lazily provisions a Conversation for every real OS10 record that should
    have one and doesn't yet — every Booking, every open BookingHold, every
    open Inquiry. Idempotent (checked via the FK, not re-created). This is
    how the inbox is populated with real data with no separate "start a
    conversation" step and no fake seed rows: the first time the inbox is
    opened, it reflects exactly what's already in OS10.
    """
    linked_booking_ids = {row[0] for row in db.execute(select(Conversation.booking_id).where(Conversation.booking_id.is_not(None)))}
    for booking in db.scalars(select(Booking)).all():
        if booking.id not in linked_booking_ids:
            get_or_create_for_booking(db, booking)

    linked_hold_ids = {row[0] for row in db.execute(select(Conversation.hold_id).where(Conversation.hold_id.is_not(None)))}
    for hold in db.scalars(select(BookingHold).where(BookingHold.status.in_(OPEN_HOLD_STATUSES))).all():
        if hold.id not in linked_hold_ids:
            get_or_create_for_hold(db, hold)

    linked_inquiry_ids = {row[0] for row in db.execute(select(Conversation.inquiry_id).where(Conversation.inquiry_id.is_not(None)))}
    for inquiry in db.scalars(select(Inquiry).where(Inquiry.status.in_(OPEN_INQUIRY_STATUSES))).all():
        if inquiry.id not in linked_inquiry_ids:
            get_or_create_for_inquiry(db, inquiry)

    db.commit()


def unread_count(db: Session, conversation_id: uuid.UUID) -> int:
    return len(
        db.scalars(
            select(ConversationMessage.id).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.sender_type == "guest",
                ConversationMessage.read_at.is_(None),
            )
        ).all()
    )


def stay_fields(booking: Booking | None, hold: BookingHold | None) -> dict:
    """Best-effort check-in/check-out/guest-count for the conversation-list
    card — from whichever real record exists, never fabricated when
    neither does."""
    if booking is not None:
        return {"check_in": booking.check_in, "check_out": booking.check_out, "guests": booking.adults + booking.children}
    if hold is not None:
        return {"check_in": hold.check_in, "check_out": hold.check_out, "guests": hold.adults}
    return {"check_in": None, "check_out": None, "guests": None}
