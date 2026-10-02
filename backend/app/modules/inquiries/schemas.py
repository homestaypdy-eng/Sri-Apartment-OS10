import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class InquiryCreate(BaseModel):
    client_name: str
    client_phone: str
    client_email: str | None = None
    client_address: str | None = None

    unit_id: uuid.UUID | None = None
    flat_room_label: str
    num_guests: int = Field(default=1, ge=1)
    check_in_date: date
    check_in_time: str = "12:30 PM"
    check_out_date: date
    check_out_time: str = "11:00 AM"
    tariff_type: str = "Room Tariff"

    rate_per_night: float | None = Field(default=None, gt=0)  # omit to auto-calculate from the unit's pricing rules
    extra_charges: float = 0
    discount: float = 0
    advance_amount: float = Field(default=0, ge=0)

    valid_upto: date | None = None  # defaults to check_in_date if not given
    payment_mode: str = "Online / Bank Transfer"
    override_reason: str | None = None  # required in practice when rate_per_night is manually given alongside a linked unit — see router
    source: str = "phone"
    priority: str = "medium"
    assigned_to: uuid.UUID | None = None

    @field_validator("check_out_date")
    @classmethod
    def checkout_after_checkin(cls, v, info):
        check_in = info.data.get("check_in_date")
        if check_in and v <= check_in:
            raise ValueError("check_out_date must be after check_in_date")
        return v


class InquiryUpdate(BaseModel):
    status: str | None = None
    advance_amount: float | None = None
    lead_status: str | None = None
    next_follow_up_date: date | None = None
    follow_up_notes: str | None = None
    priority: str | None = None
    assigned_to: uuid.UUID | None = None
    lost_reason: str | None = None

    # Full-edit fields — only accepted while the inquiry has NOT yet been
    # converted to a booking (see router: editing is rejected with 409 once
    # booking_id is set, so a finalized booking's paperwork can't silently
    # drift from what the guest actually agreed to and paid for).
    client_name: str | None = None
    client_phone: str | None = None
    client_email: str | None = None
    client_address: str | None = None
    unit_id: uuid.UUID | None = None
    flat_room_label: str | None = None
    num_guests: int | None = Field(default=None, ge=1)
    check_in_date: date | None = None
    check_in_time: str | None = None
    check_out_date: date | None = None
    check_out_time: str | None = None
    tariff_type: str | None = None
    rate_per_night: float | None = Field(default=None, gt=0)  # omit to re-auto-calculate from the unit's pricing rules
    extra_charges: float | None = None
    discount: float | None = None
    valid_upto: date | None = None
    payment_mode: str | None = None
    override_reason: str | None = None


class InquiryOut(BaseModel):
    id: uuid.UUID
    offer_no: str
    offer_date: date
    valid_upto: date

    client_name: str
    client_phone: str
    client_email: str | None
    client_address: str | None

    unit_id: uuid.UUID | None
    flat_room_label: str
    num_guests: int
    check_in_date: date
    check_in_time: str
    check_out_date: date
    check_out_time: str
    tariff_type: str

    rate_per_night: float
    nights: int
    extra_charges: float
    discount: float
    taxable_value: float
    gst_rate_percent: float
    cgst: float
    sgst: float
    total_amount: float
    rounded_off: float
    grand_total: float

    advance_amount: float
    balance_amount: float
    payment_mode: str

    status: str
    booking_id: uuid.UUID | None
    guest_id: uuid.UUID | None
    created_at: datetime

    lead_status: str
    last_contacted_at: datetime | None
    next_follow_up_date: date | None
    follow_up_notes: str | None
    is_expired: bool
    pricing_source: str
    rate_breakdown: str | None
    source: str
    priority: str
    assigned_to: uuid.UUID | None
    lost_reason: str | None

    class Config:
        from_attributes = True


class ConvertToBookingRequest(BaseModel):
    unit_id: uuid.UUID | None = None  # required only if the inquiry wasn't already linked to a unit


class ConvertToBookingResponse(BaseModel):
    inquiry: InquiryOut
    booking_id: uuid.UUID
    guest_id: uuid.UUID
    payment_id: uuid.UUID | None
    guest_reused: bool


class SendEmailRequest(BaseModel):
    to_email: str | None = None  # defaults to inquiry.client_email if not given


class SendWhatsAppResponse(BaseModel):
    whatsapp_url: str
    message_preview: str


class SendEmailResponse(BaseModel):
    sent: bool
    to_email: str


class CommunicationOut(BaseModel):
    id: uuid.UUID
    inquiry_id: uuid.UUID
    channel: str
    sent_to: str
    success: bool
    error_message: str | None
    sent_at: datetime

    class Config:
        from_attributes = True


class SalesFunnelStage(BaseModel):
    lead_status: str
    count: int


class SalesFunnelReport(BaseModel):
    date_from: date
    date_to: date
    total_inquiries: int
    stages: list[SalesFunnelStage]
    converted_count: int
    lost_count: int
    conversion_rate_percent: float
    total_quoted_value: float
    total_converted_value: float


class InquiryAttachmentOut(BaseModel):
    id: uuid.UUID
    inquiry_id: uuid.UUID
    filename: str
    content_type: str
    uploaded_at: datetime

    class Config:
        from_attributes = True
