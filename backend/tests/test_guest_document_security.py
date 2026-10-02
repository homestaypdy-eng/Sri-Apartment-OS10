"""
OS10 Phase 3 Step 3A: focused tests for the two implemented fixes from the
read-only storage/security audit —

1. Invoice PDF: staff download now regenerates from current DB data on
   every request (payments/service.py::generate_invoice_pdf_for_booking),
   the same approach already proven by the customer-portal download, so
   it no longer depends on a previously-generated file surviving on disk.
2. Guest ID documents: upload now validates the declared content type
   AND verifies the actual file bytes match it (real magic-byte check,
   not just the trivially-spoofable Content-Type header), the stored
   file's extension is derived from the validated type rather than the
   client-supplied filename, and every download now writes an audit log
   entry naming the actual downloading user.

Storage-backend selection (Railway Volume vs. S3-compatible object
storage) is a separate, unresolved business decision from the Step 3
read-only report and is NOT implemented here.

SAFETY: no external call anywhere in this file. No notification trigger
exists on any guest-document or invoice-download code path (confirmed by
reading them), so no send-mocking is needed here.

Run with: pytest tests/test_guest_document_security.py -v
"""
import io
import os
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)

_REAL_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 40
_REAL_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_guest(headers):
    return client.post("/api/v1/guests", headers=headers,
                        json={"name": "Doc Security Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()


# ---------------------------------------------------------------------------
# Guest document upload validation
# ---------------------------------------------------------------------------

def test_html_upload_rejected_by_declared_content_type(headers):
    guest = _make_guest(headers)
    resp = client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "aadhaar", "side": "front"},
        files={"file": ("id.html", io.BytesIO(b"<html><script>alert(1)</script></html>"), "text/html")},
    )
    assert resp.status_code == 415, resp.text


def test_svg_upload_rejected(headers):
    guest = _make_guest(headers)
    resp = client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "aadhaar", "side": "front"},
        files={"file": ("id.svg", io.BytesIO(b"<svg onload='alert(1)'></svg>"), "image/svg+xml")},
    )
    assert resp.status_code == 415, resp.text


def test_html_disguised_as_image_content_type_rejected_by_signature_check(headers):
    """The exact spoofing scenario the audit flagged: declaring
    image/jpeg while the bytes are actually something else entirely."""
    guest = _make_guest(headers)
    resp = client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "aadhaar", "side": "front"},
        files={"file": ("id.jpg", io.BytesIO(b"<html><script>alert(1)</script></html>"), "image/jpeg")},
    )
    assert resp.status_code == 415, resp.text
    assert "does not match" in resp.json()["detail"]


def test_real_jpeg_accepted_and_extension_derived_from_content_type(headers):
    guest = _make_guest(headers)
    resp = client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "passport", "side": "document"},
        # Deliberately mismatched original filename extension (.html) —
        # the STORED file's extension must come from the validated
        # content type, never from the client-supplied filename.
        files={"file": ("scan.html", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["content_type"] == "image/jpeg"

    db = SessionLocal()
    try:
        from app.modules.bookings.models import GuestDocument
        stored = db.get(GuestDocument, uuid.UUID(doc["id"]))
        assert stored.file_path.endswith(".jpg")
        assert not stored.file_path.endswith(".html")
    finally:
        db.close()


def test_real_pdf_accepted(headers):
    guest = _make_guest(headers)
    resp = client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "passport", "side": "document"},
        files={"file": ("passport.pdf", io.BytesIO(_REAL_PDF), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["content_type"] == "application/pdf"


# ---------------------------------------------------------------------------
# Download audit trail
# ---------------------------------------------------------------------------

def test_download_writes_audit_log_with_actual_user(headers):
    guest = _make_guest(headers)
    upload = client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "aadhaar", "side": "front"},
        files={"file": ("id.jpg", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    )
    doc_id = upload.json()["id"]

    download = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc_id}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == _REAL_JPEG

    db = SessionLocal()
    try:
        from app.modules.audit.models import AuditLog
        from sqlalchemy import select
        logs = db.scalars(
            select(AuditLog).where(AuditLog.action == "guest.document_download", AuditLog.entity_id == str(guest["id"]))
        ).all()
        assert len(logs) == 1
        assert logs[0].user_id is not None  # the actual downloading admin, not anonymous
    finally:
        db.close()


def test_unauthenticated_cannot_download(headers):
    guest = _make_guest(headers)
    upload = client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "aadhaar", "side": "front"},
        files={"file": ("id.jpg", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    )
    doc_id = upload.json()["id"]
    resp = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc_id}/download")
    assert resp.status_code == 401


def test_unauthenticated_cannot_upload(headers):
    """Distinct from the unauthenticated-download and unauthenticated-list
    checks already covered elsewhere — this is the upload endpoint itself,
    with no Authorization header at all."""
    guest = _make_guest(headers)
    resp = client.post(
        f"/api/v1/guests/{guest['id']}/documents",
        params={"document_type": "aadhaar", "side": "front"},
        files={"file": ("id.jpg", io.BytesIO(_REAL_JPEG), "image/jpeg")},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Invoice PDF regeneration
# ---------------------------------------------------------------------------

def _make_invoice(headers, day_offset):
    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"InvStoreProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"InvStoreUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers,
                         json={"name": "Invoice Storage Guest", "phone": f"95{uuid.uuid4().int % 10**8:08d}", "state_code": "34"}).json()
    check_in = (date.today() + timedelta(days=day_offset)).isoformat()
    check_out = (date.today() + timedelta(days=day_offset + 3)).isoformat()
    booking = client.post("/api/v1/bookings", headers=headers,
                           json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in, "check_out": check_out}).json()
    invoice = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)
    assert invoice.status_code == 201, invoice.text
    return invoice.json()


def test_invoice_download_succeeds_after_stored_pdf_file_is_deleted(headers):
    """Reproduces exactly the production failure mode: the file the
    Invoice row's pdf_path points to is gone (e.g. an ephemeral-storage
    redeploy wiped it), but the invoice itself still exists in the
    database. Regeneration must serve a correct PDF anyway."""
    invoice = _make_invoice(headers, 900)

    db = SessionLocal()
    try:
        from app.modules.payments.models import Invoice
        db_invoice = db.get(Invoice, uuid.UUID(invoice["id"]))
        assert os.path.exists(db_invoice.pdf_path)
        os.remove(db_invoice.pdf_path)
        assert not os.path.exists(db_invoice.pdf_path)
        original_invoice_no = db_invoice.invoice_no
    finally:
        db.close()

    resp = client.get(f"/api/v1/invoices/{invoice['id']}/pdf", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"

    # invoice_no must be the SAME existing number — regeneration must
    # never allocate a new sequential invoice number.
    db = SessionLocal()
    try:
        from app.modules.payments.models import Invoice
        db_invoice = db.get(Invoice, uuid.UUID(invoice["id"]))
        assert db_invoice.invoice_no == original_invoice_no
    finally:
        db.close()


def test_invoice_download_still_works_normally_when_file_present(headers):
    """Regression: the ordinary case (file still present) must keep working."""
    invoice = _make_invoice(headers, 910)
    resp = client.get(f"/api/v1/invoices/{invoice['id']}/pdf", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == b"%PDF"


def test_invoice_download_unknown_invoice_404s(headers):
    resp = client.get(f"/api/v1/invoices/{uuid.uuid4()}/pdf", headers=headers)
    assert resp.status_code == 404
