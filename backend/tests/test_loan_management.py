"""
Tests for Phase 19C: Loan / EMI management — a controlled financial
recording/tracking capability layered on the existing finance module.

Covers: CRUD, RBAC (admin-only, unlike most of finance which is
admin+staff), idempotent/concurrent EMI-paid marking, audit logging, and
the isolation guarantees this phase is required to hold (no automatic
Expense/Payment/Cashbook mutation, P&L unchanged, AI Strategist unchanged,
Autonomous Actions unchanged).

Run with: pytest tests/test_loan_management.py -v
"""
import ast
import re
import threading
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

FINANCE_DIR = Path(__file__).resolve().parent.parent / "app" / "modules" / "finance"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def staff_headers(headers):
    email = f"loantest_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Loan Test Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"LoanProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"LoanUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return unit["id"]


def _make_loan(headers, emi_amount=25000, unit_id=None, loan_name=None):
    payload = {
        "loan_name": loan_name or f"Test Loan {uuid.uuid4().hex[:6]}",
        "lender_name": "Test Bank",
        "loan_type": "equipment_loan",
        "emi_amount": emi_amount,
        "start_date": "2024-06-01",
        "end_date": "2027-05-31",
    }
    if unit_id is not None:
        payload["unit_id"] = unit_id
    resp = client.post("/api/v1/finance/loans", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- RBAC: admin-only across reads and writes ---

def test_staff_cannot_create_loan(staff_headers):
    resp = client.post("/api/v1/finance/loans", headers=staff_headers, json={
        "loan_name": "Home Loan", "lender_name": "HDFC Bank", "emi_amount": 105000, "start_date": "2023-01-01",
    })
    assert resp.status_code == 403


def test_staff_cannot_list_loans(staff_headers):
    resp = client.get("/api/v1/finance/loans", headers=staff_headers)
    assert resp.status_code == 403


def test_staff_cannot_view_loan_summary(staff_headers):
    resp = client.get("/api/v1/finance/loans/summary", headers=staff_headers)
    assert resp.status_code == 403


def test_unauthenticated_cannot_access_loans():
    resp = client.get("/api/v1/finance/loans")
    assert resp.status_code == 401


def test_admin_can_create_and_read_loan(headers):
    loan = _make_loan(headers)
    resp = client.get(f"/api/v1/finance/loans/{loan['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["lender_name"] == "Test Bank"


# --- Model correctness: property-wide (unit_id null) vs unit-scoped ---

def test_loan_defaults_to_property_wide_unit_id_null(headers):
    loan = _make_loan(headers)
    assert loan["unit_id"] is None
    assert loan["status"] == "active"


def test_loan_can_be_scoped_to_a_unit(headers):
    unit_id = _make_unit(headers)
    loan = _make_loan(headers, unit_id=unit_id)
    assert loan["unit_id"] == unit_id


def test_loan_with_invalid_unit_id_rejected(headers):
    resp = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": "Bad Unit Loan", "lender_name": "Test Bank", "emi_amount": 5000,
        "start_date": "2024-01-01", "unit_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 404


def test_loan_principal_and_interest_rate_optional(headers):
    """The real Excel source data has no principal/interest columns — these
    must not be required, matching the Phase 19B 'do not invent data' finding."""
    resp = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": "No Principal Loan", "lender_name": "Test Bank", "emi_amount": 12000, "start_date": "2024-01-01",
    })
    assert resp.status_code == 201
    assert resp.json()["principal_amount"] is None
    assert resp.json()["interest_rate"] is None


# --- Validation ---

def test_loan_rejects_negative_emi(headers):
    resp = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": "Negative EMI Loan", "lender_name": "Test Bank", "emi_amount": -1000, "start_date": "2024-01-01",
    })
    assert resp.status_code == 422


def test_loan_rejects_negative_principal(headers):
    resp = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": "Negative Principal Loan", "lender_name": "Test Bank", "emi_amount": 1000,
        "principal_amount": -50000, "start_date": "2024-01-01",
    })
    assert resp.status_code == 422


def test_loan_rejects_end_date_before_start_date(headers):
    resp = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": "Bad Dates Loan", "lender_name": "Test Bank", "emi_amount": 1000,
        "start_date": "2024-06-01", "end_date": "2024-01-01",
    })
    assert resp.status_code == 422


def test_loan_creation_logs_audit(headers):
    loan = _make_loan(headers)
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.loan.create"}).json()
    assert any(l["entity_id"] == loan["id"] for l in logs)


# --- Status lifecycle ---

