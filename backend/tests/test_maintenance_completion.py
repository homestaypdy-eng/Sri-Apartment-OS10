"""
Tests completing the Maintenance module to full production coverage: the
Finance integration (resolved ticket cost -> real Expense record, feeding
Apartment P&L), notification triggers, Reports Center export, and
dashboard integration.
Run with: pytest tests/test_maintenance_completion.py -v
"""
import uuid
from datetime import date

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
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"MFProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"MFUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def test_resolving_ticket_with_cost_creates_real_expense(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Leaking tap repair"}).json()

    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 750})
    assert resp.status_code == 200, resp.text

    today = date.today()
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": today.isoformat(), "date_to": today.isoformat()}).json()
    matching = [e for e in expenses if e["unit_id"] == unit_id and e["category"] == "maintenance"]
    assert len(matching) == 1
    assert matching[0]["amount"] == 750.0
    assert "Leaking tap repair" in (matching[0]["notes"] or "")


def test_resolving_ticket_cost_flows_into_apartment_pnl(headers):
    unit_id = _make_unit(headers, rate=2000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Maint PnL Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2037, 6
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-04",
    })

    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "AC repair"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 600})

    today = date.today()
    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    maint_line = next((l for l in pnl["expense_lines"] if l["category"] == "maintenance"), None)
    assert maint_line is not None
    assert maint_line["amount"] == 600.0


def test_updating_resolved_ticket_cost_updates_same_expense_not_duplicate(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Plumbing fix"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 300})
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"cost": 450})

    today = date.today()
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": today.isoformat(), "date_to": today.isoformat()}).json()
    matching = [e for e in expenses if e["unit_id"] == unit_id and e["category"] == "maintenance" and "Plumbing fix" in (e["notes"] or "")]
    assert len(matching) == 1, "Must be exactly one expense, not two"
    assert matching[0]["amount"] == 450.0


def test_open_ticket_with_cost_does_not_create_expense_yet(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Estimate only"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"cost": 999})

    today = date.today()
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": today.isoformat(), "date_to": today.isoformat()}).json()
    matching = [e for e in expenses if e["unit_id"] == unit_id and "Estimate only" in (e["notes"] or "")]
    assert len(matching) == 0


def test_resolving_ticket_without_cost_creates_no_expense(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "No cost repair"}).json()
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved"})
    assert resp.status_code == 200

    today = date.today()
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"date_from": today.isoformat(), "date_to": today.isoformat()}).json()
    matching = [e for e in expenses if e["unit_id"] == unit_id and "No cost repair" in (e["notes"] or "")]
    assert len(matching) == 0


def test_expense_auto_creation_logs_audit(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Audit check repair"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 200})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.expense.create"}).json()
    assert any(l["details"] and str(ticket["id"]) in l["details"] for l in logs)


def test_ticket_creation_attempts_notification(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Notify create test"}).json()
    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "maintenance_ticket_created"}).json()
    assert any(l["maintenance_ticket_id"] == ticket["id"] for l in logs)


def test_ticket_resolution_attempts_notification(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Notify resolve test"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved"})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "maintenance_ticket_resolved"}).json()
    assert any(l["maintenance_ticket_id"] == ticket["id"] for l in logs)


def test_resolving_twice_does_not_duplicate_notification(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "No dup notify test"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved"})
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "closed"})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "maintenance_ticket_resolved"}).json()
    matching = [l for l in logs if l["maintenance_ticket_id"] == ticket["id"]]
    assert len(matching) == 1


def test_dashboard_reflects_open_maintenance_tickets(headers):
    before = client.get("/api/v1/reports/dashboard", headers=headers).json()
    unit_id = _make_unit(headers)
    client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Dashboard count test"})

    after = client.get("/api/v1/reports/dashboard", headers=headers).json()
    assert after["maintenance_open"] >= before["maintenance_open"] + 1


def test_maintenance_report_export_pdf_and_excel(headers):
    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "maintenance", "format": fmt})
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_full_ticket_lifecycle_still_works(headers):
    unit_id = _make_unit(headers)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "Full lifecycle test", "priority": "high"}).json()
    assert ticket["status"] == "open"

    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "in_progress"})
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 100})
    assert resp.json()["status"] == "resolved"
    assert resp.json()["resolved_at"] is not None

    timeline = client.get(f"/api/v1/maintenance-tickets/{ticket['id']}/timeline", headers=headers).json()
    actions = [e["action"] for e in timeline["events"]]
    assert "maintenance.create" in actions
    assert "maintenance.update" in actions
