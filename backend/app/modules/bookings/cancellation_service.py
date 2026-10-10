"""
OS10 Phase 2B: the single authoritative cancellation workflow.

Both callers — the manual OS10 endpoint (bookings/router.py) and the
Zotel-triggered path (zotel/service.py) — call cancel_booking() below
instead of each implementing their own refund/credit-note/audit logic.
Per-caller concerns that genuinely differ (validating the status
transition is legal, translating outcomes into an HTTP response vs. a
ZotelSyncRecord outcome, committing the transaction, scheduling the
notification background task) stay with each caller; everything AFTER
"yes, cancel this booking now" lives here exactly once.

PROVIDER-REFUND SAFETY (the core of this phase): a refund against a
Razorpay-mode payment is only ever recorded as successful (a negative
Payment row created, a credit note issued, a "refund succeeded"
notification sent) after the provider call itself has actually succeeded.
An explicit pre-request configuration failure leaves a FAILED Refund row.
An ambiguous transport/provider failure leaves the Refund in CREATED and
keeps its amount reserved until reconciliation; neither case creates a
Payment row or credit note. The internal ledger never claims money moved
when it did not. Refunds
against non-Razorpay payments (cash/UPI/bank_transfer/zotel — money the
business already holds or that moved through a channel OS10 has no API
into) are recorded directly, exactly as the pre-existing manual refund
endpoint (payments/router.py::refund_payment) always has.

IDEMPOTENCY: Refund.gateway_refund_id carries the real Razorpay-side
refund identifier once one exists, with a DB-level partial unique index
(refunds_gateway_refund_id_unique, additive_migrations.py) guaranteeing
the same provider-side refund can never be recorded twice — not a Python
boolean. _refund_available_for_booking nets out every already-successful
refund (however it was recorded) before any new attempt is allowed, so a
retry after a failure — or a repeated cancellation call, blocked earlier
by ALLOWED_TRANSITIONS/with_for_update anyway — can never double-refund.
"""
import datetime as dt
import uuid
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.schemas import record_audit_log
from app.modules.bookings.models import Booking, Guest
from app.modules.payment_gateway.models import Refund
from app.modules.payment_gateway.service import GatewayNotConfiguredError, create_refund
from app.modules.payments.models import CreditNote, Invoice, Payment
from app.modules.payments.service import credit_note_gst_breakdown, generate_credit_note_pdf, next_credit_note_number
from app.modules.properties.models import Property, Unit

POLICY_TIMEZONE = ZoneInfo("Asia/Kolkata")
STANDARD_CHECKIN_TIME = dt.time(12, 30)


@dataclass
class RefundAttemptResult:
    """One original Payment's worth of a refund attempt."""
    original_payment: Payment
    amount: float
    succeeded: bool
    payment_row: Payment | None = None  # the new negative Payment row, if succeeded
    refund_row: Refund | None = None  # only for razorpay-mode attempts
    error: str | None = None


@dataclass
class CancellationOutcome:
    booking: Booking
    old_status: str
    refund_requested_amount: float | None = None
    refund_attempts: list[RefundAttemptResult] = field(default_factory=list)
    credit_note: CreditNote | None = None

    @property
    def refund_succeeded_amount(self) -> float:
        return round(sum(a.amount for a in self.refund_attempts if a.succeeded), 2)

    @property
    def refund_failed_amount(self) -> float:
        return round(sum(a.amount for a in self.refund_attempts if not a.succeeded), 2)

    @property
    def any_refund_failed(self) -> bool:
        return any(not a.succeeded for a in self.refund_attempts)


@dataclass(frozen=True)
class CancellationPolicyDecision:
    cancellation_at: dt.datetime
    scheduled_checkin_at: dt.datetime
    percentage: int
    target_amount: float
    amount_to_issue: float
    source: str
    requires_review: bool


class CancellationRefundAmountMismatch(ValueError):
    """The optional caller confirmation differs from the server policy result."""


