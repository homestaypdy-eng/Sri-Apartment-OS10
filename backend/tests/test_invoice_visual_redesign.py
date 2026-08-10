import os
import subprocess
import tempfile
from datetime import date

from app.modules.payments.service import generate_invoice_pdf


def test_generate_invoice_pdf_uses_offer_style_layout_and_single_page(tmp_path):
    bank_details = {
        "bank_name": "Axis Bank",
        "bank_account_name": "Sri Apartment",
        "bank_account_number": "925020053638481",
        "bank_ifsc": "UTIB0003923",
        "bank_branch": "Puducherry Branch",
    }
    qr_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "assets", "payment_qr.png")

    pdf_path = generate_invoice_pdf(
        invoice_no="INV/2026-2027/000001",
        property_name="SRI APARTMENT HOMESTAY",
        property_gst_number="34PACPS5159H1Z5",
        guest_name="Test Guest",
        guest_gst_number="33ABCDE1234F1Z5",
        unit_name="Deluxe Room",
        check_in=date(2026, 8, 1),
        check_out=date(2026, 8, 3),
        subtotal=4800.0,
        cgst=240.0,
        sgst=240.0,
        igst=0.0,
        total=5280.0,
        hsn_code="996311",
        gst_rate_percent=10.0,
        itc_available=False,
        bank_details=bank_details,
        qr_path=qr_path,
        payment_received=3000.0,
        balance_due=2280.0,
    )

    assert os.path.exists(pdf_path)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(open(pdf_path, "rb").read())
        temp_pdf = handle.name

    try:
        text = subprocess.run(["pdftotext", temp_pdf, "-"], capture_output=True, text=True, check=True).stdout
    finally:
        os.unlink(temp_pdf)

    assert "TAX INVOICE" in text
    assert "SRI APARTMENT HOMESTAY" in text
    assert "Luxury 2BHK Family Homestay" in text
    assert "Invoice No" in text
    assert "Amount in Words" in text
    assert "Payment Received" in text
    assert "Balance Due" in text
    assert "PAID" not in text or "PARTIALLY PAID" in text
    assert "PRE-BOOKING OFFER" not in text
    assert "Quotation" not in text
    assert "Quote" not in text
    assert "Axis Bank" in text
    assert "UTIB0003923" in text
