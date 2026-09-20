"""
Unit tests for customer_auth.phone_service — the international E.164
phone-number normalizer used by customer registration, profile edit, and
login (mobile channel). Deliberately separate from the Guest/booking-side
portal.auth_service.normalize_phone, which is untouched and out of scope.

Run with: pytest tests/test_customer_phone.py -v
"""
import pytest

from app.modules.customer_auth.phone_service import InvalidPhoneNumberError, normalize_phone_e164


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+91 98765 43210", "+919876543210"),  # India, spaced
        ("+919876543210", "+919876543210"),  # India, already E.164
        ("+33 6 12 34 56 78", "+33612345678"),  # France
        ("+44 20 7946 0958", "+442079460958"),  # United Kingdom
        ("+1 415-555-0132", "+14155550132"),  # United States
        ("+61 4 1234 5678", "+61412345678"),  # Australia
        ("+81 90-1234-5678", "+819012345678"),  # Japan
        ("+971 50 123 4567", "+971501234567"),  # UAE
    ],
)
def test_normalizes_international_numbers_to_e164(raw, expected):
    assert normalize_phone_e164(raw) == expected


def test_bare_national_number_falls_back_to_default_region_india():
    assert normalize_phone_e164("9876543210") == "+919876543210"


def test_idempotent_on_an_already_normalized_value():
    once = normalize_phone_e164("+91 98765 43210")
    twice = normalize_phone_e164(once)
    assert once == twice == "+919876543210"


@pytest.mark.parametrize(
    "same_number_variants",
    [
        # India is the assumed default region, so a bare national number
        # (no country code at all) is also unambiguous here.
        ["+91 98765 43210", "91-98765-43210", "098765 43210", "9876543210", "+919876543210"],
        # For any other country, the variants must at least carry a "+" or
        # "00" international prefix — a bare national number with no
        # country code is genuinely ambiguous without a region hint (that
        # ambiguity is exactly why the website's own PhoneInput always
        # sends a full "+"-prefixed value; see phone_service's docstring).
        ["+33 6 12 34 56 78", "0033612345678", "+33612345678"],
    ],
)
def test_same_real_number_in_different_formatting_normalizes_identically(same_number_variants):
    normalized = {normalize_phone_e164(v) for v in same_number_variants}
    assert len(normalized) == 1, f"expected all variants to converge to one value, got {normalized}"


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "   ",
        "123",
        "abcdefghij",
        "+1 123",  # too short to be a real US number
        "+91 123",  # too short to be a real Indian number
        "+999999999999999999",  # not a real country code / too long
    ],
)
def test_invalid_numbers_rejected_with_generic_message(invalid):
    with pytest.raises(InvalidPhoneNumberError) as exc_info:
        normalize_phone_e164(invalid)
    # Generic, customer-facing wording only — never phonenumbers' own
    # internal exception text, which would leak implementation details.
    assert str(exc_info.value) == "Please enter a valid phone number."


def test_existing_legacy_indian_bare_digit_number_still_normalizes_correctly():
    """Regression: the exact shape every pre-existing CustomerAccount.phone
    value was stored in (bare 10-digit, no country code, no leading zero)
    before this international redesign must still parse and normalize
    correctly — this is what makes the additive backfill migration safe."""
    assert normalize_phone_e164("8870237237") == "+918870237237"
