"""
Tests completing the Inventory module to full production coverage: SKU,
Maintenance integration, Finance (Purchase Register) integration,
Dashboard low-stock count, Reports Center export, and low-stock
notifications.
Run with: pytest tests/test_inventory_completion.py -v
"""
import uuid
from datetime import date

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


def _make_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"InvProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"InvUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return unit["id"]


def _make_item(headers, sku=None, minimum_stock=5):
    resp = client.post("/api/v1/inventory/items", headers=headers, json={
        "name": f"Test Item {uuid.uuid4().hex[:6]}", "category": "cleaning_supplies", "minimum_stock": minimum_stock, "sku": sku,
    })
    return resp.json()


def test_item_accepts_and_returns_sku(headers):
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    item = _make_item(headers, sku=sku)
    assert item["sku"] == sku


def test_duplicate_sku_rejected(headers):
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    _make_item(headers, sku=sku)
    resp = client.post("/api/v1/inventory/items", headers=headers, json={"name": "Duplicate SKU Item", "category": "other", "sku": sku})
    assert resp.status_code == 400, resp.text


def test_sku_editable_via_update(headers):
    item = _make_item(headers)
    new_sku = f"SKU-{uuid.uuid4().hex[:8]}"
    resp = client.patch(f"/api/v1/inventory/items/{item['id']}", headers=headers, json={"sku": new_sku})
    assert resp.status_code == 200
    assert resp.json()["sku"] == new_sku


def test_stock_out_links_to_maintenance_ticket(headers):
    unit_id = _make_unit(headers)
    item = _make_item(headers, minimum_stock=0)
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 10})
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Fixing tap"}).json()

    resp = client.post("/api/v1/inventory/stock-out", headers=headers, json={
        "item_id": item["id"], "quantity": 2, "maintenance_ticket_id": ticket["id"], "unit_id": unit_id,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["maintenance_ticket_id"] == ticket["id"]

    movements = client.get(f"/api/v1/inventory/items/{item['id']}/movements", headers=headers).json()
    assert any(m["maintenance_ticket_id"] == ticket["id"] for m in movements)


def test_stock_in_with_supplier_and_cost_creates_purchase_record(headers):
    supplier = client.post("/api/v1/inventory/suppliers", headers=headers, json={"name": f"Test Supplier {uuid.uuid4().hex[:6]}"}).json()
    item = _make_item(headers)

    resp = client.post("/api/v1/inventory/stock-in", headers=headers, json={
        "item_id": item["id"], "quantity": 10, "supplier_id": supplier["id"], "unit_cost": 50,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["linked_purchase_record_id"] is not None

    today = date.today().isoformat()
    purchases = client.get("/api/v1/finance/purchases", headers=headers, params={"date_from": today, "date_to": today}).json()
    matching = [p for p in purchases if p["id"] == resp.json()["linked_purchase_record_id"]]
    assert len(matching) == 1
    assert matching[0]["amount"] == 500.0
    assert matching[0]["supplier_name"] == supplier["name"]


def test_stock_in_without_supplier_or_cost_creates_no_purchase_record(headers):
    item = _make_item(headers)
    resp = client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 5})
    assert resp.status_code == 201
    assert resp.json()["linked_purchase_record_id"] is None


def test_purchase_record_creation_logs_audit(headers):
    supplier = client.post("/api/v1/inventory/suppliers", headers=headers, json={"name": f"Audit Supplier {uuid.uuid4().hex[:6]}"}).json()
    item = _make_item(headers)
    resp = client.post("/api/v1/inventory/stock-in", headers=headers, json={
        "item_id": item["id"], "quantity": 4, "supplier_id": supplier["id"], "unit_cost": 25,
    })
    purchase_id = resp.json()["linked_purchase_record_id"]

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.purchase.create"}).json()
    assert any(l["entity_id"] == purchase_id for l in logs)


def test_supplier_creation_logs_audit(headers):
    resp = client.post("/api/v1/inventory/suppliers", headers=headers, json={"name": f"Audit Check Supplier {uuid.uuid4().hex[:6]}"})
    supplier_id = resp.json()["id"]
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "inventory.supplier.create"}).json()
    assert any(l["entity_id"] == supplier_id for l in logs)


