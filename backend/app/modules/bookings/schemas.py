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
    # GST place-of-supply: 2-letter Indian state/UT GST code (e.g. "34" for
    # Puducherry). Optional — a guest with no state recorded keeps the
    # existing "unknown -> IGST" behavior in calculate_gst(), unchanged.
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
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
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
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
    # Identity Verification — read-only here; only ever changed via the
    # dedicated /identity/* endpoints below, never through PATCH /guests.
    # Deliberately separate from Account Verification (see Guest.identity_status's
    # own docstring in models.py) — this block means nothing about whether
    # this person's account/contact method was ever verified.
    identity_status: str = "not_required"
    identity_verified_at: datetime | None = None
    identity_verified_by: uuid.UUID | None = None
    identity_rejection_reason: str | None = None
    identity_verified_document_id: uuid.UUID | None = None

    class Config:
        from_attributes = True


class IdentityVerifyRequest(BaseModel):
    document_id: uuid.UUID  # which already-uploaded GuestDocument was reviewed and accepted


class IdentityRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class IdentityResetRequest(BaseModel):
    """Admin-triggered re-verification (section 22): document expired,
    guest information changed, or an admin otherwise wants a fresh
    review. Distinct from `reject` — this isn't "your submission was
    invalid," it's "please verify again." reason is required so the
    audit trail always explains why an already-verified guest was sent
    back to needing review."""
    reason: str = Field(min_length=1, max_length=500)


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
    # Both fields already existed here with defaults before Phase 1 — kept
    # optional (not required) so no existing API client that omits them
    # breaks; the Admin UI is expected to start sending them explicitly
    # from Phase 2 onward, but the API contract itself doesn't force that.
    unit_type: str = "apartment"
    max_occupancy: int = Field(default=2, gt=0)
    base_rate: float = Field(gt=0)
    weekend_rate: float | None = Field(default=None, gt=0)
    # Long-stay absolute nightly rate (see Unit model + pricing.py) — a
    # stay of long_stay_min_nights+ nights is billed at long_stay_rate for
    # every night, not a percentage discount. Concrete defaults (not None)
    # matching the column's own DB-level default, same reasoning as
    # unit_type/max_occupancy above: create_unit() below passes the full
    # model_dump() straight into Unit(...), and the column is NOT NULL, so
    # a None default here would try to insert an explicit null instead of
    # actually using the column default.
    long_stay_min_nights: int = Field(default=30, gt=0)
    long_stay_rate: float = Field(default=2000, gt=0)
    unit_code: str | None = None
    floor: str | None = None
    flat_number: str | None = None
    amenities: str | None = None
    description: str | None = None
    bedroom_count: int | None = Field(default=None, ge=0)
    bathroom_count: int | None = Field(default=None, ge=0)
    house_rules: str | None = None
    checkin_time: str | None = None
    checkout_time: str | None = None


class UnitUpdate(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    max_occupancy: int | None = Field(default=None, gt=0)
    base_rate: float | None = Field(default=None, gt=0)
    weekend_rate: float | None = Field(default=None, gt=0)
    long_stay_min_nights: int | None = Field(default=None, gt=0)
    long_stay_rate: float | None = Field(default=None, gt=0)
    status: str | None = None  # available / maintenance / blocked
    housekeeping_status: str | None = None
    unit_code: str | None = None
    floor: str | None = None
    flat_number: str | None = None
    amenities: str | None = None
    description: str | None = None
    bedroom_count: int | None = Field(default=None, ge=0)
    bathroom_count: int | None = Field(default=None, ge=0)
    house_rules: str | None = None
    checkin_time: str | None = None
    checkout_time: str | None = None


class UnitPricingUpdate(BaseModel):
    """
    RBAC Phase B: the narrow counterpart to UnitUpdate used by
    PATCH /units/{id}/pricing — deliberately declares ONLY the price
    fields, none of UnitUpdate's identity/configuration fields (name,
    status, unit_type, max_occupancy, unit_code, floor, flat_number,
    amenities, description). This is what lets Manager be granted price
    changes without also granting unit-editing power through the same
    endpoint. extra="forbid" rejects any other field in the request body
    outright (422) rather than silently ignoring it — a client can never
    smuggle a broader update through this schema.
    """
    base_rate: float | None = Field(default=None, gt=0)
    weekend_rate: float | None = Field(default=None, gt=0)
    long_stay_min_nights: int | None = Field(default=None, gt=0)
    long_stay_rate: float | None = Field(default=None, gt=0)

    class Config:
        extra = "forbid"


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
    # Optimistic-concurrency token: the value of BookingOut.updated_at the
    # editor loaded before making changes. The API returns HTTP 428 when an
    # older client omits it, preventing unchecked stale writes.
    expected_updated_at: datetime | None = None


class BookingArchiveRequest(BaseModel):
    reason: str | None = None


class BookingCancellationRequest(BaseModel):
    """
    Optional body for booking cancellation/retry.

    refund_amount is only an optional confirmation of the server-calculated
    gross (tax-inclusive) eligible amount. It is rejected when it differs
    from the persisted cancellation-policy decision; it never selects the
    policy outcome.
    """
    refund_amount: float | None = Field(default=None, ge=0)
    refund_reason: str | None = None


class BookingAttachmentOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    filename: str
    content_type: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class BookingGuestCreate(BaseModel):
    """Attach an EXISTING Guest to a booking as an Additional Guest.
    Deliberately just a guest_id — searching for or creating that Guest is
    already the existing GuestPicker/POST-/guests flow's job, reused
    as-is rather than duplicated here (section 28: guest search/reuse)."""
    guest_id: uuid.UUID


class BookingGuestOut(BaseModel):
    id: uuid.UUID  # the BookingGuest assignment row's own id (used to remove it)
    guest_id: uuid.UUID
    name: str
    phone: str
    identity_status: str
    added_at: datetime


class PrincipalGuestOut(BaseModel):
    """The Principal Guest is Booking.guest_id itself — never a
    BookingGuest row — so this has no assignment id to remove; a
    booking's principal guest is changed via the existing PATCH
    /bookings/{id} guest_id field, unchanged by this feature."""
    guest_id: uuid.UUID
    name: str
    phone: str
    identity_status: str


class BookingGuestsOut(BaseModel):
    principal_guest: PrincipalGuestOut
    additional_guests: list[BookingGuestOut]


class BookingOut(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    guest_id: uuid.UUID
    check_in: date
    check_out: date
    status: str
    source: str
    cancellation_at: datetime | None = None
    cancellation_checkin_at: datetime | None = None
    cancellation_policy_percent: int | None = None
    cancellation_refund_target: float | None = None
    cancellation_refund_source: str | None = None
    cancellation_requires_review: bool = False
    rate_applied: float
    internal_notes: str | None = None
    special_requests: str | None = None
    due_date: date | None = None
    adults: int = 1
    children: int = 0
    extra_bed: bool = False
    arrival_time: str | None = None
    departure_time: str | None = None
    updated_at: datetime
    is_archived: bool = False
    archived_at: datetime | None = None
    archive_reason: str | None = None

    class Config:
        from_attributes = True
