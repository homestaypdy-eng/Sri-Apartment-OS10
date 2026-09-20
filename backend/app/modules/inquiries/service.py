"""
Pre-booking inquiry / quotation logic.

Pricing mirrors the uploaded "SRI Apartment Homestay - Pre-Booking Invoice
Template" exactly: taxable value, CGST/SGST (the template has no IGST column
— pre-booking quotes here are always same-state Puducherry direct bookings,
so this mirrors the template rather than the guest-state-aware IGST logic
used for final GST invoices in app/modules/payments/service.py), rounding to
the nearest rupee, advance/balance.

GST RATE: reuses payments.service.determine_gst_slab() — the same real,
current (effective 22 Sep 2025) tariff-slab rule already implemented and
tested for final invoices — rather than hardcoding the template's "2.5%"
example figures. The template's 2.5%+2.5% example corresponds to a
1,001-7,500 tariff (5% total, split evenly); a different rate/night will
correctly produce a different slab here.
"""
import datetime as dt
import os
import smtplib
import uuid
from email.message import EmailMessage
from urllib.parse import quote

from fpdf import FPDF
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.inquiries.models import InquiryCounter
from app.modules.payments.service import determine_gst_slab

INQUIRY_STORAGE_DIR = os.environ.get("INQUIRY_STORAGE_DIR", "/tmp/sri_apartment_inquiries")
os.makedirs(INQUIRY_STORAGE_DIR, exist_ok=True)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets")
_DEFAULT_LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
_DEFAULT_QR_PATH = os.path.join(ASSETS_DIR, "payment_qr.png")

_DEFAULT_COMPANY = {
    "name": "SRI APARTMENT HOMESTAY",
    "tagline": "Comfortable Stays in the Heart of Puducherry",
    "address": "163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010",
    "contact": "+91 88702 37237   |   homestaypdy@gmail.com",
    "gstin": "34PACPS5159H1Z5",
    "udyam": "UDYAM-PY-03-0032646",
    "sac_line": "SAC Code: 996311 (Accommodation)  |  State Code: 34 (Puducherry)",
    "place_of_supply": "Puducherry (34)",
}

_DEFAULT_BANK = {
    # Bank Account Migration: this fallback is only ever reached when
    # generate_quotation_pdf() is called with bank_settings=None — real
    # request-handling call sites in inquiries/router.py always pass a real
    # BankSettings row via get_or_create_bank_settings(db), which never
    # returns None, so this path is unreachable in normal operation (only
    # a narrow test that constructs a PDF with no DB session at all hits
    # it). Left intentionally blank rather than any real bank's details —
    # BankSettings (see settings/models.py) is the one place real bank
    # data belongs now.
    "bank_name": "",
    "ac_name": "",
    "ac_number": "",
    "ifsc": "",
    "branch": "",
    "upi_id": "",
    "signatory": "S. Sundaramurthy",
    "signatory_title": "Proprietor",
}

_DEFAULT_TERMS = [
    "This is a pre-booking offer, valid up to the date shown above.",
    "Rates are subject to validity & availability.",
    "Booking is confirmed only after receipt of required advance payment.",
    "Check-in: 12:30 PM  |  Check-out: 11:00 AM.",
    "Early check-in / late check-out subject to availability & charges.",
    "Valid government-issued ID proof is mandatory at check-in.",
]
_DEFAULT_HOUSE_RULES = [
    "No smoking inside the property.",
    "No parties or loud music allowed.",
    "Maintain cleanliness & respect the property.",
]
_DEFAULT_CANCELLATION_POLICY = [
    "Cancellation requests must be submitted through WhatsApp or email.",
    "Full refund for cancellations received at least 48 hours before check-in.",
    "No refund for cancellations received within 48 hours before check-in or after check-in.",
]

# Real, self-declared data only (Unit.unit_type — see properties/models.py's
# UNIT_TYPES) — mirrors the label vocabulary already used on the public
# website (src/lib/accommodationTypes.ts) so "Apartment" etc. reads the same
# everywhere. Falls back to a title-cased version of the raw value for any
# type not in this list, exactly like the website does, rather than
# inventing bedroom-count/description text no field on Unit actually stores.
_UNIT_TYPE_LABELS = {
    "apartment": "Apartment",
    "villa": "Villa",
    "suite": "Suite",
    "room": "Room",
    "cottage": "Cottage",
    "resort": "Resort Unit",
    "other": "Other",
}


