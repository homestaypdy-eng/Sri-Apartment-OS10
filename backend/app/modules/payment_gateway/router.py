import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.payment_gateway.models import GatewayOrder, Refund, WebhookEvent
from app.modules.payment_gateway.schemas import (
    CreateOrderRequest,
    GatewayOrderOut,
    RefundOut,
    RefundRequest,
)
from app.modules.payment_gateway.service import (
    GatewayNotConfiguredError,
    create_order,
    create_refund,
    verify_webhook_signature,
)
from app.modules.payments.models import Payment
from app.shared.rbac import get_current_user, require_role

logger = logging.getLogger("sri_apartment_os")

router = APIRouter(prefix="/api/v1/payment-gateway", tags=["payment-gateway"])


@router.post("/orders", response_model=GatewayOrderOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
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
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(None), db: Session = Depends(get_db)):
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
        if gateway_order_id:
            order = db.query(GatewayOrder).filter(GatewayOrder.gateway_order_id == gateway_order_id).first()
            if order:
                order.status = "paid"
                order.gateway_payment_id = gateway_payment_id
                event.processed = True

                # Backup path: if this order is linked to a BookingHold that is still
                # pending_payment, convert it to a confirmed Booking. This handles the
                # case where the guest's browser closes after payment but before the
                # verify-payment endpoint is called.
                if order.hold_id:
                    from datetime import datetime, timezone
                    from app.modules.public_booking.models import BookingHold
                    from app.modules.public_booking.router import _convert_hold_to_booking

                    hold = db.get(BookingHold, order.hold_id)
                    if hold and hold.status == "pending_payment":
                        if datetime.now(timezone.utc) <= hold.expires_at:
                            hold.status = "paid"
                            hold.razorpay_payment_id = gateway_payment_id
                            from datetime import datetime, timezone
                            hold.updated_at = datetime.now(timezone.utc)
                            try:
                                _convert_hold_to_booking(db, hold)
                            except Exception as exc:
                                logger.error("Webhook hold conversion failed for hold %s: %s", order.hold_id, exc)
                        else:
                            hold.status = "expired"
                            hold.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"status": "ok"}


@router.post("/refunds", response_model=RefundOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def issue_refund(payload: RefundRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    payment = db.get(Payment, payload.payment_id)
    if payment is None:
        raise HTTPException(404, "Payment not found")
    if payload.amount > float(payment.amount):
        raise HTTPException(400, f"Refund amount ({payload.amount}) cannot exceed the original payment ({payment.amount})")

    refund = Refund(payment_id=payload.payment_id, amount=payload.amount, reason=payload.reason, created_by=user.id, status="created")
    db.add(refund)
    db.flush()

    try:
        create_refund(payment.reference_no or "", payload.amount)
        refund.status = "processed"
    except GatewayNotConfiguredError as e:
        db.commit()
        raise HTTPException(501, str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(502, f"Refund failed: {e}")

    db.commit()
    db.refresh(refund)
    return refund
