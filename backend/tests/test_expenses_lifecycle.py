"""Focused local API tests for expense mutation and approval safety."""
import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.modules.finance import router as finance_router
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

client = TestClient(app)


def _headers() -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create(headers: dict[str, str], status: str = "pending") -> dict:
    response = client.post("/api/v1/finance/expenses", headers=headers, json={
        "category": "Lifecycle Test", "amount": "125.50", "expense_date": date.today().isoformat(),
        "notes": "initial", "approval_status": status,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_expense_create_list_and_amount_validation():
    headers = _headers()
    created = _create(headers)
    listed = client.get("/api/v1/finance/expenses", headers=headers)
    assert listed.status_code == 200
    assert any(row["id"] == created["id"] for row in listed.json())
    created_audit = client.get("/api/v1/audit/logs", headers=headers, params={
        "action": "finance.expense.create", "entity_id": created["id"],
    })
    assert len(created_audit.json()) == 1
    for amount in (0, -1, "-0.01"):
        response = client.post("/api/v1/finance/expenses", headers=headers, json={
            "category": "Invalid", "amount": amount, "expense_date": date.today().isoformat(),
        })
        assert response.status_code == 422


def test_pending_expense_can_be_edited_and_audited_once():
    headers = _headers()
    expense = _create(headers)
    response = client.patch(f"/api/v1/finance/expenses/{expense['id']}", headers=headers, json={
        "category": "Updated", "amount": "300.25", "expense_date": date.today().isoformat(),
        "unit_id": None, "notes": "corrected",
    })
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 300.25
    audit = client.get("/api/v1/audit/logs", headers=headers, params={
        "action": "finance.expense.edit", "entity_id": expense["id"],
    })
    assert audit.status_code == 200
    assert len(audit.json()) == 1
    assert audit.json()[0]["previous_value"] and audit.json()[0]["new_value"]


def test_approval_is_idempotent_and_rejection_is_terminal():
    headers = _headers()
    expense = _create(headers)
    original_trigger = finance_router.trigger_expense_approval
    finance_router.trigger_expense_approval = lambda *args, **kwargs: None
    try:
        path = f"/api/v1/finance/expenses/{expense['id']}/approval"
        approved = client.post(path, headers=headers, json={"approve": True})
        repeated = client.post(path, headers=headers, json={"approve": True})
        assert approved.status_code == repeated.status_code == 200
        assert repeated.json()["approval_status"] == "approved"
        assert client.post(path, headers=headers, json={"approve": False}).status_code == 409
    finally:
        finance_router.trigger_expense_approval = original_trigger
    audit = client.get("/api/v1/audit/logs", headers=headers, params={
        "action": "finance.expense.approved", "entity_id": expense["id"],
    })
    assert len(audit.json()) == 1


def test_rejected_expense_cannot_be_approved_or_edited(monkeypatch):
    headers = _headers()
    expense = _create(headers)
    monkeypatch.setattr(finance_router, "trigger_expense_approval", lambda *args, **kwargs: None)
    path = f"/api/v1/finance/expenses/{expense['id']}/approval"
    rejected = client.post(path, headers=headers, json={"approve": False})
    assert rejected.status_code == 200
    assert client.post(path, headers=headers, json={"approve": True}).status_code == 409
    response = client.patch(f"/api/v1/finance/expenses/{expense['id']}", headers=headers, json={
        "category": "Updated", "amount": "200", "expense_date": date.today().isoformat(),
        "notes": None,
    })
    assert response.status_code == 409


def test_pending_delete_is_audited_and_repeat_is_safe():
    headers = _headers()
    expense = _create(headers)
    path = f"/api/v1/finance/expenses/{expense['id']}"
    assert client.delete(path, headers=headers).status_code == 204
    assert client.delete(path, headers=headers).status_code == 204
    audit = client.get("/api/v1/audit/logs", headers=headers, params={
        "action": "finance.expense.delete", "entity_id": expense["id"],
    })
    assert len(audit.json()) == 1


def test_approved_expense_cannot_be_edited_or_deleted_and_auth_is_required():
    headers = _headers()
    expense = _create(headers, status="approved")
    edit = client.patch(f"/api/v1/finance/expenses/{expense['id']}", headers=headers, json={
        "category": "Changed", "amount": "125", "expense_date": date.today().isoformat(),
        "notes": None,
    })
    assert edit.status_code == 409
    assert client.delete(f"/api/v1/finance/expenses/{expense['id']}", headers=headers).status_code == 409
    assert client.post("/api/v1/finance/expenses", json={}).status_code == 401
    assert client.delete(f"/api/v1/finance/expenses/{uuid.uuid4()}").status_code == 401


def test_staff_cannot_mutate_expenses():
    admin_headers = _headers()
    expense = _create(admin_headers)
    # Not @example.test: that's an IANA reserved special-use TLD and the
    # email-validator dependency now rejects it outright (unrelated to this
    # test's own logic) — matching the working domain convention used
    # elsewhere in this suite, e.g. test_expense_payment_ledger.py's _new_user.
    email = f"expense_staff_{uuid.uuid4().hex[:10]}@sriapartmenthomestay.com"
    registered = client.post("/api/v1/auth/register", headers=admin_headers, json={
        "name": "Expense Staff", "email": email, "password": "ExpensePass123!", "role": "staff",
    })
    assert registered.status_code in (200, 201), registered.text
    logged_in = client.post("/api/v1/auth/login", json={"email": email, "password": "ExpensePass123!"})
    assert logged_in.status_code == 200
    staff_headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    assert client.post("/api/v1/finance/expenses", headers=staff_headers, json={}).status_code == 403
    assert client.post(f"/api/v1/finance/expenses/{expense['id']}/approval", headers=staff_headers, json={"approve": True}).status_code == 403
    assert client.patch(f"/api/v1/finance/expenses/{expense['id']}", headers=staff_headers, json={}).status_code == 403
    assert client.delete(f"/api/v1/finance/expenses/{expense['id']}", headers=staff_headers).status_code == 403


def test_audit_failure_rolls_back_expense_create(monkeypatch):
    headers = _headers()
    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(finance_router, "record_audit_log", fail_audit)
    isolated_client = TestClient(app, raise_server_exceptions=False)
    response = isolated_client.post("/api/v1/finance/expenses", headers=headers, json={
        "category": "Rollback Test", "amount": "9.99", "expense_date": date.today().isoformat(),
    })
    assert response.status_code == 500
    monkeypatch.undo()
    rows = client.get("/api/v1/finance/expenses", headers=headers, params={"category": "Rollback Test"})
    assert not any(row["category"] == "Rollback Test" for row in rows.json())