def test_update_loan_status_active_to_paid_off(headers):
    loan = _make_loan(headers)
    resp = client.patch(f"/api/v1/finance/loans/{loan['id']}", headers=headers, json={"status": "paid_off"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid_off"


def test_update_loan_rejects_invalid_status(headers):
    loan = _make_loan(headers)
    resp = client.patch(f"/api/v1/finance/loans/{loan['id']}", headers=headers, json={"status": "cancelled"})
    assert resp.status_code == 400


def test_closed_loan_status_is_terminal(headers):
    loan = _make_loan(headers)
    client.patch(f"/api/v1/finance/loans/{loan['id']}", headers=headers, json={"status": "closed"})
    resp = client.patch(f"/api/v1/finance/loans/{loan['id']}", headers=headers, json={"status": "active"})
    assert resp.status_code == 400


def test_loan_update_logs_audit(headers):
    loan = _make_loan(headers)
    client.patch(f"/api/v1/finance/loans/{loan['id']}", headers=headers, json={"emi_amount": 30000})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.loan.update"}).json()
    assert any(l["entity_id"] == loan["id"] for l in logs)


# --- EMI schedule ---

def test_create_emi_payment_due_record(headers):
    loan = _make_loan(headers)
    resp = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2026-09-05"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "due"
    assert body["emi_amount"] == loan["emi_amount"]


def test_emi_payment_defaults_amount_to_loan_emi(headers):
    loan = _make_loan(headers, emi_amount=17500)
    resp = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2026-10-05"})
    assert resp.json()["emi_amount"] == 17500


def test_duplicate_due_date_for_same_loan_rejected(headers):
    loan = _make_loan(headers)
    client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2026-11-05"})
    dup = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2026-11-05"})
    assert dup.status_code == 409


def test_emi_payment_for_nonexistent_loan_404(headers):
    resp = client.post(f"/api/v1/finance/loans/{uuid.uuid4()}/emi-payments", headers=headers, json={"due_date": "2026-11-05"})
    assert resp.status_code == 404


def test_list_emi_payments_for_loan(headers):
    loan = _make_loan(headers)
    client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2026-12-05"})
    resp = client.get(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers)
    assert resp.status_code == 200
    assert any(e["due_date"] == "2026-12-05" for e in resp.json())


# --- Mark paid: idempotency ---

def test_mark_emi_paid(headers):
    loan = _make_loan(headers)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2027-01-05"}).json()
    resp = client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={"paid_date": "2027-01-04"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"
    assert resp.json()["paid_date"] == "2027-01-04"


def test_mark_emi_paid_twice_is_idempotent_no_duplicate(headers):
    loan = _make_loan(headers)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2027-02-05"}).json()

    first = client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={"paid_date": "2027-02-04"})
    second = client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={"paid_date": "2027-02-20"})

    assert first.status_code == 200
    assert second.status_code == 200
    # The second call must NOT shift paid_date — repeating an already-applied
    # mark-paid returns the existing state, not a new payment event.
    assert second.json()["paid_date"] == "2027-02-04"

    all_emis = client.get(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers).json()
    matching = [e for e in all_emis if e["due_date"] == "2027-02-05"]
    assert len(matching) == 1


def test_mark_emi_paid_audit_logged(headers):
    loan = _make_loan(headers)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2027-03-05"}).json()
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.loan.emi_payment.mark_paid"}).json()
    assert any(l["entity_id"] == emi["id"] for l in logs)


def test_mark_emi_paid_for_nonexistent_emi_404(headers):
    resp = client.patch(f"/api/v1/finance/loans/emi-payments/{uuid.uuid4()}/mark-paid", headers=headers, json={})
    assert resp.status_code == 404


def test_staff_cannot_mark_emi_paid(headers, staff_headers):
    loan = _make_loan(headers)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2027-04-05"}).json()
    resp = client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=staff_headers, json={})
    assert resp.status_code == 403


# --- Concurrency ---

def test_concurrent_mark_emi_paid_exactly_one_effective_transition(headers):
    loan = _make_loan(headers)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2027-05-05"}).json()

    results = []

    def do_mark_paid(paid_date):
        results.append(client.patch(
            f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={"paid_date": paid_date},
        ))

    t1 = threading.Thread(target=do_mark_paid, args=("2027-05-01",))
    t2 = threading.Thread(target=do_mark_paid, args=("2027-05-31",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert all(r.status_code == 200 for r in results), [r.text for r in results]

    all_emis = client.get(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers).json()
    matching = [e for e in all_emis if e["due_date"] == "2027-05-05"]
    assert len(matching) == 1
    # Exactly one of the two paid_dates "won" the race; no corrupted/mixed state.
    assert matching[0]["paid_date"] in ("2027-05-01", "2027-05-31")


# --- Summary aggregation ---

def test_loan_summary_reflects_active_loan_and_emi_totals(headers):
    before = client.get("/api/v1/finance/loans/summary", headers=headers).json()
    loan = _make_loan(headers, emi_amount=40000)
    after = client.get("/api/v1/finance/loans/summary", headers=headers).json()

    assert after["total_active_loans"] == before["total_active_loans"] + 1
    assert after["total_monthly_emi_obligation"] >= before["total_monthly_emi_obligation"] + 40000


def test_loan_summary_counts_overdue_emi(headers):
    loan = _make_loan(headers)
    past_due = (date.today() - timedelta(days=10)).isoformat()
    client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": past_due})

    summary = client.get("/api/v1/finance/loans/summary", headers=headers).json()
    assert summary["overdue_emi_count"] >= 1


# --- Financial isolation: the core safety requirement of this phase ---

def _finance_snapshot(headers):
    # There is no flat "list all payments" endpoint in this codebase
    # (payments are only listable per-booking via /payments/{booking_id}),
    # so Expense count/total + Cashbook totals are the isolation proof here
    # — both are read from every Payment/Expense in range, so any stray
    # Payment or Expense row created by the loan endpoints would show up.
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"limit": 500}).json()
    cashbook = client.get("/api/v1/finance/cashbook", headers=headers).json()
    return {
        "expense_count": len(expenses),
        "expense_total": round(sum(e["amount"] for e in expenses), 2),
        "cashbook_total_in": cashbook["total_in"],
        "cashbook_total_out": cashbook["total_out"],
    }