def evaluate_cancellation_policy(
    check_in: dt.date,
    paid_amount: float,
    now: dt.datetime,
) -> tuple[dt.datetime, int, float, bool]:
    """Pure policy calculation for a normalized date and aware timestamp."""
    if now.tzinfo is None:
        raise ValueError("cancellation timestamp must be timezone-aware")
    scheduled_checkin_at = dt.datetime.combine(
        check_in, STANDARD_CHECKIN_TIME, tzinfo=POLICY_TIMEZONE,
    )
    cancellation_at = now.astimezone(dt.timezone.utc)
    checkin_utc = scheduled_checkin_at.astimezone(dt.timezone.utc)
    if cancellation_at >= checkin_utc:
        return scheduled_checkin_at, 0, 0.0, True
    percentage = 100 if checkin_utc - cancellation_at > dt.timedelta(hours=48) else 50
    return scheduled_checkin_at, percentage, round(paid_amount * percentage / 100, 2), False


def _completed_booking_payments(db: Session, booking_id: uuid.UUID) -> list[Payment]:
    return db.scalars(
        select(Payment).where(
            Payment.booking_id == booking_id,
            Payment.amount > 0,
            Payment.status == "completed",
        ).order_by(Payment.paid_at, Payment.id)
    ).all()


def _has_unresolved_provider_refund(db: Session, booking_id: uuid.UUID) -> bool:
    payment_ids = [p.id for p in _completed_booking_payments(db, booking_id)]
    if not payment_ids:
        return False
    return db.scalar(
        select(Refund.id).where(Refund.payment_id.in_(payment_ids), Refund.status == "created").limit(1)
    ) is not None


def _policy_refund_source(booking: Booking, payments: list[Payment]) -> tuple[str, bool]:
    modes = {p.mode for p in payments}
    source = (booking.source or "").lower()
    ota_origin = "zotel" in source or source.startswith("ota_") or bool(booking.external_reservation_id)
    if "zotel" in modes:
        return "ota_reconciliation", True
    # OTA-originated reservations must never be sent to Razorpay just
    # because a local row is tagged with that mode. Only explicitly
    # recorded local pay-at-property funds can use the manual ledger path.
    if ota_origin:
        if payments and all(p.mode in {"cash", "upi", "bank_transfer", "card"} for p in payments):
            return "manual_pay_at_property", False
        return "ota_reconciliation", True
    if any(p.mode == "razorpay" and not p.reference_no for p in payments):
        return "razorpay_reference_review", True
    if "razorpay" in modes:
        return "razorpay", False
    if payments:
        return "manual_payment", False
    return "unpaid", False


def calculate_cancellation_policy(
    db: Session, booking: Booking, *, now: dt.datetime | None = None,
) -> CancellationPolicyDecision:
    """Return the authoritative policy snapshot and currently issuable amount.

    The check-in boundary is the approved fixed 12:30 PM in Asia/Kolkata;
    guest-provided arrival_time and free-text unit overrides are ignored.
    Refund base is completed positive payment rows (gross, including the
    recorded GST/discount result) less completed and safely reserved refunds.
    """
    cancellation_at = now or dt.datetime.now(dt.timezone.utc)
    if cancellation_at.tzinfo is None:
        raise ValueError("cancellation timestamp must be timezone-aware")
    cancellation_at = cancellation_at.astimezone(dt.timezone.utc)
    paid_payments = _completed_booking_payments(db, booking.id)
    paid_total = round(sum(float(p.amount) for p in paid_payments), 2)
    available = refund_available_for_booking(db, booking.id)
    already_used = max(0.0, round(paid_total - available, 2))
    scheduled_checkin_at, percentage, target, after_checkin = evaluate_cancellation_policy(
        booking.check_in, paid_total, cancellation_at,
    )

    if after_checkin:
        requires_review = True
        source = "after_checkin_manual_review"
    else:
        source, requires_review = _policy_refund_source(booking, paid_payments)

    amount_to_issue = min(max(0.0, round(target - already_used, 2)), available)
    pending_rows = db.scalars(
        select(Refund).where(
            Refund.payment_id.in_([p.id for p in paid_payments]),
            Refund.status == "created",
        )
    ).all() if paid_payments else []
    # Any unresolved provider reservation requires reconciliation before
    # another automatic cancellation refund is issued, even if some policy
    # balance appears to remain.
    if pending_rows:
        requires_review = True
        amount_to_issue = 0.0

    return CancellationPolicyDecision(
        cancellation_at=cancellation_at,
        scheduled_checkin_at=scheduled_checkin_at,
        percentage=percentage,
        target_amount=target,
        amount_to_issue=amount_to_issue,
        source=source,
        requires_review=requires_review,
    )


