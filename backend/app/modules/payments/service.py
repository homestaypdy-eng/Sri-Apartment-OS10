import datetime as dt
import os
import uuid

from fpdf import FPDF
from sqlalchemy.orm import Session

from app.modules.payments.models import InvoiceCounter

INVOICE_STORAGE_DIR = os.environ.get("INVOICE_STORAGE_DIR", "/tmp/sri_apartment_invoices")
os.makedirs(INVOICE_STORAGE_DIR, exist_ok=True)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets")
_DEFAULT_LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
_DEFAULT_QR_PATH = os.path.join(ASSETS_DIR, "payment_qr.png")

_DEFAULT_COMPANY = {
    "name": "SRI APARTMENT HOMESTAY",
    "tagline": "Luxury 2BHK Family Homestay",
    "address": "163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010",
    "contact": "+91 88702 37237   |   homestaypdy@gmail.com",
    "gstin": "34PACPS5159H1Z5",
    "udyam": "UDYAM-PY-03-0032646",
    "sac_line": "SAC Code: 996311 (Accommodation)",
    "state_code": "34",
}


def _amount_in_words(amount: float) -> str:
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two_digit(n: int) -> str:
        if n < 20:
            return ones[n]
        return (tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")).strip()

    def three_digit(n: int) -> str:
        if n >= 100:
            return ones[n // 100] + " Hundred" + (" " + two_digit(n % 100) if n % 100 else "")
        return two_digit(n)

    n = int(round(amount))
    if n == 0:
        return "Zero Rupees Only"
    parts = []
    crore, n = divmod(n, 10000000)
    if crore:
        parts.append(three_digit(crore) + " Crore")
    lakh, n = divmod(n, 100000)
    if lakh:
        parts.append(three_digit(lakh) + " Lakh")
    thousand, n = divmod(n, 1000)
    if thousand:
        parts.append(three_digit(thousand) + " Thousand")
    if n:
        parts.append(three_digit(n))
    return " ".join(parts) + " Rupees Only"


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
    payment_received: float | None = None,
    balance_due: float | None = None,
    guest_phone: str | None = None,
    guest_email: str | None = None,
    guest_address: str | None = None,
    tariff_type: str | None = None,
    num_guests: int | None = None,
    rate_per_night: float | None = None,
    payment_mode: str | None = None,
) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(False)
    navy = (15, 23, 60)
    gold = (176, 141, 62)

    logo_path = _DEFAULT_LOGO_PATH
    qr_image_path = qr_path or _DEFAULT_QR_PATH

    if os.path.exists(logo_path):
        try:
            pdf.image(logo_path, x=10, y=8, w=28)
        except Exception:
            pass

    pdf.set_xy(42, 8)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*navy)
    pdf.cell(100, 6, _DEFAULT_COMPANY["name"], ln=True)
    pdf.set_x(42)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*gold)
    pdf.cell(100, 5, _DEFAULT_COMPANY["tagline"], ln=True)
    pdf.set_x(42)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(60, 60, 60)
    pdf.multi_cell(110, 4, _DEFAULT_COMPANY["address"])
    pdf.set_x(42)
    pdf.cell(110, 4, _DEFAULT_COMPANY["contact"], ln=True)
    pdf.set_x(42)
    pdf.cell(110, 4, f"GSTIN: {_DEFAULT_COMPANY['gstin']}", ln=True)
    pdf.set_x(42)
    pdf.cell(110, 4, f"UDYAM (MSME): {_DEFAULT_COMPANY['udyam']}", ln=True)
    pdf.set_x(42)
    pdf.cell(110, 4, _DEFAULT_COMPANY["sac_line"], ln=True)

    box_x = 152
    pdf.set_xy(box_x, 8)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*navy)
    pdf.cell(48, 6, "TAX INVOICE", ln=False, align="R")
    pdf.set_xy(box_x, 15)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(0, 0, 0)
    for label, value in [
        ("Invoice No.", invoice_no),
        ("Invoice Date", dt.date.today().isoformat()),
        ("Place of Supply", f"Puducherry ({_DEFAULT_COMPANY['state_code']})"),
        ("Reverse Charge", "No"),
        ("Payment Mode", payment_mode or "N/A"),
    ]:
        pdf.set_x(box_x)
        pdf.cell(24, 5, label, align="L")
        pdf.cell(24, 5, str(value), align="R", ln=True)

    pdf.set_y(46)
    pdf.set_draw_color(*gold)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)

    col_w = 95
    y_start = pdf.get_y()
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*navy)
    pdf.set_xy(10, y_start)
    pdf.cell(col_w, 6, "CLIENT DETAILS")
    pdf.set_xy(10 + col_w, y_start)
    pdf.cell(col_w, 6, "STAY DETAILS", ln=True)

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(0, 0, 0)
    left_rows = [
        ("Client Name", guest_name),
        ("Phone No.", guest_phone or "-"),
        ("Email ID", guest_email or "-"),
        ("Address", guest_address or "-"),
        ("GSTIN", guest_gst_number or "-"),
    ]
    right_rows = [
        ("Flat / Room", unit_name),
        ("No. of Guests", str(num_guests) if num_guests is not None else "-"),
        ("Check-in", check_in.isoformat()),
        ("Check-out", check_out.isoformat()),
        ("No. of Nights", str((check_out - check_in).days) if check_out >= check_in else "-"),
        ("Tariff Type", tariff_type or "-"),
        ("Rate / Night", f"{float(rate_per_night):.2f}" if rate_per_night is not None else "-"),
    ]
    y = y_start + 7
    for i in range(max(len(left_rows), len(right_rows))):
        pdf.set_xy(10, y)
        if i < len(left_rows):
            label, value = left_rows[i]
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(28, 5, label)
            pdf.set_font("Helvetica", "", 8)
            pdf.multi_cell(col_w - 28, 5, str(value))
        if i < len(right_rows):
            label, value = right_rows[i]
            pdf.set_xy(10 + col_w, y)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(38, 5, label)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(col_w - 38, 5, str(value))
        y += 5.5
    pdf.set_y(y + 2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*navy)
    pdf.cell(0, 6, "ACCOMMODATION CHARGES", ln=True)
    pdf.set_text_color(0, 0, 0)

    headers = ["SL", "Description", "SAC", "Nights", "Rate (Rs.)", "Amount (Rs.)"]
    widths = [10, 78, 20, 18, 27, 37]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(240, 240, 245)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, h, border=1, align="C", fill=True)
    pdf.ln()

    nights = (check_out - check_in).days if check_out >= check_in else 1
    rate_value = float(rate_per_night) if rate_per_night is not None else (float(subtotal) / nights if nights else 0.0)
    pdf.set_font("Helvetica", "", 8)
    row1 = ["1", "Accommodation Charges", hsn_code or "996311", str(nights), f"{rate_value:.2f}", f"{subtotal:.2f}"]
    for v, w, align in zip(row1, widths, ["C", "L", "C", "C", "R", "R"]):
        pdf.cell(w, 6, v, border=1, align=align)
    pdf.ln()

    for label, val in [("Extra Charges (if any)", 0.0), ("Discount (if any)", 0.0)]:
        pdf.cell(widths[0] + widths[1] + widths[2] + widths[3], 6, label, border=1)
        pdf.cell(widths[4], 6, "", border=1)
        pdf.cell(widths[5], 6, f"{val:.2f}", border=1, align="R")
        pdf.ln()

    summary_rows = [
        ("TAXABLE VALUE", float(subtotal)),
        ("CGST / SGST OR IGST", float(cgst + sgst) if cgst or sgst else float(igst)),
        ("TOTAL AMOUNT", float(total)),
        ("ROUNDED OFF", round(round(total) - float(total), 2)),
    ]
    for label, val in summary_rows:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(sum(widths[:4]), 6, label, border=1)
        pdf.cell(widths[4], 6, "", border=1)
        pdf.cell(widths[5], 6, f"{val:.2f}", border=1, align="R")
        pdf.ln()

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*navy)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(sum(widths[:4]), 7, "GRAND TOTAL (Rs.)", border=1, fill=True)
    pdf.cell(widths[4], 7, "", border=1, fill=True)
    pdf.cell(widths[5], 7, f"{round(total):.2f}", border=1, align="R", fill=True)
    pdf.ln(7)
    pdf.set_text_color(0, 0, 0)

    pdf.set_font("Helvetica", "I", 7.5)
    pdf.multi_cell(0, 4, f"Amount in Words: {_amount_in_words(round(total))}")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 245, 235)
    pdf.cell(95, 6, "PAYMENT SUMMARY", border=1, fill=True)
    pdf.cell(95, 6, "PAYMENT STATUS", border=1, fill=True, ln=True)
    pdf.set_font("Helvetica", "", 9)
    received_value = float(payment_received) if payment_received is not None else 0.0
    balance_value = float(balance_due) if balance_due is not None else 0.0
    payment_status = "PAID" if balance_value <= 0 else "PARTIALLY PAID" if received_value > 0 else "NOT PAID"
    pdf.cell(95, 7, f"Invoice Total: Rs. {float(total):.2f}", border=1)
    pdf.cell(95, 7, f"Payment Status: {payment_status}", border=1, ln=True)
    pdf.cell(95, 7, f"Payment Received: Rs. {received_value:.2f}", border=1)
    pdf.cell(95, 7, f"Balance Due: Rs. {balance_value:.2f}", border=1, ln=True)
    pdf.ln(3)

    col3 = 63
    y2 = pdf.get_y()
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*navy)
    pdf.set_xy(10, y2)
    pdf.cell(col3, 5, "BANK DETAILS")
    pdf.set_xy(10 + col3, y2)
    pdf.cell(col3, 5, "AUTHORISED SIGNATORY")
    pdf.set_xy(10 + 2 * col3, y2)
    pdf.cell(col3, 5, "SCAN & PAY")

    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(0, 0, 0)
    bank_lines = [
        f"Bank Name: {bank_details.get('bank_name', '') if bank_details else ''}",
        f"A/C Name: {bank_details.get('bank_account_name', '') if bank_details else ''}",
        f"A/C Number: {bank_details.get('bank_account_number', '') if bank_details else ''}",
        f"IFSC Code: {bank_details.get('bank_ifsc', '') if bank_details else ''}",
        f"Branch: {bank_details.get('bank_branch', '') if bank_details else ''}",
    ]
    pdf.set_xy(10, y2 + 6)
    pdf.multi_cell(col3 - 2, 4, "\n".join(bank_lines))

    signatory_name = bank_details.get("signatory_name", "") if bank_details else ""
    signatory_title = bank_details.get("signatory_title", "") if bank_details else ""
    pdf.set_xy(10 + col3, y2 + 6)
    pdf.multi_cell(col3 - 2, 4, f"For {property_name}\n\n\n{signatory_name}\n{signatory_title}")

    if os.path.exists(qr_image_path):
        try:
            pdf.image(qr_image_path, x=10 + 2 * col3, y=y2 + 6, w=32)
        except Exception:
            pass
    pdf.set_xy(10 + 2 * col3, y2 + 40)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(col3 - 2, 4, f"UPI ID: {bank_details.get('upi_id', '') if bank_details else ''}")

    pdf.set_y(max(pdf.get_y(), y2 + 46))
    pdf.ln(3)
    pdf.set_draw_color(*gold)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "BI", 8)
    pdf.set_text_color(*navy)
    pdf.cell(0, 5, "THANK YOU FOR CHOOSING SRI APARTMENT HOMESTAY  -  Feel at Home, Away from Home", align="C")

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
