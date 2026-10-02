import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.bookings.models import Booking
from app.modules.bookings.cancellation_service import CancellationRefundAmountMismatch, enforce_standalone_refund_policy
from app.modules.payment_gateway.models import GatewayOrder, Refund, REFUND_STATUSES_COUNTED, WebhookEvent
from app.modules.payment_gateway.schemas import (
    CreateOrderRequest,
    GatewayOrderOut,
    RefundOut,
    RefundRequest,
    PaymentReconciliationCase,
)
from app.modules.payment_gateway.service import (
    GatewayNotConfiguredError,
    create_order,
    create_refund,
    payment_matches_order,
    verify_webhook_signature,
)
from app.modules.payments.models import Payment
from app.shared.availability import lock_unit_for_reservation
from app.shared.rbac import get_current_user, require_role

logger = logging.getLogger("sri_apartment_os")

router = APIRouter(prefix="/api/v1/payment-gateway", tags=["payment-gateway"])


@router.get(
    "/reconciliation",
    response_model=list[PaymentReconciliationCase],
    dependencies=[Depends(require_role("admin", "manager"))],
)
def list_payment_reconciliation_cases(db: Session = Depends(get_db)):
    """Read-only queue for signed webhook events and refunds needing review.

    Raw webhook payloads/signatures are deliberately never returned.
    """
    cases: list[dict] = []
    events = db.scalars(
        select(WebhookEvent)
        .where(WebhookEvent.signature_valid.is_(True), WebhookEvent.processed.is_(False))
        .order_by(WebhookEvent.created_at.desc())
        .limit(200)
    ).all()
    for event in events:
        try:
            payload = json.loads(event.payload)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        event_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        payment_payload = event_payload.get("payment") if isinstance(event_payload.get("payment"), dict) else {}
        payment = payment_payload.get("entity") if isinstance(payment_payload.get("entity"), dict) else {}
        provider_order_id = payment.get("order_id")
        provider_payment_id = payment.get("id")
        order = db.scalar(select(GatewayOrder).where(GatewayOrder.gateway_order_id == provider_order_id)) if provider_order_id else None
        hold_id = order.hold_id if order else None
        booking_id = order.booking_id if order else None
        if hold_id and not booking_id:
            from app.modules.public_booking.models import BookingHold
            hold = db.get(BookingHold, hold_id)
            booking_id = hold.booking_id if hold else None
        reason = (
            "Captured payment has no matching OS10 order"
            if event.event_type == "payment.captured" and order is None
            else "Captured payment requires review because its hold is missing, expired, cancelled, or otherwise not convertible"
            if event.event_type == "payment.captured"
            else "Provider event is recorded but is not automatically processed by OS10"
        )
        try:
            amount = float(payment["amount"]) / 100 if payment.get("amount") is not None else (float(order.amount) if order else None)
        except (TypeError, ValueError):
            amount = float(order.amount) if order else None
        cases.append({
            "id": event.id, "case_type": "webhook_event",
            "event_or_payment_reference": provider_payment_id or provider_order_id,
            "booking_id": booking_id, "hold_id": hold_id,
            "order_id": order.id if order else None, "reason": reason,
            "provider_event_type": event.event_type,
            "amount": amount,
            "currency": payment.get("currency") or (order.currency if order else None),
            "created_at": event.created_at, "status": "unresolved",
        })

    unresolved_refunds = db.scalars(
        select(Refund).where(Refund.status == "created").order_by(Refund.created_at.desc()).limit(200)
    ).all()
    for refund in unresolved_refunds:
        payment = db.get(Payment, refund.payment_id)
        cases.append({
            "id": refund.id, "case_type": "refund", "event_or_payment_reference": payment.reference_no if payment else None,
            "booking_id": payment.booking_id if payment else None, "hold_id": None, "order_id": None,
            "reason": "Refund provider outcome is unresolved; verify with Razorpay before any retry",
            "provider_event_type": "refund", "amount": float(refund.amount), "currency": "INR",
            "created_at": refund.created_at, "status": "unresolved",
        })
    return sorted(cases, key=lambda case: case["created_at"], reverse=True)