def remaining_snapshot_refund(db: Session, booking: Booking) -> float:
    """Return only the outstanding portion of the persisted cancellation target."""
    if booking.cancellation_refund_target is None:
        raise CancellationRefundAmountMismatch("This legacy cancellation has no policy snapshot and requires manual review")
    paid_total = round(sum(float(p.amount) for p in _completed_booking_payments(db, booking.id)), 2)
    available = refund_available_for_booking(db, booking.id)
    already_used = max(0.0, round(paid_total - available, 2))
    return min(max(0.0, round(float(booking.cancellation_refund_target) - already_used, 2)), available)


def enforce_standalone_refund_policy(db: Session, booking: Booking, amount: float) -> None:
    """Prevent separate refund actions exceeding a finalized policy target.

    Bookings marked for manual review remain available to the admin-only
    manual refund flow; that explicit action is the reviewed exception.
    """
    if _has_unresolved_provider_refund(db, booking.id):
        raise CancellationRefundAmountMismatch(
            "A provider refund is unresolved for this booking; reconcile it before creating another refund"
        )
    if booking.status != "cancelled" or booking.cancellation_refund_target is None:
        return
    if booking.cancellation_requires_review:
        return
    remaining = remaining_snapshot_refund(db, booking)
    if amount > remaining + 0.009:
        raise CancellationRefundAmountMismatch(
            f"Refund exceeds the remaining cancellation-policy target (Rs.{remaining:.2f})"
        )


def refund_available_for_booking(db: Session, booking_id: uuid.UUID) -> float:
    """Sum of positive Payment.amount for this booking minus everything
    already successfully refunded against it (abs of every negative row —
    however that row was created, provider-mediated or manual)."""
    payments = db.scalars(select(Payment).where(Payment.booking_id == booking_id)).all()
    total_paid = sum(float(p.amount) for p in payments if float(p.amount) > 0 and p.status == "completed")
    total_refunded = sum(abs(float(p.amount)) for p in payments if float(p.amount) < 0)
    original_ids = [p.id for p in payments if float(p.amount) > 0 and p.status == "completed"]
    # A provider request with an ambiguous network outcome is intentionally
    # left in `created`; reserve that amount until a human reconciles it.
    # Processed refunds already have a matching negative Payment row and are
    # included in total_refunded above, so only pending rows are subtracted.
    provider_refunds = db.scalars(
        select(Refund).where(Refund.payment_id.in_(original_ids), Refund.status.in_(("created", "processed")))
    ).all() if original_ids else []
    negative_by_original: dict[uuid.UUID, float] = {}
    for payment in payments:
        if float(payment.amount) < 0 and payment.refund_of_payment_id:
            negative_by_original[payment.refund_of_payment_id] = (
                negative_by_original.get(payment.refund_of_payment_id, 0.0) + abs(float(payment.amount))
            )
    refunds_by_original: dict[uuid.UUID, float] = {}
    pending_by_original: dict[uuid.UUID, float] = {}
    for refund in provider_refunds:
        target = pending_by_original if refund.status == "created" else refunds_by_original
        target[refund.payment_id] = target.get(refund.payment_id, 0.0) + float(refund.amount)
    # Processed cancellation refunds also have negative Payment rows. Avoid
    # counting those twice, while still counting processed gateway refunds
    # created from the separate gateway-refund endpoint.
    unrepresented_processed = sum(
        max(0.0, amount - negative_by_original.get(original_id, 0.0))
        for original_id, amount in refunds_by_original.items()
    )
    pending_total = sum(pending_by_original.values())
    return round(total_paid - total_refunded - pending_total - unrepresented_processed, 2)


