import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class DisplayStatusOut(BaseModel):
    """A UI-facing status badge — always computed fresh from Booking /
    BookingHold / Inquiry (see service.derive_display_status), never
    stored. `label` is the exact text to show; `tone` selects the badge
    colour family (green/red/amber/orange/blue/neutral)."""

    label: str
    tone: str


class MessageOut(BaseModel):
    id: uuid.UUID
    sender_type: str
    sender_name: str | None
    body: str
    message_type: str
    created_at: datetime
    read_at: datetime | None

    class Config:
        from_attributes = True


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    # Almost always "staff" (the logged-in user typing a reply). "guest" is
    # for the one real, honest manual-logging case described in the report:
    # staff transcribing what a guest said over phone/WhatsApp, since no
    # automatic guest-message channel exists yet. "system" is not accepted
    # here — system_event rows are only ever written by OS10 itself.
    sender_type: str = Field(default="staff", pattern="^(staff|guest)$")
    message_type: str = Field(default="text", pattern="^(text|note)$")


class ConversationSummaryOut(BaseModel):
    id: uuid.UUID
    guest_name: str | None
    guest_phone: str | None
    guest_email: str | None
    unit_name: str | None
    unit_code: str | None
    check_in: date | None
    check_out: date | None
    nights: int | None
    guests: int | None
    booking_reference: str | None
    display_status: DisplayStatusOut
    last_message_preview: str | None
    last_message_at: datetime | None
    unread_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationDetailOut(ConversationSummaryOut):
    booking_id: uuid.UUID | None
    hold_id: uuid.UUID | None
    inquiry_id: uuid.UUID | None
    guest_id: uuid.UUID | None
    messages: list[MessageOut]


class BookingDetailsOut(BaseModel):
    """Everything the Booking Details panel shows — reuses the exact same
    total/paid/balance/payment-status computation the guest confirmation
    message and the real invoice already use (see
    notifications.service._booking_notification_context), so the numbers
    shown here can never disagree with what the guest was actually told."""

    booking_reference: str
    booking_id: uuid.UUID
    guest_name: str
    guest_phone: str | None
    guest_email: str | None
    unit_name: str | None
    check_in: date
    check_out: date
    checkin_time: str
    checkout_time: str
    nights: int
    guests: int
    booking_status: str
    display_status: DisplayStatusOut
    payment_status: str
    total_amount: float
    paid_amount: float
    balance_due: float


class ConfirmationPreviewOut(BaseModel):
    subject: str
    body: str
    html_body: str
