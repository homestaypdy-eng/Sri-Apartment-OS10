"""
Bank Account Migration (Axis Bank -> IDFC FIRST Bank) verification tests.

Covers: BankSettings access boundary with the new values in place, that
freshly-generated invoice and quotation/pre-quotation documents source
bank details only from BankSettings (never a hardcoded literal, old or
new), that no obsolete Axis Bank reference remains in active
document-generation source (backend or the OS10 admin frontend), and that
an already-generated invoice PDF is not retroactively altered by a later
BankSettings change.

Run with: pytest tests/test_bank_account_migration.py -v
"""
import os
import subprocess
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

NEW_BANK = {
    "bank_name": "IDFC FIRST Bank",
    "bank_account_name": "SRI APARTMENT",
    "bank_account_number": "10304118357",
    "bank_ifsc": "IDFB0080134",
    "bank_branch": "Pondicherry",
    "upi_id": "sriapartment@idfcbank",
}

OLD_AXIS_STRINGS = ["Axis Bank", "925020053638481", "UTIB0003923", "8870237237@axisbank"]

# Two levels up from tests/ is the backend root (contains app/) in both a
# local checkout and this container's /app layout.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# The OS10 admin frontend isn't part of the backend image/container, so its
# source has no fixed in-container path — FRONTEND_SRC_DIR lets the test
# harness point at wherever frontend/src is mounted (defaults to the
# sibling frontend/src of a normal monorepo checkout, for a local run
# outside Docker).
_FRONTEND_SRC = os.environ.get(
    "FRONTEND_SRC_DIR",
    os.path.abspath(os.path.join(_BACKEND_ROOT, "..", "frontend", "src")),
)


def _read_backend(*parts: str) -> str:
    with open(os.path.join(_BACKEND_ROOT, *parts)) as f:
        return f.read()


def _read_frontend(*parts: str) -> str:
    with open(os.path.join(_FRONTEND_SRC, *parts)) as f:
        return f.read()


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    proc = subprocess.run(["pdftotext", "-", "-"], input=pdf_bytes, capture_output=True, check=True)
    return proc.stdout.decode()


@pytest.fixture(scope="module")
def admin_headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _register_and_login(admin_headers, role: str) -> dict:
    email = f"bankmig_{role}_{uuid.uuid4().hex[:8]}@sriapartmenthomestay.com"
    password = f"BankMig-{uuid.uuid4().hex[:12]}!"
    resp = client.post(
        "/api/v1/auth/register",
        headers=admin_headers,
        json={"name": f"Bank Migration {role} test", "email": email, "password": password, "role": role},
    )
    assert resp.status_code == 201, resp.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_invoiced_booking(admin_headers, rate=2500, nights=2, days_offset=9700):
    prop = client.post("/api/v1/properties", headers=admin_headers, json={"name": f"BankMigProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=admin_headers, json={"property_id": prop["id"], "name": f"BankMigUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    guest = client.post("/api/v1/guests", headers=admin_headers, json={"name": "Bank Migration Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    booking = client.post(
        "/api/v1/bookings",
        headers=admin_headers,
        json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=nights)).isoformat()},
    ).json()
    client.post("/api/v1/payments", headers=admin_headers, json={"booking_id": booking["id"], "amount": rate * nights, "mode": "upi", "reference_no": "BANKMIGTEST"})
    invoice = client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=admin_headers).json()
    return booking, invoice


# ---------------------------------------------------------------------------
# BankSettings access boundary + current values
# ---------------------------------------------------------------------------