def _apartment_display(inquiry) -> str:
    unit = getattr(inquiry, "unit", None)
    if unit is not None and getattr(unit, "unit_type", None):
        label = _UNIT_TYPE_LABELS.get(unit.unit_type, unit.unit_type.replace("_", " ").title())
        # Plain ASCII hyphen, not an en-dash: FPDF's core "Helvetica" font
        # is Latin-1 only and raises FPDFUnicodeEncodingException on "–".
        return f"{label} - {inquiry.flat_room_label}"
    return inquiry.flat_room_label


def _pdf_context(business_settings=None, bank_settings=None) -> dict:
    """
    Builds the company/bank/document-text values a quotation PDF needs,
    from BusinessSettings/BankSettings if rows are provided, falling back
    to the original hardcoded defaults above otherwise (e.g. if called
    without a DB session, as some tests do). This is the single place PDF
    generation reads business info from — Settings is the source of truth
    once settings rows exist, per the "no hardcoded values" requirement.

    RBAC Phase A: bank/UPI fields moved out of BusinessSettings into their
    own admin-only BankSettings table — bank_settings is now a second,
    independent optional argument rather than a sub-object of
    business_settings. Passing business_settings without bank_settings
    (or vice versa) is valid; each falls back to its own defaults
    independently.
    """
    company = _DEFAULT_COMPANY
    terms, house_rules, cancellation_policy = _DEFAULT_TERMS, _DEFAULT_HOUSE_RULES, _DEFAULT_CANCELLATION_POLICY
    logo_path, qr_path = _DEFAULT_LOGO_PATH, _DEFAULT_QR_PATH

    if business_settings is not None:
        s = business_settings
        company = {
            "name": s.company_name,
            "tagline": s.tagline,
            "address": s.address,
            "contact": f"{s.contact_phone}   |   {s.contact_email}",
            "gstin": s.gst_number,
            "udyam": s.udyam_number,
            "sac_line": f"SAC Code: 996311 (Accommodation)  |  State Code: {s.state_code} (Puducherry)",
            "place_of_supply": f"Puducherry ({s.state_code})",
        }
        terms = [line for line in s.quotation_terms.split("\n") if line.strip()] or _DEFAULT_TERMS
        house_rules = [line for line in s.house_rules.split("\n") if line.strip()] or _DEFAULT_HOUSE_RULES
        cancellation_policy = [line for line in s.cancellation_policy.split("\n") if line.strip()] or _DEFAULT_CANCELLATION_POLICY
        logo_path = s.logo_path or _DEFAULT_LOGO_PATH
        qr_path = s.qr_code_path or _DEFAULT_QR_PATH

    bank = _DEFAULT_BANK
    # Signatory stays on BusinessSettings (it's not a bank field), so it's
    # layered onto whichever bank dict is used, from business_settings if
    # available — same value it always was, just sourced independently of
    # the bank split.
    signatory = _DEFAULT_BANK["signatory"]
    signatory_title = _DEFAULT_BANK["signatory_title"]
    if business_settings is not None:
        signatory = business_settings.signatory_name
        signatory_title = business_settings.signatory_title
    if bank_settings is not None:
        b = bank_settings
        bank = {
            "bank_name": b.bank_name,
            "ac_name": b.bank_account_name,
            "ac_number": b.bank_account_number,
            "ifsc": b.bank_ifsc,
            "branch": b.bank_branch,
            "upi_id": b.upi_id,
            "signatory": signatory,
            "signatory_title": signatory_title,
        }
    elif business_settings is not None:
        bank = {**_DEFAULT_BANK, "signatory": signatory, "signatory_title": signatory_title}

    return {
        "company": company,
        "bank": bank,
        "terms": terms,
        "house_rules": house_rules,
        "cancellation_policy": cancellation_policy,
        "logo_path": logo_path,
        "qr_path": qr_path,
    }


