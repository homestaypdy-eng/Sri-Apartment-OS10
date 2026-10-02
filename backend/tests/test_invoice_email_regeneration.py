"""
OS10 Phase 4 Step 4A: invoice-email attachment no longer depends on
Invoice.pdf_path surviving on disk — both the automatic post-payment path
(notifications/service.py::dispatch_booking_notifications) and the manual
staff resend endpoint (payments/router.py::send_invoice_email) now
regenerate the PDF fresh from current DB data
(payments/service.py::generate_invoice_pdf_for_booking, the same function
already proven by the Step 3A staff-download fix), and clean up the
regenerated temp file afterward regardless of send outcome.

SAFETY: every test in this file mocks notifications.service.send_email at
the source — no real call to Resend, Gmail, or any other external service
is possible from any test here. No test calls Razorpay/Zotel/WhatsApp/SMS
either (dispatch_booking_notifications also fires SMS/WhatsApp triggers,
both mocked below).

Run with: pytest tests/test_invoice_email_regeneration.py -v
"""
import os
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_sends(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.send_whatsapp", lambda *a, **kw: None)
    monkeypatch.setattr("app.modules.notifications.service.send_sms", lambda *a, **kw: None)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_invoice(headers, day_offset, with_email=True):
    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"InvEmailProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"InvEmailUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest_payload = {"name": "Invoice Email Guest", "phone": f"96{uuid.uuid4().int % 10**8:08d}", "state_code": "34"}
    if with_email:
        guest_payload["email"] = f"invemail{uuid.uuid4().hex[:8]}@example.com"
    guest = client.post("/api/v1/guests", headers=headers, json=guest_payload).json()
    check_in = f"2032-01-{(day_offset % 25) + 1:02d}"
    check_out_day = (day_offset % 25) + 1 + 3
    check_out = f"2032-01-{check_out_day:02d}" if check_out_day <= 28 else f"2032-02-{check_out_day - 28:02d}"
    booking = client.post("/api/v1/bookings", headers=headers,
                           json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in, "check_out": check_out}).json()
    invoice = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)
    assert invoice.status_code == 201, invoice.text
    return booking, invoice.json()


def _delete_stored_pdf(invoice_id):
    """Simulates the exact production failure mode: the file
    invoice.pdf_path points to is gone (e.g. an ephemeral-storage
    redeploy), but the Invoice row itself is untouched."""
    db = SessionLocal()
    try:
        from app.modules.payments.models import Invoice
        db_invoice = db.get(Invoice, uuid.UUID(invoice_id))
        if db_invoice.pdf_path and os.path.exists(db_invoice.pdf_path):
            os.remove(db_invoice.pdf_path)
        assert not os.path.exists(db_invoice.pdf_path)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# A. Automatic booking confirmation — regenerates, missing pdf_path irrelevant
# ---------------------------------------------------------------------------

def test_automatic_dispatch_attaches_valid_regenerated_pdf_even_with_missing_stored_file(headers):
    booking, invoice = _make_invoice(headers, 1)
    _delete_stored_pdf(invoice["id"])

    captured = {}

    def _capture_send_email(to_email, subject, body, attachment_path=None, **kwargs):
        if attachment_path:
            with open(attachment_path, "rb") as f:
                captured["bytes"] = f.read()
            captured["path"] = attachment_path

    with patch("app.modules.notifications.service.send_email", side_effect=_capture_send_email):
        from app.modules.notifications.service import dispatch_booking_notifications
        dispatch_booking_notifications(uuid.UUID(booking["id"]))

    assert "bytes" in captured, "no attachment was ever passed to send_email"
    assert captured["bytes"][:4] == b"%PDF"

    # invoice number is unchanged by regeneration
    db = SessionLocal()
    try:
        from app.modules.payments.models import Invoice
        db_invoice = db.get(Invoice, uuid.UUID(invoice["id"]))
        assert db_invoice.invoice_no == invoice["invoice_no"]
    finally:
        db.close()

    # temp file cleaned up after the dispatch completes
    assert not os.path.exists(captured["path"]), "regenerated temp PDF was not cleaned up"


