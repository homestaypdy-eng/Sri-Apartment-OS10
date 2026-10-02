import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PaymentCreate(BaseModel):
    booking_id: uuid.UUID
    amount: float = Field(gt=0)
    mode: str  # upi/card/bank_transfer/cash
    reference_no: str | None = None


class PaymentOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    amount: float
    mode: str
    status: str
    reference_no: str | None
    paid_at: datetime
    refund_of_payment_id: uuid.UUID | None = None
    refund_reason: str | None = None
    idempotency_key: str | None = None

    class Config:
        from_attributes = True


class RefundCreate(BaseModel):
    refund_amount: float = Field(gt=0)
    reason: str
    # Optional. When supplied, a retry of the exact same request (same
    # payment_id + idempotency_key) returns the original refund row instead
    # of creating a second one.
    idempotency_key: str | None = None


class InvoiceOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    invoice_no: str
    subtotal: float
    cgst: float
    sgst: float
    igst: float
    total: float
    gst_rate_percent: float
    itc_available: bool
    hsn_code: str
    issued_at: datetime

    class Config:
        from_attributes = True


class CreditNoteCreate(BaseModel):
    amount: float = Field(gt=0)  # the reduction, excluding GST — GST portion is computed proportionally to the invoice's own rate
    reason: str


class CreditNoteOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    credit_note_no: str
    amount: float
    cgst: float
    sgst: float
    igst: float
    total: float
    reason: str
    issued_at: datetime

    class Config:
        from_attributes = True


class SendInvoiceEmailRequest(BaseModel):
    to_email: str | None = None  # falls back to the guest's email on file


class SendInvoiceEmailResponse(BaseModel):
    sent: bool
    to_email: str


class SendInvoiceWhatsAppResponse(BaseModel):
    whatsapp_url: str
    message_preview: str
