"""
Zotel channel-manager sync logic.

SCOPE OF THIS FILE, PRECISELY:
- check_connectivity(): read-only diagnostic against the 5 documented GET
  endpoints (availability/prices/bookings/booking-detail) plus hotel_id
  validity, safe to run at any time. Persists its own ZotelSyncLog row
  (sync_type="connectivity_test") so a run's result is still visible in
  the Sync Logs list afterward — the only write it makes.
- sync_bookings(): imports Zotel reservations (GET /bookings, paginated)
  into the SAME OS10 Booking model everything else uses — never a second
  "Zotel booking" table. Read-only against Zotel; the only OS10 writes are
  Guest/Booking/Payment rows plus this module's own sync log/records.
- run_reconciliation(): pure read+compare report against Zotel and OS10's
  own data; makes no writes to either system's operational data (never
  touches Booking/Guest/Payment). It does persist its own ZotelSyncLog row
  (sync_type="reconciliation") — an audit-trail write, not an operational
  one — so a run's result is still visible afterward.

WHAT THIS FILE DELIBERATELY DOES NOT DO:
- It never calls POST /bookings (create a live Zotel reservation) — no
  such call exists anywhere in this codebase.
- It never pushes availability or rates to Zotel. The documented API
  (see client.py's module docstring) has no PUT/PATCH/POST endpoint for
  either — GET /availability and GET /prices are the only such endpoints
  in the entire 25-page reference document. There is nothing to build an
  outbound push against; this is reported as a documentation-driven gap,
  not silently invented.
- It never creates an Invoice or marks a booking "paid" from Zotel data
  alone. A reservation existing in Zotel is not, by itself, grounds for
  OS10 to issue a tax invoice under this business's existing invoicing
  rules (which are guest-checkout/payment-driven — see payments/service.py)
  and there's no explicit product requirement to change that. Only a
  Payment ROW is recorded, and only when Zotel's own booking-detail
  response explicitly reports paid_amount > 0 — that Payment is tagged
  mode="zotel" (a new, non-DB-enforced value — see payments/models.py's
  PAYMENT_MODES, which has never been a DB CHECK constraint) precisely
  because Zotel's documented payment_method field (pay_at_hotel/pay_now/
  part_payments) describes a billing arrangement, not a card/UPI/cash
  instrument — inventing a specific instrument from that field would be
  guessing. reference_no carries the Zotel transaction_id so the row is
  always traceable back to its source.
- It never overwrites a manually-managed OS10 booking. If an existing
  OS10 booking (found via external_reservation_id) has already progressed
  (checked_in/checked_out) or its core financial/date fields differ from
  what Zotel now reports, the conflict is only LOGGED (outcome=
  "conflicted") — the local booking is left untouched. The one safe,
  narrow auto-update permitted is propagating a Zotel status of
  "cancelled" onto an OS10 booking that is still just "confirmed" (i.e.
  nothing operationally irreversible has happened locally yet).
"""
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.bookings import cancellation_service
from app.modules.bookings.models import Booking, Guest
from app.modules.notifications.service import dispatch_cancellation_notifications
from app.modules.payments.models import Payment
from app.modules.properties.models import Unit
from app.modules.zotel import client
from app.modules.zotel.client import ZotelAPIError, ZotelNotConfiguredError
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncLog, ZotelSyncRecord
from app.shared.availability import lock_unit_for_reservation, unit_is_available
from app.shared.occupancy import enforce_unit_max_occupancy

logger = logging.getLogger("sri_apartment_os")

# Derive the real DB column limit for Guest.phone (String(20)); fall back to 20.
_MAX_PHONE_LEN = getattr(Guest.phone.property.columns[0].type, "length", None) or 20

# Zotel sometimes wraps a number as "<tel:+E164|human display>".
_TEL_URI_RE = re.compile(r"<tel:([^|>]+)\|[^>]*>", re.IGNORECASE)


def _canonicalize_phone(value: str) -> str:
    """Reduce a cleaned phone string to the OS10 convention.

    Indian numbers collapse to the bare 10-digit form (matching
    portal.normalize_phone); valid non-Indian numbers keep a stable
    E.164-style identity with the leading '+'. Output never exceeds the
    Guest.phone column width.
    """
    has_plus = value.strip().startswith("+")
    digits = "".join(c for c in value if c.isdigit())
    if not digits:
        return ""
    cc = settings.default_country_code  # e.g. "91"
    if len(digits) == len(cc) + 10 and digits.startswith(cc):
        return digits[len(cc):]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    if len(digits) == 10:
        return digits
    candidate = ("+" + digits) if has_plus else digits
    return candidate[:_MAX_PHONE_LEN]


def _normalize_zotel_phone(raw: object) -> str:
    """Safely normalize a Zotel-supplied phone value for Guest lookup/storage.

    Returns "" when the value is missing or too ambiguous to trust, so the
    caller falls back to the 'unknown' placeholder instead of fabricating an
    identity. The result is always safe to store in Guest.phone.
    """
    if raw is None:
        return ""
    value = str(raw).strip()
    if not value:
        return ""
    if "<tel:" in value.lower():
        # A trustworthy wrapped value is EXACTLY one <tel:...|...> token.
        # Extra content around it (e.g. "+91 <tel:+33...|...>") is
        # ambiguous/untrusted — never invent a number from it.
        match = _TEL_URI_RE.fullmatch(value)
        if match is None:
            return ""
        value = match.group(1).strip()
    return _canonicalize_phone(value)

# Reservation lifecycle states this file treats as "still open" locally —
# an OS10 booking imported from Zotel that has moved past this set has had
# real, operational, possibly-irreversible things happen to it (a guest
# physically checked in/out), so an automatic Zotel-driven update must not
# touch it beyond the one explicit exception (cancellation propagation,
# handled separately and only from this same state).
SAFE_TO_AUTO_UPDATE_STATUSES = ("confirmed",)