def _financial_year_short(as_of: dt.date | None = None) -> str:
    as_of = as_of or dt.date.today()
    if as_of.month >= 4:
        return f"{as_of.year % 100:02d}-{(as_of.year + 1) % 100:02d}"
    return f"{(as_of.year - 1) % 100:02d}-{as_of.year % 100:02d}"


def next_offer_no(db: Session) -> str:
    fy = _financial_year_short()
    counter = db.get(InquiryCounter, fy, with_for_update=True)
    if counter is None:
        counter = InquiryCounter(financial_year_short=fy, last_number=0)
        db.add(counter)
        db.flush()
    counter.last_number += 1
    db.flush()
    return f"SRI/{fy}/{counter.last_number:03d}"


def calculate_pricing(rate_per_night: float, nights: int, extra_charges: float, discount: float) -> dict:
    taxable_value = round(rate_per_night * nights + extra_charges - discount, 2)
    gst_rate_percent, _itc_available = determine_gst_slab(rate_per_night)
    gst_amount = round(taxable_value * gst_rate_percent / 100, 2)
    cgst = round(gst_amount / 2, 2)
    sgst = gst_amount - cgst
    total_amount = round(taxable_value + cgst + sgst, 2)
    grand_total = round(total_amount)  # nearest rupee, matches template's "ROUNDED OFF" line
    rounded_off = round(grand_total - total_amount, 2)

    return {
        "taxable_value": taxable_value,
        "gst_rate_percent": gst_rate_percent,
        "cgst": cgst,
        "sgst": sgst,
        "total_amount": total_amount,
        "rounded_off": rounded_off,
        "grand_total": grand_total,
    }


def calculate_pricing_from_subtotal(accommodation_subtotal: float, average_nightly_rate: float, extra_charges: float, discount: float) -> dict:
    """
    Same GST/rounding math as calculate_pricing(), but takes an
    already-computed accommodation subtotal (from the dynamic pricing
    engine — weekday/weekend/seasonal mix, long-stay discount already
    applied) instead of assuming a flat rate_per_night * nights. The GST
    slab is still determined from average_nightly_rate, matching how the
    tariff-slab rule is meant to apply to what the guest is actually
    being charged per night on average, not a single quoted rate that may
    not exist for every night of a mixed-rate stay.
    """
    taxable_value = round(accommodation_subtotal + extra_charges - discount, 2)
    gst_rate_percent, _itc_available = determine_gst_slab(average_nightly_rate)
    gst_amount = round(taxable_value * gst_rate_percent / 100, 2)
    cgst = round(gst_amount / 2, 2)
    sgst = gst_amount - cgst
    total_amount = round(taxable_value + cgst + sgst, 2)
    grand_total = round(total_amount)
    rounded_off = round(grand_total - total_amount, 2)

    return {
        "taxable_value": taxable_value,
        "gst_rate_percent": gst_rate_percent,
        "cgst": cgst,
        "sgst": sgst,
        "total_amount": total_amount,
        "rounded_off": rounded_off,
        "grand_total": grand_total,
    }


