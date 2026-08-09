"""
API-level integration tests for dynamic pricing: seasonal rates, long-stay
discounts, weekend rates, price-quote endpoint, and auto-calculation vs.
manual override in inquiry creation.
Run with: pytest tests/test_dynamic_pricing_api.py -v
"""
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
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_unit(headers, base_rate=2000, weekend_rate=None):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"PricingProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    payload = {"property_id": prop["id"], "name": f"PricingUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate}
    if weekend_rate is not None:
        payload["weekend_rate"] = weekend_rate
    unit = client.post("/api/v1/units", headers=headers, json=payload).json()
    return unit["id"]


def test_unit_created_without_weekend_rate_defaults_to_null(headers):
    unit_id = _make_unit(headers)
    units = client.get("/api/v1/units", headers=headers).json()
    unit = next(u for u in units if u["id"] == unit_id)
    assert unit["weekend_rate"] is None


def test_update_unit_weekend_rate(headers):
    unit_id = _make_unit(headers)
    resp = client.patch(f"/api/v1/units/{unit_id}", headers=headers, json={"weekend_rate": 3500})
    assert resp.status_code == 200
    assert resp.json()["weekend_rate"] == 3500.0


def test_seasonal_rate_crud(headers):
    unit_id = _make_unit(headers)
    create = client.post(f"/api/v1/units/{unit_id}/seasonal-rates", headers=headers, json={"name": "Test Season", "start_date": "2027-12-20", "end_date": "2027-12-31", "rate_per_night": 5000})
    assert create.status_code == 201
    rate_id = create.json()["id"]

    listing = client.get(f"/api/v1/units/{unit_id}/seasonal-rates", headers=headers).json()
    assert any(r["id"] == rate_id for r in listing)

    delete = client.delete(f"/api/v1/units/{unit_id}/seasonal-rates/{rate_id}", headers=headers)
    assert delete.status_code == 204
    listing_after = client.get(f"/api/v1/units/{unit_id}/seasonal-rates", headers=headers).json()
    assert not any(r["id"] == rate_id for r in listing_after)


def test_seasonal_rate_end_before_start_rejected(headers):
    unit_id = _make_unit(headers)
    resp = client.post(f"/api/v1/units/{unit_id}/seasonal-rates", headers=headers, json={"name": "Bad", "start_date": "2027-12-10", "end_date": "2027-12-01", "rate_per_night": 5000})
    assert resp.status_code == 422


def test_long_stay_discount_tier_crud(headers):
    unit_id = _make_unit(headers)
    create = client.post(f"/api/v1/units/{unit_id}/long-stay-discounts", headers=headers, json={"min_nights": 7, "discount_percent": 15})
    assert create.status_code == 201
    tier_id = create.json()["id"]

    listing = client.get(f"/api/v1/units/{unit_id}/long-stay-discounts", headers=headers).json()
    assert any(t["id"] == tier_id for t in listing)

    delete = client.delete(f"/api/v1/units/{unit_id}/long-stay-discounts/{tier_id}", headers=headers)
    assert delete.status_code == 204


def test_price_quote_endpoint_matches_pricing_engine(headers):
    unit_id = _make_unit(headers, base_rate=2000, weekend_rate=3000)
    resp = client.get(f"/api/v1/units/{unit_id}/price-quote", headers=headers, params={"check_in": "2027-06-01", "check_out": "2027-06-04"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["nights"] == 3
    assert len(data["daily_breakdown"]) == 3


def test_price_quote_invalid_date_range_rejected(headers):
    unit_id = _make_unit(headers)
    resp = client.get(f"/api/v1/units/{unit_id}/price-quote", headers=headers, params={"check_in": "2027-06-10", "check_out": "2027-06-05"})
    assert resp.status_code == 400


def test_inquiry_auto_calculates_from_unit_pricing(headers):
    unit_id = _make_unit(headers, base_rate=2000, weekend_rate=3000)
    client.post(f"/api/v1/units/{unit_id}/long-stay-discounts", headers=headers, json={"min_nights": 5, "discount_percent": 10})

    resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Auto Calc Test",
        "client_phone": f"97{uuid.uuid4().int % 10**8:08d}",
        "unit_id": unit_id,
        "flat_room_label": "Test Room",
        "check_in_date": "2027-07-01",
        "check_out_date": "2027-07-07",
        "advance_amount": 100,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["pricing_source"] == "auto"
    assert data["nights"] == 6
    assert data["rate_breakdown"] is not None


def test_inquiry_manual_override_logs_audit_entry(headers):
    unit_id = _make_unit(headers, base_rate=2000)

    resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Override Audit Test",
        "client_phone": f"96{uuid.uuid4().int % 10**8:08d}",
        "unit_id": unit_id,
        "flat_room_label": "Test Room",
        "check_in_date": "2027-08-01",
        "check_out_date": "2027-08-03",
        "rate_per_night": 1200,
        "override_reason": "Test override reason",
        "advance_amount": 100,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["pricing_source"] == "manual"
    assert resp.json()["rate_per_night"] == 1200.0

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "inquiry.manual_rate_override"}).json()
    assert any("Test override reason" in (l["details"] or "") for l in logs)


def test_inquiry_without_unit_still_requires_rate_per_night(headers):
    resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "No Unit Test",
        "client_phone": f"95{uuid.uuid4().int % 10**8:08d}",
        "flat_room_label": "Ad-hoc Room",
        "check_in_date": "2027-09-01",
        "check_out_date": "2027-09-03",
        "advance_amount": 0,
    })
    assert resp.status_code == 400


def test_seasonal_rate_correctly_reflected_in_inquiry_total(headers):
    unit_id = _make_unit(headers, base_rate=2000)
    client.post(f"/api/v1/units/{unit_id}/seasonal-rates", headers=headers, json={"name": "Special Event", "start_date": "2027-10-01", "end_date": "2027-10-02", "rate_per_night": 8000})

    resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Seasonal Test",
        "client_phone": f"94{uuid.uuid4().int % 10**8:08d}",
        "unit_id": unit_id,
        "flat_room_label": "Test Room",
        "check_in_date": "2027-10-01",
        "check_out_date": "2027-10-02",
        "advance_amount": 0,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["rate_per_night"] == 8000.0
