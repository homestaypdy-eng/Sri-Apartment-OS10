"""
Tests for full inquiry editing (client details, rate, dates) and the
post-conversion lock that prevents editing a finalized booking's terms.
Run with: pytest tests/test_inquiry_editing.py -v
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


def _make_unit(headers, base_rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"EditProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"EditUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate}).json()
    return unit["id"]


def _make_inquiry(headers, unit_id=None, **overrides):
    payload = {
        "client_name": "Edit Test Client",
        "client_phone": f"93{uuid.uuid4().int % 10**8:08d}",
        "flat_room_label": "Test Room",
        "check_in_date": "2028-01-10",
        "check_out_date": "2028-01-13",
        "advance_amount": 0,
    }
    if unit_id:
        payload["unit_id"] = unit_id
    else:
        payload["rate_per_night"] = 2000
    payload.update(overrides)
    resp = client.post("/api/v1/inquiries", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_edit_client_details(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id)

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={
        "client_name": "Updated Name",
        "client_phone": "9999999999",
        "client_email": "updated@example.com",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["client_name"] == "Updated Name"
    assert data["client_phone"] == "9999999999"
    assert data["client_email"] == "updated@example.com"


def test_edit_dates_recalculates_pricing(headers):
    unit_id = _make_unit(headers, base_rate=2000)
    inquiry = _make_inquiry(headers, unit_id=unit_id)
    assert inquiry["nights"] == 3

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={
        "check_in_date": "2028-02-01",
        "check_out_date": "2028-02-06",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["nights"] == 5
    assert data["pricing_source"] == "auto"
    assert data["rate_per_night"] == 2000.0


def test_edit_manual_rate_override_logs_audit(headers):
    unit_id = _make_unit(headers, base_rate=2000)
    inquiry = _make_inquiry(headers, unit_id=unit_id)

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={
        "rate_per_night": 1500,
        "override_reason": "Edit-time override test",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["pricing_source"] == "manual"
    assert resp.json()["rate_per_night"] == 1500.0

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "inquiry.manual_rate_override"}).json()
    assert any("Edit-time override test" in (l["details"] or "") for l in logs)


def test_edit_can_switch_back_to_auto_pricing(headers):
    unit_id = _make_unit(headers, base_rate=2000)
    inquiry = _make_inquiry(headers, unit_id=unit_id, rate_per_night=1000, override_reason="initial manual")
    assert inquiry["pricing_source"] == "manual"

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"check_in_date": "2028-03-01", "check_out_date": "2028-03-04"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pricing_source"] == "auto"
    assert resp.json()["rate_per_night"] == 2000.0


def test_edit_advance_exceeding_new_grand_total_rejected(headers):
    unit_id = _make_unit(headers, base_rate=2000)
    inquiry = _make_inquiry(headers, unit_id=unit_id, advance_amount=5000)

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={
        "check_in_date": "2028-01-10", "check_out_date": "2028-01-11",
    })
    assert resp.status_code == 400


def test_edit_checkout_before_checkin_rejected(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id)
    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={
        "check_in_date": "2028-05-10", "check_out_date": "2028-05-05",
    })
    assert resp.status_code == 400


def _convert(headers, inquiry_id):
    resp = client.post(f"/api/v1/inquiries/{inquiry_id}/convert-to-booking", headers=headers, json={})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_editing_locked_after_conversion(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id, advance_amount=1000)
    _convert(headers, inquiry["id"])

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"client_name": "Should Not Work"})
    assert resp.status_code == 409


def test_rate_editing_locked_after_conversion(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id, advance_amount=1000)
    _convert(headers, inquiry["id"])

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"rate_per_night": 1})
    assert resp.status_code == 409


def test_advance_amount_editing_locked_after_conversion(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id, advance_amount=1000)
    _convert(headers, inquiry["id"])

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"advance_amount": 1})
    assert resp.status_code == 409


def test_lead_status_and_follow_up_still_editable_after_conversion(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id, advance_amount=1000)
    _convert(headers, inquiry["id"])

    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={
        "follow_up_notes": "Post-conversion note, should be allowed",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["follow_up_notes"] == "Post-conversion note, should be allowed"


def test_convert_to_booking_still_rejects_double_conversion(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id, advance_amount=1000)
    _convert(headers, inquiry["id"])

    resp = client.post(f"/api/v1/inquiries/{inquiry['id']}/convert-to-booking", headers=headers, json={})
    assert resp.status_code == 409


def test_converted_inquiry_reflects_confirmed_status_and_booking_link(headers):
    unit_id = _make_unit(headers)
    inquiry = _make_inquiry(headers, unit_id=unit_id, advance_amount=1000)
    result = _convert(headers, inquiry["id"])

    refreshed = client.get(f"/api/v1/inquiries/{inquiry['id']}", headers=headers).json()
    assert refreshed["status"] == "confirmed"
    assert refreshed["lead_status"] == "converted"
    assert refreshed["booking_id"] == result["booking_id"]