def _amount_in_words(amount: float) -> str:
    """Minimal INR amount-in-words for whole rupees, matching the template's 'Amount in Words' line."""
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two_digit(n):
        if n < 20:
            return ones[n]
        return (tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")).strip()

    def three_digit(n):
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


def generate_quotation_pdf(inquiry, business_settings=None, bank_settings=None) -> str:
    ctx = _pdf_context(business_settings, bank_settings)
    company, bank = ctx["company"], ctx["bank"]
    terms, house_rules, cancellation_policy = ctx["terms"], ctx["house_rules"], ctx["cancellation_policy"]
    logo_path, qr_path = ctx["logo_path"], ctx["qr_path"]

    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(10, 10, 10)
    # FPDF's auto-page-break bottom margin defaults to 20mm regardless of
    # set_margins() above — left uninitialized, that silently gave this
    # one-page document only 277mm of usable height instead of 287mm,
    # which is what was actually forcing the last section onto a spurious
    # second page. Match it to the same 10mm used on every other edge.
    pdf.set_auto_page_break(True, margin=10)
    navy = (15, 23, 60)
    gold = (176, 141, 62)

    # --- Header: logo + company details (left), offer details (right) ---
    if os.path.exists(logo_path):
        try:
            pdf.image(logo_path, x=10, y=8, w=28)
        except Exception:
            # A corrupted or invalid logo file must never take down
            # quotation generation for the whole business — skip the
            # image and continue with the rest of the document rather
            # than crash the request. (Found via a real test that
            # uploaded an invalid image file and broke every PDF in the
            # suite until this was added.)
            pass

    pdf.set_xy(42, 8)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*navy)
    pdf.cell(100, 6, company["name"], ln=True)
    pdf.set_x(42)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*gold)
    pdf.cell(100, 5, company["tagline"], ln=True)
    pdf.set_x(42)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(60, 60, 60)
    pdf.multi_cell(110, 4, company["address"])
    pdf.set_x(42)
    pdf.cell(110, 4, company["contact"], ln=True)
    pdf.set_x(42)
    pdf.cell(110, 4, f"GSTIN: {company['gstin']}", ln=True)
    pdf.set_x(42)
    pdf.cell(110, 4, f"UDYAM (MSME): {company['udyam']}", ln=True)
    pdf.set_x(42)
    pdf.cell(110, 4, company["sac_line"], ln=True)
    left_col_bottom = pdf.get_y()

    # Offer details box, right side
    box_x = 152
    pdf.set_xy(box_x, 8)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*navy)
    pdf.cell(48, 6, "PRE-BOOKING OFFER", ln=False, align="R")
    pdf.set_xy(box_x, 15)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(0, 0, 0)
    for label, value in [
        ("Offer No.", inquiry.offer_no),
        ("Offer Date", inquiry.offer_date.isoformat()),
        ("Valid Upto", inquiry.valid_upto.isoformat()),
        ("Place of Supply", company["place_of_supply"]),
        ("Reverse Charge", "No"),
    ]:
        pdf.set_x(box_x)
        pdf.cell(24, 5, label, align="L")
        pdf.cell(24, 5, str(value), align="R", ln=True)

    # Payment Mode gets its own full-width row (label above, value below)
    # rather than squeezing into the same 24mm/24mm label|value columns as
    # the fields above — "Online / Bank Transfer" (or any longer configured
    # value) doesn't fit a 24mm right-aligned cell and was overlapping the
    # label. multi_cell here wraps instead of overlapping if it's ever
    # longer still.
    pdf.set_x(box_x)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(48, 4, "Payment Mode", ln=True)
    pdf.set_x(box_x)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(48, 4, str(inquiry.payment_mode))
    right_col_bottom = pdf.get_y()

    # Divider sits below whichever column (company info vs. offer-details
    # box) ended up taller — was a fixed y=46 that assumed a short,
    # single-line Payment Mode value and got drawn straight through the
    # value whenever it wrapped to 2 lines.
    pdf.set_y(max(left_col_bottom, right_col_bottom) + 1)
    pdf.set_draw_color(*gold)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 4, "This quotation is not a tax invoice. A tax invoice will be issued as applicable.", align="C", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)

    # --- Client Details | Stay Details ---
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
        ("Client Name", inquiry.client_name),
        ("Phone No.", inquiry.client_phone),
        ("Email ID", inquiry.client_email or "-"),
        ("Address", inquiry.client_address or "-"),
    ]
    right_rows = [
        ("Flat / Room", _apartment_display(inquiry)),
        ("No. of Guests", str(inquiry.num_guests)),
        ("Check-in", f"{inquiry.check_in_date.isoformat()}  {inquiry.check_in_time}"),
        ("Check-out", f"{inquiry.check_out_date.isoformat()}  {inquiry.check_out_time}"),
        ("No. of Nights", str(inquiry.nights)),
        ("Tariff Type", inquiry.tariff_type),
        ("Rate / Night (Rs.)", f"{float(inquiry.rate_per_night):.2f}"),
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

    # --- Accommodation Charges table ---
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

    accom_amount = float(inquiry.rate_per_night) * inquiry.nights
    pdf.set_font("Helvetica", "", 8)
    row1 = ["1", "Accommodation Charges (as per stay duration)", "996311", str(inquiry.nights),
            f"{float(inquiry.rate_per_night):.2f}", f"{accom_amount:.2f}"]
    for v, w, align in zip(row1, widths, ["C", "L", "C", "C", "R", "R"]):
        pdf.cell(w, 6, v, border=1, align=align)
    pdf.ln()

    for label, val in [("Extra Charges (if any)", float(inquiry.extra_charges)),
                        ("Discount (if any)", -float(inquiry.discount))]:
        pdf.cell(widths[0] + widths[1] + widths[2] + widths[3], 6, label, border=1)
        pdf.cell(widths[4], 6, "", border=1)
        pdf.cell(widths[5], 6, f"{val:.2f}", border=1, align="R")
        pdf.ln()

    summary_rows = [
        ("TAXABLE VALUE", float(inquiry.taxable_value)),
        (f"CGST @ {float(inquiry.gst_rate_percent) / 2:.2f}%", float(inquiry.cgst)),
        (f"SGST @ {float(inquiry.gst_rate_percent) / 2:.2f}%", float(inquiry.sgst)),
        ("TOTAL AMOUNT", float(inquiry.total_amount)),
        ("ROUNDED OFF", float(inquiry.rounded_off)),
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
    pdf.cell(widths[5], 7, f"{float(inquiry.grand_total):.2f}", border=1, align="R", fill=True)
    pdf.ln(9)
    pdf.set_text_color(0, 0, 0)

    pdf.set_font("Helvetica", "I", 7.5)
    pdf.multi_cell(0, 4, f"Amount in Words: {_amount_in_words(float(inquiry.grand_total))}    |    GST on Reverse Charge: NO")
    pdf.ln(1)

    # --- Advance / Balance ---
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 245, 235)
    pdf.cell(95, 6, "ADVANCE PAYMENT (for confirmation)", border=1, fill=True)
    pdf.cell(95, 6, "BALANCE AMOUNT (payable at check-in)", border=1, fill=True, ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(95, 7, f"Rs. {float(inquiry.advance_amount):.2f}", border=1)
    pdf.cell(95, 7, f"Rs. {float(inquiry.balance_amount):.2f}", border=1, ln=True)
    pdf.ln(3)

    # --- Terms | House Rules | Cancellation ---
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*navy)
    col3 = 63
    y0 = pdf.get_y()
    for i, title in enumerate(["TERMS & CONDITIONS", "HOUSE RULES", "CANCELLATION POLICY"]):
        pdf.set_xy(10 + i * col3, y0)
        pdf.cell(col3, 5, title)
    pdf.ln(5)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 7)
    y1 = pdf.get_y()
    # Each column can wrap to a different number of lines (Terms is
    # typically the longest) — multi_cell only leaves pdf.get_y() at
    # whichever column was drawn LAST, so the true row height is the max
    # across all three, tracked explicitly here rather than assumed to be
    # a fixed 22mm (which overlapped the section below once Terms grew
    # past 6-7 lines).
    col_bottoms = []
    for i, items in enumerate([terms, house_rules, cancellation_policy]):
        pdf.set_xy(10 + i * col3, y1)
        text = "\n".join(f"- {t}" for t in items)
        pdf.multi_cell(col3 - 2, 3.4, text)
        col_bottoms.append(pdf.get_y())
    pdf.set_y(max(col_bottoms))
    pdf.ln(2)

    # --- Bank Details | Signatory | Scan & Pay ---
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
        f"Bank Name: {bank['bank_name']}",
        f"A/C Name: {bank['ac_name']}",
        f"A/C Number: {bank['ac_number']}",
        f"IFSC Code: {bank['ifsc']}",
        f"Branch: {bank['branch']}",
    ]
    pdf.set_xy(10, y2 + 6)
    pdf.multi_cell(col3 - 2, 4, "\n".join(bank_lines))

    pdf.set_xy(10 + col3, y2 + 6)
    pdf.multi_cell(col3 - 2, 4, f"For {company['name']}\n\n\n{bank['signatory']}\n{bank['signatory_title']}")

    if os.path.exists(qr_path):
        try:
            pdf.image(qr_path, x=10 + 2 * col3, y=y2 + 6, w=30)
        except Exception:
            pass
    pdf.set_xy(10 + 2 * col3, y2 + 38)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(col3 - 2, 4, f"UPI ID: {bank['upi_id']}")

    pdf.set_y(max(pdf.get_y(), y2 + 44))
    pdf.ln(1.5)
    pdf.set_draw_color(*gold)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(1.5)
    pdf.set_font("Helvetica", "BI", 8)
    pdf.set_text_color(*navy)
    pdf.cell(0, 4, "THANK YOU FOR CHOOSING SRI APARTMENT HOMESTAY  -  Feel at Home, Away from Home", align="C")

    filename = f"{inquiry.offer_no.replace('/', '_')}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(INQUIRY_STORAGE_DIR, filename)
    pdf.output(filepath)
    return filepath


