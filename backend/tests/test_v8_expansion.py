"""
Tests for the V8 audit-and-expand pass: Guest profile expansion + document
uploads + merge duplicates, Booking guest-count/timing fields, Property/
Unit identification fields, and the new Laundry Register.
Run with: pytest tests/test_v8_expansion.py -v
"""
import io
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


def _make_unit(headers, rate=2000, max_occupancy=2):
    prop = client.post("/api/v1/properties", headers=headers, json={
        "name": f"V8Prop {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34",
        "property_code": "V8-01", "property_type": "homestay", "owner_name": "Test Owner",
    }).json()
    unit = client.post("/api/v1/units", headers=headers, json={
        "property_id": prop["id"], "name": f"V8Unit {uuid.uuid4().hex[:6]}", "base_rate": rate,
        "unit_code": "U-01", "floor": "1", "flat_number": "1A", "amenities": "AC, WiFi",
        "max_occupancy": max_occupancy,
    }).json()
    return unit["id"]


def test_property_and_unit_accept_new_identification_fields(headers):
    unit_id = _make_unit(headers)
    units = client.get("/api/v1/units", headers=headers).json()
    unit = next(u for u in units if u["id"] == unit_id)
    assert unit["unit_code"] == "U-01"
    assert unit["floor"] == "1"
    assert unit["flat_number"] == "1A"
    assert unit["amenities"] == "AC, WiFi"


