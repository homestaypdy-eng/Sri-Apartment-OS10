"""
Proves Settings is genuinely the source of truth for quotation PDF
generation — not just that the endpoint returns 200, but that changing a
Settings field actually changes the PDF's rendered text content.
Run with: pytest tests/test_settings_drives_pdf.py -v
"""
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    proc = subprocess.run(["pdftotext", "-", "-"], input=pdf_bytes, capture_output=True, check=True)
    return proc.stdout.decode()


def test_changing_settings_changes_quotation_pdf_content(headers):
    original = client.get("/api/v1/settings", headers=headers).json()
    marker = f"MARKER_{uuid.uuid4().hex[:10].upper()}"

    try:
        update = client.put("/api/v1/settings", headers=headers, json={"company_name": marker})
        assert update.status_code == 200
        assert update.json()["company_name"] == marker

        inquiry = client.post(
            "/api/v1/inquiries",
            headers=headers,
            json={
                "client_name": "Settings PDF Test",
                "client_phone": f"96{uuid.uuid4().int % 10**8:08d}",
                "flat_room_label": "Test Room",
                "check_in_date": "2029-01-10",
                "check_out_date": "2029-01-12",
                "rate_per_night": 2000,
                "advance_amount": 500,
            },
        ).json()

        pdf_resp = client.get(f"/api/v1/inquiries/{inquiry['id']}/pdf", headers=headers)
        assert pdf_resp.status_code == 200

        text = _extract_pdf_text(pdf_resp.content)
        assert marker in text, "Settings.company_name did not appear in the generated PDF"
    finally:
        client.put("/api/v1/settings", headers=headers, json={"company_name": original["company_name"]})


def test_bank_account_number_from_settings_appears_in_pdf(headers):
    original = client.get("/api/v1/settings", headers=headers).json()
    marker_account = f"{uuid.uuid4().int % 10**12:012d}"

    try:
        client.put("/api/v1/settings", headers=headers, json={"bank_account_number": marker_account})

        inquiry = client.post(
            "/api/v1/inquiries",
            headers=headers,
            json={
                "client_name": "Bank Settings PDF Test",
                "client_phone": f"95{uuid.uuid4().int % 10**8:08d}",
                "flat_room_label": "Test Room",
                "check_in_date": "2029-02-10",
                "check_out_date": "2029-02-12",
                "rate_per_night": 2000,
                "advance_amount": 500,
            },
        ).json()

        pdf_resp = client.get(f"/api/v1/inquiries/{inquiry['id']}/pdf", headers=headers)
        text = _extract_pdf_text(pdf_resp.content)
        assert marker_account in text
    finally:
        client.put("/api/v1/settings", headers=headers, json={"bank_account_number": original["bank_account_number"]})


def test_pdf_generation_without_settings_row_falls_back_to_defaults():
    from app.modules.inquiries.service import generate_quotation_pdf
    from unittest.mock import MagicMock
    import datetime as dt

    fake_inquiry = MagicMock()
    fake_inquiry.offer_no = "SRI/TEST/000"
    fake_inquiry.offer_date = dt.date.today()
    fake_inquiry.valid_upto = dt.date.today()
    fake_inquiry.client_name = "Fallback Test"
    fake_inquiry.client_phone = "9000000000"
    fake_inquiry.client_email = None
    fake_inquiry.client_address = None
    fake_inquiry.flat_room_label = "Test"
    fake_inquiry.num_guests = 1
    fake_inquiry.check_in_date = dt.date.today()
    fake_inquiry.check_in_time = "12:30 PM"
    fake_inquiry.check_out_date = dt.date.today()
    fake_inquiry.check_out_time = "11:00 AM"
    fake_inquiry.tariff_type = "Room Tariff"
    fake_inquiry.rate_per_night = 2000
    fake_inquiry.nights = 1
    fake_inquiry.extra_charges = 0
    fake_inquiry.discount = 0
    fake_inquiry.taxable_value = 2000
    fake_inquiry.gst_rate_percent = 5
    fake_inquiry.cgst = 50
    fake_inquiry.sgst = 50
    fake_inquiry.total_amount = 2100
    fake_inquiry.rounded_off = 0
    fake_inquiry.grand_total = 2100
    fake_inquiry.advance_amount = 500
    fake_inquiry.balance_amount = 1600

    filepath = generate_quotation_pdf(fake_inquiry, business_settings=None)
    with open(filepath, "rb") as f:
        assert f.read()[:4] == b"%PDF"