def _refund_one_payment(db: Session, original: Payment, take: float, reason: str, actor_user_id: uuid.UUID | None) -> RefundAttemptResult:
    """
    Refunds `take` against a single original Payment row. Dispatches to a
    real (mocked-in-tests) Razorpay refund when the original payment was
    actually made via Razorpay (mode="razorpay", the only mode that ever
    carries a real gateway payment ID in reference_no — see
    public_booking/router.py's _convert_hold_to_booking). Every other mode
    (cash/upi/bank_transfer/zotel) is money the business already holds or
    that moved through a channel OS10 has no refund API into, so it's
    recorded directly, exactly as the pre-existing manual endpoint does.
    """
    if original.mode == "razorpay" and original.reference_no:
        refund_row = Refund(payment_id=original.id, amount=take, reason=reason, status="created", created_by=actor_user_id)
        db.add(refund_row)
        db.flush()
        # Persist the cancellation and refund reservation before the provider
        # call. If the process dies after Razorpay accepts the request but
        # before we can save its response, the durable `created` row keeps
        # the amount reserved for reconciliation and prevents a duplicate
        # refund attempt. Zotel cancellations pass refund_amount=None and do
        # not enter this provider path.
        db.commit()
        try:
            result = create_refund(original.reference_no, take)
        except GatewayNotConfiguredError as e:
            refund_row.status = "failed"
            db.flush()
            return RefundAttemptResult(original_payment=original, amount=take, succeeded=False, refund_row=refund_row, error=str(e))
        except Exception as e:  # noqa: BLE001 — any provider/network failure must be caught, never propagate as a 500 that hides a failed-but-recorded attempt
            # The provider may have accepted the refund before the connection
            # timed out. Keep the reservation pending so cancellation retry
            # cannot issue a second refund for the same amount.
            db.flush()
            return RefundAttemptResult(original_payment=original, amount=take, succeeded=False, refund_row=refund_row, error=f"{type(e).__name__}: {e}")

        refund_row.status = "processed"
        refund_row.gateway_refund_id = result.get("id")
        db.flush()

        payment_row = Payment(
            booking_id=original.booking_id, amount=-take, mode=original.mode, status="refunded",
            reference_no=refund_row.gateway_refund_id or f"REFUND-{original.reference_no or original.id}",
            refund_of_payment_id=original.id, refund_reason=reason, created_by=actor_user_id,
        )
        db.add(payment_row)
        db.flush()
        return RefundAttemptResult(original_payment=original, amount=take, succeeded=True, payment_row=payment_row, refund_row=refund_row)

    # Non-Razorpay: money the business already holds (cash/upi/bank_transfer)
    # or that moved through a channel with no refund API (zotel/OTA) —
    # recorded directly, same convention as the pre-existing manual endpoint.
    payment_row = Payment(
        booking_id=original.booking_id, amount=-take, mode=original.mode, status="refunded",
        reference_no=f"REFUND-{original.reference_no or original.id}",
        refund_of_payment_id=original.id, refund_reason=reason, created_by=actor_user_id,
    )
    db.add(payment_row)
    db.flush()
    return RefundAttemptResult(original_payment=original, amount=take, succeeded=True, payment_row=payment_row)


