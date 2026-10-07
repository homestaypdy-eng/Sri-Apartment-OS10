"""Regression tests for Zotel guest-phone normalization.

Guards the bug where the Zotel import stored/matched the guest phone raw,
so a country-code-prefixed OTA phone never matched the bare-10-digit
Guest.phone convention and a duplicate Guest was created on every import.
"""
import pytest

from app.modules.zotel.service import _extract_customer


@pytest.mark.parametrize("raw, expected", [
    ("+91 98765 43210", "9876543210"),   # spaced, +country code
    ("919876543210", "9876543210"),      # bare country-code prefix
    ("098765 43210", "9876543210"),      # trunk-prefixed national
    ("9876543210", "9876543210"),        # already canonical
    ("tel:+919876543210", "9876543210"), # a tel: target is trusted (digits extracted)
])
def test_extract_customer_normalizes_phone_to_guest_convention(raw, expected):
    result = _extract_customer({"customer": {"name": "A", "phone": raw, "email": None}})
    assert result["phone"] == expected


@pytest.mark.parametrize("raw", ["", None])
def test_extract_customer_blank_phone_is_not_fabricated(raw):
    # No usable phone must stay blank here — never a guessed/invented number.
    result = _extract_customer({"customer": {"name": "A", "phone": raw, "email": None}})
    assert result["phone"] == ""