def test_automatic_dispatch_does_not_crash_when_booking_has_no_invoice(headers):
    """C: missing invoice — existing behavior stays safe, no crash."""
    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"NoInvProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"NoInvUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers,
                         json={"name": "No Invoice Guest", "phone": f"97{uuid.uuid4().int % 10**8:08d}",
                               "email": f"noinv{uuid.uuid4().hex[:8]}@example.com"}).json()
    booking = client.post("/api/v1/bookings", headers=headers,
                           json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": "2032-02-10", "check_out": "2032-02-12"}).json()
    # deliberately no invoice generated

    with patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        from app.modules.notifications.service import dispatch_booking_notifications
        dispatch_booking_notifications(uuid.UUID(booking["id"]))  # must not raise

    assert mock_send.called  # confirmation email still attempted, just with no attachment
    assert mock_send.call_args[0][3] is None  # attachment_path positional arg is None


def test_automatic_dispatch_survives_pdf_generation_failure(headers):
    """D: generation failure is detected/logged, no false success, and the
    dispatch itself never crashes."""
    booking, invoice = _make_invoice(headers, 2)

    with patch("app.modules.notifications.service.generate_invoice_pdf_for_booking", side_effect=RuntimeError("simulated render failure")), \
         patch("app.modules.notifications.service.logger") as mock_logger, \
         patch("app.modules.notifications.service.send_email", return_value=None) as mock_send:
        from app.modules.notifications.service import dispatch_booking_notifications
        dispatch_booking_notifications(uuid.UUID(booking["id"]))  # must not raise

    assert mock_logger.exception.called, "PDF generation failure must be logged, not swallowed silently"
    assert mock_send.called  # the confirmation email itself still goes out
    assert mock_send.call_args[0][3] is None  # ...but with no attachment, since generation failed


# ---------------------------------------------------------------------------
# B. Manual resend — regenerates, missing pdf_path irrelevant
# ---------------------------------------------------------------------------

def test_manual_resend_regenerates_and_attaches_valid_pdf_with_missing_stored_file(headers):
    booking, invoice = _make_invoice(headers, 3)
    _delete_stored_pdf(invoice["id"])

    captured = {}

    def _capture_send_email(to_email, subject, body, attachment_path=None, **kwargs):
        if attachment_path:
            with open(attachment_path, "rb") as f:
                captured["bytes"] = f.read()
            captured["path"] = attachment_path

    with patch("app.modules.notifications.service.send_email", side_effect=_capture_send_email):
        resp = client.post(f"/api/v1/invoices/{invoice['id']}/send/email", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["sent"] is True

    assert captured["bytes"][:4] == b"%PDF"
    assert not os.path.exists(captured["path"]), "regenerated temp PDF was not cleaned up after manual resend"


def test_manual_resend_unknown_invoice_404s(headers):
    resp = client.post(f"/api/v1/invoices/{uuid.uuid4()}/send/email", headers=headers, json={})
    assert resp.status_code == 404


def test_manual_resend_surfaces_pdf_generation_failure_as_error_not_false_success(headers):
    """D, manual path: unlike the background/automatic path (which must
    never break booking finalization), this is a synchronous, staff-
    initiated action — a generation failure must be a clear error, never
    a false "sent" response."""
    booking, invoice = _make_invoice(headers, 4)

    with patch("app.modules.payments.router.generate_invoice_pdf_for_booking", side_effect=RuntimeError("simulated render failure")), \
         patch("app.modules.notifications.service.send_email") as mock_send:
        resp = client.post(f"/api/v1/invoices/{invoice['id']}/send/email", headers=headers, json={})

    assert resp.status_code == 500, resp.text
    assert not mock_send.called, "send_email must never be reached when PDF generation itself failed"


# ---------------------------------------------------------------------------
# E. Temp file cleanup on both success and failure
# ---------------------------------------------------------------------------

def test_manual_resend_cleans_up_temp_file_even_when_send_fails(headers):
    booking, invoice = _make_invoice(headers, 5)
    captured_path = {}

    def _capture_and_fail(to_email, subject, body, attachment_path=None, **kwargs):
        captured_path["path"] = attachment_path
        raise OSError("simulated SMTP failure")

    with patch("app.modules.notifications.service.send_email", side_effect=_capture_and_fail):
        resp = client.post(f"/api/v1/invoices/{invoice['id']}/send/email", headers=headers, json={})

    # trigger_invoice_delivery's best-effort contract means this still
    # returns 200 with sent=false — the important thing here is cleanup.
    assert resp.status_code == 200, resp.text
    assert resp.json()["sent"] is False
    assert "path" in captured_path
    assert not os.path.exists(captured_path["path"]), "temp PDF must be cleaned up even when the send itself fails"
