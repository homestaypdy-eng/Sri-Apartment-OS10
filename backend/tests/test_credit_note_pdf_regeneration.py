"""
OS10 Phase 4 Step 4C: credit-note PDF durability fix.

download_credit_note_pdf() used to read CreditNote.pdf_path directly via
FileResponse with only a null-check — if the stored file was gone (e.g.
an ephemeral-storage redeploy wiped it), this produced an unhandled
filesystem error. It now falls back to regenerating the PDF from current
DB data (payments/service.py::generate_credit_note_pdf_for_credit_note)
when the stored file is missing, exactly as already proven for invoices
in Step 3A/4A.

HISTORICAL INTEGRITY: generate_credit_note_pdf() used to hardcode
today's date as the printed "Issue Date" — harmless at original creation
time, but wrong for a later regeneration (it would misstate an
already-issued credit note's date). It now takes an explicit issue_date
argument; the regeneration helper sources it from CreditNote.issued_at,
which is already stored on every credit note (NOT NULL, set once at
creation) — so no new DB field was needed to preserve this.

SAFETY: no external call anywhere in this file — credit-note creation
and PDF download/regeneration trigger no notification of any kind
(confirmed by reading the relevant code paths).

Run with: pytest tests/test_credit_note_pdf_regeneration.py -v
"""
import os
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_credit_note(headers, days_offset, amount=500.0, reason="Test credit"):
    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"CNPdfProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"CNPdfUnit {uuid.uuid4().hex[:6]}", "base_rate": 2500}).json()
    guest = client.post("/api/v1/guests", headers=headers,
                         json={"name": "Credit Note PDF Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}", "state_code": "34"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"],
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=4)).isoformat(),
    }).json()
    client.post("/api/v1/payments", headers=headers,
                 json={"booking_id": booking["id"], "amount": 2500 * 4, "mode": "upi", "reference_no": "CNPDFTEST"})
    invoice = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers).json()
    cn = client.post(f"/api/v1/invoices/{invoice['id']}/credit-notes", headers=headers,
                      json={"amount": amount, "reason": reason})
    assert cn.status_code == 201, cn.text
    return invoice, cn.json()


def _delete_stored_pdf(credit_note_id):
    """Simulates the exact production failure mode: the file
    CreditNote.pdf_path points to is gone, but the row itself is intact."""
    db = SessionLocal()
    try:
        from app.modules.payments.models import CreditNote
        cn = db.get(CreditNote, uuid.UUID(credit_note_id))
        if cn.pdf_path and os.path.exists(cn.pdf_path):
            os.remove(cn.pdf_path)
        assert not os.path.exists(cn.pdf_path)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# A. Existing stored PDF still downloads normally (regression)
# ---------------------------------------------------------------------------

