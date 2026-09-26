"""
OS10 Group Inbox — persistent staff/guest conversation model.

Genuinely new: no prior two-way conversation/message-thread model existed
anywhere in OS10 before this (confirmed by inspection — InquiryCommunication
is a one-directional quotation-send log, NotificationLog is a one-directional
system-send log, GuestRequest is a single support ticket, none stores an
ordered back-and-forth thread). Additive only: two brand-new tables, created
via the existing Base.metadata.create_all() bootstrap (see seed.py) — no
existing table, column, or row is touched.

A Conversation is deliberately NOT the source of truth for booking/payment
status — it only links to whichever of Booking / BookingHold / Inquiry
started it. The actual booking lifecycle stays entirely owned by
Booking.status (see messaging/service.py's derive_display_status), and
payment status stays entirely derived from Payment rows — this table adds
no competing status system.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

# Conversation.status is a THREAD-management state (open for follow-up vs
# archived) — unrelated to, and never a substitute for, the booking-lifecycle
# badge shown in the UI, which is always computed fresh from Booking/
# BookingHold/Inquiry (see service.derive_display_status).
CONVERSATION_STATUSES = ("open", "archived")
SENDER_TYPES = ("guest", "staff", "system")
# "text" = an ordinary message. "note" = an internal staff-only note (not
# something a guest ever said or received — useful for handoff context).
# "system_event" = an automatic timeline entry (e.g. "Booking confirmed"),
# rendered distinctly, never mistaken for something a person typed.
MESSAGE_TYPES = ("text", "note", "system_event")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (CheckConstraint(f"status IN {CONVERSATION_STATUSES}", name="ck_conversation_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Exactly one of these three is normally set — whichever OS10 record
    # this conversation is actually about. All nullable: a conversation
    # about a hold has no booking yet, one about a plain enquiry has
    # neither. Never fabricate a booking_id to fill this in.
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    hold_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("booking_holds.id"), nullable=True)
    inquiry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inquiries.id"), nullable=True)
    guest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    # Denormalized for cheap list-sorting (ORDER BY last_message_at DESC
    # without a join/subquery per row) — kept in sync by service.py
    # whenever a message is appended, same pattern as Booking.updated_at.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    booking = relationship("Booking")
    hold = relationship("BookingHold")
    inquiry = relationship("Inquiry")
    guest = relationship("Guest")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        "ConversationMessage", back_populates="conversation",
        order_by="ConversationMessage.created_at", cascade="all, delete-orphan",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint(f"sender_type IN {SENDER_TYPES}", name="ck_message_sender_type"),
        CheckConstraint(f"message_type IN {MESSAGE_TYPES}", name="ck_message_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # Only ever set for sender_type='staff' — the OS10 user who wrote it.
    sender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Denormalized display name, snapshotted at send time (guest name via
    # Conversation.guest normally covers the guest side; this exists so a
    # message still reads correctly even if that guest is later merged/
    # renamed, and so system_event rows can carry a label with no user).
    sender_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), default="text")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Set when staff has viewed a GUEST message (unread badge = count of
    # sender_type='guest' messages in a conversation with read_at IS NULL).
    # Staff's own messages never carry an unread state — there's no reason
    # to track whether staff "read" their own reply.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
    sender = relationship("User")
