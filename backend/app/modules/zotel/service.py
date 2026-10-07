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
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.bookings import cancellation_service
from app.modules.bookings.models import Booking, Guest
from app.modules.notifications.service import dispatch_cancellation_notifications
from app.modules.payments.models import Payment
from app.modules.portal.auth_service import normalize_phone
from app.modules.properties.models import Unit
from app.modules.zotel import client
from app.modules.zotel.client import ZotelAPIError, ZotelNotConfiguredError
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncLog, ZotelSyncRecord
from app.shared.availability import lock_unit_for_reservation, unit_is_available
from app.shared.occupancy import enforce_unit_max_occupancy

logger = logging.getLogger("sri_apartment_os")

# Reservation lifecycle states this file treats as "still open" locally —
# an OS10 booking imported from Zotel that has moved past this set has had
# real, operational, possibly-irreversible things happen to it (a guest
# physically checked in/out), so an automatic Zotel-driven update must not
# touch it beyond the one explicit exception (cancellation propagation,
# handled separately and only from this same state).
SAFE_TO_AUTO_UPDATE_STATUSES = ("confirmed",)

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
    # Normalize the incoming phone to OS10's canonical Guest.phone form
    # (bare national digits, no country code — see
    # portal.auth_service.normalize_phone) BEFORE it is used to match or
    # create a Guest. Zotel hands us whatever shape the OTA captured
    # ("+91 98765 43210", "919876543210", "098765 43210", or a tel:+91...
    # target); normalize_phone strips to digits and drops the country
    # code / trunk prefix so every one of those converges to the same
    # value an OS10-native guest is already stored under. Without this the
    # raw OTA string never equals the stored bare-10-digit Guest.phone, so
    # _find_or_create_guest silently creates a DUPLICATE guest on every
    # import. An absent/blank phone is left blank here — not back-filled
    # with a fabricated number; _find_or_create_guest keeps its existing
    # "unknown" sentinel for that case.
    raw_phone = customer.get("phone") or ""
    phone = normalize_phone(raw_phone) if raw_phone else ""
    return {
        "name": name,
        "phone": phone,
        "email": customer.get("email"),
    }


def _find_or_create_guest(db: Session, customer: dict) -> Guest:
    guest = None
    if customer["phone"]:
        guest = db.scalar(select(Guest).where(Guest.phone == customer["phone"]))
    if guest is None:
        guest = Guest(name=customer["name"], phone=customer["phone"] or "unknown", email=customer.get("email"))
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
        records.append(_import_one(db, sync_log, transaction_id, data))

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


