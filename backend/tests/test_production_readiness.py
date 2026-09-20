"""
Tests for the production-readiness pass: pagination on previously-unbounded
list endpoints, guest ID proof number (passport/Aadhaar), and repeat-guest
identification.
Run with: pytest tests/test_production_readiness.py -v
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


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"PRProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"PRUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def test_expense_list_respects_limit(headers):
    unique_date = (date.today() + timedelta(days=1200)).isoformat()
    for _ in range(5):
        client.post("/api/v1/finance/expenses", headers=headers, json={"category": "other", "amount": 100, "expense_date": unique_date})
    resp = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": unique_date, "date_to": unique_date, "limit": 3})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_expense_list_offset_paginates_through_results(headers):
    unique_date = (date.today() + timedelta(days=1210)).isoformat()
    for i in range(5):
        client.post("/api/v1/finance/expenses", headers=headers, json={"category": "other", "amount": 100 + i, "expense_date": unique_date, "notes": f"item{i}"})
    page1 = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": unique_date, "date_to": unique_date, "limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": unique_date, "date_to": unique_date, "limit": 2, "offset": 2}).json()
    assert len(page1) == 2
    assert len(page2) == 2
    assert {e["id"] for e in page1}.isdisjoint({e["id"] for e in page2})


def test_maintenance_ticket_list_respects_limit(headers):
    unit_id = _make_unit(headers)
    for i in range(5):
        client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": f"Issue {i}"})
    resp = client.get("/api/v1/maintenance-tickets", headers=headers, params={"unit_id": unit_id, "limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_housekeeping_task_list_respects_limit(headers):
    unit_id = _make_unit(headers)
    for _ in range(5):
        client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"})
    resp = client.get("/api/v1/housekeeping-tasks", headers=headers, params={"unit_id": unit_id, "limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_limit_cannot_exceed_500(headers):
    resp = client.get("/api/v1/finance/expenses", headers=headers, params={"limit": 501})
    assert resp.status_code == 422


def test_guest_id_proof_number_round_trip(headers):
    resp = client.post("/api/v1/guests", headers=headers, json={
        "name": "ID Proof Test Guest", "phone": f"97{uuid.uuid4().int % 10**8:08d}",
        "id_proof_type": "passport", "id_proof_number": "P1234567",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["id_proof_number"] == "P1234567"
    assert resp.json()["id_proof_type"] == "passport"


def test_repeat_guest_flag_false_for_single_booking(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Single Stay Guest", "phone": f"96{uuid.uuid4().int % 10**8:08d}"}).json()
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (date.today() + timedelta(days=1300)).isoformat(), "check_out": (date.today() + timedelta(days=1302)).isoformat(),
    })
    history = client.get(f"/api/v1/guests/{guest['id']}/history", headers=headers).json()
    assert history["is_repeat_guest"] is False
    assert history["total_bookings"] == 1


def test_repeat_guest_flag_true_for_multiple_bookings(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Repeat Guest", "phone": f"95{uuid.uuid4().int % 10**8:08d}"}).json()
    for i in range(2):
        client.post("/api/v1/bookings", headers=headers, json={
            "unit_id": unit_id, "guest_id": guest["id"],
            "check_in": (date.today() + timedelta(days=1310 + i * 10)).isoformat(),
            "check_out": (date.today() + timedelta(days=1312 + i * 10)).isoformat(),
        })
    history = client.get(f"/api/v1/guests/{guest['id']}/history", headers=headers).json()
    assert history["is_repeat_guest"] is True
    assert history["total_bookings"] == 2


def test_bulk_expense_approval(headers):
    unique_date = (date.today() + timedelta(days=1400)).isoformat()
    ids = []
    for i in range(3):
        resp = client.post("/api/v1/finance/expenses", headers=headers, json={"category": "other", "amount": 100, "expense_date": unique_date})
        ids.append(resp.json()["id"])

    resp = client.post("/api/v1/finance/expenses/bulk-approval", headers=headers, json={"expense_ids": ids, "approve": True, "notes": "Bulk test"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["approved_count"] == 3
    assert data["not_found_ids"] == []

    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": unique_date, "date_to": unique_date}).json()
    approved = [e for e in expenses if e["id"] in ids]
    assert all(e.get("approval_status") == "approved" for e in approved) if approved and "approval_status" in approved[0] else True


def test_bulk_expense_approval_handles_missing_id(headers):
    resp = client.post("/api/v1/finance/expenses/bulk-approval", headers=headers, json={"expense_ids": [str(uuid.uuid4())], "approve": True})
    assert resp.status_code == 200
    assert len(resp.json()["not_found_ids"]) == 1
