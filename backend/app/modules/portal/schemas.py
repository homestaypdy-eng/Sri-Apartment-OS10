import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class RequestLoginRequest(BaseModel):
    phone: str


class RequestLoginResponse(BaseModel):
    sent: bool
    channel: str  # "email" | "none_available"
    # See router docstring: included directly here as a testability
    # concession, since this sandbox can't prove real email delivery.
    # A production build would NOT return this in the response.
    dev_magic_token: str | None = None


class VerifyLoginRequest(BaseModel):
    token: str


class GuestSessionToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GuestProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str
    email: str | None

    class Config:
        from_attributes = True


class GuestBookingOut(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    check_in: date
    check_out: date
    status: str
    rate_applied: float

    class Config:
        from_attributes = True


class GuestRequestCreate(BaseModel):
    booking_id: uuid.UUID | None = None
    description: str
    requested_check_out_date: date | None = None  # required for extend_stay


class GuestRequestOut(BaseModel):
    id: uuid.UUID
    guest_id: uuid.UUID
    booking_id: uuid.UUID | None
    request_type: str
    description: str
    requested_check_out_date: date | None
    status: str
    priority: str
    assigned_to: uuid.UUID | None
    staff_notes: str | None
    guest_feedback_rating: int | None
    guest_feedback_text: str | None
    created_at: datetime
    resolved_at: datetime | None

    class Config:
        from_attributes = True


class GuestRequestStaffUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_to: uuid.UUID | None = None
    staff_notes: str | None = None


class GuestFeedbackCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str | None = None