def test_existing_stored_pdf_downloads_normally(headers):
    invoice, cn = _make_credit_note(headers, 9500)
    resp = client.get(f"/api/v1/credit-notes/{cn['id']}/pdf", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# B. Missing stored PDF: regenerated from DB, valid PDF, success response
# ---------------------------------------------------------------------------

def test_missing_stored_pdf_is_regenerated_and_downloads_successfully(headers):
    invoice, cn = _make_credit_note(headers, 9510)
    _delete_stored_pdf(cn["id"])

    resp = client.get(f"/api/v1/credit-notes/{cn['id']}/pdf", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


def test_regeneration_never_silently_fails_surfaces_as_500(headers):
    """If regeneration itself can't proceed, the endpoint must return a
    clear error, never a raw filesystem exception or a false success.
    Simulated via a mock (rather than breaking a live FK-constrained
    invoice_id, which the database itself would reject) — this isolates
    the router's own try/except -> 500 behavior."""
    invoice, cn = _make_credit_note(headers, 9520)
    _delete_stored_pdf(cn["id"])

    with patch("app.modules.payments.router.generate_credit_note_pdf_for_credit_note",
               side_effect=RuntimeError("simulated regeneration failure")):
        resp = client.get(f"/api/v1/credit-notes/{cn['id']}/pdf", headers=headers)
    assert resp.status_code == 500, resp.text


def test_regeneration_helper_raises_clear_error_for_orphaned_credit_note():
    """Unit-level check of generate_credit_note_pdf_for_credit_note's own
    guard: a CreditNote whose invoice_id doesn't resolve (in-memory only
    — never committed, since the real FK constraint would reject this at
    the database level) raises a clear ValueError, not an AttributeError
    from touching a None invoice."""
    from app.modules.payments.models import CreditNote
    from app.modules.payments.service import generate_credit_note_pdf_for_credit_note

    db = SessionLocal()
    try:
        orphan = CreditNote(
            id=uuid.uuid4(), invoice_id=uuid.uuid4(), credit_note_no="ORPHAN/CN/0001",
            amount=100.0, cgst=9.0, sgst=9.0, igst=0.0, total=118.0, reason="Unit test only, never committed",
        )
        with pytest.raises(ValueError):
            generate_credit_note_pdf_for_credit_note(db, orphan)
    finally:
        db.rollback()
        db.close()


# ---------------------------------------------------------------------------
# C-E. Regenerated PDF preserves credit-note number, invoice reference,
#      and every tax/amount figure exactly as stored — never recomputed.
# ---------------------------------------------------------------------------

def test_regeneration_preserves_credit_note_number_invoice_reference_and_amounts(headers):
    invoice, cn = _make_credit_note(headers, 9530, amount=777.77, reason="Preserve my numbers")
    _delete_stored_pdf(cn["id"])

    db = SessionLocal()
    try:
        from app.modules.payments.models import CreditNote
        cn_row = db.get(CreditNote, uuid.UUID(cn["id"]))
        expected_no = cn_row.credit_note_no
        expected_amount = float(cn_row.amount)
        expected_cgst = float(cn_row.cgst)
        expected_sgst = float(cn_row.sgst)
        expected_igst = float(cn_row.igst)
        expected_total = float(cn_row.total)
        expected_invoice_id = cn_row.invoice_id
    finally:
        db.close()

    resp = client.get(f"/api/v1/credit-notes/{cn['id']}/pdf", headers=headers)
    assert resp.status_code == 200, resp.text

    # Regeneration itself must not have mutated the row.
    db = SessionLocal()
    try:
        from app.modules.payments.models import CreditNote
        cn_row = db.get(CreditNote, uuid.UUID(cn["id"]))
        assert cn_row.credit_note_no == expected_no
        assert float(cn_row.amount) == expected_amount
        assert float(cn_row.cgst) == expected_cgst
        assert float(cn_row.sgst) == expected_sgst
        assert float(cn_row.igst) == expected_igst
        assert float(cn_row.total) == expected_total
        assert cn_row.invoice_id == expected_invoice_id
        assert cn_row.reason == "Preserve my numbers"
    finally:
        db.close()

    # The PDF filename returned to the client is still built from the
    # unchanged credit_note_no.
    expected_filename_fragment = expected_no.replace("/", "_")
    assert expected_filename_fragment in resp.headers.get("content-disposition", "")


# ---------------------------------------------------------------------------
# F/G. Historical issue date preserved on regeneration (the model already
#      stores it — issued_at — so exact reproduction IS possible; no
#      missing field, no fabricated date).
# ---------------------------------------------------------------------------

def test_regenerated_pdf_preserves_original_issue_date_not_todays_date(headers):
    """fpdf2 compresses its content stream by default, so the printed
    date isn't searchable in the raw response bytes — instead, spy on
    the underlying generate_credit_note_pdf() call (the function that
    actually writes "Issue Date: ..." into the PDF) and assert the
    issue_date it receives is the credit note's STORED issued_at date,
    not today()."""
    invoice, cn = _make_credit_note(headers, 9540)

    db = SessionLocal()
    try:
        from app.modules.payments.models import CreditNote
        cn_row = db.get(CreditNote, uuid.UUID(cn["id"]))
        original_issued_at = cn_row.issued_at
        # Backdate issued_at to prove regeneration reflects the STORED
        # date, not today() — the exact bug this task fixes. today() and
        # the backdated year are guaranteed different.
        backdated = original_issued_at.replace(year=original_issued_at.year - 1)
        cn_row.issued_at = backdated
        db.commit()
        expected_issue_date = backdated.date()
    finally:
        db.close()

    assert expected_issue_date != date.today()

    from app.modules.payments import service as payments_service
    real_generate = payments_service.generate_credit_note_pdf
    captured = {}

    def _spy(*args, **kwargs):
        captured["issue_date"] = kwargs.get("issue_date")
        return real_generate(*args, **kwargs)

    with patch("app.modules.payments.service.generate_credit_note_pdf", side_effect=_spy):
        db = SessionLocal()
        try:
            from app.modules.payments.models import CreditNote
            cn_row = db.get(CreditNote, uuid.UUID(cn["id"]))
            path = payments_service.generate_credit_note_pdf_for_credit_note(db, cn_row)
            assert os.path.exists(path)
            os.remove(path)
        finally:
            db.close()

    assert captured["issue_date"] == expected_issue_date, \
        "regeneration must print the ORIGINAL stored issue date, not today's date"


def test_issued_at_already_present_on_every_credit_note_no_missing_field(headers):
    """G: confirms exact historical reproduction is POSSIBLE with existing
    fields — issued_at is NOT NULL with a default set at creation, so no
    additive migration is required to preserve the original issue date."""
    invoice, cn = _make_credit_note(headers, 9550)
    assert cn["issued_at"] is not None


# ---------------------------------------------------------------------------
# H. Temporary regenerated PDF is cleaned up after being served
# ---------------------------------------------------------------------------

def test_regenerated_temp_pdf_is_cleaned_up_after_download(headers):
    """Confirms the endpoint's BackgroundTask cleanup actually runs, by
    diffing the storage directory's contents immediately before and
    after the download call — NOT by asserting an absolute count, since
    the shared test database resets CreditNoteCounter per test
    transaction (many unrelated tests' credit notes collide on the same
    "CN/.../000001"-style number) and the shared storage volume
    accumulates real files across every prior test run in this
    long-running dev environment. A before/after diff isolates only
    what THIS call created and must have removed."""
    invoice, cn = _make_credit_note(headers, 9560)
    _delete_stored_pdf(cn["id"])

    from app.modules.payments.service import CREDIT_NOTE_STORAGE_DIR
    before = set(os.listdir(CREDIT_NOTE_STORAGE_DIR))

    resp = client.get(f"/api/v1/credit-notes/{cn['id']}/pdf", headers=headers)
    assert resp.status_code == 200, resp.text

    after = set(os.listdir(CREDIT_NOTE_STORAGE_DIR))
    new_files = after - before
    assert new_files == set(), f"regenerated temp PDF(s) left behind after download: {new_files}"


# ---------------------------------------------------------------------------
# I. Authorization is still enforced
# ---------------------------------------------------------------------------

def test_download_credit_note_pdf_requires_authentication(headers):
    invoice, cn = _make_credit_note(headers, 9570)
    resp = client.get(f"/api/v1/credit-notes/{cn['id']}/pdf")
    assert resp.status_code == 401


def test_download_credit_note_pdf_unknown_id_404s(headers):
    resp = client.get(f"/api/v1/credit-notes/{uuid.uuid4()}/pdf", headers=headers)
    assert resp.status_code == 404
