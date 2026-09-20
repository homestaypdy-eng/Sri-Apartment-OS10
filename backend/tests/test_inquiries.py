"""
Tests for the pre-booking inquiry module: creation, pricing math, sequential
offer numbering, validation, and PDF export.
Run with: pytest tests/test_inquiries.py -v
"""
import uuid

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


def _base_payload(**overrides):
    payload = {
        "client_name": "Test Client",
        "client_phone": "9000000000",
        "client_email": "test@example.com",
        "flat_room_label": "2BHK - Test Room",
        "num_guests": 2,
        "check_in_date": "2028-01-10",
        "check_out_date": "2028-01-13",  # 3 nights
        "rate_per_night": 2000,  # 5% GST slab
        "extra_charges": 0,
        "discount": 0,
        "advance_amount": 2000,
    }
    payload.update(overrides)
    return payload


def test_create_inquiry_pricing_matches_template_math(headers):
    """rate 2000 x 3 nights = 6000 taxable; 5% slab -> CGST 150 + SGST 150 = 6300 total."""
    resp = client.post("/api/v1/inquiries", headers=headers, json=_base_payload())
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["offer_no"].startswith("SRI/")
    assert data["nights"] == 3
    assert data["taxable_value"] == 6000.0
    assert data["gst_rate_percent"] == 5.0
    assert data["cgst"] == 150.0
    assert data["sgst"] == 150.0
    assert data["total_amount"] == 6300.0
    assert data["grand_total"] == 6300.0
    assert data["advance_amount"] == 2000.0
    assert data["balance_amount"] == 4300.0
    assert data["status"] == "draft"


def test_extra_charges_and_discount_applied(headers):
    resp = client.post(
        "/api/v1/inquiries",
        headers=headers,
        json=_base_payload(rate_per_night=1000, extra_charges=500, discount=200, check_out_date="2028-01-12", advance_amount=500),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # 2 nights x 1000 = 2000 + 500 extra - 200 discount = 2300 taxable
    assert data["taxable_value"] == 2300.0


def test_offer_numbers_are_sequential(headers):
    r1 = client.post("/api/v1/inquiries", headers=headers, json=_base_payload())
    r2 = client.post("/api/v1/inquiries", headers=headers, json=_base_payload())
    n1 = int(r1.json()["offer_no"].split("/")[-1])
    n2 = int(r2.json()["offer_no"].split("/")[-1])
    assert n2 == n1 + 1


def test_budget_tariff_zero_gst_slab(headers):
    """rate <= 1000/night -> 0% GST slab, same as the final-invoice logic."""
    resp = client.post(
        "/api/v1/inquiries",
        headers=headers,
        json=_base_payload(rate_per_night=900, check_out_date="2028-01-11", advance_amount=500),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["gst_rate_percent"] == 0.0
    assert data["cgst"] == 0.0
    assert data["sgst"] == 0.0
    assert data["taxable_value"] == 900.0
    assert data["grand_total"] == 900.0


def test_advance_exceeding_total_rejected(headers):
    resp = client.post(
        "/api/v1/inquiries",
        headers=headers,
        json=_base_payload(advance_amount=999999),
    )
    assert resp.status_code == 400


def test_checkout_before_checkin_rejected(headers):
    resp = client.post(
        "/api/v1/inquiries",
        headers=headers,
        json=_base_payload(check_in_date="2028-02-10", check_out_date="2028-02-05"),
    )
    assert resp.status_code == 422  # pydantic validation error


def test_list_and_get_inquiry(headers):
    created = client.post("/api/v1/inquiries", headers=headers, json=_base_payload()).json()

    listing = client.get("/api/v1/inquiries", headers=headers)
    assert listing.status_code == 200
    assert any(i["id"] == created["id"] for i in listing.json())

    single = client.get(f"/api/v1/inquiries/{created['id']}", headers=headers)
    assert single.status_code == 200
    assert single.json()["client_name"] == "Test Client"


def test_get_nonexistent_inquiry_404(headers):
    resp = client.get(f"/api/v1/inquiries/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


def test_update_inquiry_status(headers):
    created = client.post("/api/v1/inquiries", headers=headers, json=_base_payload()).json()
    resp = client.patch(f"/api/v1/inquiries/{created['id']}", headers=headers, json={"status": "sent"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


def test_update_invalid_status_rejected(headers):
    created = client.post("/api/v1/inquiries", headers=headers, json=_base_payload()).json()
    resp = client.patch(f"/api/v1/inquiries/{created['id']}", headers=headers, json={"status": "teleported"})
    assert resp.status_code == 400


def test_update_advance_recalculates_balance(headers):
    created = client.post("/api/v1/inquiries", headers=headers, json=_base_payload()).json()
    grand_total = created["grand_total"]
    resp = client.patch(f"/api/v1/inquiries/{created['id']}", headers=headers, json={"advance_amount": 1000})
    assert resp.status_code == 200
    data = resp.json()
    assert data["advance_amount"] == 1000.0
    assert data["balance_amount"] == round(grand_total - 1000, 2)


def test_update_advance_exceeding_total_rejected(headers):
    created = client.post("/api/v1/inquiries", headers=headers, json=_base_payload()).json()
    resp = client.patch(f"/api/v1/inquiries/{created['id']}", headers=headers, json={"advance_amount": 999999})
    assert resp.status_code == 400


def test_quotation_pdf_generated_and_downloadable(headers):
    created = client.post("/api/v1/inquiries", headers=headers, json=_base_payload()).json()
    resp = client.get(f"/api/v1/inquiries/{created['id']}/pdf", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
    assert len(resp.content) > 10_000  # sanity check it's not a near-empty/broken PDF (logo+QR embedded)


def test_pdf_for_nonexistent_inquiry_404(headers):
    resp = client.get(f"/api/v1/inquiries/{uuid.uuid4()}/pdf", headers=headers)
    assert resp.status_code == 404


def test_staff_can_create_and_export_inquiry():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"inquiry_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post(
        "/api/v1/auth/register",
        headers=admin_headers,
        json={"name": "Inquiry Staff", "email": email, "password": "Pass12345!", "role": "staff"},
    )
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    create = client.post("/api/v1/inquiries", headers=staff_headers, json=_base_payload())
    assert create.status_code == 201

    pdf = client.get(f"/api/v1/inquiries/{create.json()['id']}/pdf", headers=staff_headers)
    assert pdf.status_code == 200


def test_unauthenticated_cannot_access_inquiries():
    assert client.get("/api/v1/inquiries").status_code == 401
    assert client.post("/api/v1/inquiries", json=_base_payload()).status_code == 401
