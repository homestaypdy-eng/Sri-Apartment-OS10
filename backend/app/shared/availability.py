"""
Single authoritative overlap-check for "is this unit bookable for these
dates" — used by OS10 (manual booking creation, per-unit and batch
availability) and the public website (availability display, quote, hold
creation). One implementation, reused everywhere, so "available" can never
mean something different in two places.

A unit is UNAVAILABLE for a range if either:
  - a Booking with status IN ('confirmed', 'checked_in') overlaps it —
    mirrors bookings.no_overlapping_bookings, the actual DB-level EXCLUDE
    constraint that is the final guarantee against two confirmed bookings
    for the same unit/dates; or
  - a BookingHold with status IN ('pending_payment', 'paid') overlaps it,
    where a 'pending_payment' hold only counts while still within its TTL
    (expires_at > now()) — mirrors booking_holds.no_overlapping_holds.

Without the second check, a unit mid-payment on the website would still
show as available in OS10 (and a unit OS10 just booked could still look
open to a website customer already holding it), because neither table's
EXCLUDE constraint can see across to the other table — a booking insert
is never compared against holds, and a hold insert is never compared
against bookings, at the database level.

CONCURRENCY NOTE: this function is a pre-flight read, not a lock, and
exists to give fast/friendly "unavailable" responses before a write is
even attempted. It does not change any existing DB-level guarantee:
  - Two Bookings racing for the same range are still safely resolved by
    the bookings.no_overlapping_bookings EXCLUDE constraint.
  - Two BookingHolds racing for the same range are still safely resolved
    by booking_holds.no_overlapping_holds.
Neither of those requires this function to exist or be called correctly.

What this function does NOT add a hard DB-level guarantee for is a
Booking and a BookingHold being created for the same unit/range at the
literal same instant (in either direction) — Postgres EXCLUDE constraints
are single-table, and there is no cross-table exclusion here. That is a
documented, narrow residual limitation (see the website/OS10 availability
audit report), not something this module can fully close without a larger
structural change (e.g. a single unified reservations table) that was
judged too risky to make silently in an audit task. Given a hold's TTL is
short (15 minutes) and every write path calls this check immediately
before its own insert, the realistic exposure is a microseconds-wide race
window, not the everyday case — which this function does correctly close.
"""
import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session


def unit_is_available(db: Session, unit_id: uuid.UUID, check_in: date, check_out: date) -> bool:
    """True if no confirmed/checked_in booking and no active hold overlaps
    [check_in, check_out) for this unit. Does not consider unit.status
    (maintenance/blocked) or unit.is_active/property.is_active — callers
    that care about those (most do) check them separately, since those are
    unit-level flags rather than date-range availability."""
    return not _has_overlap(db, [unit_id], check_in, check_out)[unit_id]


def units_availability(db: Session, unit_ids: list[uuid.UUID], check_in: date, check_out: date) -> dict[uuid.UUID, bool]:
    """Batch form of unit_is_available — one pair of queries covering every
    unit_id given, instead of one pair of queries per unit. Returns
    {unit_id: True-if-available} for every id in unit_ids."""
    overlap = _has_overlap(db, unit_ids, check_in, check_out)
    return {uid: not overlap[uid] for uid in unit_ids}


def _has_overlap(db: Session, unit_ids: list[uuid.UUID], check_in: date, check_out: date) -> dict[uuid.UUID, bool]:
    ids = [str(u) for u in unit_ids]
    result = {u: False for u in unit_ids}
    if not ids:
        return result

    booking_rows = db.execute(
        text(
            """
            SELECT DISTINCT unit_id FROM bookings
            WHERE unit_id::text = ANY(:unit_ids) AND status IN ('confirmed', 'checked_in')
              AND daterange(check_in, check_out, '[)') && daterange(:ci, :co, '[)')
            """
        ),
        {"unit_ids": ids, "ci": check_in, "co": check_out},
    ).fetchall()
    for row in booking_rows:
        result[row[0]] = True

    hold_rows = db.execute(
        text(
            """
            SELECT DISTINCT unit_id FROM booking_holds
            WHERE unit_id::text = ANY(:unit_ids)
              AND (status = 'paid' OR (status = 'pending_payment' AND expires_at > now()))
              AND daterange(check_in, check_out, '[)') && daterange(:ci, :co, '[)')
            """
        ),
        {"unit_ids": ids, "ci": check_in, "co": check_out},
    ).fetchall()
    for row in hold_rows:
        result[row[0]] = True

    return result