# New-reservation import policy (applies ONLY to a brand-new Zotel
# reservation with no existing OS10 booking for its transaction id — NOT
# to reconciliation of an already-imported booking, which is handled by
# _reconcile_existing and left deliberately unchanged). Statuses are
# normalized (stripped + lowercased) before comparison.
#   - SKIP: terminal/non-bookable states that aren't worth occupying a
#     slot for (cancelled/completed/enquired) — nothing to protect.
#   - ACTIVE: states that represent a live reservation worth importing as
#     an OS10 "confirmed" booking, but only when the stay is still in the
#     future (check_out strictly after today).
# Any status outside both sets (or a missing/None status) is treated as
# unrecognized and flagged "conflicted" for manual review rather than
# guessed at.
ZOTEL_NEW_IMPORT_SKIP_STATUSES = ("cancelled", "completed", "enquired")
ZOTEL_NEW_IMPORT_ACTIVE_STATUSES = ("created", "modified", "confirmed")

# Stable, application-specific PostgreSQL session advisory-lock key (ASCII
# "ZOTELSYN"). It is constant across every worker and never comes from input.
ZOTEL_SYNC_ADVISORY_LOCK_KEY = 0x5A4F54454C53594E


class ZotelSyncAlreadyRunning(Exception):
    """Raised when another worker already owns the global booking-sync lock."""


class ZotelSyncLockUnavailable(Exception):
    """Raised when the lock store cannot safely establish exclusivity."""


def _persist_connectivity_log(db: Session, triggered_by: uuid.UUID | None, configured: bool, checks: list[dict], overall_passed: bool) -> None:
    """
    Records one ZotelSyncLog row per connectivity-test run, closing the gap
    disclosed in the Task K final report (item 17): previously only
    sync_bookings() persisted a log, so a connectivity test's result only
    ever existed in the moment it ran, in the HTTP response — nothing was
    left behind to look back at later. Counters are mapped onto the same
    fields sync_bookings() uses so both sync_types render consistently in
    one Sync Logs table, even though "imported/updated/skipped/unmapped"
    don't literally apply to a connectivity test:
      - records_fetched = number of check categories actually run
      - records_failed  = number of those that failed
      - records_imported/updated/skipped/unmapped/conflicted = 0 (not applicable)
    status mirrors sync_bookings()'s own success/partial/failed logic:
    success when every check passed, failed when none did (including the
    "not configured" case, which runs no live check at all), partial when
    only some did.
    """
    if not configured:
        status = "failed"
    elif overall_passed:
        status = "success"
    elif any(c["passed"] for c in checks):
        status = "partial"
    else:
        status = "failed"

    passed_count = sum(1 for c in checks if c["passed"])
    failed_count = len(checks) - passed_count
    message = (
        f"configured={configured} checks={len(checks)} passed={passed_count} failed={failed_count}"
        if configured
        else "Zotel is not configured — no live check was attempted"
    )

    sync_log = ZotelSyncLog(
        sync_type="connectivity_test",
        status=status,
        message=message,
        records_fetched=len(checks),
        records_failed=failed_count,
        triggered_by=triggered_by,
    )
    db.add(sync_log)
    db.commit()


def check_connectivity(db: Session, triggered_by: uuid.UUID | None = None) -> dict:
    """
    Read-only diagnostic per the task's required PASS/FAIL-per-category
    format. Every check is safe to run any number of times — all are GETs.
    Never includes any credential value in its output, only which env vars
    are set (booleans) and Zotel's own safe error messages. Persists its
    own ZotelSyncLog row (see _persist_connectivity_log) so a run's result
    is still visible in the Sync Logs list afterward, not just in the
    immediate HTTP response.
    """
    checks: list[dict] = []

    if not client.is_configured():
        checks.append({
            "category": "configuration",
            "passed": False,
            "message": (
                "Zotel is not configured in this environment (ZOTEL_BASE_URL / ZOTEL_API_KEY / "
                "ZOTEL_API_SECRET / ZOTEL_HOTEL_ID are empty). No live check was attempted."
            ),
        })
        _persist_connectivity_log(db, triggered_by, configured=False, checks=checks, overall_passed=False)
        return {"configured": False, "checks": checks, "overall_passed": False}

    checks.append({"category": "configuration", "passed": True, "message": "Zotel credentials and hotel_id are set."})

    today = datetime.now(timezone.utc).date()
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)

    def _try(category: str, fn):
        try:
            fn()
            checks.append({"category": category, "passed": True, "message": "OK"})
        except ZotelNotConfiguredError as e:
            checks.append({"category": category, "passed": False, "message": str(e)})
        except ZotelAPIError as e:
            checks.append({"category": category, "passed": False, "message": f"HTTP {e.status_code}: {e}"})
        except Exception as e:  # noqa: BLE001 — diagnostic must never crash, only report
            checks.append({"category": category, "passed": False, "message": f"Unexpected error: {type(e).__name__}"})

    _try("availability", lambda: client.get_availability(today, tomorrow))
    _try("prices", lambda: client.get_prices(today, tomorrow))
    _try("bookings", lambda: client.get_bookings(per_page=1))

    overall = all(c["passed"] for c in checks)
    _persist_connectivity_log(db, triggered_by, configured=True, checks=checks, overall_passed=overall)
    return {"configured": True, "checks": checks, "overall_passed": overall}


def _resolve_mapping(db: Session, zotel_room_type_id: str) -> ZotelRoomMapping | None:
    return db.scalar(select(ZotelRoomMapping).where(ZotelRoomMapping.zotel_room_type_id == zotel_room_type_id))


def _extract_customer(detail: dict) -> dict:
    customer = detail.get("customer") or {}
    name = customer.get("name")
    if not name:
        first = customer.get("first_name", "")
        last = customer.get("last_name", "")
        name = f"{first} {last}".strip() or "Zotel Guest"
    return {
        "name": name,
        "phone": _normalize_zotel_phone(customer.get("phone")),
        "email": customer.get("email"),
    }


def _find_or_create_guest(db: Session, customer: dict) -> Guest:
    guest = None
    phone = customer["phone"]               # already normalized by _extract_customer
    if phone:
        guest = db.scalar(select(Guest).where(Guest.phone == phone))
    if guest is None:
        guest = Guest(
            name=customer["name"],
            phone=(phone or "unknown")[:_MAX_PHONE_LEN],
            email=customer.get("email"),
        )
        db.add(guest)
        db.flush()
    return guest


