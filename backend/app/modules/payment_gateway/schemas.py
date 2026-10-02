import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    booking_id: uuid.UUID | None = None
    inquiry_id: uuid.UUID | None = None
    amount: float = Field(gt=0, allow_inf_nan=False)
    receipt: str = Field(min_length=1, max_length=40)


class GatewayOrderOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID | None
    inquiry_id: uuid.UUID | None
    amount: float
    currency: str
    gateway_order_id: str | None
    gateway_payment_id: str | None
    status: str
    receipt: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class RefundRequest(BaseModel):
    payment_id: uuid.UUID
    amount: float = Field(gt=0, allow_inf_nan=False)
    reason: str | None = Field(default=None, max_length=255)
    # Optional. When supplied, a retry of the exact same request (same
    # payment_id + idempotency_key) returns the original Refund row instead
    # of attempting a second live Razorpay refund.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class RefundOut(BaseModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    amount: float
    reason: str | None
    gateway_refund_id: str | None
    idempotency_key: str | None = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class PaymentReconciliationCase(BaseModel):
    id: uuid.UUID
    case_type: str
    event_or_payment_reference: str | None = None
    booking_id: uuid.UUID | None = None
    hold_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    reason: str
    provider_event_type: str | None = None
    amount: float | None = None
    currency: str | None = None
    created_at: datetime
    status: str
