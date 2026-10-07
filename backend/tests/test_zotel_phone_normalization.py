"""
Regression tests for Zotel guest-phone normalization.

Zotel-supplied phone values arrive in several shapes — bare digits, a
country-code-prefixed form, a leading-zero trunk form, and a wrapped
"<tel:+E164|human display>" token — and some are malformed (e.g. a
country-code fragment glued in front of a <tel:...> wrapper). Before the
fix, these fed straight into Guest lookup/storage, so formatting
differences created duplicate guests and a malformed value could fabricate
a phone identity or (in the worst case) overflow the Guest.phone column.

These tests lock down _normalize_zotel_phone's exact outputs, prove the
column width is never exceeded, prove malformed/blank values fall back to
the "unknown" placeholder (never a fabricated number), and prove the same
Indian number in different valid formats resolves to a single Guest row.

DB tests mirror test_zotel_integration.py: SessionLocal() via _db_session()
in a try/finally, the conftest session-scoped reset fixture supplies a
clean schema.
"""
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.modules.bookings.models import Guest
from app.modules.zotel import service as zotel_service
from app.modules.zotel.service import (
    _extract_customer,
    _find_or_create_guest,
    _normalize_zotel_phone,
)


def _db_session():
    return SessionLocal()


# ---------------------------------------------------------------------------
# Part A — direct unit tests of _normalize_zotel_phone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("<tel:+918870237237|+91 88702 37237>", "8870237237"),
    ("918870237237", "8870237237"),
    ("08870237237", "8870237237"),
    ("8870237237", "8870237237"),
    ("<tel:+33610741692|+33 6 10 74 16 92>", "+33610741692"),
    ("+91 <tel:+33610741692|+33 6 10 74 16 92>", ""),
    ("", ""),
    (None, ""),
])
def test_normalize_zotel_phone_exact_outputs(raw, expected):
    result = _normalize_zotel_phone(raw)
    assert result == expected
    # The result is always safe to store in Guest.phone = String(20).
    assert len(result) <= 20


# ---------------------------------------------------------------------------
# Part B — storage / no-crash tests via _find_or_create_guest
# ---------------------------------------------------------------------------

def _customer(phone, name="Phone Norm Test Guest"):
    """Build the detail dict _extract_customer expects, run it through, so
    the stored phone is exactly what the real import path would store."""
    return _extract_customer({"customer": {"name": name, "phone": phone, "email": None}})


def test_malformed_wrapped_value_falls_back_to_unknown_never_fabricated():
    db = _db_session()
    try:
        customer = _customer("+91 <tel:+33610741692|+33 6 10 74 16 92>")
        # _extract_customer already refuses this ambiguous value.
        assert customer["phone"] == ""
        guest = _find_or_create_guest(db, customer)
        assert guest.phone == "unknown"
        # No fabricated number — not the naive concat, and no fragment of it.
        assert guest.phone != "9133610741692"
        assert "33610741692" not in guest.phone
        assert len(guest.phone) <= 20
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("raw", ["", None])
def test_blank_and_none_fall_back_to_unknown_no_crash(raw):
    db = _db_session()
    try:
        customer = _customer(raw)
        assert customer["phone"] == ""
        guest = _find_or_create_guest(db, customer)
        assert guest.phone == "unknown"
        assert len(guest.phone) <= 20
    finally:
        db.rollback()
        db.close()


def test_valid_international_value_is_stored_as_e164_and_bounded():
    db = _db_session()
    try:
        customer = _customer("<tel:+33610741692|+33 6 10 74 16 92>")
        assert customer["phone"] == "+33610741692"
        guest = _find_or_create_guest(db, customer)
        assert guest.phone == "+33610741692"
        assert len(guest.phone) <= 20
    finally:
        db.rollback()
        db.close()


def test_no_stored_phone_exceeds_column_width():
    db = _db_session()
    try:
        for raw in [
            "<tel:+918870237237|+91 88702 37237>",
            "918870237237",
            "08870237237",
            "8870237237",
            "<tel:+33610741692|+33 6 10 74 16 92>",
            "+91 <tel:+33610741692|+33 6 10 74 16 92>",
            "",
            None,
        ]:
            guest = _find_or_create_guest(db, _customer(raw, name=f"Width Test {raw!r}"))
            assert len(guest.phone) <= 20
    finally:
        db.rollback()
        db.close()


# ---------------------------------------------------------------------------
# Part C — guest deduplication across valid formats of the SAME number
# ---------------------------------------------------------------------------

def test_same_indian_number_in_different_formats_resolves_to_one_guest():
    db = _db_session()
    try:
        formats = [
            "918870237237",
            "8870237237",
            "<tel:+918870237237|+91 88702 37237>",
            "08870237237",
        ]
        guests = []
        for raw in formats:
            customer = _customer(raw)
            # Every valid format collapses to the bare 10-digit OS10 form.
            assert customer["phone"] == "8870237237"
            guests.append(_find_or_create_guest(db, customer))
            db.flush()

        # All four imports resolved to the same single Guest row.
        ids = {g.id for g in guests}
        assert len(ids) == 1

        count = db.scalar(
            select(func.count()).select_from(Guest).where(Guest.phone == "8870237237")
        )
        assert count == 1
    finally:
        db.rollback()
        db.close()
