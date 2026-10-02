"""
Single authoritative overlap-check for "is this unit bookable for these
dates" — used by OS10 (manual booking creation, per-unit and batch
availability) and the public website (availability display, quote, hold
creation). One implementation, reused everywhere, so "available" can never
mean something different in two places.

A unit is UNAVAILABLE for a range if any of the following is true:
  - unit.status is not 'available' (i.e. 'maintenance' or 'blocked') —
    Phase 5A: checked here, once, so every caller inherits it rather than
    each having to remember to check it independently (see
    unit_is_available's own docstring for why this was added and how it
    interacts with callers that already checked it themselves); or
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

What this function alone does NOT add a hard DB-level guarantee for is a
Booking and a BookingHold being created for the same unit/range at the
literal same instant (in either direction) — Postgres EXCLUDE constraints
are single-table, and there is no cross-table exclusion here. That gap is
closed not by this function but by lock_unit_for_reservation() below: every
write path (direct booking, public hold/conversion, inquiry conversion,
booking edit, and Zotel import) acquires that unit-scoped advisory lock
before calling this function and holds it through its reservation write's
COMMIT, so two such writes for the same unit can never interleave. This function remains exactly what
its own docstring says — a pre-flight read, not a lock — the locking lives
one layer up, at the call sites.
"""
import uuid
from datetime import date

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session


def lock_unit_for_reservation(db: Session, unit_id: uuid.UUID) -> None:
    """Acquires a PostgreSQL transaction-scoped advisory lock keyed by this
    unit_id — pg_advisory_xact_lock, not pg_advisory_lock — so it releases
    automatically at this transaction's COMMIT or ROLLBACK, with no manual
    unlock and no way to leak past a crashed request.

    Every write path that can create/reschedule a Booking or create an
    active BookingHold for a unit calls this before checking availability
    and holds it through its reservation write's commit. Since they use the
    same lock-key derivation, they always contend on the same lock for the
    same unit — closing the one race unit_is_available()'s own EXCLUDE
    constraints cannot see across (see this module's docstring).

    hashtext() collapses the UUID to a 32-bit key; pg_advisory_xact_lock's
    single-argument form takes a bigint, and Postgres implicitly widens the
    int4 result — a rare hash collision between two different unit_ids only
    ever costs unrelated units a moment of unnecessary contention, never
    incorrect behavior.

    MUST NOT be held across an external API call (e.g. Razorpay order
    creation) — callers that need to make one must COMMIT (ending this
    transaction, and with it the lock) first, then call out unlocked. See
    create_hold()'s two-phase structure for the reference pattern.
    """
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:unit_id))"), {"unit_id": str(unit_id)})


def unit_is_available(
    db: Session,
    unit_id: uuid.UUID,
    check_in: date,
    check_out: date,
    *,
    exclude_booking_id: uuid.UUID | None = None,
) -> bool:
    """True if the unit's status is 'available' AND no confirmed/checked_in
    booking and no active hold overlaps [check_in, check_out) for this
    unit. Does not consider unit.is_active/property.is_active —
    deactivation is a distinct concept from maintenance/blocked, and
    callers that care about it (most do) still check it separately.

    Phase 5A: unit.status (maintenance/blocked) is now checked HERE, in
    the one shared function every booking/availability path already
    calls through — rather than being every individual caller's own
    responsibility, which is how the Zotel import path and the hold-to-
    booking conversion path had each independently missed it. Callers
    that already check unit.status themselves before calling this (most
    of the existing ones do) are unaffected: this can only ever agree
    with an already-passed check, never contradict it."""
    if exclude_booking_id is None:
        return units_availability(db, [unit_id], check_in, check_out)[unit_id]
    if unit_id in _blocked_unit_ids(db, [unit_id]):
        return False
    return not _has_overlap(db, [unit_id], check_in, check_out, exclude_booking_id=exclude_booking_id)[unit_id]


def units_availability(db: Session, unit_ids: list[uuid.UUID], check_in: date, check_out: date) -> dict[uuid.UUID, bool]:
    """Batch form of unit_is_available — one pair of overlap queries plus
    one status query covering every unit_id given, instead of one round
    trip per unit. Returns {unit_id: True-if-available} for every id in
    unit_ids."""
    if not unit_ids:
        return {}
    overlap = _has_overlap(db, unit_ids, check_in, check_out)
    blocked = _blocked_unit_ids(db, unit_ids)
    return {uid: (uid not in blocked) and not overlap[uid] for uid in unit_ids}


def _blocked_unit_ids(db: Session, unit_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Units whose status is anything other than 'available' — same
    string-cast-id-list pattern as _has_overlap below, for the same
    reason (a single ANY() query instead of one per unit)."""
    ids = [str(u) for u in unit_ids]
    rows = db.execute(
        text("SELECT id FROM units WHERE id::text = ANY(:unit_ids) AND status != 'available'"),
        {"unit_ids": ids},
    ).fetchall()
    return {row[0] for row in rows}


def _has_overlap(
    db: Session,
    unit_ids: list[uuid.UUID],
    check_in: date,
    check_out: date,
    *,
    exclude_booking_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, bool]:
    ids = [str(u) for u in unit_ids]
    result = {u: False for u in unit_ids}
    if not ids:
        return result

    exclusion_sql = ""
    booking_params = {"unit_ids": ids, "ci": check_in, "co": check_out}
    if exclude_booking_id is not None:
        exclusion_sql = "AND id != :exclude_booking_id"
        booking_params["exclude_booking_id"] = uuid.UUID(str(exclude_booking_id))

    booking_query = text(
        f"""
        SELECT DISTINCT unit_id FROM bookings
        WHERE unit_id::text = ANY(:unit_ids) AND status IN ('confirmed', 'checked_in')
          {exclusion_sql}
          AND daterange(check_in, check_out, '[)') && daterange(:ci, :co, '[)')
        """
    )
    if exclude_booking_id is not None:
        booking_query = booking_query.bindparams(
            bindparam("exclude_booking_id", type_=UUID(as_uuid=True))
        )
    booking_rows = db.execute(booking_query, booking_params).fetchall()
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
