"""
Test for the consolidated Guest History endpoint.
Run with: pytest tests/test_guest_history.py -v
"""
import uuid
from datetime import date, timedelta

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


def test_guest_history_aggregates_bookings_and_payments(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"HistProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"HistUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "History Test Guest", "phone": f"97{uuid.uuid4().int % 10**8:08d}"}).json()

    check_in = (date.today() + timedelta(days=800)).isoformat()
    check_out = (date.today() + timedelta(days=802)).isoformat()
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in, "check_out": check_out}).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1500, "mode": "upi", "reference_no": "HISTTEST"})

    history = client.get(f"/api/v1/guests/{guest['id']}/history", headers=headers).json()
    assert history["guest"]["id"] == guest["id"]
    assert history["total_bookings"] == 1
    assert history["total_spent"] == 1500.0
    assert history["bookings"][0]["booking_id"] == booking["id"]
    assert history["bookings"][0]["total_paid"] == 1500.0


def test_guest_history_unknown_guest_404s(headers):
    resp = client.get(f"/api/v1/guests/{uuid.uuid4()}/history", headers=headers)
    assert resp.status_code == 404


def test_guest_history_unauthenticated():
    assert client.get(f"/api/v1/guests/{uuid.uuid4()}/history").status_code == 401
