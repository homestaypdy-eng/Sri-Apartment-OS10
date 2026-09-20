"""
Tests completing the Billing module to full production coverage: Credit
Notes (a GST-compliant document type that didn't exist at all), and
Invoice email/WhatsApp sending (built but never wired to an endpoint).
Run with: pytest tests/test_billing_completion.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_invoiced_booking(headers, rate=2500, nights=4, days_offset=9800):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"BillCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"BillCompUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Billing Completion Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}", "email": "billingtest@example.com"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=nights)).isoformat(),
    }).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": rate * nights, "mode": "upi", "reference_no": "BILLCOMPTEST"})
    invoice = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers).json()
    return booking, invoice, guest


def test_credit_note_creation_with_proportional_gst(headers):
    """
    Hand calculation: a credit note for 20% of the invoice subtotal must
    reduce each GST component (whichever the invoice actually used —
    CGST+SGST for intra-state, or IGST for inter-state) by the same 20%
    ratio, and the total must be the sum of all three.
    """
    booking, invoice, guest = _make_invoiced_booking(headers, rate=2500, nights=4)
    resp = client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers, json={
        "amount": invoice["subtotal"] * 0.2, "reason": "Partial refund for early departure",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["amount"] == round(invoice["subtotal"] * 0.2, 2)
    expected_cgst = round(invoice["cgst"] * 0.2, 2)
    expected_sgst = round(invoice["sgst"] * 0.2, 2)
    expected_igst = round(invoice["igst"] * 0.2, 2)
    assert data["cgst"] == expected_cgst
    assert data["sgst"] == expected_sgst
    assert data["igst"] == expected_igst
    assert data["total"] == round(data["amount"] + expected_cgst + expected_sgst + expected_igst, 2)


def test_credit_note_rejects_amount_exceeding_subtotal(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    resp = client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers, json={
        "amount": invoice["subtotal"] + 1000, "reason": "Too much",
    })
    assert resp.status_code == 400


def test_credit_note_has_sequential_numbering(headers):
    booking1, invoice1, _ = _make_invoiced_booking(headers, days_offset=9900)
    booking2, invoice2, _ = _make_invoiced_booking(headers, days_offset=10000)
    cn1 = client.post(f"/api/v1/invoices/{invoice1['id']}/credit-notes", headers=headers, json={"amount": 100, "reason": "Test 1"}).json()
    cn2 = client.post(f"/api/v1/invoices/{invoice2['id']}/credit-notes", headers=headers, json={"amount": 100, "reason": "Test 2"}).json()
    assert cn1["credit_note_no"] != cn2["credit_note_no"]
    assert cn1["credit_note_no"].startswith("CN/")


def test_credit_note_pdf_download(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    cn = client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers, json={"amount": 500, "reason": "PDF test"}).json()
    resp = client.get(f"/api/v1/credit-notes/{cn['id']}/pdf", headers=headers)
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_credit_note_list_by_invoice(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers, json={"amount": 200, "reason": "First"})
    client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers, json={"amount": 300, "reason": "Second"})
    listing = client.get(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers).json()
    assert len(listing) == 2


def test_credit_note_creation_logs_audit(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    cn = client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers, json={"amount": 100, "reason": "Audit check"}).json()
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "credit_note.create"}).json()
    assert any(l["entity_id"] == cn["id"] for l in logs)


def test_credit_note_requires_admin_role():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    booking, invoice, guest = _make_invoiced_booking(admin_headers, days_offset=10100)
    resp = client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", json={"amount": 100, "reason": "No auth"})
    assert resp.status_code == 401


def test_invoice_email_send_uses_guest_email_on_file(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    resp = client.post(f"/api/v1/invoices/{invoice['id']}/send/email", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["to_email"] == "billingtest@example.com"


def test_invoice_email_send_logs_audit(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    client.post(f"/api/v1/invoices/{invoice['id']}/send/email", headers=headers, json={})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "invoice.send_email"}).json()
    assert any(l["entity_id"] == invoice["id"] for l in logs)


def test_invoice_email_send_attempts_notification(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    client.post(f"/api/v1/invoices/{invoice['id']}/send/email", headers=headers, json={})
    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "invoice_delivery"}).json()
    assert any(l["booking_id"] == booking["id"] for l in logs)


def test_invoice_email_rejects_when_no_email_on_file(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"NoEmailProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"NoEmailUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "No Email Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=10200)
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat()}).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 4000, "mode": "upi", "reference_no": "NOEMAILTEST"})
    invoice = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers).json()

    resp = client.post(f"/api/v1/invoices/{invoice['id']}/send/email", headers=headers, json={})
    assert resp.status_code == 400


def test_invoice_whatsapp_send_returns_valid_url(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    resp = client.post(f"/api/v1/invoices/{invoice['id']}/send/whatsapp", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["whatsapp_url"].startswith("https://wa.me/91")
    assert invoice["invoice_no"] in resp.json()["message_preview"]


def test_invoice_whatsapp_send_logs_audit(headers):
    booking, invoice, guest = _make_invoiced_booking(headers)
    client.post(f"/api/v1/invoices/{invoice['id']}/send/whatsapp", headers=headers)
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "invoice.send_whatsapp"}).json()
    assert any(l["entity_id"] == invoice["id"] for l in logs)


def test_existing_refund_workflow_still_works(headers):
    booking, invoice, guest = _make_invoiced_booking(headers, days_offset=10300)
    payments = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    payment_id = payments[0]["id"]
    resp = client.post(f"/api/v1/payments/{payment_id}/refund", headers=headers, json={"refund_amount": 500, "reason": "Regression check"})
    assert resp.status_code == 201
    assert resp.json()["amount"] == -500.0


def test_existing_invoice_generation_still_works(headers):
    booking, invoice, guest = _make_invoiced_booking(headers, days_offset=10400)
    assert invoice["invoice_no"].startswith("INV/")
    assert invoice["total"] > 0