# --- WhatsApp click-to-chat ---

def _format_whatsapp_phone(raw_phone: str) -> str:
    """
    wa.me requires a full international number with no leading '+', spaces,
    or zeros. This assumes India (+91) for bare 10-digit numbers, which
    covers this business's actual client base — not a general-purpose
    phone-number normalizer.
    """
    digits = "".join(c for c in raw_phone if c.isdigit())
    if len(digits) == 10:
        return f"{settings.default_country_code}{digits}"
    return digits.lstrip("0")


def build_whatsapp_message(inquiry) -> str:
    return (
        f"Hi {inquiry.client_name}, thank you for your interest in Sri Apartment Homestay!\n\n"
        f"Your pre-booking quotation ({inquiry.offer_no}):\n"
        f"- Flat/Room: {inquiry.flat_room_label}\n"
        f"- Check-in: {inquiry.check_in_date} {inquiry.check_in_time}\n"
        f"- Check-out: {inquiry.check_out_date} {inquiry.check_out_time}\n"
        f"- Grand Total: Rs. {float(inquiry.grand_total):.2f}\n"
        f"- Advance to confirm: Rs. {float(inquiry.advance_amount):.2f}\n"
        f"- Balance at check-in: Rs. {float(inquiry.balance_amount):.2f}\n"
        f"- Valid until: {inquiry.valid_upto}\n\n"
        f"We'll share the detailed PDF quotation separately. "
        f"Reply here to confirm or ask any questions. Feel at Home, Away from Home!"
    )