def test_loan_creation_does_not_create_expense_or_change_cashbook(headers):
    before = _finance_snapshot(headers)
    _make_loan(headers, emi_amount=99999)
    after = _finance_snapshot(headers)
    assert after["expense_count"] == before["expense_count"]
    assert after["expense_total"] == before["expense_total"]
    assert after["cashbook_total_in"] == before["cashbook_total_in"]
    assert after["cashbook_total_out"] == before["cashbook_total_out"]


def test_mark_emi_paid_does_not_create_expense_or_change_cashbook(headers):
    loan = _make_loan(headers)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": "2027-06-05"}).json()

    before = _finance_snapshot(headers)
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})
    after = _finance_snapshot(headers)

    assert after["expense_count"] == before["expense_count"]
    assert after["expense_total"] == before["expense_total"]
    assert after["cashbook_total_in"] == before["cashbook_total_in"]
    assert after["cashbook_total_out"] == before["cashbook_total_out"]


def test_loan_activity_does_not_change_reports_summary_expenses(headers):
    today = date.today()
    date_from = (today - timedelta(days=5)).isoformat()
    date_to = (today + timedelta(days=5)).isoformat()

    before = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": date_from, "date_to": date_to}).json()
    loan = _make_loan(headers, emi_amount=55555)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": today.isoformat()}).json()
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})
    after = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": date_from, "date_to": date_to}).json()

    assert after["revenue"]["total"] == before["revenue"]["total"]
    assert after["maintenance_cost_total"] == before["maintenance_cost_total"]
    assert after["net_revenue_after_maintenance"] == before["net_revenue_after_maintenance"]


def test_loan_activity_does_not_change_apartment_or_property_pnl(headers):
    unit_id = _make_unit(headers)
    today = date.today()
    loan = _make_loan(headers, unit_id=unit_id, emi_amount=12345)

    before = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": today.isoformat()}).json()
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})
    after = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()

    assert after["total_expenses"] == before["total_expenses"]
    assert after["net_profit"] == before["net_profit"]


def test_ai_strategist_endpoints_still_healthy_after_loan_activity(headers):
    loan = _make_loan(headers)
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": date.today().isoformat()}).json()
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})

    for path in ("/api/v1/reports/ai-strategist", "/api/v1/reports/ai-strategist/decisions", "/api/v1/reports/ai-strategist/narrative"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


# --- Static isolation checks (source scan, mirrors test_staff_operations.py's pattern) ---

def test_finance_module_does_not_reference_ai_strategist_or_narrative():
    forbidden = ("ai_strategist", "narrative_provider", "narrative_service", "decision_engine")
    for path in FINANCE_DIR.glob("*.py"):
        source = path.read_text()
        for term in forbidden:
            assert term not in source, f"{path.name} unexpectedly references '{term}'"


def test_finance_module_does_not_import_automation_submodules():
    forbidden_modules = (
        "automation.policy_engine", "automation.action_registry", "automation.action_executor",
        "automation.dispatcher", "automation.signal_generator", "automation.governance",
    )
    for path in FINANCE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        for forbidden in forbidden_modules:
            assert not any(forbidden in m for m in imported), f"{path.name} imports {forbidden}"


def test_finance_module_automation_constants_absent():
    automation_dir = FINANCE_DIR.parent / "automation"
    if not automation_dir.exists():
        pytest.skip("automation module not present in this checkout")
    for path in FINANCE_DIR.glob("*.py"):
        source = path.read_text()
        assert "GLOBAL_AUTOMATION_ENABLED" not in source
        assert "RELEASE_EXPIRED_HOLD" not in source
        assert "NOTIFY_STAFF" not in source


def test_loan_router_creates_no_expense_payment_invoice_records():
    """Static check on the new loan endpoints specifically: no code path in
    the loan create/update/emi-payment/mark-paid section should construct
    an Expense(...), Payment(...), or Invoice(...) object. Uses a
    word-boundary regex (not plain substring) so this doesn't false-positive
    on LoanEmiPayment( — the module's own new, isolated table."""
    source = (FINANCE_DIR / "router.py").read_text()
    loan_section_start = source.index("# --- Loans / EMI (Phase 19C) ---")
    loan_section = source[loan_section_start:]
    for forbidden_construct in ("Expense", "Payment", "Invoice"):
        pattern = r"(?<![A-Za-z0-9_])" + forbidden_construct + r"\("
        assert not re.search(pattern, loan_section), f"Loan section unexpectedly constructs {forbidden_construct}("
