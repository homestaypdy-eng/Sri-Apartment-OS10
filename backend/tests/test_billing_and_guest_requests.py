"""
Tests for the next module-completion cycle: Billing refunds, invoice
PDF bank/QR details, and Guest Requests priority/assignment/feedback.
Run with: pytest tests/test_billing_and_guest_requests.py -v
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


def _make_booking(headers, rate=2000, days_offset=3000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"BillProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"BillUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Billing Test Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"],
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat(),
    }).json()
    return booking


# --- Refunds ---

def test_refund_creates_negative_payment(headers):
    booking = _make_booking(headers)
    payment = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 2000, "mode": "upi", "reference_no": "REFTEST"}).json()

    resp = client.post(f"/api/v1/payments/{payment['id']}/refund", headers=headers, json={"refund_amount": 500, "reason": "Guest cancelled part of the stay"})
    assert resp.status_code == 201, resp.text
    refund = resp.json()
    assert refund["amount"] == -500.0
    assert refund["status"] == "refunded"
    assert refund["refund_of_payment_id"] == payment["id"]


def test_refund_nets_out_total_paid(headers):
    booking = _make_booking(headers)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 3000, "mode": "cash", "reference_no": "NETTEST"})
    payments_before = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    total_before = sum(p["amount"] for p in payments_before)
    assert total_before == 3000.0

    payment_id = payments_before[0]["id"]
    client.post(f"/api/v1/payments/{payment_id}/refund", headers=headers, json={"refund_amount": 1000, "reason": "Partial refund"})

    payments_after = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    total_after = sum(p["amount"] for p in payments_after)
    assert total_after == 2000.0


def test_refund_cannot_exceed_payment_amount(headers):
    booking = _make_booking(headers)
    payment = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 500, "mode": "upi", "reference_no": "OVERREFUND"}).json()
    resp = client.post(f"/api/v1/payments/{payment['id']}/refund", headers=headers, json={"refund_amount": 600, "reason": "Too much"})
    assert resp.status_code == 400


def test_refund_cannot_double_refund_beyond_available(headers):
    booking = _make_booking(headers)
    payment = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "upi", "reference_no": "DOUBLEREFUND"}).json()
    first = client.post(f"/api/v1/payments/{payment['id']}/refund", headers=headers, json={"refund_amount": 700, "reason": "First refund"})
    assert first.status_code == 201
    second = client.post(f"/api/v1/payments/{payment['id']}/refund", headers=headers, json={"refund_amount": 400, "reason": "Second refund, exceeds remaining 300"})
    assert second.status_code == 400


def test_cannot_refund_a_refund(headers):
    booking = _make_booking(headers)
    payment = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "upi", "reference_no": "REFOFREF"}).json()
    refund = client.post(f"/api/v1/payments/{payment['id']}/refund", headers=headers, json={"refund_amount": 500, "reason": "First"}).json()
    resp = client.post(f"/api/v1/payments/{refund['id']}/refund", headers=headers, json={"refund_amount": 100, "reason": "Refunding a refund"})
    assert resp.status_code == 400


def test_refund_logs_audit(headers):
    booking = _make_booking(headers)
    payment = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "upi", "reference_no": "AUDITREFUND"}).json()
    refund = client.post(f"/api/v1/payments/{payment['id']}/refund", headers=headers, json={"refund_amount": 200, "reason": "Audit check refund"}).json()
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "payment.refund"}).json()
    assert any(l["entity_id"] == refund["id"] for l in logs)


def test_invoice_pdf_includes_bank_details_and_qr(headers):
    booking = _make_booking(headers)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 2000, "mode": "upi", "reference_no": "INVQRTEST"})
    gen = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)
    assert gen.status_code in (200, 201), gen.text
    invoice = gen.json()

    pdf_resp = client.get(f"/api/v1/invoices/{invoice['id']}/pdf", headers=headers)
    assert pdf_resp.status_code == 200
    assert pdf_resp.content[:4] == b"%PDF"
    # Confirm the payment-details text is actually present in the generated PDF
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf_resp.content)
        f.flush()
        text = subprocess.run(["pdftotext", f.name, "-"], capture_output=True, text=True).stdout
    assert "BANK DETAILS" in text
    assert "IFSC" in text


# --- Guest Requests: priority, assignment, feedback ---

def _make_guest_request(headers):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "GR Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    from app.db.session import SessionLocal
    from app.modules.portal.models import GuestRequest
    db = SessionLocal()
    try:
        req = GuestRequest(guest_id=uuid.UUID(guest["id"]), request_type="service", description="Extra towels please")
        db.add(req)
        db.commit()
        db.refresh(req)
        return str(req.id)
    finally:
        db.close()


def test_guest_request_default_priority_is_medium(headers):
    request_id = _make_guest_request(headers)
    listing = client.get("/api/v1/guest-requests", headers=headers).json()
    match = next(r for r in listing if r["id"] == request_id)
    assert match["priority"] == "medium"


def test_guest_request_update_priority_and_assignment(headers):
    request_id = _make_guest_request(headers)
    admin_id = client.get("/api/v1/users", headers=headers).json()[0]["id"]

    resp = client.patch(f"/api/v1/guest-requests/{request_id}", headers=headers, json={"priority": "urgent", "assigned_to": admin_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["priority"] == "urgent"
    assert resp.json()["assigned_to"] == admin_id


def test_guest_request_update_logs_audit(headers):
    request_id = _make_guest_request(headers)
    client.patch(f"/api/v1/guest-requests/{request_id}", headers=headers, json={"status": "in_progress"})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "guest_request.update"}).json()
    assert any(l["entity_id"] == request_id for l in logs)


def test_guest_feedback_submission(headers):
    request_id = _make_guest_request(headers)
    resp = client.post(f"/api/v1/portal/guest-requests/{request_id}/feedback", headers=headers, json={"rating": 5, "text": "Excellent service"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["guest_feedback_rating"] == 5
    assert resp.json()["guest_feedback_text"] == "Excellent service"


def test_guest_feedback_rejects_invalid_rating(headers):
    request_id = _make_guest_request(headers)
    resp = client.post(f"/api/v1/portal/guest-requests/{request_id}/feedback", headers=headers, json={"rating": 7})
    assert resp.status_code == 422