def test_guest_create_with_full_profile(headers):
    resp = client.post("/api/v1/guests", headers=headers, json={
        "name": "V8 Full Profile Guest", "phone": f"90{uuid.uuid4().int % 10**8:08d}",
        "date_of_birth": "1990-05-15", "gender": "female", "nationality": "Indian", "occupation": "Engineer",
        "whatsapp_number": "9876543210", "emergency_contact_name": "John Doe", "emergency_contact_phone": "9123456789",
        "notes": "Prefers ground floor",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["gender"] == "female"
    assert data["emergency_contact_name"] == "John Doe"


def test_guest_update_previously_impossible(headers):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "V8 Update Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    resp = client.patch(f"/api/v1/guests/{guest['id']}", headers=headers, json={"is_vip": True, "notes": "Updated note"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_vip"] is True
    assert resp.json()["notes"] == "Updated note"


def test_guest_document_upload_list_download_delete(headers):
    # OS10 Phase 3 Step 3A: guest document uploads now verify the actual
    # file bytes match the declared Content-Type (real JPEG magic bytes
    # required for image/jpeg) — this fixture uses a real JPEG signature
    # instead of arbitrary text, matching that new, intentionally stricter
    # validation. See test_guest_document_security.py for focused
    # coverage of the validation itself.
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "V8 Doc Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
    fake_file = io.BytesIO(jpeg_bytes)
    upload = client.post(
        f"/api/v1/guests/{guest['id']}/documents",
        headers=headers,
        params={"document_type": "aadhaar", "document_number": "1234-5678-9012", "side": "front"},
        files={"file": ("aadhaar_front.jpg", fake_file, "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    doc = upload.json()
    assert doc["document_type"] == "aadhaar"
    assert doc["side"] == "front"

    listing = client.get(f"/api/v1/guests/{guest['id']}/documents", headers=headers).json()
    assert any(d["id"] == doc["id"] for d in listing)

    download = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == jpeg_bytes

    delete_resp = client.delete(f"/api/v1/guests/{guest['id']}/documents/{doc['id']}", headers=headers)
    assert delete_resp.status_code == 204


def test_guest_merge_reassigns_bookings(headers):
    unit_id = _make_unit(headers)
    primary = client.post("/api/v1/guests", headers=headers, json={"name": "Primary Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    duplicate = client.post("/api/v1/guests", headers=headers, json={"name": "Duplicate Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()

    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": duplicate["id"],
        "check_in": (date.today() + timedelta(days=2000)).isoformat(), "check_out": (date.today() + timedelta(days=2002)).isoformat(),
    }).json()

    merge_resp = client.post("/api/v1/guests/merge", headers=headers, json={"primary_guest_id": primary["id"], "duplicate_guest_id": duplicate["id"]})
    assert merge_resp.status_code == 200, merge_resp.text
    assert merge_resp.json()["bookings_reassigned"] == 1

    updated_booking = client.get("/api/v1/bookings", headers=headers, params={"unit_id": unit_id}).json()
    match = next(b for b in updated_booking if b["id"] == booking["id"])
    assert match["guest_id"] == primary["id"]

    guests = client.get("/api/v1/guests", headers=headers, params={"search": "Duplicate Guest"}).json()
    assert not any(g["id"] == duplicate["id"] for g in guests)

    guests_incl = client.get("/api/v1/guests", headers=headers, params={"search": "Duplicate Guest", "include_merged": True}).json()
    assert any(g["id"] == duplicate["id"] for g in guests_incl)


def test_guest_merge_rejects_already_merged_guest(headers):
    a = client.post("/api/v1/guests", headers=headers, json={"name": "A", "phone": f"95{uuid.uuid4().int % 10**8:08d}"}).json()
    b = client.post("/api/v1/guests", headers=headers, json={"name": "B", "phone": f"96{uuid.uuid4().int % 10**8:08d}"}).json()
    c = client.post("/api/v1/guests", headers=headers, json={"name": "C", "phone": f"97{uuid.uuid4().int % 10**8:08d}"}).json()

    client.post("/api/v1/guests/merge", headers=headers, json={"primary_guest_id": a["id"], "duplicate_guest_id": b["id"]})
    resp = client.post("/api/v1/guests/merge", headers=headers, json={"primary_guest_id": c["id"], "duplicate_guest_id": b["id"]})
    assert resp.status_code == 409


def test_booking_accepts_guest_count_and_timing_fields(headers):
    unit_id = _make_unit(headers, max_occupancy=3)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "V8 Booking Guest", "phone": f"98{uuid.uuid4().int % 10**8:08d}"}).json()
    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (date.today() + timedelta(days=2100)).isoformat(), "check_out": (date.today() + timedelta(days=2102)).isoformat(),
        "adults": 2, "children": 1, "extra_bed": True, "arrival_time": "14:00", "departure_time": "11:00",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["adults"] == 2
    assert data["children"] == 1
    assert data["extra_bed"] is True
    assert data["arrival_time"] == "14:00"


def test_booking_edit_can_update_guest_count(headers):
    unit_id = _make_unit(headers, max_occupancy=3)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "V8 Edit Count Guest", "phone": f"89{uuid.uuid4().int % 10**8:08d}"}).json()
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (date.today() + timedelta(days=2110)).isoformat(), "check_out": (date.today() + timedelta(days=2112)).isoformat(),
    }).json()
    resp = client.patch(f"/api/v1/bookings/{booking['id']}", headers=headers, json={"adults": 3, "extra_bed": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["adults"] == 3
    assert resp.json()["extra_bed"] is True


def test_laundry_entry_create_and_total(headers):
    resp = client.post("/api/v1/finance/laundry", headers=headers, json={
        "entry_date": "2037-01-01", "item_type": "bedsheet", "quantity": 10, "rate_per_item": 25,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["total_amount"] == 250.0


def test_laundry_entry_invalid_item_type_rejected(headers):
    resp = client.post("/api/v1/finance/laundry", headers=headers, json={
        "entry_date": "2037-01-01", "item_type": "socks", "quantity": 5, "rate_per_item": 10,
    })
    assert resp.status_code == 400


def test_laundry_monthly_summary_aggregates_correctly(headers):
    # Random year avoids collisions with previous runs of this same test
    # against the persistent, shared test database (this suite has no
    # per-test transactional rollback) — a hardcoded year/month here would
    # double-count on every re-run, which is exactly what happened before
    # this fix.
    year = 2100 + uuid.uuid4().int % 900
    month = 5
    client.post("/api/v1/finance/laundry", headers=headers, json={"entry_date": f"{year}-{month:02d}-01", "item_type": "bath_towel", "quantity": 20, "rate_per_item": 15})
    client.post("/api/v1/finance/laundry", headers=headers, json={"entry_date": f"{year}-{month:02d}-15", "item_type": "bath_towel", "quantity": 10, "rate_per_item": 15})
    client.post("/api/v1/finance/laundry", headers=headers, json={"entry_date": f"{year}-{month:02d}-10", "item_type": "pillow_cover", "quantity": 30, "rate_per_item": 8})

    summary = client.get("/api/v1/finance/laundry/summary", headers=headers, params={"period_year": year, "period_month": month}).json()
    towel_line = next(l for l in summary["lines"] if l["item_type"] == "bath_towel")
    assert towel_line["total_quantity"] == 30
    assert towel_line["total_amount"] == 450.0
    pillow_line = next(l for l in summary["lines"] if l["item_type"] == "pillow_cover")
    assert pillow_line["total_amount"] == 240.0
    assert summary["grand_total"] == 450.0 + 240.0


def test_laundry_entry_delete(headers):
    entry = client.post("/api/v1/finance/laundry", headers=headers, json={"entry_date": "2037-02-01", "item_type": "curtains", "quantity": 2, "rate_per_item": 100}).json()
    resp = client.delete(f"/api/v1/finance/laundry/{entry['id']}", headers=headers)
    assert resp.status_code == 204


def test_unauthenticated_cannot_access_new_endpoints():
    assert client.get("/api/v1/finance/laundry").status_code == 401
    assert client.post("/api/v1/guests/merge", json={}).status_code == 401


def test_inventory_item_brand_and_expiry_round_trip(headers):
    resp = client.post("/api/v1/inventory/items", headers=headers, json={
        "name": "Shampoo Bottles", "category": "toiletries", "brand": "TestBrand", "expiry_date": "2027-12-31",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["brand"] == "TestBrand"
    assert resp.json()["expiry_date"] == "2027-12-31"


def test_inventory_item_update_previously_impossible(headers):
    """Regression coverage: there was no way to edit an inventory item at
    all after creation before this fix."""
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": "Edit Test Item", "category": "other", "minimum_stock": 5}).json()
    resp = client.patch(f"/api/v1/inventory/items/{item['id']}", headers=headers, json={"minimum_stock": 20, "brand": "New Brand"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["minimum_stock"] == 20
    assert resp.json()["brand"] == "New Brand"


def test_inventory_item_update_logs_audit(headers):
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": "Audit Test Item", "category": "other"}).json()
    client.patch(f"/api/v1/inventory/items/{item['id']}", headers=headers, json={"minimum_stock": 10})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "inventory.item.update"}).json()
    assert any(l["entity_id"] == item["id"] for l in logs)
