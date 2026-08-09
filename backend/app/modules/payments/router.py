import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.bookings.models import Booking, Guest
from app.modules.payments.models import CreditNote, Invoice, Payment
from app.modules.payments.schemas import (
    CreditNoteCreate,
    CreditNoteOut,
    InvoiceOut,
    PaymentCreate,
    PaymentOut,
    RefundCreate,
    SendInvoiceEmailRequest,
    SendInvoiceEmailResponse,
    SendInvoiceWhatsAppResponse,
)
from app.modules.payments.service import calculate_gst, generate_credit_note_pdf, generate_invoice_pdf, next_credit_note_number, next_invoice_number
from app.modules.notifications.service import trigger_invoice_delivery, trigger_payment_received
from app.modules.properties.models import Property, Unit
from app.modules.settings.models import BusinessSettings
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1", tags=["payments"])


@router.post("/payments", response_model=PaymentOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def record_payment(payload: PaymentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = db.get(Booking, payload.booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    if payload.mode not in ("upi", "card", "bank_transfer", "cash"):
        raise HTTPException(400, f"Invalid payment mode '{payload.mode}'")

    payment = Payment(
        booking_id=payload.booking_id,
        amount=payload.amount,
        mode=payload.mode,
        reference_no=payload.reference_no,
        status="completed",
        created_by=user.id,
    )
    db.add(payment)
    db.flush()
    record_audit_log(db, user.id, "payment.create", entity_type="payment", entity_id=str(payment.id), details=f"Rs.{payload.amount} via {payload.mode} for booking {payload.booking_id}")
    trigger_payment_received(db, payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.post("/payments/{payment_id}/refund", response_model=PaymentOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def refund_payment(payment_id: uuid.UUID, payload: RefundCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Creates a new Payment row for the refund (negative amount), rather
    than mutating the original — every existing "total paid" calculation
    across the codebase just sums Payment.amount for a booking, so this
    nets out correctly everywhere automatically, with the refund itself
    keeping its own timestamp and audit trail instead of rewriting history.
    """
    original = db.get(Payment, payment_id)
    if original is None:
        raise HTTPException(404, "Payment not found")
    if original.amount < 0:
        raise HTTPException(400, "Cannot refund a refund")

    already_refunded = db.scalars(select(Payment).where(Payment.refund_of_payment_id == payment_id)).all()
    net_refunded = sum(abs(float(r.amount)) for r in already_refunded)
    if payload.refund_amount > float(original.amount) - net_refunded:
        raise HTTPException(400, f"Refund amount exceeds what's left to refund on this payment (Rs.{float(original.amount) - net_refunded} available)")

    refund = Payment(
        booking_id=original.booking_id,
        amount=-payload.refund_amount,
        mode=original.mode,
        status="refunded",
        reference_no=f"REFUND-{original.reference_no or original.id}",
        refund_of_payment_id=payment_id,
        refund_reason=payload.reason,
        created_by=user.id,
    )
    db.add(refund)
    db.flush()
    record_audit_log(db, user.id, "payment.refund", entity_type="payment", entity_id=str(refund.id), details=f"Refunded Rs.{payload.refund_amount} from payment {payment_id}: {payload.reason}")
    trigger_payment_received(db, refund)
    db.commit()
    db.refresh(refund)
    return refund


@router.get("/payments/{booking_id}", response_model=list[PaymentOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_payments_for_booking(booking_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(Payment).where(Payment.booking_id == booking_id)).all()


@router.post(
    "/invoices/generate/{booking_id}",
    response_model=InvoiceOut,
    status_code=201,
    dependencies=[Depends(require_role("admin", "staff"))],
)
def generate_invoice(booking_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    existing = db.scalar(select(Invoice).where(Invoice.booking_id == booking_id))
    if existing is not None:
        raise HTTPException(409, f"Invoice already generated for this booking: {existing.invoice_no}")

    unit = db.get(Unit, booking.unit_id)
    guest = booking.guest
    property_ = unit.property

    nights = (booking.check_out - booking.check_in).days
    subtotal = round(float(booking.rate_applied) * nights + float(booking.extra_guest_charge), 2)

    gst = calculate_gst(
        subtotal=subtotal,
        tariff_per_night=float(booking.rate_applied),
        guest_state=guest.state_code,
        property_state=property_.state_code,
    )

    invoice_no = next_invoice_number(db)

    invoice = Invoice(
        booking_id=booking_id,
        invoice_no=invoice_no,
        subtotal=subtotal,
        cgst=gst["cgst"],
        sgst=gst["sgst"],
        igst=gst["igst"],
        total=gst["total"],
        gst_rate_percent=gst["gst_rate_percent"],
        itc_available=gst["itc_available"],
    )
    db.add(invoice)
    db.flush()  # get invoice.id without committing yet

    settings_row = db.scalar(select(BusinessSettings))
    bank_details = None
    qr_path = None
    if settings_row:
        bank_details = {
            "bank_name": settings_row.bank_name,
            "bank_account_name": settings_row.bank_account_name,
            "bank_account_number": settings_row.bank_account_number,
            "bank_ifsc": settings_row.bank_ifsc,
            "bank_branch": settings_row.bank_branch,
        }
        qr_path = settings_row.qr_code_path

    pdf_path = generate_invoice_pdf(
        invoice_no=invoice_no,
        property_name=property_.name,
        property_gst_number=property_.gst_number,
        guest_name=guest.name,
        guest_gst_number=guest.gst_number,
        unit_name=unit.name,
        check_in=booking.check_in,
        check_out=booking.check_out,
        subtotal=subtotal,
        cgst=gst["cgst"],
        sgst=gst["sgst"],
        igst=gst["igst"],
        total=gst["total"],
        hsn_code=invoice.hsn_code,
        gst_rate_percent=gst["gst_rate_percent"],
        itc_available=gst["itc_available"],
        bank_details=bank_details,
        qr_path=qr_path,
    )
    invoice.pdf_path = pdf_path

    record_audit_log(db, user.id, "invoice.generate", entity_type="invoice", entity_id=str(invoice.id), details=f"{invoice_no} for booking {booking_id}: Rs.{gst['total']}")

    db.commit()
    db.refresh(invoice)
    return invoice


@router.get("/invoices/{invoice_id}/pdf", dependencies=[Depends(require_role("admin", "staff"))])
def download_invoice_pdf(invoice_id: uuid.UUID, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or not invoice.pdf_path:
        raise HTTPException(404, "Invoice PDF not found")
    return FileResponse(invoice.pdf_path, media_type="application/pdf", filename=f"{invoice.invoice_no.replace('/', '_')}.pdf")


@router.get("/invoices/by-booking/{booking_id}", response_model=InvoiceOut, dependencies=[Depends(require_role("admin", "staff"))])
def get_invoice_by_booking(booking_id: uuid.UUID, db: Session = Depends(get_db)):
    invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking_id))
    if invoice is None:
        raise HTTPException(404, "No invoice for this booking yet")
    return invoice


@router.post("/invoices/{invoice_id}/send/email", response_model=SendInvoiceEmailResponse, dependencies=[Depends(require_role("admin", "staff"))])
def send_invoice_email(invoice_id: uuid.UUID, payload: SendInvoiceEmailRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice not found")
    booking = db.get(Booking, invoice.booking_id)
    guest = db.get(Guest, booking.guest_id) if booking else None
    to_email = payload.to_email or (guest.email if guest else None)
    if not to_email:
        raise HTTPException(400, "No email address on file for this booking's guest — provide to_email in the request")
    if not invoice.pdf_path:
        raise HTTPException(400, "Generate the invoice PDF first (GET /invoices/{id}/pdf triggers generation during /invoices/generate)")

    sent = trigger_invoice_delivery(db, invoice, invoice.pdf_path)
    record_audit_log(db, user.id, "invoice.send_email", entity_type="invoice", entity_id=str(invoice.id), details=f"To {to_email}: {'sent' if sent else 'attempted, see notification log for failure reason'}")
    db.commit()
    return SendInvoiceEmailResponse(sent=bool(sent), to_email=to_email)


@router.post("/invoices/{invoice_id}/send/whatsapp", response_model=SendInvoiceWhatsAppResponse, dependencies=[Depends(require_role("admin", "staff"))])
def send_invoice_whatsapp(invoice_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from urllib.parse import quote as _urlquote

    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice not found")
    booking = db.get(Booking, invoice.booking_id)
    guest = db.get(Guest, booking.guest_id) if booking else None
    if not guest or not guest.phone:
        raise HTTPException(400, "No phone number on file for this booking's guest")

    phone = "".join(c for c in guest.phone if c.isdigit())
    if len(phone) == 10:
        phone = "91" + phone
    message = (
        f"Hi {guest.name}, your invoice {invoice.invoice_no} for Rs. {float(invoice.total):.2f} is ready. "
        f"We'll share the PDF separately. Thank you for staying with Sri Apartment Homestay!"
    )
    url = f"https://wa.me/{phone}?text={_urlquote(message)}"
    record_audit_log(db, user.id, "invoice.send_whatsapp", entity_type="invoice", entity_id=str(invoice.id), details=f"Prepared for {guest.phone}")
    db.commit()
    return SendInvoiceWhatsAppResponse(whatsapp_url=url, message_preview=message)


@router.post("/invoices/{invoice_id}/credit-notes", response_model=CreditNoteOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_credit_note(invoice_id: uuid.UUID, payload: CreditNoteCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice not found")
    if payload.amount > float(invoice.subtotal):
        raise HTTPException(400, f"Credit amount (Rs.{payload.amount}) cannot exceed the invoice's subtotal (Rs.{invoice.subtotal})")

    # GST portion is proportional to the original invoice's own rate — a
    # partial credit against a part-CGST/SGST invoice must reduce each
    # component in the same ratio it was originally charged, not just
    # zero out GST or apply today's rate.
    ratio = payload.amount / float(invoice.subtotal) if invoice.subtotal else 0
    cgst = round(float(invoice.cgst) * ratio, 2)
    sgst = round(float(invoice.sgst) * ratio, 2)
    igst = round(float(invoice.igst) * ratio, 2)
    total = round(payload.amount + cgst + sgst + igst, 2)

    credit_note_no = next_credit_note_number(db)
    booking = db.get(Booking, invoice.booking_id)
    guest = db.get(Guest, booking.guest_id) if booking else None
    property_ = db.get(Property, booking.unit.property_id) if booking and booking.unit else None

    pdf_path = generate_credit_note_pdf(
        credit_note_no=credit_note_no, invoice_no=invoice.invoice_no,
        property_name=property_.name if property_ else "Sri Apartment Homestay",
        property_gst_number=property_.gst_number if property_ else None,
        guest_name=guest.name if guest else "Guest",
        amount=payload.amount, cgst=cgst, sgst=sgst, igst=igst, total=total, reason=payload.reason,
    )

    credit_note = CreditNote(
        invoice_id=invoice_id, credit_note_no=credit_note_no, amount=payload.amount,
        cgst=cgst, sgst=sgst, igst=igst, total=total, reason=payload.reason,
        pdf_path=pdf_path, created_by=user.id,
    )
    db.add(credit_note)
    db.flush()
    record_audit_log(db, user.id, "credit_note.create", entity_type="credit_note", entity_id=str(credit_note.id), details=f"{credit_note_no} against {invoice.invoice_no}: Rs.{total} — {payload.reason}")
    db.commit()
    db.refresh(credit_note)
    return credit_note


@router.get("/invoices/{invoice_id}/credit-notes", response_model=list[CreditNoteOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_credit_notes(invoice_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(CreditNote).where(CreditNote.invoice_id == invoice_id)).all()


@router.get("/credit-notes/{credit_note_id}/pdf", dependencies=[Depends(require_role("admin", "staff"))])
def download_credit_note_pdf(credit_note_id: uuid.UUID, db: Session = Depends(get_db)):
    credit_note = db.get(CreditNote, credit_note_id)
    if credit_note is None or not credit_note.pdf_path:
        raise HTTPException(404, "Credit note PDF not found")
    return FileResponse(credit_note.pdf_path, media_type="application/pdf", filename=f"{credit_note.credit_note_no.replace('/', '_')}.pdf")