@router.post("/orders", response_model=GatewayOrderOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def create_gateway_order(payload: CreateOrderRequest, db: Session = Depends(get_db)):
    order = GatewayOrder(
        booking_id=payload.booking_id,
        inquiry_id=payload.inquiry_id,
        amount=payload.amount,
        receipt=payload.receipt,
        status="created",
    )
    db.add(order)
    db.flush()

    try:
        result = create_order(payload.amount, payload.receipt, notes={"internal_order_id": str(order.id)})
        order.gateway_order_id = result.get("id")
    except GatewayNotConfiguredError as e:
        db.commit()
        raise HTTPException(501, str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(502, f"Razorpay order creation failed: {e}")

    db.commit()
    db.refresh(order)
    return order


@router.post("/webhook")
async def razorpay_webhook(request: Request, background_tasks: BackgroundTasks, x_razorpay_signature: str = Header(None), db: Session = Depends(get_db)):
    """
    Public endpoint (Razorpay calls this directly, no user JWT) — security
    comes entirely from signature verification, not auth headers. This is
    the correct pattern for payment webhooks, not an oversight.
    """
    raw_body = await request.body()

    try:
        is_valid = verify_webhook_signature(raw_body, x_razorpay_signature or "")
    except GatewayNotConfiguredError as e:
        raise HTTPException(501, str(e))

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        payload = {}

    event = WebhookEvent(
        event_type=payload.get("event", "unknown"),
        signature_valid=is_valid,
        payload=raw_body.decode(errors="replace"),
        processed=False,
    )
    db.add(event)

    if not is_valid:
        db.commit()
        raise HTTPException(400, "Invalid webhook signature")

    if payload.get("event") == "payment.captured":
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        gateway_order_id = payment_entity.get("order_id")
        gateway_payment_id = payment_entity.get("id")
        order = db.scalar(select(GatewayOrder).where(GatewayOrder.gateway_order_id == gateway_order_id)) if gateway_order_id else None
        if order and gateway_payment_id and payment_entity.get("status") == "captured":
            # Browser verification locks hold -> unit -> order. Follow that
            # same lock order so webhook/browser delivery serializes without
            # a lock inversion and duplicate delivery becomes an idempotent
            # observation of the already converted hold.
            hold = None
            if order.hold_id:
                from app.modules.public_booking.models import BookingHold

                hold = db.scalar(select(BookingHold).where(BookingHold.id == order.hold_id).with_for_update())
                if hold is not None:
                    lock_unit_for_reservation(db, hold.unit_id)
                order = db.scalar(select(GatewayOrder).where(GatewayOrder.id == order.id).with_for_update())

            hold_link_valid = (
                order.hold_id is None
                or (
                    hold is not None
                    and hold.gateway_order_id == order.id
                    and round(float(hold.advance_amount), 2) == round(float(order.amount), 2)
                    and hold.currency.upper() == order.currency.upper()
                )
            )
            if not hold_link_valid or not payment_matches_order(
                {**payment_entity, "id": gateway_payment_id},
                order_id=order.gateway_order_id,
                amount_rupees=float(order.amount),
                currency=order.currency,
            ):
                logger.error("Ignoring Razorpay webhook with order/amount/currency mismatch for local order %s", order.id)
            else:
                order.status = "paid"
                order.gateway_payment_id = gateway_payment_id
                if hold is None:
                    event.processed = order.hold_id is None
                    if order.hold_id is not None:
                        logger.error("Captured payment %s references missing hold %s; reconciliation required", gateway_payment_id, order.hold_id)
                elif hold.status == "converted":
                    # Browser request already completed the same payment.
                    event.processed = True
                elif hold.status == "pending_payment":
                    from datetime import datetime, timezone
                    from app.modules.public_booking.router import _convert_hold_to_booking
                    from app.modules.notifications.service import dispatch_booking_notifications

                    now = datetime.now(timezone.utc)
                    hold.razorpay_payment_id = gateway_payment_id
                    hold.updated_at = now
                    if now <= hold.expires_at:
                        hold.status = "paid"
                        booking, _invoice = _convert_hold_to_booking(db, hold)
                        background_tasks.add_task(dispatch_booking_notifications, booking.id)
                        event.processed = True
                    else:
                        # The charge is real, but the inventory claim expired.
                        # Never silently re-book dates which may now belong to
                        # another guest; leave an explicit reconciliation case.
                        hold.status = "expired"
                        event.processed = False
                        logger.error("Captured payment %s arrived after hold %s expired; reconciliation required", gateway_payment_id, hold.id)
                else:
                    # Keep paid provider state visible while requiring an
                    # explicit operator reconciliation for cancelled/expired
                    # holds; never convert them implicitly.
                    event.processed = False
                    logger.error("Captured payment %s has non-convertible hold %s in state %s", gateway_payment_id, hold.id, hold.status)

    db.commit()
    return {"status": "ok"}


@router.post("/refunds", response_model=RefundOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def issue_refund(payload: RefundRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Phase 2 (payment/refund safety) — two-phase structure, same principle
    as public_booking's create_hold(): a short DB transaction reserves the
    refund amount under a row lock, then the slow external Razorpay call
    happens AFTER that transaction has committed (lock released), never
    while holding it. See lock_unit_for_reservation's own docstring for why
    holding a DB lock across an external API call is unsafe (crash/hang
    mid-call would leak it for the lock's — here, the row's — lifetime).

    Phase A (this function's first half, one transaction):
      - Idempotent replay: payload.idempotency_key matching an existing
        Refund for this payment returns that row as-is, no new attempt.
      - payment is locked FOR UPDATE so two concurrent requests for the
        same payment serialize here rather than both reading a stale
        "amount already refunded".
      - net_refunded counts Refund rows in REFUND_STATUSES_COUNTED
        ("created" + "processed") — "created" specifically so a second
        concurrent request sees THIS request's not-yet-processed refund
        as already reserving its amount, closing the same race a
        payment-amount-only check (comparing only against the original
        payment, not the running total) would miss.
      - Commits with the new Refund row at status="created" — this is the
        reservation; the lock is released here.

    Phase B (after Phase A's commit, unlocked):
      - The live Razorpay call happens with no DB lock held.
      - Success: capture gateway_refund_id from Razorpay's response, mark
        "processed". A duplicate gateway_refund_id (ux_refunds_gateway_
        refund_id firing) means Razorpay itself already has a refund
        object with this id under a different row — extremely unlikely,
        but handled by returning that existing row rather than a 500.
      - GatewayNotConfiguredError is known to occur before any provider
        request, so it can safely mark the reservation failed.
      - Transport/provider errors after a request begins keep the reservation
        in "created" until reconciliation; the outcome may be ambiguous.
    """
    if payload.idempotency_key:
        existing = db.scalar(
            select(Refund).where(Refund.payment_id == payload.payment_id, Refund.idempotency_key == payload.idempotency_key)
        )
        if existing is not None:
            return existing

    unlocked = db.get(Payment, payload.payment_id)
    if unlocked is None:
        raise HTTPException(404, "Payment not found")
    # Use the same booking -> payment lock order as cancellation and manual
    # refunds. The booking lock also serializes separate payments under one
    # cancellation target; the payment lock protects the cumulative provider
    # balance and pending-refund reservation.
    booking = db.get(Booking, unlocked.booking_id, with_for_update=True)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    payment = db.scalar(select(Payment).where(Payment.id == payload.payment_id).with_for_update())
    if payment is None:
        raise HTTPException(404, "Payment not found")
    # Recheck after acquiring the payment lock: two same-key requests can
    # both miss the first lookup before either one commits its reservation.
    if payload.idempotency_key:
        existing = db.scalar(
            select(Refund).where(Refund.payment_id == payload.payment_id, Refund.idempotency_key == payload.idempotency_key)
        )
        if existing is not None:
            return existing
    if payment.amount < 0:
        raise HTTPException(400, "Cannot refund a refund")
    if payment.mode != "razorpay" or not payment.reference_no:
        raise HTTPException(409, "Only Razorpay payments with a provider payment ID can use this refund workflow")
    if payment.status != "completed":
        raise HTTPException(409, "Only successfully completed payments can be refunded")

    try:
        enforce_standalone_refund_policy(db, booking, payload.amount)
    except CancellationRefundAmountMismatch as exc:
        raise HTTPException(409, str(exc)) from None

    already_reserved = db.scalars(
        select(Refund).where(Refund.payment_id == payload.payment_id, Refund.status.in_(REFUND_STATUSES_COUNTED))
    ).all()
    net_refunded = sum(float(r.amount) for r in already_reserved)
    if payload.amount > float(payment.amount) - net_refunded:
        raise HTTPException(
            400,
            f"Refund amount ({payload.amount}) exceeds what's left to refund on this payment "
            f"(Rs.{float(payment.amount) - net_refunded} available)",
        )

    refund = Refund(
        payment_id=payload.payment_id, amount=payload.amount, reason=payload.reason,
        idempotency_key=payload.idempotency_key, created_by=user.id, status="created",
    )
    db.add(refund)
    db.commit()
    db.refresh(refund)

    try:
        response = create_refund(payment.reference_no or "", payload.amount)
        refund.gateway_refund_id = response.get("id")
        refund.status = "processed"
        db.commit()
    except GatewayNotConfiguredError as e:
        refund.status = "failed"
        db.commit()
        raise HTTPException(501, str(e))
    except IntegrityError as e:
        db.rollback()
        if "ux_refunds_gateway_refund_id" in str(e.orig):
            # Razorpay returned a gateway_refund_id another row already has
            # — this attempt is a duplicate of that existing, already-
            # processed refund, so it must not count as a second one.
            duplicate = db.scalar(select(Refund).where(Refund.gateway_refund_id == response.get("id")))
            if duplicate is not None and duplicate.payment_id == payload.payment_id and float(duplicate.amount) == float(payload.amount):
                refund.status = "failed"
                db.commit()
                return duplicate
            logger.error("Razorpay refund identifier collision requires reconciliation for local refund %s", refund.id)
            raise HTTPException(503, "Refund outcome is being reconciled; do not submit a new refund until it is resolved")
        raise HTTPException(502, f"Refund could not be recorded: {e}")
    except Exception as e:
        # A timeout/reset can happen after Razorpay accepted the refund.
        # Keep the committed reservation in `created` so it continues to
        # consume refundable balance and same-key retries cannot issue a
        # second provider request. An operator must reconcile it with the
        # provider before marking it failed or retrying with a new intent.
        db.rollback()
        logger.exception("Razorpay refund outcome is ambiguous for local refund %s", refund.id)
        raise HTTPException(503, "Refund outcome is being reconciled; do not submit a new refund until it is resolved")

    # RBAC Phase B: closes a real, pre-existing audit gap — this endpoint
    # previously recorded no audit trail at all for a genuine financial
    # refund. Logged only on the success path (refund.status == "processed"),
    # matching the existing convention elsewhere of logging completed
    # actions, not attempts. Business logic above is unchanged.
    record_audit_log(
        db, user.id, "payment_gateway.refund", entity_type="refund", entity_id=str(refund.id),
        details=f"Refunded {payload.amount} against payment {payload.payment_id}" + (f": {payload.reason}" if payload.reason else ""),
        previous_value=f"payment_amount={payment.amount}",
        new_value=f"refund_amount={payload.amount}, refund_status={refund.status}",
    )
    db.commit()
    db.refresh(refund)
    return refund