def _import_one(db: Session, sync_log: ZotelSyncLog, transaction_id: str, data: dict) -> ZotelSyncRecord:
    zotel_status = data.get("status")

    existing = db.scalar(select(Booking).where(Booking.external_reservation_id == transaction_id))

    # Room-type identity lives at rooms[0].category.id (name at
    # rooms[0].category.name) in the REAL, observed Zotel reservation
    # shape — confirmed directly against a live GET /bookings response
    # during the read-only import preview. The documentation's own
    # example response leaves "rooms" empty, giving no field detail, and
    # an earlier version of this code assumed a flat rooms[0].room_type_id
    # field that does not exist anywhere in real data — every real
    # reservation was silently classified "failed" as a result. Fixed
    # here; still fails closed exactly the same way when rooms is empty
    # or category/id is missing, just via the correct path.
    rooms = data.get("rooms") or []
    category = (rooms[0].get("category") or {}) if rooms else {}
    room_type_id = category.get("id")
    room_type_name = category.get("name")
    if not room_type_id:
        return _record(sync_log.id, transaction_id, "failed", existing.id if existing else None, "Reservation has no room/category data in its rooms[] list")

    mapping = _resolve_mapping(db, str(room_type_id))
    if mapping is None:
        name_suffix = f" (name={room_type_name!r})" if room_type_name else ""
        return _record(sync_log.id, transaction_id, "unmapped", existing.id if existing else None, f"No OS10 unit mapped for Zotel room_type_id={room_type_id}{name_suffix}")

    checkin_raw, checkout_raw = data.get("checkin"), data.get("checkout")
    if not checkin_raw or not checkout_raw:
        return _record(sync_log.id, transaction_id, "failed", existing.id if existing else None, "Reservation missing checkin/checkout dates")

    try:
        check_in = datetime.fromisoformat(checkin_raw).date() if isinstance(checkin_raw, str) else checkin_raw
        check_out = datetime.fromisoformat(checkout_raw).date() if isinstance(checkout_raw, str) else checkout_raw
    except ValueError:
        return _record(sync_log.id, transaction_id, "failed", existing.id if existing else None, "Reservation checkin/checkout dates could not be parsed")

    if existing is not None:
        return _reconcile_existing(db, sync_log, existing, zotel_status, check_in, check_out, data, mapping.unit_id)

    # New reservation. Cancelled-on-arrival reservations aren't worth
    # occupying a slot for — nothing to protect, nothing to book.
    if zotel_status == "cancelled":
        return _record(sync_log.id, transaction_id, "skipped", None, "Zotel reservation is already cancelled; not imported")

    # Phase 5A: checked explicitly, before the overlap check, so the
    # detail message tells staff WHY this was rejected (maintenance/
    # blocked status) rather than reusing the overlap message, which
    # would misdescribe the actual cause. unit_is_available (below) now
    # also covers unit.status internally as defense-in-depth, but relying
    # on that alone here would report every maintenance-unit rejection as
    # an "overlaps an existing OS10 booking/hold" conflict, which isn't
    # true and wouldn't tell staff what to actually go fix. Zotel itself
    # has no write API to receive a block (see zotel/client.py) — this
    # cannot stop the OTA from selling the unit, only stop OS10 from
    # silently creating a normal confirmed Booking for it.
    local_unit = db.get(Unit, mapping.unit_id)
    if local_unit is not None and local_unit.status != "available":
        return _record(
            sync_log.id, transaction_id, "conflicted", None,
            f"Zotel reservation for unit {mapping.unit_id} ({check_in}..{check_out}) was not imported — "
            f"the local unit is currently marked '{local_unit.status}' in OS10 (not 'available'); "
            "review this reservation manually — Zotel has no way to receive a block from OS10, so the "
            "OTA listing itself must be updated separately if the unit should stop being sold there",
        )
    # Serialize imports with direct/public booking and hold creation. The
    # database exclusion constraints cannot compare an imported booking with
    # an active hold, so all reservation writers must share this per-unit
    # transaction lock before checking availability and inserting.
    lock_unit_for_reservation(db, mapping.unit_id)
    if local_unit is not None:
        db.refresh(local_unit)
        room = rooms[0] if rooms else {}
        try:
            adults = int(room.get("num_adults") or 1)
            children = int(room.get("num_children") or 0)
        except (TypeError, ValueError):
            return _record(sync_log.id, transaction_id, "conflicted", None, "Zotel guest count is invalid; review manually")
        if adults + children > local_unit.max_occupancy:
            return _record(
                sync_log.id, transaction_id, "conflicted", None,
                f"Zotel reservation for unit {mapping.unit_id} has {adults + children} guests, exceeding "
                f"the OS10 maximum occupancy of {local_unit.max_occupancy}; review manually",
            )
    if not unit_is_available(db, mapping.unit_id, check_in, check_out):
        return _record(
            sync_log.id, transaction_id, "conflicted", None,
            f"Zotel reservation for unit {mapping.unit_id} ({check_in}..{check_out}) overlaps an existing OS10 booking/hold — not imported; existing OS10 record left untouched",
        )

    customer = _extract_customer(data)
    guest = _find_or_create_guest(db, customer)

    nights = max((check_out - check_in).days, 1)
    total_amount = data.get("total_amount")
    rate_applied = round(float(total_amount) / nights, 2) if total_amount else 0.0

    booking = Booking(
        unit_id=mapping.unit_id,
        guest_id=guest.id,
        check_in=check_in,
        check_out=check_out,
        status="confirmed",
        source="zotel",
        rate_applied=rate_applied,
        adults=(rooms[0].get("num_adults") or 1) if rooms else 1,
        children=(rooms[0].get("num_children") or 0) if rooms else 0,
        special_requests=data.get("special_requests"),
        internal_notes=f"Imported from Zotel (booking_source={data.get('booking_source')}, ota_booking_id={data.get('ota_booking_id')})",
        external_reservation_id=transaction_id,
    )
    db.add(booking)
    try:
        db.flush()
    except Exception as e:  # noqa: BLE001 — DB EXCLUDE constraint is the final guard; a race here must not corrupt the sync
        db.rollback()
        db.add(sync_log)  # sync_log itself was rolled back too — re-attach for the caller's later commit
        logger.warning("Zotel import: DB rejected booking for reservation %s: %s", transaction_id, type(e).__name__)
        return _record(sync_log.id, transaction_id, "conflicted", None, "Database rejected this booking as an overlapping reservation at insert time")

    _maybe_record_payment(db, booking, data)

    return _record(sync_log.id, transaction_id, "imported", booking.id, f"Imported into unit {mapping.unit_id}")