def issue_cancellation_refund(db: Session, booking: Booking, refund_amount: float, reason: str, actor_user_id: uuid.UUID | None) -> list[RefundAttemptResult]:
    """
    Consumes the booking's positive payments in order (oldest first) up to
    each one's own remaining refundable balance, same distribution as
    before — just now dispatching each slice through _refund_one_payment
    instead of unconditionally creating a Payment row. Caller must have
    already validated refund_amount against refund_available_for_booking.
    A provider failure on one payment does not stop attempts against
    later payments (each is independent); the caller inspects the
    returned list to see exactly what succeeded and what didn't.
    """
    # Match the payment-row lock used by standalone refund routes. Those
    # routes take the booking lock first, then this same payment lock, so a
    # cancellation and direct refund cannot both consume a stale balance.
    positive_payments = db.scalars(
        select(Payment).where(
            Payment.booking_id == booking.id,
            Payment.amount > 0,
            Payment.status == "completed",
        ).order_by(Payment.paid_at, Payment.id).with_for_update()
    ).all()
    attempts: list[RefundAttemptResult] = []
    remaining = min(refund_amount, refund_available_for_booking(db, booking.id))
    for original in positive_payments:
        if remaining <= 0:
            break
        already_refunded = sum(
            abs(float(r.amount))
            for r in db.scalars(select(Payment).where(Payment.refund_of_payment_id == original.id)).all()
        )
        provider_reservations = db.scalars(
            select(Refund).where(
                Refund.payment_id == original.id,
                Refund.status.in_(("created", "processed")),
            )
        ).all()
        processed_gateway = sum(float(r.amount) for r in provider_reservations if r.status == "processed")
        pending_gateway = sum(float(r.amount) for r in provider_reservations if r.status == "created")
        # Processed cancellation refunds also have a negative Payment row;
        # avoid counting that provider record twice.
        unrepresented_processed = max(0.0, processed_gateway - already_refunded)
        available = float(original.amount) - already_refunded - pending_gateway - unrepresented_processed
        if available <= 0:
            continue
        take = round(min(available, remaining), 2)
        attempt = _refund_one_payment(db, original, take, reason, actor_user_id)
        attempts.append(attempt)
        if attempt.succeeded:
            remaining = round(remaining - take, 2)
    return attempts


def create_cancellation_credit_note(db: Session, booking: Booking, invoice: Invoice, refunded_cash_amount: float, reason: str, actor_user_id: uuid.UUID | None) -> CreditNote:
    """
    Cash refund -> equivalent NET (pre-tax) amount to credit, same
    proportion as invoice.subtotal is of invoice.total — see
    credit_note_gst_breakdown's docstring. Only ever called after a
    refund has actually succeeded (see cancel_booking below) — never
    speculatively.
    """
    net_credit_amount = round(refunded_cash_amount / float(invoice.total) * float(invoice.subtotal), 2) if invoice.total else 0.0
    net_credit_amount = min(net_credit_amount, float(invoice.subtotal))
    breakdown = credit_note_gst_breakdown(invoice, net_credit_amount)

    credit_note_no = next_credit_note_number(db)
    guest = db.get(Guest, booking.guest_id)
    unit = db.get(Unit, booking.unit_id)
    property_ = db.get(Property, unit.property_id) if unit else None
    # OS10 Phase 4 Step 4C: captured once, used for both the PDF's printed
    # issue date and the stored row — see payments/router.py::create_credit_note
    # for the same pattern and generate_credit_note_pdf_for_credit_note's
    # docstring for why a regeneration must never use today's date instead.
    issued_at = dt.datetime.now(dt.timezone.utc)
    pdf_path = generate_credit_note_pdf(
        credit_note_no=credit_note_no, invoice_no=invoice.invoice_no,
        property_name=property_.name if property_ else "Sri Apartment Homestay",
        property_gst_number=property_.gst_number if property_ else None,
        guest_name=guest.name if guest else "Guest",
        amount=net_credit_amount, cgst=breakdown["cgst"], sgst=breakdown["sgst"], igst=breakdown["igst"],
        total=breakdown["total"], reason=reason, issue_date=issued_at.date(),
    )
    credit_note = CreditNote(
        invoice_id=invoice.id, credit_note_no=credit_note_no, amount=net_credit_amount,
        cgst=breakdown["cgst"], sgst=breakdown["sgst"], igst=breakdown["igst"], total=breakdown["total"],
        reason=reason, pdf_path=pdf_path, created_by=actor_user_id, issued_at=issued_at,
    )
    db.add(credit_note)
    db.flush()
    record_audit_log(
        db, actor_user_id, "credit_note.create", entity_type="credit_note", entity_id=str(credit_note.id),
        details=f"{credit_note_no} against {invoice.invoice_no}: Rs.{breakdown['total']} — {reason} (auto, cancellation of booking {booking.id})",
    )
    return credit_note


