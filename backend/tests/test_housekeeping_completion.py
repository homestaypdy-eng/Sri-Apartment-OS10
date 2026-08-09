"""
Tests completing the Housekeeping module to full production coverage: real
inventory consumption tracking (linking stock movements to a specific
housekeeping task and apartment), notification triggers, Reports Center
export, and dashboard integration.
Run with: pytest tests/test_housekeeping_completion.py -v
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


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"HKCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"HKCompUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def _make_item(headers, name="Cleaning Item"):
    resp = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"{name} {uuid.uuid4().hex[:6]}", "category": "cleaning_supplies"})
    return resp.json()


def test_stock_out_links_to_housekeeping_task_and_unit(headers):
    unit_id = _make_unit(headers)
    item = _make_item(headers)
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 50, "unit_cost": 10})

    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()

    resp = client.post("/api/v1/inventory/stock-out", headers=headers, json={
        "item_id": item["id"], "quantity": 5, "unit_id": unit_id, "housekeeping_task_id": task["id"], "reason": "Room cleaning",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["unit_id"] == unit_id
    assert data["housekeeping_task_id"] == task["id"]


def test_stock_movements_logs_audit(headers):
    item = _make_item(headers)
    resp = client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 20, "unit_cost": 5})
    movement_id = resp.json()["id"]
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "inventory.stock_in"}).json()
    assert any(l["entity_id"] == movement_id for l in logs)

    unit_id = _make_unit(headers)
    resp2 = client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 3, "unit_id": unit_id})
    movement_id2 = resp2.json()["id"]
    logs2 = client.get("/api/v1/audit/logs", headers=headers, params={"action": "inventory.stock_out"}).json()
    assert any(l["entity_id"] == movement_id2 for l in logs2)


def test_stock_out_without_task_or_unit_still_works(headers):
    item = _make_item(headers)
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 10, "unit_cost": 5})
    resp = client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 2, "reason": "damaged"})
    assert resp.status_code == 201
    assert resp.json()["unit_id"] is None
    assert resp.json()["housekeeping_task_id"] is None


def test_task_creation_attempts_notification(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "deep_clean"}).json()
    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "housekeeping_task_created"}).json()
    assert any(l["housekeeping_task_id"] == task["id"] for l in logs)


def test_task_completion_attempts_notification(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"status": "completed"})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "housekeeping_task_completed"}).json()
    assert any(l["housekeeping_task_id"] == task["id"] for l in logs)


def test_completing_twice_does_not_duplicate_notification(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"}).json()
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"status": "completed"})
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"notes": "re-saved, still completed"})
    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"status": "completed"})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "housekeeping_task_completed"}).json()
    matching = [l for l in logs if l["housekeeping_task_id"] == task["id"]]
    assert len(matching) == 1


def test_dashboard_reflects_pending_housekeeping_tasks(headers):
    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    unit_id = _make_unit(headers)
    client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "routine"})

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["housekeeping_pending"] >= before["housekeeping_pending"] + 1


def test_housekeeping_report_export_pdf_and_excel(headers):
    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "housekeeping", "format": fmt})
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_full_task_lifecycle_still_works(headers):
    unit_id = _make_unit(headers)
    task = client.post("/api/v1/housekeeping-tasks", headers=headers, json={"unit_id": unit_id, "task_type": "checkout_clean"}).json()
    assert task["status"] == "pending"

    client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"status": "in_progress"})
    resp = client.patch(f"/api/v1/housekeeping-tasks/{task['id']}", headers=headers, json={"status": "completed"})
    assert resp.json()["status"] == "completed"
    assert resp.json()["completed_at"] is not None

    timeline = client.get(f"/api/v1/housekeeping-tasks/{task['id']}/timeline", headers=headers).json()
    actions = [e["action"] for e in timeline["events"]]
    assert "housekeeping.create" in actions
    assert "housekeeping.update" in actions

    unit_after = client.get("/api/v1/units", headers=headers).json()
    match = next(u for u in unit_after if u["id"] == unit_id)
    assert match["housekeeping_status"] == "clean"
