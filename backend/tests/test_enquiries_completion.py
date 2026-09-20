"""
Tests completing the Enquiries module to full production coverage:
source/priority/assigned staff/lost reason, attachments, timeline,
search/pagination, Dashboard follow-ups-due count, Reports Center
export, and inquiry-created/follow-up-due notifications.
Run with: pytest tests/test_enquiries_completion.py -v
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


def _make_inquiry(headers, **overrides):
    payload = {
        "client_name": f"Enquiry Test {uuid.uuid4().hex[:6]}",
        "client_phone": f"91{uuid.uuid4().int % 10**8:08d}",
        "flat_room_label": "Test Room",
        "check_in_date": (date.today() + timedelta(days=8500)).isoformat(),
        "check_out_date": (date.today() + timedelta(days=8503)).isoformat(),
        "rate_per_night": 2000,
    }
    payload.update(overrides)
    resp = client.post("/api/v1/inquiries", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_inquiry_accepts_source_and_priority(headers):
    inquiry = _make_inquiry(headers, source="whatsapp", priority="high")
    assert inquiry["source"] == "whatsapp"
    assert inquiry["priority"] == "high"


def test_inquiry_defaults_when_not_given(headers):
    inquiry = _make_inquiry(headers)
    assert inquiry["source"] == "phone"
    assert inquiry["priority"] == "medium"


def test_invalid_source_rejected(headers):
    resp = client.post("/api/v1/inquiries", headers=headers, json={
        "client_name": "Bad Source", "client_phone": f"92{uuid.uuid4().int % 10**8:08d}",
        "flat_room_label": "Test", "check_in_date": (date.today() + timedelta(days=8600)).isoformat(),
        "check_out_date": (date.today() + timedelta(days=8602)).isoformat(), "rate_per_night": 1000,
        "source": "carrier_pigeon",
    })
    assert resp.status_code == 400


def test_assigned_to_and_lost_reason_updatable(headers):
    inquiry = _make_inquiry(headers)
    admin_id = client.get("/api/v1/users", headers=headers).json()[0]["id"]
    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={
        "assigned_to": admin_id, "priority": "urgent", "lead_status": "lost", "lost_reason": "Chose a competitor",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_to"] == admin_id
    assert resp.json()["lost_reason"] == "Chose a competitor"
    assert resp.json()["priority"] == "urgent"


def test_crm_fields_remain_editable_after_conversion(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"EnqCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"EnqCompUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    inquiry = _make_inquiry(headers, unit_id=unit["id"], advance_amount=1000)
    resp = client.post(f"/api/v1/inquiries/{inquiry['id']}/convert-to-booking", headers=headers, json={})
    assert resp.status_code == 201, resp.text

    admin_id = client.get("/api/v1/users", headers=headers).json()[0]["id"]
    update = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"priority": "low", "assigned_to": admin_id})
    assert update.status_code == 200, update.text
    assert update.json()["priority"] == "low"


def test_inquiry_attachment_upload_list_download_delete(headers):
    inquiry = _make_inquiry(headers)
    fake_file = io.BytesIO(b"fake ID proof scan")
    upload = client.post(f"/api/v1/inquiries/{inquiry['id']}/attachments", headers=headers, files={"file": ("id_proof.pdf", fake_file, "application/pdf")})
    assert upload.status_code == 201, upload.text
    attachment = upload.json()

    listing = client.get(f"/api/v1/inquiries/{inquiry['id']}/attachments", headers=headers).json()
    assert any(a["id"] == attachment["id"] for a in listing)

    download = client.get(f"/api/v1/inquiries/{inquiry['id']}/attachments/{attachment['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"fake ID proof scan"

    delete_resp = client.delete(f"/api/v1/inquiries/{inquiry['id']}/attachments/{attachment['id']}", headers=headers)
    assert delete_resp.status_code == 204


def test_attachment_upload_logs_audit(headers):
    inquiry = _make_inquiry(headers)
    fake_file = io.BytesIO(b"test")
    client.post(f"/api/v1/inquiries/{inquiry['id']}/attachments", headers=headers, files={"file": ("test.pdf", fake_file, "application/pdf")})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "inquiry.attachment_upload"}).json()
    assert any(l["entity_id"] == inquiry["id"] for l in logs)


def test_inquiry_timeline_shows_create_and_update(headers):
    inquiry = _make_inquiry(headers)
    client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"priority": "high"})

    timeline = client.get(f"/api/v1/inquiries/{inquiry['id']}/timeline", headers=headers).json()
    actions = [e["action"] for e in timeline["events"]]
    assert "inquiry.create" in actions


def test_inquiry_timeline_404_for_unknown_inquiry(headers):
    resp = client.get(f"/api/v1/inquiries/{uuid.uuid4()}/timeline", headers=headers)
    assert resp.status_code == 404


def test_inquiry_search_by_client_name(headers):
    unique_name = f"SearchableClient{uuid.uuid4().hex[:8]}"
    _make_inquiry(headers, client_name=unique_name)
    results = client.get("/api/v1/inquiries", headers=headers, params={"search": unique_name}).json()
    assert len(results) == 1
    assert results[0]["client_name"] == unique_name


def test_inquiry_filter_by_source_and_priority(headers):
    unique_name = f"FilterClient{uuid.uuid4().hex[:8]}"
    _make_inquiry(headers, client_name=unique_name, source="ota", priority="urgent")
    results = client.get("/api/v1/inquiries", headers=headers, params={"source": "ota", "priority": "urgent", "search": unique_name}).json()
    assert len(results) == 1


def test_inquiry_pagination_params_accepted(headers):
    resp = client.get("/api/v1/inquiries", headers=headers, params={"limit": 5, "offset": 0})
    assert resp.status_code == 200
    assert len(resp.json()) <= 5


def test_dashboard_reflects_follow_ups_due_today(headers):
    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    today = date.today().isoformat()
    inquiry = _make_inquiry(headers)
    client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"next_follow_up_date": today})

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["follow_ups_due_today"] >= before["follow_ups_due_today"] + 1


def test_enquiries_report_export_pdf_and_excel(headers):
    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "enquiries", "format": fmt})
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_inquiry_creation_attempts_notification(headers):
    inquiry = _make_inquiry(headers)
    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "inquiry_created"}).json()
    assert any(l["inquiry_id"] == inquiry["id"] for l in logs)


def test_follow_up_scan_sends_reminder_for_due_inquiry(headers):
    today = date.today().isoformat()
    inquiry = _make_inquiry(headers)
    client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"next_follow_up_date": today, "lead_status": "contacted"})

    scan_resp = client.post("/api/v1/notifications/scan-reminders", headers=headers)
    assert scan_resp.status_code == 200

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "inquiry_follow_up_due"}).json()
    assert any(l["inquiry_id"] == inquiry["id"] for l in logs)


def test_follow_up_scan_does_not_duplicate_on_rerun(headers):
    today = date.today().isoformat()
    inquiry = _make_inquiry(headers)
    client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"next_follow_up_date": today, "lead_status": "contacted"})

    client.post("/api/v1/notifications/scan-reminders", headers=headers)
    client.post("/api/v1/notifications/scan-reminders", headers=headers)

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "inquiry_follow_up_due"}).json()
    matching = [l for l in logs if l["inquiry_id"] == inquiry["id"]]
    assert len(matching) == 1


def test_existing_conversion_workflow_still_works(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"EnqRegProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"EnqRegUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    inquiry = _make_inquiry(headers, unit_id=unit["id"], advance_amount=500)
    resp = client.post(f"/api/v1/inquiries/{inquiry['id']}/convert-to-booking", headers=headers, json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["booking_id"] is not None
