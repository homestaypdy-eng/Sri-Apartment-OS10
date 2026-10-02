"""
Phase 3A: minimal schemas for the customer account/session foundation.
Phase 3B adds CustomerRegisterRequest for the /register endpoint.
Phase 3C adds CustomerLoginRequest/CustomerLoginResponse for /login.
Phase 3D adds CustomerForgotPasswordRequest/Response and
CustomerResetPasswordRequest/Response for /forgot-password and
/reset-password.
Phase 3E (public-website login redesign): adds `phone` to
CustomerRegisterRequest/CustomerAccountOut, and turns login into two
steps — CustomerLoginRequest now starts it (password check + OTP
dispatch) and CustomerLoginVerifyRequest completes it (OTP check +
session issuance); CustomerLoginResponse is unchanged in shape and is now
returned by the verify step instead of the initial one.
Phase 3F (professional account experience): adds CustomerUpdateProfileRequest
for PATCH /me (first/last name + phone — deliberately NEVER email, which
would need its own re-verification flow this phase does not build) and
CustomerChangePasswordRequest/Response for POST /change-password. GET /me
needs no request schema — it returns CustomerAccountOut for whichever
customer the session token resolves to, same as every other endpoint here.
"""
import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.modules.customer_auth.phone_service import InvalidPhoneNumberError, normalize_phone_e164


def _validate_customer_password_complexity(v: str) -> str:
    """Same rule as auth.schemas.UserCreate.password_complexity (the
    existing staff convention: at least one letter and one digit, on top
    of a min_length=8 the caller's own Field already enforces) —
    re-expressed here rather than imported, since that validator is
    private to UserCreate and importing it would mean modifying
    app/modules/auth/schemas.py, which no customer-auth phase may touch.
    The hashing itself (app.core.security.hash_password) IS reused
    unchanged; only this small, stable complexity rule is mirrored.
    Shared by CustomerRegisterRequest.password and
    CustomerResetPasswordRequest.new_password so the two can't drift into
    two different password policies."""
    if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("Password must contain at least one letter and one digit")
    return v


class CustomerRegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    # Phase 3E: required (the public website's Sign Up form always
    # collects it now) — see CustomerAccount.phone's own docstring for why
    # the underlying DB column stays nullable regardless.
    phone: str
    password: str = Field(min_length=8)
    terms_accepted: bool
    privacy_accepted: bool

    @field_validator("first_name", "last_name")
    @classmethod
    def _required_after_strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field is required")
        return v

    @field_validator("phone")
    @classmethod
    def _phone_valid_and_normalized(cls, v: str) -> str:
        """International E.164 normalization via
        customer_auth.phone_service — a dedicated normalizer for this
        module, deliberately separate from the Guest/booking-side bare
        10-digit convention (see phone_service's own docstring for why).
        Stores the full E.164 form (e.g. "+919876543210", "+33612345678"),
        so the same real number always converges to the same stored value
        regardless of how the customer typed or formatted it."""
        try:
            return normalize_phone_e164(v)
        except InvalidPhoneNumberError:
            raise ValueError("Please enter a valid phone number.")

    @field_validator("password")
    @classmethod
    def _password_complexity(cls, v: str) -> str:
        return _validate_customer_password_complexity(v)


class CustomerUpdateProfileRequest(BaseModel):
    """PATCH /me — deliberately excludes email: changing the identifier a
    customer logs in and receives security notices with needs its own
    re-verification step (confirm the NEW address before it takes effect),
    which is separate, later work — not a safe shortcut to build here."""

    first_name: str
    last_name: str
    phone: str

    @field_validator("first_name", "last_name")
    @classmethod
    def _required_after_strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field is required")
        return v

    @field_validator("phone")
    @classmethod
    def _phone_valid_and_normalized(cls, v: str) -> str:
        try:
            return normalize_phone_e164(v)
        except InvalidPhoneNumberError:
            raise ValueError("Please enter a valid phone number.")


class CustomerChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def _password_complexity(cls, v: str) -> str:
        return _validate_customer_password_complexity(v)


class CustomerChangePasswordResponse(BaseModel):
    message: str


class CustomerAccountOut(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    email: EmailStr
    phone: str | None
    email_verified_at: datetime | None
    phone_verified_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class CustomerSessionToken(BaseModel):
    """Same shape as portal.schemas.GuestSessionToken — kept as its own
    class (not reused) so the customer and guest auth surfaces stay
    independently evolvable, per this module's security-boundary intent."""

    access_token: str
    token_type: str = "bearer"


class CustomerLoginRequest(BaseModel):
    """Phase 3E: step 1 of login — password only, no session yet. The
    customer picks which channel they're identifying with (their own
    UI choice, e.g. an Email/Mobile toggle); `identifier` is the raw
    value as typed, normalized server-side per channel before lookup."""

    channel: Literal["email", "phone"]
    identifier: str
    password: str


class CustomerLoginOtpRequestResponse(BaseModel):
    challenge_id: uuid.UUID
    message: str
    # Only ever populated outside production — same convention as every
    # other dev_otp_code/dev_reset_token field in this codebase.
    dev_otp_code: str | None = None
    # LOCAL EMAIL OTP TESTING: tells the caller exactly what actually
    # happened, so the frontend never has to assume delivery succeeded —
    # see customer_auth/router.py's login_customer docstring for the full
    # set of values. Always "sent" in production (the same meaning this
    # response always had before this field existed — unchanged wording,
    # just now named explicitly).
    otp_delivery_status: str = "sent"


class CustomerLoginVerifyRequest(BaseModel):
    """Phase 3E: step 2 of login — the OTP sent to whichever channel was
    used in step 1. Only on success does a CustomerSession/JWT get minted."""

    challenge_id: uuid.UUID
    code: str


class CustomerLoginResponse(BaseModel):
    """access_token + the minimum account info a frontend needs right
    after login, so it doesn't need a second round-trip just to greet the
    customer by name. Never includes password_hash — CustomerAccountOut
    doesn't have that field at all. Now returned by the OTP-verify step
    (Phase 3E), not the initial password-check step."""

    access_token: str
    token_type: str = "bearer"
    customer: CustomerAccountOut


class CustomerForgotPasswordRequest(BaseModel):
    email: EmailStr


class CustomerForgotPasswordResponse(BaseModel):
    message: str
    # Only ever populated outside production — same convention as
    # PhoneOtpRequestResponse.dev_otp_code / RequestLoginResponse.
    # dev_magic_token. Never rendered by any real website UI.
    dev_reset_token: str | None = None


class CustomerResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def _password_complexity(cls, v: str) -> str:
        return _validate_customer_password_complexity(v)


class CustomerResetPasswordResponse(BaseModel):
    message: str


class CustomerOAuthStatusResponse(BaseModel):
    """GET /oauth/status — lets the frontend decide, before the customer
    ever clicks the button, whether "Continue with Google" should do a
    real redirect or show the honest not-configured message. Never
    reveals the client id/secret themselves — only whether Google has
    credentials configured at all. Google is the only social-login
    provider this project supports."""

    google: bool


class CustomerOAuthLinkConfirmRequest(BaseModel):
    link_token: str


class CustomerOAuthLinkConfirmResponse(BaseModel):
    message: str