def _record(sync_log_id: uuid.UUID, external_reservation_id: str, outcome: str, booking_id: uuid.UUID | None, detail: str | None) -> ZotelSyncRecord:
    return ZotelSyncRecord(
        sync_log_id=sync_log_id,
        external_reservation_id=external_reservation_id,
        outcome=outcome,
        booking_id=booking_id,
        detail=detail,
    )


def sync_bookings(db: Session, triggered_by: uuid.UUID | None = None, status_filter: str | None = None) -> ZotelSyncLog:
    """Serialize all booking syncs across processes using PostgreSQL.

    The session-level lock is held on its own pinned connection because the
    sync's normal Session commits on success and may roll back after an
    overlap race. Neither operation can release this lock connection's
    advisory lock. The lock is explicitly released (or its connection
    invalidated) on every exit path.
    """
    lock_connection = None
    try:
        lock_connection = db.get_bind().connect()
        acquired = lock_connection.execute(
            select(func.pg_try_advisory_lock(ZOTEL_SYNC_ADVISORY_LOCK_KEY))
        ).scalar_one()
        # Session advisory locks survive transaction commit; commit here so
        # the dedicated connection is not left with an open DB transaction.
        lock_connection.commit()
    except Exception as exc:
        if lock_connection is not None:
            try:
                lock_connection.rollback()
            except Exception:
                pass
            lock_connection.invalidate()
            lock_connection.close()
        logger.error("Could not acquire Zotel sync lock (%s)", type(exc).__name__)
        raise ZotelSyncLockUnavailable() from None

    if not acquired:
        lock_connection.close()
        raise ZotelSyncAlreadyRunning()

    try:
        return _sync_bookings_locked(db, triggered_by=triggered_by, status_filter=status_filter)
    except BaseException:
        # The import session must be reusable/clean after an exceptional exit.
        try:
            db.rollback()
        except Exception:
            logger.error("Could not roll back the Zotel sync session after an exception")
        raise
    finally:
        try:
            released = lock_connection.execute(
                select(func.pg_advisory_unlock(ZOTEL_SYNC_ADVISORY_LOCK_KEY))
            ).scalar_one()
            lock_connection.commit()
            if not released:
                # Never return a possibly still-locked server session to the
                # pool if PostgreSQL reports that this connection did not own it.
                lock_connection.invalidate()
                logger.error("PostgreSQL reported that the Zotel sync lock was not held during release")
        except Exception as exc:
            # Invalidating closes the physical PostgreSQL session, which
            # releases any session-level advisory locks owned by it.
            lock_connection.invalidate()
            logger.error("Could not explicitly release Zotel sync lock (%s)", type(exc).__name__)
        finally:
            lock_connection.close()


def _sync_bookings_locked(db: Session, triggered_by: uuid.UUID | None = None, status_filter: str | None = None) -> ZotelSyncLog:
    """
    Fetch Zotel reservations (paginated) and import/reconcile each one into
    the OS10 Booking table, keyed by external_reservation_id (Zotel's
    transaction_id) for idempotency — see this file's own module docstring
    for exactly what is and isn't auto-applied.
    """
    sync_log = ZotelSyncLog(sync_type="bookings_import", status="success", triggered_by=triggered_by)
    db.add(sync_log)
    db.flush()

    try:
        summaries = client.get_all_bookings(status=status_filter)
    except ZotelNotConfiguredError as e:
        sync_log.status = "failed"
        sync_log.message = str(e)
        db.commit()
        return sync_log
    except ZotelAPIError as e:
        sync_log.status = "failed"
        sync_log.message = f"Failed to fetch bookings from Zotel: HTTP {e.status_code}: {e}"
        db.commit()
        return sync_log

    sync_log.records_fetched = len(summaries)
    records: list[ZotelSyncRecord] = []

    for summary in summaries:
        transaction_id = summary.get("transaction_id")
        if not transaction_id:
            sync_log.records_failed += 1
            records.append(_record(sync_log.id, "unknown", "failed", None, "Zotel reservation summary missing transaction_id"))
            continue

        try:
            detail = client.get_booking_detail(transaction_id)
        except ZotelAPIError as e:
            sync_log.records_failed += 1
            records.append(_record(sync_log.id, transaction_id, "failed", None, f"Could not fetch booking detail: HTTP {e.status_code}"))
            continue

        data = detail.get("data") or {}
        # A single Zotel reservation (transaction_id) can contain multiple
        # rooms; each becomes its own OS10 booking, so this now yields one
        # ZotelSyncRecord PER ROOM, not per reservation.
        records.extend(_import_reservation(db, sync_log, transaction_id, data))

    for r in records:
        db.add(r)
        if r.outcome == "imported":
            sync_log.records_imported += 1
        elif r.outcome == "updated":
            sync_log.records_updated += 1
        elif r.outcome == "skipped":
            sync_log.records_skipped += 1
        elif r.outcome == "unmapped":
            sync_log.records_unmapped += 1
        elif r.outcome == "conflicted":
            sync_log.records_conflicted += 1
        elif r.outcome == "failed":
            sync_log.records_failed += 1

    if sync_log.records_failed or sync_log.records_conflicted:
        sync_log.status = "partial" if (sync_log.records_imported or sync_log.records_updated or sync_log.records_skipped) else "failed"
    sync_log.message = (
        f"fetched={sync_log.records_fetched} imported={sync_log.records_imported} "
        f"updated={sync_log.records_updated} skipped={sync_log.records_skipped} "
        f"unmapped={sync_log.records_unmapped} conflicted={sync_log.records_conflicted} failed={sync_log.records_failed}"
    )
    # "updated" is used for exactly one thing in this file: a propagated
    # Zotel cancellation (see _reconcile_existing) — collected here rather
    # than dispatched inline, since this transaction isn't committed until
    # just below, and dispatch_cancellation_notifications opens its own
    # session (would read this booking's pre-cancellation state if called
    # any earlier).
    cancelled_booking_ids = [r.booking_id for r in records if r.outcome == "updated" and r.booking_id is not None]

    db.commit()
    db.refresh(sync_log)

    for booking_id in cancelled_booking_ids:
        dispatch_cancellation_notifications(booking_id, None)

    return sync_log