def _process_refund_and_credit_note(
    db: Session, booking: Booking, refund_amount: float, refund_reason: str | None, actor_user_id: uuid.UUID | None, source: str,
) -> CancellationOutcome:
    """
    Shared by cancel_booking (first attempt, alongside the status change)
    and retry_cancellation_refund (a later attempt against an already-
    cancelled booking whose refund previously failed or was only partial)
    — the actual refund-issuing/credit-note/per-attempt-audit logic is
    identical either way; only what happens to booking.status differs
    between the two callers.
    """
    reason = (refund_reason or "Booking cancelled").strip()
    outcome = CancellationOutcome(booking=booking, old_status=booking.status, refund_requested_amount=refund_amount)

    attempts = issue_cancellation_refund(db, booking, refund_amount, reason, actor_user_id)
    outcome.refund_attempts = attempts

    for attempt in attempts:
        if attempt.succeeded:
            record_audit_log(
                db, actor_user_id, "payment.refund", entity_type="payment", entity_id=str(attempt.payment_row.id),
                details=f"Refunded Rs.{attempt.amount} from payment {attempt.original_payment.id} "
                        f"(cancellation of booking {booking.id}, source={source})"
                        + (f" via Razorpay (gateway_refund_id={attempt.refund_row.gateway_refund_id})" if attempt.refund_row else "")
                        + f": {reason}",
            )
        else:
            unresolved = attempt.refund_row is not None and attempt.refund_row.status == "created"
            record_audit_log(
                db, actor_user_id, "payment.refund_outcome_unresolved" if unresolved else "payment.refund_failed",
                entity_type="refund", entity_id=str(attempt.refund_row.id) if attempt.refund_row else str(attempt.original_payment.id),
                details=(
                    f"Refund of Rs.{attempt.amount} against payment {attempt.original_payment.id} has an ambiguous provider outcome; "
                    f"reconciliation required (cancellation of booking {booking.id}, source={source}): {attempt.error}"
                    if attempt.refund_row is not None and attempt.refund_row.status == "created"
                    else f"Refund of Rs.{attempt.amount} against payment {attempt.original_payment.id} failed before provider processing "
                         f"(cancellation of booking {booking.id}, source={source}): {attempt.error}"
                ),
            )

    if outcome.refund_succeeded_amount > 0:
        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking.id))
        if invoice is not None:
            outcome.credit_note = create_cancellation_credit_note(db, booking, invoice, outcome.refund_succeeded_amount, reason, actor_user_id)

    return outcome


