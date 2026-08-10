import datetime as dt
import os
import uuid

from fpdf import FPDF
from sqlalchemy.orm import Session

from app.modules.payments.models import InvoiceCounter

INVOICE_STORAGE_DIR = os.environ.get("INVOICE_STORAGE_DIR", "/tmp/sri_apartment_invoices")
os.makedirs(INVOICE_STORAGE_DIR, exist_ok=True)


def current_financial_year(as_of: dt.date | None = None) -> str:
    """Indian financial year runs Apr 1 - Mar 31."""
    as_of = as_of or dt.date.today()
    if as_of.month >= 4:
        return f"{as_of.year}-{as_of.year + 1}"
    return f"{as_of.year - 1}-{as_of.year}"


def next_invoice_number(db: Session) -> str:
    """
    Atomically increments a per-financial-year counter and returns a
    sequential invoice number, e.g. 'INV/2026-2027/000001'.
    Sequential, gap-free numbering within a financial year is a real
    Indian GST invoicing requirement, not a cosmetic choice.
    """
    fy = current_financial_year()
    counter = db.get(InvoiceCounter, fy, with_for_update=True)
    if counter is None:
        counter = InvoiceCounter(financial_year=fy, last_number=0)
        db.add(counter)
        db.flush()
    counter.last_number += 1
    db.flush()
    return f"INV/{fy}/{counter.last_number:06d}"


def next_credit_note_number(db: Session) -> str:
    """Same sequential, financial-year-scoped numbering as invoices —
    e.g. 'CN/2026-2027/000001'."""
    from app.modules.payments.models import CreditNoteCounter
    fy = current_financial_year()
    counter = db.get(CreditNoteCounter, fy, with_for_update=True)
    if counter is None:
        counter = CreditNoteCounter(financial_year=fy, last_number=0)
        db.add(counter)
        db.flush()
    counter.last_number += 1
    db.flush()
    return f"CN/{fy}/{counter.last_number:06d}"


def determine_gst_slab(tariff_per_night: float) -> tuple[float, bool]:
    """
    Returns (gst_rate_percent, itc_available) for a given declared/billed
    room tariff per night, per the GST slab structure effective 22 Sep 2025:

        <= Rs 1,000/night   -> 0%  GST, no ITC
        Rs 1,001-7,500/night -> 5%  GST, ITC not available
        > Rs 7,500/night     -> 18% GST, ITC available

    NOTE: the slab is meant to be based on the "declared tariff." In this
    system that's approximated by the nightly rate actually charged
    (Booking.rate_applied) — which also matches the rule that a discounted/
    final billed price can determine the slab in some cases. This is a
    simplification of a real, currently-in-force tax rule, not a permanent
    hardcoded assumption — GST slabs have changed before (this structure
    itself replaced an older one on 22 Sep 2025) and may change again, so
    this function is the single place to update if/when that happens.
    """
    if tariff_per_night <= 1000:
        return 0.0, False
    elif tariff_per_night <= 7500:
        return 5.0, False
    else:
        return 18.0, True


def calculate_gst(subtotal: float, tariff_per_night: float, guest_state: str | None, property_state: str) -> dict:
    """
    Same state -> CGST + SGST (split evenly).
    Different state (or unknown guest state) -> IGST (full rate).
    The rate itself is determined by the tariff slab (see determine_gst_slab),
    not a flat/manually configured percentage.
    """
    gst_rate_percent, itc_available = determine_gst_slab(tariff_per_night)
    tax_amount = round(subtotal * gst_rate_percent / 100, 2)
    same_state = guest_state is not None and guest_state == property_state

    if same_state:
        half = round(tax_amount / 2, 2)
        result = {"cgst": half, "sgst": tax_amount - half, "igst": 0.0, "total": round(subtotal + tax_amount, 2)}
    else:
        result = {"cgst": 0.0, "sgst": 0.0, "igst": tax_amount, "total": round(subtotal + tax_amount, 2)}

    result["gst_rate_percent"] = gst_rate_percent
    result["itc_available"] = itc_available
    return result