def _to_minor(amount) -> int:
    """Rupees (float/str/None) -> integer paise. Non-numeric/None -> 0.
    Used so every allocation is done in exact integer minor units and can
    never drift by a rounding fraction."""
    if amount is None:
        return 0
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return 0


def _allocate_minor_units(total_minor: int, n: int) -> list[int]:
    """Split an integer minor-unit total into n deterministic shares whose
    sum is EXACTLY total_minor. Equal split; the rounding remainder is handed
    to the earliest rooms (largest-remainder, index-stable), so there is zero
    drift and the assignment is reproducible run to run."""
    if n <= 0:
        return []
    base = total_minor // n
    rem = total_minor - base * n
    return [base + (1 if i < rem else 0) for i in range(n)]


def _room_external_id(room: dict, category_id: str, seq: int) -> str:
    """Stable per-room identity within a reservation. Prefer Zotel's own room
    id when the object carries one; otherwise a deterministic key of the
    room-type (category) id plus its occurrence index among rooms of that same
    category. In the normal case (distinct categories, one room each) this is
    just "<category_id>#0"; it stays stable across re-syncs regardless of array
    reordering as long as the per-category counts are stable."""
    return str(room.get("id") or f"{category_id}#{seq}")


def _find_existing_room_booking(db: Session, transaction_id: str, external_room_id: str, *, single_room: bool) -> Booking | None:
    """Idempotency lookup on the composite (external_reservation_id,
    external_room_id). A legacy single-room Zotel booking imported before
    external_room_id existed has it NULL; for a reservation that still has
    exactly one room, adopt and backfill that legacy row rather than creating
    a duplicate."""
    existing = db.scalar(
        select(Booking).where(
            Booking.external_reservation_id == transaction_id,
            Booking.external_room_id == external_room_id,
        )
    )
    if existing is not None:
        return existing
    if single_room:
        legacy = db.scalar(
            select(Booking).where(
                Booking.external_reservation_id == transaction_id,
                Booking.external_room_id.is_(None),
            )
        )
        if legacy is not None:
            legacy.external_room_id = external_room_id
            db.flush()
            return legacy
    return None


