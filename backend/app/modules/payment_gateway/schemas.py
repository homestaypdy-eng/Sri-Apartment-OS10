import uuid
from datetime import datetime

from pydantic import BaseModel


class CreateOrderRequest(BaseModel):
    booking_id: uuid.UUID | None = None
    inquiry_id: uuid.UUID | None = None
    amount: float
    receipt: str


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
    amount: float
    reason: str | None = None


class RefundOut(BaseModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    amount: float
    reason: str | None
    gateway_refund_id: str | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