def generate_invoice_pdf(
    invoice_no: str,
    property_name: str,
    property_gst_number: str | None,
    guest_name: str,
    guest_gst_number: str | None,
    unit_name: str,
    check_in: dt.date,
    check_out: dt.date,
    subtotal: float,
    cgst: float,
    sgst: float,
    igst: float,
    total: float,
    hsn_code: str,
    gst_rate_percent: float,
    itc_available: bool,
    bank_details: dict | None = None,
    qr_path: str | None = None,
) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "TAX INVOICE", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, property_name, ln=True)
    pdf.set_font("Helvetica", "", 10)
    if property_gst_number:
        pdf.cell(0, 6, f"GSTIN: {property_gst_number}", ln=True)
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Invoice No: {invoice_no}", ln=True)
    pdf.cell(0, 6, f"Issue Date: {dt.date.today().isoformat()}", ln=True)
    pdf.ln(3)

    pdf.cell(0, 6, f"Billed To: {guest_name}", ln=True)
    if guest_gst_number:
        pdf.cell(0, 6, f"Guest GSTIN: {guest_gst_number}", ln=True)
    pdf.ln(3)

    pdf.cell(0, 6, f"Unit: {unit_name}", ln=True)
    pdf.cell(0, 6, f"Stay: {check_in.isoformat()} to {check_out.isoformat()}", ln=True)
    pdf.cell(0, 6, f"HSN/SAC: {hsn_code}", ln=True)
    pdf.cell(0, 6, f"GST Rate Applied: {gst_rate_percent:.0f}% (tariff slab, {'ITC available' if itc_available else 'ITC not available'})", ln=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(95, 7, "Description", border=1)
    pdf.cell(95, 7, "Amount (INR)", border=1, ln=True, align="R")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(95, 7, "Accommodation charges (subtotal)", border=1)
    pdf.cell(95, 7, f"{subtotal:.2f}", border=1, ln=True, align="R")

    if cgst or sgst:
        pdf.cell(95, 7, "CGST", border=1)
        pdf.cell(95, 7, f"{cgst:.2f}", border=1, ln=True, align="R")
        pdf.cell(95, 7, "SGST", border=1)
        pdf.cell(95, 7, f"{sgst:.2f}", border=1, ln=True, align="R")
    if igst:
        pdf.cell(95, 7, "IGST", border=1)
        pdf.cell(95, 7, f"{igst:.2f}", border=1, ln=True, align="R")

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(95, 8, "Total", border=1)
    pdf.cell(95, 8, f"{total:.2f}", border=1, ln=True, align="R")

    # Bank details + payment QR — the same "how to pay" block the
    # quotation PDF already includes, previously missing here entirely
    # despite Billing explicitly requiring "Automatic ... QR Payment".
    pdf.ln(8)
    y_before_payment_block = pdf.get_y()
    if bank_details:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Payment Details", ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, f"Bank: {bank_details.get('bank_name', '')}", ln=True)
        pdf.cell(0, 5, f"Account Name: {bank_details.get('bank_account_name', '')}", ln=True)
        pdf.cell(0, 5, f"Account No: {bank_details.get('bank_account_number', '')}", ln=True)
        pdf.cell(0, 5, f"IFSC: {bank_details.get('bank_ifsc', '')}", ln=True)
        pdf.cell(0, 5, f"Branch: {bank_details.get('bank_branch', '')}", ln=True)
    if qr_path and os.path.exists(qr_path):
        try:
            pdf.image(qr_path, x=150, y=y_before_payment_block, w=32)
            pdf.set_xy(150, y_before_payment_block + 33)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(32, 4, "Scan to pay", align="C")
        except Exception:
            # Same defensive handling as the logo in quotation PDFs — a
            # corrupted QR file must not crash invoice generation.
            pass

    filename = f"{invoice_no.replace('/', '_')}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(INVOICE_STORAGE_DIR, filename)
    pdf.output(filepath)
    return filepath


CREDIT_NOTE_STORAGE_DIR = os.environ.get("CREDIT_NOTE_STORAGE_DIR", "/tmp/sri_apartment_credit_notes")
os.makedirs(CREDIT_NOTE_STORAGE_DIR, exist_ok=True)


def generate_credit_note_pdf(
    credit_note_no: str,
    invoice_no: str,
    property_name: str,
    property_gst_number: str | None,
    guest_name: str,
    amount: float,
    cgst: float,
    sgst: float,
    igst: float,
    total: float,
    reason: str,
) -> str:
    """A GST-compliant credit note PDF — references the original invoice
    it reduces, states the reason, and shows the same CGST/SGST/IGST
    breakdown structure as the invoice it's correcting."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "CREDIT NOTE", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, property_name, ln=True)
    pdf.set_font("Helvetica", "", 10)
    if property_gst_number:
        pdf.cell(0, 6, f"GSTIN: {property_gst_number}", ln=True)
    pdf.ln(3)

    pdf.cell(0, 6, f"Credit Note No: {credit_note_no}", ln=True)
    pdf.cell(0, 6, f"Against Invoice: {invoice_no}", ln=True)
    pdf.cell(0, 6, f"Issue Date: {dt.date.today().isoformat()}", ln=True)
    pdf.ln(3)

    pdf.cell(0, 6, f"Issued To: {guest_name}", ln=True)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Reason for Credit", ln=True)
    pdf.set_font("Helvetica", "", 10)
    safe_reason = "".join(c if ord(c) < 256 else "-" for c in reason)  # same Latin-1 safety as invoice text
    pdf.multi_cell(0, 6, safe_reason)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 10)
    col_w = 95
    pdf.cell(col_w, 8, "Description", border=1)
    pdf.cell(col_w, 8, "Amount (Rs.)", border=1, ln=True, align="R")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(col_w, 7, "Credit Amount (excl. GST)", border=1)
    pdf.cell(col_w, 7, f"{amount:.2f}", border=1, ln=True, align="R")
    if cgst:
        pdf.cell(col_w, 7, "CGST", border=1)
        pdf.cell(col_w, 7, f"{cgst:.2f}", border=1, ln=True, align="R")
        pdf.cell(col_w, 7, "SGST", border=1)
        pdf.cell(col_w, 7, f"{sgst:.2f}", border=1, ln=True, align="R")
    if igst:
        pdf.cell(col_w, 7, "IGST", border=1)
        pdf.cell(col_w, 7, f"{igst:.2f}", border=1, ln=True, align="R")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(col_w, 8, "Total Credit", border=1)
    pdf.cell(col_w, 8, f"{total:.2f}", border=1, ln=True, align="R")

    filename = f"{credit_note_no.replace('/', '_')}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(CREDIT_NOTE_STORAGE_DIR, filename)
    pdf.output(filepath)
    return filepath