def _import_reservation(db: Session, sync_log: ZotelSyncLog, transaction_id: str, data: dict) -> list[ZotelSyncRecord]:
    """
    Import/reconcile ONE Zotel reservation, which may contain N rooms, into N
    OS10 bookings (one per room). Returns one ZotelSyncRecord per room.

    Room-type identity lives at rooms[i].category.id (name at
    rooms[i].category.name) in the REAL, observed Zotel reservation shape;
    there is no per-room amount and no per-room status — only the single
    reservation-level total_amount/paid_amount/status (and shared
    checkin/checkout/customer). Fails closed exactly as before when rooms is
    empty or dates are missing/unparsable.

    COMPOSITION of the two safeguards this reservation goes through:
      1. The new-reservation STATUS + STAY-RECENCY gate (ZOTEL_NEW_IMPORT_*)
         runs FIRST, per fresh (not-yet-imported) room, using that room's
         effective status (its own `status` if Zotel ever carries one, else
         the reservation status). A gated-out room is skipped/flagged exactly
         as the single-room path intended; reconciliation of an ALREADY
         imported room is untouched by the gate (handled by _reconcile_existing).
      2. For rooms that PASS the gate, per-room placement + same-unit collision
         handling then decides HOW they are placed and which are flagged.
    """
    zotel_status = data.get("status")
    rooms = data.get("rooms") or []
    existing_any = db.scalar(select(Booking).where(Booking.external_reservation_id == transaction_id).limit(1))

    if not rooms:
        return [_record(sync_log.id, transaction_id, "failed", existing_any.id if existing_any else None,
                        "Reservation has no room/category data in its rooms[] list")]

    checkin_raw, checkout_raw = data.get("checkin"), data.get("checkout")
    if not checkin_raw or not checkout_raw:
        return [_record(sync_log.id, transaction_id, "failed", existing_any.id if existing_any else None,
                        "Reservation missing checkin/checkout dates")]
    try:
        check_in = datetime.fromisoformat(checkin_raw).date() if isinstance(checkin_raw, str) else checkin_raw
        check_out = datetime.fromisoformat(checkout_raw).date() if isinstance(checkout_raw, str) else checkout_raw
    except ValueError:
        return [_record(sync_log.id, transaction_id, "failed", existing_any.id if existing_any else None,
                        "Reservation checkin/checkout dates could not be parsed")]

    nights = max((check_out - check_in).days, 1)
    single_room = len(rooms) == 1
    today = datetime.now(timezone.utc).date()

    # ---- Pass 1: build a descriptor per room (mapping + existing lookup) ----
    descriptors: list[dict] = []
    seq_by_cat: dict[str, int] = {}
    for idx, raw_room in enumerate(rooms):
        room = raw_room if isinstance(raw_room, dict) else {}
        category = room.get("category") if isinstance(room.get("category"), dict) else {}
        category_id = category.get("id")
        room_type_name = category.get("name")
        if not category_id:
            descriptors.append({"idx": idx, "room": room, "allocatable": False, "category_id": None,
                                "room_type_name": room_type_name, "external_room_id": None,
                                "mapping": None, "existing": None, "booking": None,
                                "disposition": None, "gate_detail": None})
            continue
        category_id = str(category_id)
        seq = seq_by_cat.get(category_id, 0)
        seq_by_cat[category_id] = seq + 1
        external_room_id = _room_external_id(room, category_id, seq)
        descriptors.append({"idx": idx, "room": room, "allocatable": True, "category_id": category_id,
                            "room_type_name": room_type_name, "external_room_id": external_room_id,
                            "mapping": _resolve_mapping(db, category_id),
                            "existing": _find_existing_room_booking(db, transaction_id, external_room_id, single_room=single_room),
                            "booking": None, "disposition": None, "gate_detail": None})

    # ---- Allocation across allocatable (real) rooms, in exact minor units ----
    allocatable_idxs = [d["idx"] for d in descriptors if d["allocatable"]]
    n_alloc = len(allocatable_idxs)
    acc_shares = _allocate_minor_units(_to_minor(data.get("total_amount")), n_alloc)
    paid_shares = _allocate_minor_units(_to_minor(data.get("paid_amount")), n_alloc)
    acc_by_idx = {idx: acc_shares[pos] for pos, idx in enumerate(allocatable_idxs)}
    paid_by_idx = {idx: paid_shares[pos] for pos, idx in enumerate(allocatable_idxs)}

    # ---- Pass 1.5: disposition per room (STATUS GATE composed here) ----
    for d in descriptors:
        if not d["allocatable"]:
            d["disposition"] = "failed"
        elif d["mapping"] is None:
            d["disposition"] = "unmapped"
        elif d["existing"] is not None:
            d["disposition"] = "reconcile"
        else:
            # Fresh (not-yet-imported) room: apply the new-reservation status
            # + stay-recency gate first, per room's effective status.
            effective_status = d["room"].get("status") or zotel_status
            normalized = (effective_status or "").strip().lower()
            if normalized in ZOTEL_NEW_IMPORT_SKIP_STATUSES:
                d["disposition"] = "skipped"
                d["gate_detail"] = f"Zotel reservation status '{normalized}' is not imported as a new booking (room {d['external_room_id']})"
            elif normalized in ZOTEL_NEW_IMPORT_ACTIVE_STATUSES:
                if check_out <= today:
                    d["disposition"] = "skipped"
                    d["gate_detail"] = f"Historical Zotel reservation (check_out {check_out} on or before today); not imported (room {d['external_room_id']})"
                else:
                    d["disposition"] = "place"
            else:
                d["disposition"] = "conflicted_status"
                d["gate_detail"] = f"Unrecognized Zotel reservation status {effective_status!r}; flagged for manual review (room {d['external_room_id']})"

    # ---- Same-unit collision detection among rooms that PASSED the gate ----
    # Two rooms of the same reservation that resolve (via mapping) to the same
    # OS10 unit cannot both be placed without an overlapping booking.
    candidate_units: dict[uuid.UUID, list[dict]] = {}
    for d in descriptors:
        if d["disposition"] == "place":
            candidate_units.setdefault(d["mapping"].unit_id, []).append(d)
    collided_idxs = {d["idx"] for group in candidate_units.values() if len(group) > 1 for d in group}
    reservation_has_collision = bool(collided_idxs)
    defer_note = (
        " Reservation payment deferred: this reservation has an unresolved same-unit collision; "
        "the single Zotel payment cannot be safely allocated until a human resolves it and it is re-synced."
    )

    # ---- Pass 2: produce a record per room ----
    guest: Guest | None = None
    records: list[ZotelSyncRecord] = []
    for d in descriptors:
        idx, disp, external_room_id = d["idx"], d["disposition"], d["external_room_id"]

        if disp == "failed":
            records.append(_record(sync_log.id, transaction_id, "failed", None,
                                   f"Room #{idx} has no room/category data in its rooms[] entry"))
            continue
        if disp == "unmapped":
            name_suffix = f" (name={d['room_type_name']!r})" if d["room_type_name"] else ""
            records.append(_record(sync_log.id, transaction_id, "unmapped",
                                   d["existing"].id if d["existing"] else None,
                                   f"No OS10 unit mapped for Zotel room_type_id={d['category_id']}{name_suffix} (room {external_room_id})"))
            continue
        if disp == "reconcile":
            effective_status = d["room"].get("status") or zotel_status
            d["booking"] = d["existing"]
            records.append(_reconcile_existing(db, sync_log, d["existing"], effective_status,
                                               check_in, check_out, data, d["mapping"].unit_id, external_room_id))
            continue
        if disp == "skipped":
            records.append(_record(sync_log.id, transaction_id, "skipped", None, d["gate_detail"]))
            continue
        if disp == "conflicted_status":
            records.append(_record(sync_log.id, transaction_id, "conflicted", None, d["gate_detail"]))
            continue

        # disp == "place": gate passed; now placement / collision handling.
        if idx in collided_idxs:
            unit_id = d["mapping"].unit_id
            peers = ", ".join(sorted(p["external_room_id"] for p in candidate_units[unit_id]))
            records.append(_record(sync_log.id, transaction_id, "conflicted", None,
                                   f"Zotel reservation {transaction_id} has multiple rooms ({peers}) that all map to the "
                                   f"same OS10 unit {unit_id} for {check_in}..{check_out}; they cannot all be placed "
                                   f"without an overlapping booking. Flagged for manual review — no booking created for "
                                   f"room {external_room_id}.{defer_note}"))
            continue

        if guest is None:
            guest = _find_or_create_guest(db, _extract_customer(data))
        booking, fail_record = _place_new_booking(
            db, sync_log, transaction_id, external_room_id, d["mapping"], d["room"],
            check_in, check_out, nights, acc_by_idx[idx], data, guest,
        )
        if fail_record is not None:
            records.append(fail_record)
            continue
        d["booking"] = booking
        rec = _record(sync_log.id, transaction_id, "imported", booking.id,
                      f"Imported room {external_room_id} into unit {d['mapping'].unit_id}")
        d["record"] = rec
        records.append(rec)

    # ---- Payment integrity gate: record the single reservation payment in
    # FULL or defer it in FULL, never partially (R1) ----
    # paid_amount is allocated across EVERY allocatable room. A room that was
    # not placed into a booking — unplaceable (mapped unit in maintenance, an
    # overlapping booking/hold, over max-occupancy, or the DB EXCLUDE
    # backstop), unmapped, or carrying an unrecognized status — would
    # otherwise have its allocated share silently dropped while the placeable
    # rooms recorded theirs, leaving OS10 with a Payment total STRICTLY LESS
    # than Zotel's paid_amount and nothing flagged (silent under-recording).
    # So, mirroring the same-unit collision rule, defer the WHOLE reservation
    # payment whenever any room that was handed a positive paid share could
    # not be placed. It is recorded in full once the obstruction is resolved
    # and the reservation is re-synced (idempotent — never double-recorded).
    orphaned_paid_minor = sum(
        paid_by_idx.get(d["idx"], 0)
        for d in descriptors
        if d["allocatable"] and d["booking"] is None
    )
    payment_incomplete = orphaned_paid_minor > 0
    payment_deferred = reservation_has_collision or payment_incomplete

    incomplete_note = (
        " Reservation payment deferred: one or more rooms in this reservation could not be placed "
        "in OS10, so the single Zotel payment cannot be safely allocated across a partially-imported "
        "reservation without under-recording it; it will be recorded in full once the unplaceable "
        "room(s) are resolved and the reservation is re-synced."
    )
    note = defer_note if reservation_has_collision else (incomplete_note if payment_incomplete else None)
    if note:
        # Surface the deferral on every room actually imported, so a short
        # recorded amount is never silent — staff see WHY on the sync record.
        for d in descriptors:
            rec = d.get("record")
            if rec is not None and rec.detail and note not in rec.detail:
                rec.detail += note

    if not payment_deferred:
        for d in descriptors:
            if not d["allocatable"] or d["booking"] is None:
                continue
            _maybe_record_payment(db, d["booking"], data, paid_by_idx.get(d["idx"], 0))

    return records