def cancel_booking(
    db: Session,
    booking: Booking,
    *,
    actor_user_id: uuid.UUID | None,
    refund_amount: float | None,
    refund_reason: str | None,
    source: str,
    manual_review_only: bool = False,
) -> CancellationOutcome:
    """
    THE single authoritative cancellation workflow — see this module's
    docstring. Caller has already decided the status transition is legal
    and (for the manual path) validated refund_amount against
    refund_available_for_booking; this function does the actual work and
    is the only place any of it happens, for either caller.

    source is "manual" or "zotel" — folded into audit details only, no
    behavioral branching (there is exactly one workflow).
    """
    # Keep all callers (including Zotel cancellation) on the same booking
    # -> payment lock order used by standalone refund routes.
    booking = db.scalar(select(Booking).where(Booking.id == booking.id).with_for_update())
    if booking is None:
        raise ValueError("booking disappeared before cancellation")
    old_status = booking.status
    decision = calculate_cancellation_policy(db, booking)
    issue_amount = 0.0 if decision.requires_review else decision.amount_to_issue
    if refund_amount is not None and abs(round(float(refund_amount) - issue_amount, 2)) > 0.009:
        raise CancellationRefundAmountMismatch(
            f"Requested refund does not match the server-calculated eligible amount (Rs.{issue_amount:.2f})"
        )

    booking.status = "cancelled"
    booking.cancellation_at = decision.cancellation_at
    booking.cancellation_checkin_at = decision.scheduled_checkin_at
    booking.cancellation_policy_percent = decision.percentage
    booking.cancellation_refund_target = decision.target_amount
    booking.cancellation_refund_source = decision.source
    booking.cancellation_requires_review = decision.requires_review

    if issue_amount > 0 and not decision.requires_review and not manual_review_only:
        # This is committed together with the durable `created` provider
        # reservation before the network call. A process crash at that point
        # therefore leaves the booking explicitly requiring reconciliation.
        if decision.source == "razorpay":
            booking.cancellation_requires_review = True
        outcome = _process_refund_and_credit_note(db, booking, issue_amount, refund_reason, actor_user_id, source)
        if decision.source == "razorpay":
            booking.cancellation_requires_review = _has_unresolved_provider_refund(db, booking.id)
    else:
        outcome = CancellationOutcome(booking=booking, old_status=old_status, refund_requested_amount=issue_amount)
        if manual_review_only and issue_amount > 0:
            booking.cancellation_requires_review = True

    details = f"{old_status} -> cancelled (source={source})"
    if outcome.refund_succeeded_amount:
        details += f" (refund Rs.{outcome.refund_succeeded_amount:.2f} issued)"
    if outcome.refund_failed_amount:
        details += f" (refund Rs.{outcome.refund_failed_amount:.2f} not confirmed — reconcile provider outcome before retrying)"
    record_audit_log(db, actor_user_id, "booking.status_change", entity_type="booking", entity_id=str(booking.id), details=details)

    return outcome


def retry_cancellation_refund(
    db: Session,
    booking: Booking,
    *,
    actor_user_id: uuid.UUID | None,
    refund_amount: float,
    refund_reason: str | None,
    source: str = "manual_retry",
) -> CancellationOutcome:
    """
    Retries a refund against an ALREADY-cancelled booking whose earlier
    refund attempt failed (or was only partial) — booking.status is left
    untouched (it's already "cancelled"; re-writing "cancelled -> cancelled"
    would be a misleading audit entry). Caller must have already validated
    refund_amount against refund_available_for_booking, which nets out
    every already-successful refund regardless of how it was recorded —
    so this can never re-refund an amount already confirmed paid back.
    """
    if booking.cancellation_requires_review:
        raise CancellationRefundAmountMismatch("This cancellation requires manual/provider reconciliation")
    expected_remaining = remaining_snapshot_refund(db, booking)
    if expected_remaining <= 0:
        raise CancellationRefundAmountMismatch("No policy refund remains available to retry")
    if abs(round(float(refund_amount) - expected_remaining, 2)) > 0.009:
        raise CancellationRefundAmountMismatch(
            f"Retry amount does not match the remaining policy refund (Rs.{expected_remaining:.2f})"
        )
    if booking.cancellation_refund_source == "razorpay":
        booking.cancellation_requires_review = True
    outcome = _process_refund_and_credit_note(db, booking, expected_remaining, refund_reason, actor_user_id, source)
    if booking.cancellation_refund_source == "razorpay":
        booking.cancellation_requires_review = _has_unresolved_provider_refund(db, booking.id)
    details = f"Refund retry on cancelled booking {booking.id}"
    if outcome.refund_succeeded_amount:
        details += f" (refund Rs.{outcome.refund_succeeded_amount:.2f} issued)"
    if outcome.refund_failed_amount:
        details += f" (refund Rs.{outcome.refund_failed_amount:.2f} not confirmed — reconcile provider outcome before retrying)"
    record_audit_log(db, actor_user_id, "booking.refund_retry", entity_type="booking", entity_id=str(booking.id), details=details)
    return outcome
