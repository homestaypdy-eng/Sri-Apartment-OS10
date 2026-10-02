import datetime as dt
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.bookings.models import Booking, Guest
from app.modules.bookings.cancellation_service import CancellationRefundAmountMismatch, enforce_standalone_refund_policy
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
from app.modules.payments.service import calculate_gst, credit_note_gst_breakdown, generate_credit_note_pdf, generate_credit_note_pdf_for_credit_note, generate_invoice_pdf, generate_invoice_pdf_for_booking, next_credit_note_number, next_invoice_number
from app.modules.notifications.service import trigger_invoice_delivery, trigger_payment_received
from app.modules.properties.models import Property, Unit
from app.modules.settings.router import get_or_create_bank_settings, get_or_create_settings
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1", tags=["payments"])


@router.post("/payments", response_model=PaymentOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
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

    Phase 2 (payment/refund safety):
    - Idempotent replay: if payload.idempotency_key matches an existing
      refund row for this same payment, that row is returned as-is — no
      new row, no re-validation, no double audit-log entry. This must be
      checked BEFORE the row lock below, since a genuine retry should never
      block on (or be blocked by) another request for the same payment.
    - original is locked FOR UPDATE for the remainder of this (single,
      fast, no-external-call) transaction, so two concurrent refund
      requests against the same payment can never both pass the
      cumulative-amount check before either commits — the second request
      only proceeds once the first has committed, and then sees the
      first's refund already counted in net_refunded.
    """
    if payload.idempotency_key:
        existing = db.scalar(
            select(Payment).where(Payment.refund_of_payment_id == payment_id, Payment.idempotency_key == payload.idempotency_key)
        )
        if existing is not None:
            return existing

    unlocked = db.get(Payment, payment_id)
    if unlocked is None:
        raise HTTPException(404, "Payment not found")
    # Match cancellation and gateway-refund lock order: booking first, then
    # payment. This serializes policy cancellation refunds against the
    # standalone ledger-refund endpoint, including refunds on other rows
    # belonging to the same booking.
    booking = db.get(Booking, unlocked.booking_id, with_for_update=True)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    original = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if original is None:
        raise HTTPException(404, "Payment not found")
    # Same-key requests may both miss the optimistic lookup above. Recheck
    # after the payment row lock so the waiter returns the committed refund
    # rather than creating a second ledger row.
    if payload.idempotency_key:
        existing = db.scalar(
            select(Payment).where(Payment.refund_of_payment_id == payment_id, Payment.idempotency_key == payload.idempotency_key)
        )
        if existing is not None:
            return existing
    if original.amount < 0:
        raise HTTPException(400, "Cannot refund a refund")
    if original.mode == "razorpay":
        raise HTTPException(409, "Razorpay payments must be refunded through the payment-gateway refund workflow")
    if original.mode == "zotel":
        raise HTTPException(409, "OTA-collected payments must be reconciled/refunded through the original OTA payment channel")
    if original.status != "completed":
        raise HTTPException(409, "Only successfully completed payments can be refunded")

    try:
        enforce_standalone_refund_policy(db, booking, payload.refund_amount)
    except CancellationRefundAmountMismatch as exc:
        raise HTTPException(409, str(exc)) from None

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
        idempotency_key=payload.idempotency_key,
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
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
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
    extra_charges = float(booking.extra_guest_charge)
    subtotal = round(float(booking.rate_applied) * nights + extra_charges, 2)

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

    # Bank/UPI details moved to their own BankSettings table (RBAC Phase A) —
    # BusinessSettings no longer has these columns at all; reading them off
    # BusinessSettings here (as this endpoint previously did) raises
    # AttributeError and crashes every invoice generation. Signatory name/
    # title and the QR image path stayed on BusinessSettings, same split
    # already used by the quotation PDF (inquiries/service.py._pdf_context).
    business_settings = get_or_create_settings(db)
    bank_settings = get_or_create_bank_settings(db)
    bank_details = {
        "bank_name": bank_settings.bank_name,
        "bank_account_name": bank_settings.bank_account_name,
        "bank_account_number": bank_settings.bank_account_number,
        "bank_ifsc": bank_settings.bank_ifsc,
        "bank_branch": bank_settings.bank_branch,
        "upi_id": bank_settings.upi_id,
        "signatory_name": business_settings.signatory_name,
        "signatory_title": business_settings.signatory_title,
    }
    qr_path = business_settings.qr_code_path

    # Payment status/paid-amount/balance must reflect what was actually
    # recorded against this booking, not a hardcoded "fully paid" assumption
    # — same sum-of-Payment.amount pattern already used for guest history's
    # total_paid (bookings/router.py), which correctly nets out refunds
    # since those are stored as negative amounts.
    payments = db.scalars(select(Payment).where(Payment.booking_id == booking_id)).all()
    payment_received = round(sum(float(p.amount) for p in payments), 2)
    balance_due = round(float(gst["total"]) - payment_received, 2)
    latest_payment_mode = payments[-1].mode if payments else None

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
        payment_received=payment_received,
        balance_due=balance_due,
        guest_phone=guest.phone,
        guest_email=guest.email,
        guest_address=guest.address,
        num_guests=booking.adults + booking.children,
        rate_per_night=float(booking.rate_applied),
        payment_mode=latest_payment_mode,
        extra_charges=extra_charges,
    )
    invoice.pdf_path = pdf_path

    record_audit_log(db, user.id, "invoice.generate", entity_type="invoice", entity_id=str(invoice.id), details=f"{invoice_no} for booking {booking_id}: Rs.{gst['total']}")

    db.commit()
    db.refresh(invoice)
    return invoice


@router.get("/invoices/{invoice_id}/pdf", dependencies=[Depends(require_role("admin", "staff"))])
def download_invoice_pdf(invoice_id: uuid.UUID, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice PDF not found")
    booking = db.get(Booking, invoice.booking_id)
    if booking is None:
        raise HTTPException(404, "Invoice PDF not found")
    # Preserve the issued invoice artifact when it is available: mutable
    # bank/property/guest settings must not rewrite a historical document.
    # Regenerate only when the stored file is unavailable (for example,
    # ephemeral storage was lost); the invoice number and financial totals
    # remain sourced from the immutable Invoice row.
    pdf_path = invoice.pdf_path if invoice.pdf_path and os.path.isfile(invoice.pdf_path) else None
    if pdf_path is None:
        pdf_path = generate_invoice_pdf_for_booking(db, booking, invoice)
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{invoice.invoice_no.replace('/', '_')}.pdf")


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
    if booking is None:
        raise HTTPException(404, "Booking for this invoice not found")
    guest = db.get(Guest, booking.guest_id)
    to_email = payload.to_email or (guest.email if guest else None)
    if not to_email:
        raise HTTPException(400, "No email address on file for this booking's guest — provide to_email in the request")

    # OS10 Phase 4 Step 4A: regenerated fresh from current DB data, exactly
    # like the staff invoice-download endpoint — never dependent on
    # invoice.pdf_path still existing on disk. This is a synchronous,
    # staff-initiated action (unlike the automatic post-payment path), so a
    # generation failure is surfaced as a clear error rather than silently
    # sending an attachment-less email — the resend action explicitly
    # failed, and the staff member clicking it should know that.
    try:
        pdf_path = generate_invoice_pdf_for_booking(db, booking, invoice)
    except Exception as e:
        raise HTTPException(500, f"Failed to generate the invoice PDF for this email: {e}") from None

    try:
        sent = trigger_invoice_delivery(db, invoice, pdf_path)
    finally:
        # Cleaned up on both success and failure — a fresh regeneration for
        # this send only, never the historical invoice.pdf_path file.
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

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

    # GST portion is proportional to the original invoice's own rate — see
    # credit_note_gst_breakdown's docstring (also used by the automatic
    # cancellation-refund flow, so both compute this the same way).
    breakdown = credit_note_gst_breakdown(invoice, payload.amount)
    cgst, sgst, igst, total = breakdown["cgst"], breakdown["sgst"], breakdown["igst"], breakdown["total"]

    credit_note_no = next_credit_note_number(db)
    booking = db.get(Booking, invoice.booking_id)
    guest = db.get(Guest, booking.guest_id) if booking else None
    property_ = db.get(Property, booking.unit.property_id) if booking and booking.unit else None

    # OS10 Phase 4 Step 4C: issued_at is captured once, here, and used for
    # BOTH the PDF's printed "Issue Date" and the stored CreditNote row —
    # so a later regeneration (download_credit_note_pdf) prints the exact
    # same date this creation call did, never today's date.
    issued_at = dt.datetime.now(dt.timezone.utc)

    pdf_path = generate_credit_note_pdf(
        credit_note_no=credit_note_no, invoice_no=invoice.invoice_no,
        property_name=property_.name if property_ else "Sri Apartment Homestay",
        property_gst_number=property_.gst_number if property_ else None,
        guest_name=guest.name if guest else "Guest",
        amount=payload.amount, cgst=cgst, sgst=sgst, igst=igst, total=total, reason=payload.reason,
        issue_date=issued_at.date(),
    )

    credit_note = CreditNote(
        invoice_id=invoice_id, credit_note_no=credit_note_no, amount=payload.amount,
        cgst=cgst, sgst=sgst, igst=igst, total=total, reason=payload.reason,
        pdf_path=pdf_path, created_by=user.id, issued_at=issued_at,
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
    if credit_note is None:
        raise HTTPException(404, "Credit note PDF not found")

    filename = f"{credit_note.credit_note_no.replace('/', '_')}.pdf"

    # OS10 Phase 4 Step 4C: the common case (stored file still present) is
    # left exactly as before — unchanged behavior, zero regeneration risk.
    # Only when the stored file is missing (the ephemeral-storage failure
    # mode the Step 4 audit found) do we fall back to regenerating from
    # DB data, exactly as CreditNote.issued_at/amount/cgst/sgst/igst/
    # total/reason/credit_note_no already recorded them — see
    # generate_credit_note_pdf_for_credit_note's docstring for the
    # historical-integrity guarantees.
    if credit_note.pdf_path and os.path.exists(credit_note.pdf_path):
        return FileResponse(credit_note.pdf_path, media_type="application/pdf", filename=filename)

    try:
        regenerated_path = generate_credit_note_pdf_for_credit_note(db, credit_note)
    except Exception as e:
        raise HTTPException(500, f"Failed to regenerate this credit note's PDF: {e}") from None

    def _cleanup():
        if os.path.exists(regenerated_path):
            os.remove(regenerated_path)

    return FileResponse(
        regenerated_path, media_type="application/pdf", filename=filename,
        background=BackgroundTask(_cleanup),
    )