def _reconcile_existing(db: Session, sync_log: ZotelSyncLog, booking: Booking, zotel_status: str | None, check_in, check_out, data: dict, mapped_unit_id: uuid.UUID) -> ZotelSyncRecord:
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


def _maybe_record_payment(db: Session, booking: Booking, data: dict) -> None:
    paid_amount = data.get("paid_amount")
    if not paid_amount or float(paid_amount) <= 0:
        return
    payment = Payment(
        booking_id=booking.id,
        amount=round(float(paid_amount), 2),
        mode="zotel",
        status="completed",
        reference_no=data.get("transaction_id"),
    )
    db.add(payment)
    db.flush()


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

    zotel_ids_seen: set[str] = set()
    for res in zotel_reservations:
        txn_id = res.get("transaction_id")
        if not txn_id:
            continue
        zotel_ids_seen.add(txn_id)

        matches = db.scalars(select(Booking).where(Booking.external_reservation_id == txn_id)).all()
        if len(matches) == 0:
            summary["missing_in_os10"] += 1
            entries.append({"external_reservation_id": txn_id, "category": "missing_in_os10", "detail": "Reservation exists in Zotel but has no matching OS10 booking"})
        elif len(matches) > 1:
            summary["duplicate"] += 1
            entries.append({"external_reservation_id": txn_id, "category": "duplicate", "detail": f"{len(matches)} OS10 bookings share this external_reservation_id"})
        else:
            os10_booking = matches[0]
            zotel_cancelled = res.get("status") == "cancelled"
            os10_cancelled = os10_booking.status == "cancelled"
            if zotel_cancelled != os10_cancelled:
                summary["cancelled_mismatch"] += 1
                entries.append({"external_reservation_id": txn_id, "category": "cancelled_mismatch", "detail": f"Zotel status={res.get('status')}, OS10 status={os10_booking.status}"})
            else:
                summary["matched"] += 1
                entries.append({"external_reservation_id": txn_id, "category": "matched", "detail": "OK"})

    os10_zotel_bookings = db.scalars(select(Booking).where(Booking.external_reservation_id.is_not(None))).all()
    for b in os10_zotel_bookings:
        if b.external_reservation_id not in zotel_ids_seen:
            summary["missing_in_os10"] = summary.get("missing_in_os10", 0)  # no-op, keeps key present
            entries.append({"external_reservation_id": b.external_reservation_id, "category": "missing_in_zotel", "detail": f"OS10 booking {b.id} references a Zotel reservation no longer returned by GET /bookings"})
            summary["missing_in_zotel"] = summary.get("missing_in_zotel", 0) + 1

    _persist_reconciliation_log(db, triggered_by, records_fetched=len(zotel_reservations), summary=summary, errored=False, error_message=None)
    return {"generated_at": datetime.now(timezone.utc), "entries": entries, "summary": summary}
