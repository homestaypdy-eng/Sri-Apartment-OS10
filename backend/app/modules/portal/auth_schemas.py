"""
Phase 2 (phone OTP) and Phase 3 (email OTP) request/response shapes. Kept
separate from portal/schemas.py — which backs the live magic-link
endpoints — so that file needs no changes for either phase. Both
verify-otp responses reuse portal/schemas.py's existing GuestSessionToken
directly rather than duplicating it, per "existing OS10 API conventions."
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


class PhoneOtpRequest(BaseModel):
    phone: str


class PhoneOtpRequestResponse(BaseModel):
    challenge_id: uuid.UUID
    # Deliberately identical wording regardless of whether the phone
    # matches an existing Guest — request-otp never even checks Guest
    # existence (see auth_router.py), so there is nothing phone-specific
    # to leak here in the first place.
    message: str = "If this number can receive SMS, a verification code has been sent."
    # Dev/testability-only field, same convention as RequestLoginResponse.
    # dev_magic_token — OS10 explicitly documents this as absent in
    # production (see auth_router.py). Never rendered by the website UI.
    dev_otp_code: str | None = None


class PhoneOtpVerifyRequest(BaseModel):
    challenge_id: uuid.UUID
    code: str


class EmailOtpRequest(BaseModel):
    email: str


class EmailOtpRequestResponse(BaseModel):
    challenge_id: uuid.UUID
    # Deliberately identical wording regardless of whether the email
    # matches an existing Guest — same reasoning as PhoneOtpRequestResponse.
    message: str = "If this email is on file, a verification code has been sent."
    # Dev/testability-only — same convention as dev_magic_token and
    # PhoneOtpRequestResponse.dev_otp_code. Always None in production.
    dev_otp_code: str | None = None


class EmailOtpVerifyRequest(BaseModel):
    challenge_id: uuid.UUID
    code: str


# ---------------------------------------------------------------------------
# Phase 2A: staff-assisted first-time guest identity linking. See
# bookings/router.py's link_guest_identity for the endpoint and the full
# safety reasoning (admin-only, identity must be currently unlinked).
# ---------------------------------------------------------------------------


class LinkGuestIdentityRequest(BaseModel):
    # The channel/identifier the guest actually OTP-verified with — staff
    # get this from the guest directly (e.g. over a support call), the
    # same information the guest themselves typed into the OTP login
    # form. Deliberately not "identity_id": there is no admin UI listing
    # GuestIdentity rows by UUID today, so requiring staff to already know
    # one would make this endpoint unusable in practice.
    channel: str
    external_id: str


class GuestIdentityOut(BaseModel):
    id: uuid.UUID
    channel: str
    external_id: str
    guest_id: uuid.UUID | None
    linked_at: datetime | None
    verified_at: datetime

    class Config:
        from_attributes = True