def _place_new_booking(
    db: Session, sync_log: ZotelSyncLog, transaction_id: str, external_room_id: str,
    mapping: ZotelRoomMapping, room: dict, check_in, check_out, nights: int,
    accommodation_minor: int, data: dict, guest: Guest,
) -> tuple[Booking | None, ZotelSyncRecord | None]:
    """Place a single fresh room-booking, reusing every existing new-import
    safeguard (unit.status, per-unit advisory lock, occupancy, availability,
    DB EXCLUDE backstop). The caller has already applied the status/recency
    gate and collision check; this does the actual placement. Returns
    (booking, None) on success or (None, record) with a conflicted record on
    rejection. The guest passed in was created once per reservation from the
    NORMALIZED customer (see _extract_customer/_find_or_create_guest), so
    phone normalization is applied to every room-booking."""
    # Phase 5A: unit.status checked explicitly first so the detail says WHY
    # (maintenance/blocked) rather than reusing the overlap message. Zotel has
    # no write API to receive a block (see client.py) — this only stops OS10
    # from silently creating a confirmed Booking, not the OTA from selling it.
    local_unit = db.get(Unit, mapping.unit_id)
    if local_unit is not None and local_unit.status != "available":
        return None, _record(
            sync_log.id, transaction_id, "conflicted", None,
            f"Zotel reservation for unit {mapping.unit_id} ({check_in}..{check_out}) was not imported — "
            f"the local unit is currently marked '{local_unit.status}' in OS10 (not 'available'); "
            "review this reservation manually — Zotel has no way to receive a block from OS10, so the "
            f"OTA listing itself must be updated separately if the unit should stop being sold there (room {external_room_id})",
        )
    # Serialize imports with direct/public booking and hold creation. The DB
    # exclusion constraints cannot compare an imported booking with an active
    # hold, so all reservation writers share this per-unit transaction lock
    # before checking availability and inserting.
    lock_unit_for_reservation(db, mapping.unit_id)
    adults, children = 1, 0
    if local_unit is not None:
        db.refresh(local_unit)
        try:
            adults = int(room.get("num_adults") or 1)
            children = int(room.get("num_children") or 0)
        except (TypeError, ValueError):
            return None, _record(sync_log.id, transaction_id, "conflicted", None,
                                 f"Zotel guest count is invalid; review manually (room {external_room_id})")
        if adults + children > local_unit.max_occupancy:
            return None, _record(
                sync_log.id, transaction_id, "conflicted", None,
                f"Zotel reservation for unit {mapping.unit_id} has {adults + children} guests, exceeding "
                f"the OS10 maximum occupancy of {local_unit.max_occupancy}; review manually (room {external_room_id})",
            )
    else:
        try:
            adults = int(room.get("num_adults") or 1)
            children = int(room.get("num_children") or 0)
        except (TypeError, ValueError):
            adults, children = 1, 0
    if not unit_is_available(db, mapping.unit_id, check_in, check_out):
        return None, _record(
            sync_log.id, transaction_id, "conflicted", None,
            f"Zotel reservation for unit {mapping.unit_id} ({check_in}..{check_out}) overlaps an existing OS10 "
            f"booking/hold — not imported; existing OS10 record left untouched (room {external_room_id})",
        )

    rate_applied = round((accommodation_minor / 100.0) / nights, 2) if accommodation_minor else 0.0
    booking = Booking(
        unit_id=mapping.unit_id,
        guest_id=guest.id,
        check_in=check_in,
        check_out=check_out,
        status="confirmed",
        source="zotel",
        rate_applied=rate_applied,
        adults=adults,
        children=children,
        special_requests=data.get("special_requests"),
        internal_notes=f"Imported from Zotel (booking_source={data.get('booking_source')}, ota_booking_id={data.get('ota_booking_id')}, room={external_room_id})",
        external_reservation_id=transaction_id,
        external_room_id=external_room_id,
    )
    db.add(booking)
    try:
        db.flush()
    except Exception as e:  # noqa: BLE001 — DB EXCLUDE constraint is the final guard; a race here must not corrupt the sync
        db.rollback()
        db.add(sync_log)  # sync_log itself was rolled back too — re-attach for the caller's later commit
        logger.warning("Zotel import: DB rejected booking for reservation %s (room %s): %s", transaction_id, external_room_id, type(e).__name__)
        return None, _record(sync_log.id, transaction_id, "conflicted", None,
                             f"Database rejected this booking as an overlapping reservation at insert time (room {external_room_id})")
    return booking, None