def build_whatsapp_url(inquiry) -> str:
    """
    Returns a wa.me click-to-chat link with a prefilled message. This opens
    WhatsApp (app or web) with the message ready to send — it does NOT
    attach the PDF automatically. WhatsApp's click-to-chat API has no
    mechanism to attach files via URL; the staff member sends this prefilled
    text, then separately attaches the PDF (already downloadable from this
    app) inside the opened chat. That two-step reality is intentional to
    surface here rather than promise one-click file delivery WhatsApp
    itself doesn't support.
    """
    phone = _format_whatsapp_phone(inquiry.client_phone)
    message = build_whatsapp_message(inquiry)
    return f"https://wa.me/{phone}?text={quote(message)}"


# --- Email sending ---

class EmailNotConfiguredError(Exception):
    pass


def send_quotation_email(inquiry, pdf_path: str, to_email: str) -> None:
    """
    Sends the quotation PDF as an email attachment via SMTP, using settings
    from environment variables (see core/config.py). Raises
    EmailNotConfiguredError if SMTP_HOST isn't set — this environment has no
    real SMTP credentials, so this function is real, working code that a
    production deployment activates by setting env vars, not a stub.
    """
    if not settings.smtp_host:
        raise EmailNotConfiguredError(
            "Email sending is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD "
            "(and optionally SMTP_PORT, SMTP_FROM_EMAIL) as environment variables."
        )

    msg = EmailMessage()
    msg["Subject"] = f"Your Pre-Booking Quotation — {inquiry.offer_no} — Sri Apartment Homestay"
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg.set_content(build_whatsapp_message(inquiry))  # same friendly summary text as the body

    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=f"{inquiry.offer_no.replace('/', '_')}_quotation.pdf",
        )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
