"""
International phone-number handling for customer-facing auth (worldwide
sign up / login), using `phonenumbers` — the actively-maintained Python
port of Google's own libphonenumber, the standard library for this
problem rather than a hand-rolled "strip non-digits" approach.

Deliberately SEPARATE from app.modules.portal.auth_service.normalize_phone:
that function is the Guest/booking-side identifier normalizer (bare
10-digit Indian form, no country code — see its own docstring), and
Guest.phone is already stored that way throughout the existing booking
system. Changing that shared helper to E.164 would silently break every
existing Guest/booking phone lookup, which is out of scope for the
customer-account/login redesign this module belongs to. CustomerAccount
is its own, newer, entirely separate trust boundary (see models.py's
module docstring) with no such legacy convention to preserve — so it gets
its own normalizer, storing the full E.164 form (e.g. "+919876543210",
"+33612345678", "+442071234567"), which is what makes worldwide phone
numbers (not just India) representable and comparable at all.

Accepts two input shapes:
  - Already-prefixed international input, e.g. "+33 6 12 34 56 78" (what
    the website's own PhoneInput country-selector component always sends)
    — parsed region-independently from the leading "+".
  - Bare national-format input with no country code, e.g. "9876543210"
    — parsed against `default_region` (India, matching this project's
    existing settings.default_country_code convention) purely as a
    fallback for legacy callers that don't yet go through the
    country-selector UI; never silently reinterprets a number that
    already carries its own country code.

Either way, the OUTPUT is always the same canonical E.164 string for the
same real number, which is exactly what prevents duplicate accounts from
different formatting of the same number ("+91 98765 43210",
"91-98765-43210", "09876543210" all converge to "+919876543210").
"""
import phonenumbers

DEFAULT_REGION = "IN"


class InvalidPhoneNumberError(ValueError):
    """Raised for input that cannot be parsed as, or is not, a valid
    phone number for its (explicit or assumed) region. Callers surface a
    single generic customer-facing message ("Please enter a valid phone
    number.") — never phonenumbers' own internal error text, which would
    leak implementation details to the customer."""


def normalize_phone_e164(raw_phone: str, default_region: str = DEFAULT_REGION) -> str:
    """Parses, validates, and formats `raw_phone` as E.164. Idempotent:
    calling this again on an already-normalized E.164 string returns the
    identical string, the same safe-to-reapply property
    portal.auth_service.normalize_phone already documents for itself."""
    if not raw_phone or not raw_phone.strip():
        raise InvalidPhoneNumberError("Please enter a valid phone number.")

    try:
        parsed = phonenumbers.parse(raw_phone, default_region)
    except phonenumbers.NumberParseException:
        raise InvalidPhoneNumberError("Please enter a valid phone number.")

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumberError("Please enter a valid phone number.")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
