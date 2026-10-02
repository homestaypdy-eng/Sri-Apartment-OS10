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
    # Identity Verification, read-only — entirely separate from this
    # session's own login (which only ever proved control of the
    # magic-link's delivery channel; see get_current_guest). Never implies
    # "identity verified" just because this profile call succeeded.
    identity_status: str = "not_required"

    class Config:
        from_attributes = True


class GuestIdentityStatusOut(BaseModel):
    """
    Guest-facing Identity Verification view — deliberately narrower than
    the OS10 staff-side GuestOut: never exposes identity_verified_by (an
    internal user id) or the admin's raw identity_rejection_reason
    (section 21 — an internal review note isn't guest-facing by design;
    a rejected guest instead gets `message`, a fixed, generic string).
    """
    status: str
    document_type: str | None = None  # type of the most recently uploaded document, if any
    document_id: uuid.UUID | None = None  # caller's own document, for a later submission request
    verified_at: datetime | None = None
    message: str | None = None  # set only when status == "rejected"


class GuestIdentityDocumentOut(BaseModel):
    id: uuid.UUID
    document_type: str
    filename: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class GuestIdentitySubmitRequest(BaseModel):
    document_id: uuid.UUID


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


class GuestRequestStaffCreate(BaseModel):
    """Staff logging a request on a guest's behalf (phone call, in person)
    — booking_id is required rather than optional, since staff identify the
    guest via their current stay, not their own portal session."""
    booking_id: uuid.UUID
    request_type: str
    description: str
    priority: str = "medium"
    assigned_to: uuid.UUID | None = None


class GuestRequestStaffUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_to: uuid.UUID | None = None
    staff_notes: str | None = None


class GuestFeedbackCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str | None = None
