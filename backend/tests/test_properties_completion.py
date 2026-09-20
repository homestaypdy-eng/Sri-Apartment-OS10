"""
Tests completing the Properties module to full production coverage:
PATCH /properties/{id} (was completely missing), property documents/
photos, property-wide Finance P&L rollup, and property-created
notifications.
Run with: pytest tests/test_properties_completion.py -v
"""
import io
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


def _make_property(headers, **overrides):
    payload = {"name": f"PropCompTest {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}
    payload.update(overrides)
    resp = client.post("/api/v1/properties", headers=headers, json=payload)
    return resp.json()


def test_property_update_previously_impossible(headers):
    prop = _make_property(headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}", headers=headers, json={
        "address": "Updated Address 123", "owner_name": "New Owner", "gst_number": "34ABCDE1234F1Z5",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["address"] == "Updated Address 123"
    assert resp.json()["owner_name"] == "New Owner"
    assert resp.json()["gst_number"] == "34ABCDE1234F1Z5"


def test_property_update_requires_at_least_one_field(headers):
    prop = _make_property(headers)
    resp = client.patch(f"/api/v1/properties/{prop['id']}", headers=headers, json={})
    assert resp.status_code == 400


def test_property_update_404_for_unknown_property(headers):
    resp = client.patch(f"/api/v1/properties/{uuid.uuid4()}", headers=headers, json={"name": "X"})
    assert resp.status_code == 404


def test_property_update_logs_audit(headers):
    prop = _make_property(headers)
    client.patch(f"/api/v1/properties/{prop['id']}", headers=headers, json={"owner_phone": "9999999999"})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "property.update"}).json()
    assert any(l["entity_id"] == prop["id"] for l in logs)


def test_property_document_upload_list_download_delete(headers):
    prop = _make_property(headers)
    fake_file = io.BytesIO(b"fake ownership document content")
    upload = client.post(
        f"/api/v1/properties/{prop['id']}/files", headers=headers,
        params={"file_type": "document", "caption": "Ownership papers"},
        files={"file": ("ownership.pdf", fake_file, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    file_record = upload.json()
    assert file_record["file_type"] == "document"

    listing = client.get(f"/api/v1/properties/{prop['id']}/files", headers=headers).json()
    assert any(f["id"] == file_record["id"] for f in listing)

    download = client.get(f"/api/v1/properties/{prop['id']}/files/{file_record['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"fake ownership document content"

    delete_resp = client.delete(f"/api/v1/properties/{prop['id']}/files/{file_record['id']}", headers=headers)
    assert delete_resp.status_code == 204


def test_property_photo_rejects_non_image_content_type(headers):
    prop = _make_property(headers)
    fake_file = io.BytesIO(b"<script>alert(1)</script>")
    resp = client.post(
        f"/api/v1/properties/{prop['id']}/files", headers=headers,
        params={"file_type": "photo"},
        files={"file": ("evil.html", fake_file, "text/html")},
    )
    assert resp.status_code == 415


def test_property_document_allows_non_image_content_type(headers):
    prop = _make_property(headers)
    fake_file = io.BytesIO(b"%PDF-fake")
    resp = client.post(
        f"/api/v1/properties/{prop['id']}/files", headers=headers,
        params={"file_type": "document"},
        files={"file": ("papers.pdf", fake_file, "application/pdf")},
    )
    assert resp.status_code == 201


def test_file_upload_logs_audit(headers):
    prop = _make_property(headers)
    fake_file = io.BytesIO(b"test content")
    client.post(
        f"/api/v1/properties/{prop['id']}/files", headers=headers,
        params={"file_type": "document"},
        files={"file": ("test.pdf", fake_file, "application/pdf")},
    )
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "property.file_upload"}).json()
    assert any(l["entity_id"] == prop["id"] for l in logs)


def test_property_pnl_sums_all_units_correctly(headers):
    """
    Hand calculation: two units under one property.
    Unit A: 3 nights x Rs.2000 = Rs.6000 revenue, Rs.300 expense.
    Unit B: 2 nights x Rs.3000 = Rs.6000 revenue, Rs.200 expense.
    Property total: Rs.12,000 revenue, Rs.500 expenses, Rs.11,500 profit.
    """
    prop = _make_property(headers)
    unit_a = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"PPnLUnitA {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    unit_b = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"PPnLUnitB {uuid.uuid4().hex[:6]}", "base_rate": 3000}).json()

    guest_a = client.post("/api/v1/guests", headers=headers, json={"name": "Prop PnL Guest A", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    guest_b = client.post("/api/v1/guests", headers=headers, json={"name": "Prop PnL Guest B", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()

    year, month = 2052, 4
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_a["id"], "guest_id": guest_a["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-04"})
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_b["id"], "guest_id": guest_b["id"], "check_in": f"{year}-{month:02d}-05", "check_out": f"{year}-{month:02d}-07"})

    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "maintenance", "amount": 300, "expense_date": f"{year}-{month:02d}-10", "unit_id": unit_a["id"]})
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "electricity", "amount": 200, "expense_date": f"{year}-{month:02d}-10", "unit_id": unit_b["id"]})

    pnl = client.get(f"/api/v1/finance/property-pnl/{prop['id']}", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert pnl["total_revenue"] == 12000.0
    assert pnl["total_expenses"] == 500.0
    assert pnl["net_profit"] == 11500.0
    assert len(pnl["units"]) == 2


def test_property_pnl_404_for_unknown_property(headers):
    resp = client.get(f"/api/v1/finance/property-pnl/{uuid.uuid4()}", headers=headers, params={"period_year": 2030, "period_month": 1})
    assert resp.status_code == 404


def test_property_creation_attempts_notification(headers):
    _make_property(headers)
    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "property_created"}).json()
    assert len(logs) >= 1


def test_existing_property_creation_still_works(headers):
    resp = client.post("/api/v1/properties", headers=headers, json={
        "name": "Regression Check Hotel", "address": "Test", "state_code": "34",
        "property_code": "RC-01", "owner_name": "Regression Owner",
    })
    assert resp.status_code == 201
    assert resp.json()["property_code"] == "RC-01"


def test_existing_unit_creation_under_property_still_works(headers):
    prop = _make_property(headers)
    resp = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": "Regression Unit", "base_rate": 1500})
    assert resp.status_code == 201
    assert resp.json()["property_id"] == prop["id"]