def test_admin_can_read_new_bank_settings(admin_headers):
    resp = client.get("/api/v1/settings/bank", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    for field, expected in NEW_BANK.items():
        assert data[field] == expected


def test_staff_cannot_read_bank_settings(admin_headers):
    staff = _register_and_login(admin_headers, "staff")
    assert client.get("/api/v1/settings/bank", headers=staff).status_code == 403


def test_manager_cannot_read_bank_settings(admin_headers):
    manager = _register_and_login(admin_headers, "manager")
    assert client.get("/api/v1/settings/bank", headers=manager).status_code == 403


def test_staff_cannot_update_bank_settings(admin_headers):
    staff = _register_and_login(admin_headers, "staff")
    assert client.put("/api/v1/settings/bank", headers=staff, json={"bank_name": "Should Not Apply"}).status_code == 403


def test_manager_cannot_update_bank_settings(admin_headers):
    manager = _register_and_login(admin_headers, "manager")
    assert client.put("/api/v1/settings/bank", headers=manager, json={"bank_name": "Should Not Apply"}).status_code == 403


# ---------------------------------------------------------------------------
# New documents use current BankSettings (never a hardcoded literal)
# ---------------------------------------------------------------------------

def test_new_invoice_uses_current_bank_settings_not_old_axis(admin_headers):
    _, invoice = _make_invoiced_booking(admin_headers)
    pdf_resp = client.get(f"/api/v1/invoices/{invoice['id']}/pdf", headers=admin_headers)
    assert pdf_resp.status_code == 200
    text = _extract_pdf_text(pdf_resp.content)
    assert NEW_BANK["bank_account_number"] in text
    assert NEW_BANK["bank_ifsc"] in text
    for old in OLD_AXIS_STRINGS:
        assert old not in text


def test_new_quotation_uses_current_bank_settings_not_old_axis(admin_headers):
    """Covers both 'quotation' and 'pre-quotation/inquiry document' — this
    codebase generates one PDF for both concepts (see inquiries/service.py:
    the pre-booking offer IS the quotation)."""
    inquiry = client.post(
        "/api/v1/inquiries",
        headers=admin_headers,
        json={
            "client_name": "Bank Migration Quotation Test",
            "client_phone": f"92{uuid.uuid4().int % 10**8:08d}",
            "flat_room_label": "Test Room",
            "check_in_date": "2029-03-10",
            "check_out_date": "2029-03-12",
            "rate_per_night": 2000,
            "advance_amount": 500,
        },
    ).json()
    pdf_resp = client.get(f"/api/v1/inquiries/{inquiry['id']}/pdf", headers=admin_headers)
    assert pdf_resp.status_code == 200
    text = _extract_pdf_text(pdf_resp.content)
    assert NEW_BANK["bank_account_number"] in text
    assert NEW_BANK["bank_ifsc"] in text
    for old in OLD_AXIS_STRINGS:
        assert old not in text


# ---------------------------------------------------------------------------
# Source-level regression guards
# ---------------------------------------------------------------------------

_frontend_missing = not os.path.isdir(_FRONTEND_SRC)
_skip_no_frontend = pytest.mark.skipif(_frontend_missing, reason=f"frontend/src not mounted/found at {_FRONTEND_SRC}")


@_skip_no_frontend
def test_quotation_popup_has_no_hardcoded_bank_values_old_or_new():
    """QuotationPopup.tsx must fetch bank details live from
    /settings/bank, not hardcode either the old Axis values or the new
    IDFC ones — a second hardcoded copy would just recreate the exact
    single-source-of-truth problem this migration fixes."""
    source = _read_frontend("components", "QuotationPopup.tsx")
    for old in OLD_AXIS_STRINGS:
        assert old not in source
    # bank_account_name ("SRI APARTMENT") is deliberately excluded here —
    # it's a substring of "SRI APARTMENT HOMESTAY", the company name this
    # component legitimately displays elsewhere as branding, not as a
    # hardcoded bank field. The other four are unambiguous bank-specific
    # values with no other legitimate reason to appear in this file.
    unambiguous_new_bank_values = [
        NEW_BANK["bank_name"], NEW_BANK["bank_account_number"], NEW_BANK["bank_ifsc"], NEW_BANK["upi_id"],
    ]
    for new in unambiguous_new_bank_values:
        assert new not in source
    assert '"/settings/bank"' in source


@_skip_no_frontend
def test_settings_page_uses_bank_settings_endpoint_not_orphaned_fields():
    source = _read_frontend("pages", "SettingsPage.tsx")
    assert "form.bank_" not in source, "SettingsPage must not bind to the removed BusinessSettings bank fields"
    assert '"/settings/bank"' in source


@_skip_no_frontend
def test_business_settings_type_no_longer_declares_bank_fields():
    source = _read_frontend("lib", "api.ts")
    business_settings_block = source.split("export interface BusinessSettingsOut", 1)[1].split("}", 1)[0]
    for field in ("bank_name", "bank_account_number", "bank_ifsc", "upi_id"):
        assert field not in business_settings_block


def test_backend_document_generation_code_has_no_obsolete_axis_references():
    for path in (
        ("app", "modules", "inquiries", "service.py"),
        ("app", "modules", "payments", "service.py"),
        ("app", "modules", "payments", "router.py"),
        ("app", "modules", "settings", "models.py"),
    ):
        source = _read_backend(*path)
        for old in OLD_AXIS_STRINGS:
            assert old not in source, f"found obsolete Axis reference in backend/{'/'.join(path)}"


# ---------------------------------------------------------------------------
# Historical documents are not retroactively altered
# ---------------------------------------------------------------------------

def test_historical_invoice_pdf_not_modified_by_later_bank_settings_change(admin_headers):
    """Invoice PDFs are generated once (at /invoices/generate time) and
    served as a static file from then on (see payments/router.py's
    download_invoice_pdf — it never regenerates) — this proves a later
    BankSettings update cannot retroactively rewrite an already-issued
    invoice."""
    _, invoice = _make_invoiced_booking(admin_headers)
    first = client.get(f"/api/v1/invoices/{invoice['id']}/pdf", headers=admin_headers)
    assert first.status_code == 200
    original_bytes = first.content

    put_resp = client.put("/api/v1/settings/bank", headers=admin_headers, json=NEW_BANK)
    assert put_resp.status_code == 200

    second = client.get(f"/api/v1/invoices/{invoice['id']}/pdf", headers=admin_headers)
    assert second.status_code == 200
    assert second.content == original_bytes, "an already-generated invoice PDF must never change after a BankSettings update"