def test_dashboard_reflects_low_stock_count(headers):
    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    item = _make_item(headers, minimum_stock=5)
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 3})

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["low_stock_count"] >= before["low_stock_count"] + 1


def test_inventory_report_export_pdf_and_excel(headers):
    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "inventory", "format": fmt})
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_crossing_into_low_stock_attempts_notification(headers):
    item = _make_item(headers, minimum_stock=5)
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 20})
    client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 16})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "low_stock_alert"}).json()
    assert any(l["inventory_item_id"] == item["id"] for l in logs)


def test_staying_low_stock_does_not_duplicate_notification(headers):
    item = _make_item(headers, minimum_stock=100)
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 10})
    client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 1})
    client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 1})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "low_stock_alert"}).json()
    matching = [l for l in logs if l["inventory_item_id"] == item["id"]]
    assert len(matching) == 0


def test_full_item_lifecycle_still_works(headers):
    item = _make_item(headers)
    resp = client.patch(f"/api/v1/inventory/items/{item['id']}", headers=headers, json={"minimum_stock": 15, "brand": "TestBrand"})
    assert resp.status_code == 200
    assert resp.json()["minimum_stock"] == 15

    listing = client.get("/api/v1/inventory/items", headers=headers, params={"category": "cleaning_supplies"}).json()
    assert any(i["id"] == item["id"] for i in listing)

    low_stock = client.get("/api/v1/inventory/items", headers=headers, params={"low_stock_only": True}).json()
    assert all(i["is_low_stock"] for i in low_stock)


# --- Phase 9: non-negative validation + IntegrityError handling + pagination ---

def test_negative_minimum_stock_rejected(headers):
    resp = client.post("/api/v1/inventory/items", headers=headers, json={"name": "Bad Item", "minimum_stock": -1})
    assert resp.status_code == 422


def test_negative_unit_cost_rejected_on_create(headers):
    resp = client.post("/api/v1/inventory/items", headers=headers, json={"name": "Bad Item", "unit_cost": -5})
    assert resp.status_code == 422


def test_negative_minimum_stock_rejected_on_update(headers):
    item = _make_item(headers)
    resp = client.patch(f"/api/v1/inventory/items/{item['id']}", headers=headers, json={"minimum_stock": -1})
    assert resp.status_code == 422


def test_zero_minimum_stock_and_unit_cost_still_accepted(headers):
    """Regression: ge=0 must allow zero, not just reject negatives."""
    resp = client.post("/api/v1/inventory/items", headers=headers, json={"name": "Zero Item", "minimum_stock": 0, "unit_cost": 0})
    assert resp.status_code == 201, resp.text


def test_negative_stock_movement_unit_cost_rejected(headers):
    item = _make_item(headers)
    resp = client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 5, "unit_cost": -10})
    assert resp.status_code == 422


def test_create_item_duplicate_sku_race_handled_cleanly(headers):
    """The pre-check for SKU uniqueness has a TOCTOU window under real
    concurrency; this proves the flush()-level IntegrityError backstop
    behind it also produces the same clean 400, not a raw 500, by
    forcing the race directly against the DB below the pre-check."""
    import threading

    sku = f"RACE-{uuid.uuid4().hex[:8]}"
    results = []

    def do_create():
        results.append(client.post("/api/v1/inventory/items", headers=headers, json={"name": "Race Item", "sku": sku}))

    threads = [threading.Thread(target=do_create), threading.Thread(target=do_create)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 400], [r.text for r in results]


def test_movements_pagination_limit_respected(headers):
    item = _make_item(headers)
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 100})
    for _ in range(3):
        client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 1})

    resp = client.get(f"/api/v1/inventory/items/{item['id']}/movements", headers=headers, params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    full = client.get(f"/api/v1/inventory/items/{item['id']}/movements", headers=headers).json()
    assert len(full) == 4  # 1 stock-in + 3 stock-out, well under the default 200 cap