def _reconcile_existing(db: Session, sync_log: ZotelSyncLog, booking: Booking, zotel_status: str | None, check_in, check_out, data: dict, mapped_unit_id: uuid.UUID, external_room_id: str | None = None) -> ZotelSyncRecord:
    if booking.status not in SAFE_TO_AUTO_UPDATE_STATUSES:
        # Already checked in/out/cancelled locally — never let an external
        # feed reach back into an operationally-advanced record.
        if zotel_status == "cancelled" and booking.status == "cancelled":
            return _record(sync_log.id, booking.external_reservation_id, "skipped", booking.id, "Already cancelled on both sides")
        return _record(
            sync_log.id, booking.external_reservation_id, "conflicted", booking.id,
            f"OS10 booking has already progressed to status={booking.status}; Zotel now reports status={zotel_status} — left untouched",
        )

    if zotel_status == "cancelled":
        # OS10 Phase 2B: routed through the same single authoritative
        # cancellation workflow the manual OS10 endpoint uses (see
        # bookings/cancellation_service.py) — status change, availability
        # release, and audit all happen there, exactly once, for either
        # caller. The shared workflow records the policy snapshot, but this
        # OTA-originated cancellation remains reconciliation-only. A
        # Zotel-recorded payment (mode="zotel") has no OS10 Razorpay payment
        # reference to refund through —
        # money collected via an OTA is refunded through that OTA/Zotel,
        # not through OS10. The paid amount, if any, is surfaced in the
        # audit trail so staff know a refund decision is pending.
        paid_amount = data.get("paid_amount")
        source_note = f"zotel_transaction_id={booking.external_reservation_id}"
        if paid_amount and float(paid_amount) > 0:
            source_note += f", paid_amount={float(paid_amount):.2f} (refund decision pending — see Phase 2B cancellation-policy note; not refundable via Razorpay, no gateway payment on this booking)"
        cancellation_service.cancel_booking(
            db, booking, actor_user_id=sync_log.triggered_by, refund_amount=None, refund_reason=None,
            source=f"zotel ({source_note})",
        )
        db.flush()
        # Notification dispatch is deliberately NOT done here: this
        # function's db session isn't committed yet (sync_bookings()
        # commits once at the end, after processing every reservation in
        # the batch), and dispatch_cancellation_notifications opens its
        # OWN session — calling it now would read this booking's
        # pre-cancellation state. sync_bookings() dispatches it for every
        # booking_id in its own_result.cancelled_booking_ids, after its
        # commit succeeds. See this function's return value below.
        return _record(sync_log.id, booking.external_reservation_id, "updated", booking.id, "Propagated Zotel cancellation to OS10 booking")

    if booking.check_in != check_in or booking.check_out != check_out:
        return _record(
            sync_log.id, booking.external_reservation_id, "conflicted", booking.id,
            f"Zotel now reports different dates ({check_in}..{check_out}) than the OS10 booking ({booking.check_in}..{booking.check_out}) — not auto-applied",
        )

    if booking.unit_id != mapped_unit_id:
        return _record(
            sync_log.id, booking.external_reservation_id, "conflicted", booking.id,
            f"The current Zotel room mapping points to OS10 unit {mapped_unit_id}, but this existing booking is on OS10 unit {booking.unit_id}; "
            "the existing booking will NOT be auto-moved. Manual review is required.",
        )

    return _record(sync_log.id, booking.external_reservation_id, "skipped", booking.id, "No safe change detected; already in sync")


# Tolerance (in integer paise) below which a target-vs-recorded difference is
# treated as rounding noise and not acted on. Keeps delta recording stable
# against sub-paise float representation of rupee amounts.
_PAYMENT_DELTA_TOLERANCE_MINOR = 1


def _maybe_record_payment(db: Session, booking: Booking, data: dict, target_minor: int) -> None:
    """Record this room-booking's allocated share of the reservation payment,
    DELTA-BASED and idempotent. `target_minor` is the per-room share in exact
    integer paise the caller computed from the reservation-level paid_amount
    (see _import_reservation's _allocate_minor_units), NEVER the full
    paid_amount per room.

    Behaviour:
      - target - recorded > 1 paise  -> insert ONE new mode="zotel" Payment row
        for the delta (so a later Zotel installment / top-up, where paid_amount
        INCREASED, is captured — the previous silent under-recording is fixed).
      - target <= recorded           -> no-op (an unchanged re-sync never
        double-records; the row is still recorded exactly once overall).
      - recorded > target            -> an OTA amount DECREASE is NEVER
        auto-reversed with a negative row here; it is only logged and left for
        run_reconciliation to surface as an amount_mismatch. Conservative by
        design — money already recorded is not silently clawed back.

    `recorded` is the sum of this booking's existing POSITIVE mode="zotel"
    Payment rows, measured in the same exact integer paise unit (via _to_minor)
    so the comparison never drifts on float rupee representation."""
    existing = db.scalars(
        select(Payment).where(
            Payment.booking_id == booking.id, Payment.mode == "zotel", Payment.amount > 0
        )
    ).all()
    recorded_minor = sum(_to_minor(p.amount) for p in existing)
    delta_minor = int(target_minor) - recorded_minor

    if delta_minor > _PAYMENT_DELTA_TOLERANCE_MINOR:
        payment = Payment(
            booking_id=booking.id,
            amount=round(delta_minor / 100.0, 2),
            mode="zotel",
            status="completed",
            reference_no=data.get("transaction_id"),
        )
        db.add(payment)
        db.flush()
    elif delta_minor < -_PAYMENT_DELTA_TOLERANCE_MINOR:
        # OTA-reported amount decreased below what OS10 already recorded.
        # Do NOT write a negative/reversing row automatically — leave it for
        # reconciliation (run_reconciliation) to flag as an amount_mismatch.
        logger.warning(
            "Zotel import: reservation %s booking %s already has recorded zotel payments "
            "(%d paise) exceeding the current allocated share (%d paise); not auto-reversing — "
            "reconciliation will flag this as an amount mismatch.",
            data.get("transaction_id"), booking.id, recorded_minor, int(target_minor),
        )
    # else: within tolerance -> idempotent no-op (already recorded in full).


