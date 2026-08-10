import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class GuestCreate(BaseModel):
    name: str
    phone: str
    email: str | None = None
    address: str | None = None
    id_proof_type: str | None = None
    id_proof_number: str | None = None
    id_proof_url: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    nationality: str | None = None
    occupation: str | None = None
    whatsapp_number: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    photo_url: str | None = None
    notes: str | None = None


class GuestUpdate(BaseModel):
    """All fields optional — only what's actually being changed needs to
    be sent. is_vip/is_blacklisted included here since there was
    previously no way to change either after creation at all."""
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    id_proof_type: str | None = None
    id_proof_number: str | None = None
    id_proof_url: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    nationality: str | None = None
    occupation: str | None = None
    whatsapp_number: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    photo_url: str | None = None
    notes: str | None = None
    is_vip: bool | None = None
    is_blacklisted: bool | None = None


class GuestOut(GuestCreate):
    id: uuid.UUID
    is_vip: bool = False
    is_blacklisted: bool = False
    merged_into_guest_id: uuid.UUID | None = None

    class Config:
        from_attributes = True


class GuestSegment(BaseModel):
    """
    One guest's list-level segmentation stats — total_bookings/is_repeat_guest/
    total_spent use the exact same definitions as GuestHistory above (ALL
    bookings regardless of status; total_spent is payments actually
    received, not gross booking value), so a guest never looks "repeat" in
    one view and "new" in the other. latest_source/last_stay come from the
    guest's most recent booking by check_in — "latest", not permanent,
    since source is recorded per-booking, not per-guest.
    """
    guest_id: uuid.UUID
    name: str
    phone: str
    email: str | None = None
    is_vip: bool
    is_blacklisted: bool
    is_repeat_guest: bool
    total_bookings: int
    total_spent: float
    latest_source: str | None = None
    last_stay: date | None = None


class GuestDocumentOut(BaseModel):
    id: uuid.UUID
    guest_id: uuid.UUID
    document_type: str
    document_number: str | None
    filename: str
    content_type: str
    side: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class GuestMergeRequest(BaseModel):
    primary_guest_id: uuid.UUID  # the guest record to keep
    duplicate_guest_id: uuid.UUID  # the guest record to merge away — all its bookings/payments get re-pointed to primary_guest_id


class GuestMergeResult(BaseModel):
    primary_guest_id: uuid.UUID
    duplicate_guest_id: uuid.UUID
    bookings_reassigned: int


class GuestHistoryBooking(BaseModel):
    booking_id: uuid.UUID
    unit_name: str
    check_in: date
    check_out: date
    status: str
    rate_applied: float
    total_paid: float
    invoice_no: str | None


class GuestHistoryRequest(BaseModel):
    request_type: str
    description: str
    status: str
    created_at: datetime


class GuestHistory(BaseModel):
    guest: GuestOut
    total_bookings: int
    is_repeat_guest: bool
    total_spent: float
    bookings: list[GuestHistoryBooking]
    requests: list[GuestHistoryRequest]


class UnitCreate(BaseModel):
    property_id: uuid.UUID
    name: str
    unit_type: str = "apartment"
    max_occupancy: int = 2
    base_rate: float = Field(gt=0)
    weekend_rate: float | None = Field(default=None, gt=0)
    unit_code: str | None = None
    floor: str | None = None
    flat_number: str | None = None
    amenities: str | None = None


class UnitUpdate(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    max_occupancy: int | None = Field(default=None, gt=0)
    base_rate: float | None = Field(default=None, gt=0)
    weekend_rate: float | None = Field(default=None, gt=0)
    status: str | None = None  # available / maintenance / blocked
    housekeeping_status: str | None = None
    unit_code: str | None = None
    floor: str | None = None
    flat_number: str | None = None
    amenities: str | None = None


class UnitOut(UnitCreate):
    id: uuid.UUID
    status: str
    housekeeping_status: str
    is_active: bool = True

    class Config:
        from_attributes = True


class BookingCreate(BaseModel):
    unit_id: uuid.UUID
    guest_id: uuid.UUID
    check_in: date
    check_out: date
    rate_applied: float | None = None  # falls back to unit.base_rate if not given
    source: str = "direct"
    internal_notes: str | None = None
    special_requests: str | None = None
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    extra_bed: bool = False
    arrival_time: str | None = None
    departure_time: str | None = None

    @field_validator("check_out")
    @classmethod
    def checkout_after_checkin(cls, v, info):
        check_in = info.data.get("check_in")
        if check_in and v <= check_in:
            raise ValueError("check_out must be after check_in")
        return v


class BookingUpdate(BaseModel):
    """
    All fields optional — only what's actually being changed needs to be
    sent. Changing check_in/check_out/unit_id re-triggers the same DB-level
    overlap-safety check (EXCLUDE constraint) that new bookings go
    through, via a normal commit — there is no separate/weaker edit path.
    """
    guest_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    check_in: date | None = None
    check_out: date | None = None
    rate_applied: float | None = None
    internal_notes: str | None = None
    special_requests: str | None = None
    adults: int | None = Field(default=None, ge=1)
    children: int | None = Field(default=None, ge=0)
    extra_bed: bool | None = None
    arrival_time: str | None = None
    departure_time: str | None = None
    reason: str | None = None  # optional free-text reason, logged to the audit trail


class BookingAttachmentOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    filename: str
    content_type: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class BookingOut(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    guest_id: uuid.UUID
    check_in: date
    check_out: date
    status: str
    source: str
    rate_applied: float
    internal_notes: str | None = None
    special_requests: str | None = None
    due_date: date | None = None
    adults: int = 1
    children: int = 0
    extra_bed: bool = False
    arrival_time: str | None = None
    departure_time: str | None = None

    class Config:
        from_attributes = True