def _persist_reconciliation_log(db: Session, triggered_by: uuid.UUID | None, records_fetched: int, summary: dict, errored: bool, error_message: str | None) -> None:
    """
    Records one ZotelSyncLog row per reconciliation run — the other half of
    the sync-logging gap disclosed in the Task K final report (item 17).
    Reconciliation still makes NO writes to Zotel or to OS10's operational
    data (Booking/Guest/Payment); this is the one write it does make — its
    own audit-trail row, so the run's outcome is still visible afterward.

    Counter mapping onto the same fields sync_bookings() uses (so both
    sync_types render in one consistent Sync Logs table):
      - records_fetched   = Zotel reservations retrieved for comparison
      - records_skipped   = "matched" — nothing needs action
      - records_conflicted = duplicate + conflicting + cancelled_mismatch —
        real disagreements between the two systems
      - records_unmapped  = missing_in_os10 + missing_in_zotel — present in
        only one system (reusing this field for "one-sided", the closest
        existing meaning; there is no dedicated counter for it)
      - records_imported/updated = 0 (reconciliation never imports/updates)
      - records_failed    = 1 if the Zotel API call itself failed, else 0
    """
    if errored:
        sync_log = ZotelSyncLog(
            sync_type="reconciliation", status="failed", message=error_message,
            records_fetched=0, records_failed=1, triggered_by=triggered_by,
        )
        db.add(sync_log)
        db.commit()
        return

    conflicted = summary.get("duplicate", 0) + summary.get("conflicting", 0) + summary.get("cancelled_mismatch", 0)
    unmapped = summary.get("missing_in_os10", 0) + summary.get("missing_in_zotel", 0)
    status = "success" if (conflicted == 0 and unmapped == 0) else "partial"
    message = (
        f"reservations_fetched={records_fetched} matched={summary.get('matched', 0)} "
        f"missing_in_os10={summary.get('missing_in_os10', 0)} missing_in_zotel={summary.get('missing_in_zotel', 0)} "
        f"duplicate={summary.get('duplicate', 0)} cancelled_mismatch={summary.get('cancelled_mismatch', 0)}"
    )
    sync_log = ZotelSyncLog(
        sync_type="reconciliation", status=status, message=message,
        records_fetched=records_fetched, records_skipped=summary.get("matched", 0),
        records_conflicted=conflicted, records_unmapped=unmapped, triggered_by=triggered_by,
    )
    db.add(sync_log)
    db.commit()


def run_reconciliation(db: Session, triggered_by: uuid.UUID | None = None) -> dict:
    """
    Pure read+compare: fetches current Zotel reservations and compares
    against OS10's own external_reservation_id-linked bookings. Makes NO
    writes to Zotel or to OS10's operational data (Booking/Guest/Payment)
    — the sync_bookings() import above is the only place those writes
    happen, and it is intentionally separate from this report. The one
    write this function does make is its own ZotelSyncLog audit row (see
    _persist_reconciliation_log), so a run's result is still visible in
    the Sync Logs list afterward, not just in the immediate HTTP response.
    """
    entries: list[dict] = []
    summary = {"matched": 0, "missing_in_os10": 0, "duplicate": 0, "unmapped": 0, "conflicting": 0, "cancelled_mismatch": 0}

    try:
        zotel_reservations = client.get_all_bookings()
    except (ZotelNotConfiguredError, ZotelAPIError) as e:
        _persist_reconciliation_log(db, triggered_by, records_fetched=0, summary=summary, errored=True, error_message=str(e))
        return {
            "generated_at": datetime.now(timezone.utc),
            "entries": [{"external_reservation_id": "", "category": "error", "detail": str(e)}],
            "summary": summary,
        }

    # A single Zotel reservation (transaction_id) can legitimately map to N
    # OS10 bookings (one per room). get_all_bookings returns reservation
    # SUMMARIES (no per-room breakdown), so dedupe by transaction_id and then
    # compare at ROOM granularity against every OS10 booking that carries that
    # external_reservation_id — multiple matches are expected now, not a
    # "duplicate" error.
    zotel_ids_seen: set[str] = set()
    reservations_by_txn: dict[str, dict] = {}
    for res in zotel_reservations:
        txn_id = res.get("transaction_id")
        if not txn_id:
            continue
        reservations_by_txn[txn_id] = res  # dedupe; a later page wins

    for txn_id, res in reservations_by_txn.items():
        zotel_ids_seen.add(txn_id)

        matches = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        if len(matches) == 0:
            summary["missing_in_os10"] += 1
            entries.append({"external_reservation_id": txn_id, "category": "missing_in_os10", "detail": "Reservation exists in Zotel but has no matching OS10 booking"})
            continue

        zotel_cancelled = res.get("status") == "cancelled"
        for os10_booking in matches:
            os10_cancelled = os10_booking.status == "cancelled"
            room_label = f" room={os10_booking.external_room_id}" if os10_booking.external_room_id else ""
            if zotel_cancelled != os10_cancelled:
                summary["cancelled_mismatch"] += 1
                entries.append({"external_reservation_id": txn_id, "category": "cancelled_mismatch", "detail": f"Zotel status={res.get('status')}, OS10 status={os10_booking.status}{room_label}"})
            else:
                summary["matched"] += 1
                entries.append({"external_reservation_id": txn_id, "category": "matched", "detail": f"OK{room_label}"})

    os10_zotel_bookings = db.scalars(select(Booking).where(Booking.external_reservation_id.is_not(None))).all()
    for b in os10_zotel_bookings:
        if b.external_reservation_id not in zotel_ids_seen:
            summary["missing_in_os10"] = summary.get("missing_in_os10", 0)  # no-op, keeps key present
            entries.append({"external_reservation_id": b.external_reservation_id, "category": "missing_in_zotel", "detail": f"OS10 booking {b.id} references a Zotel reservation no longer returned by GET /bookings"})
            summary["missing_in_zotel"] = summary.get("missing_in_zotel", 0) + 1

    _persist_reconciliation_log(db, triggered_by, records_fetched=len(zotel_reservations), summary=summary, errored=False, error_message=None)
    return {"generated_at": datetime.now(timezone.utc), "entries": entries, "summary": summary}
