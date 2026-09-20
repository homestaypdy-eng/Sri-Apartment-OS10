"""
Tests for the Finance module extensions: GST Dashboard, Security Deposit
Ledger, Owner Withdrawal Ledger, OTA Commission Tracking, Outstanding
Payments, Cash Flow, Budget vs Actual, and audit trail coverage.
Run with: pytest tests/test_finance_module.py -v
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


def _make_booking(headers, rate=2000, nights=2):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"FinProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"FinUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Finance Test Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=100 + uuid.uuid4().int % 200)
    check_out = check_in + timedelta(days=nights)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit["id"], "guest_id": guest["id"],
        "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    }).json()
    return booking


def test_create_expense_accepts_custom_category(headers):
    """
    Custom categories are an explicit business requirement — expense
    categories are free text, not restricted to a fixed list. This test
    used to assert the opposite (a 400 on an unrecognized category); that
    behavior was intentionally removed, so this test now asserts the
    correct, current behavior instead.
    """
    resp = client.post("/api/v1/finance/expenses", headers=headers, json={"category": "Pest Control", "amount": 500, "expense_date": "2026-06-01"})
    assert resp.status_code == 201
    assert resp.json()["category"] == "Pest Control"


def test_create_and_list_expense(headers):
    resp = client.post("/api/v1/finance/expenses", headers=headers, json={"category": "electricity", "amount": 3500, "expense_date": "2026-06-15", "notes": "June bill"})
    assert resp.status_code == 201
    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"category": "electricity"}).json()
    assert any(e["notes"] == "June bill" for e in expenses)


def test_expense_creation_logs_audit(headers):
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "internet", "amount": 1200, "expense_date": "2026-06-01"})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.expense.create"}).json()
    assert len(logs) >= 1


def test_budget_creation_and_duplicate_rejected(headers):
    unique_year = 2100 + uuid.uuid4().int % 1900
    resp = client.post("/api/v1/finance/budgets", headers=headers, json={"category": "laundry", "period_year": unique_year, "period_month": 5, "budgeted_amount": 5000})
    assert resp.status_code == 201
    dup = client.post("/api/v1/finance/budgets", headers=headers, json={"category": "laundry", "period_year": unique_year, "period_month": 5, "budgeted_amount": 6000})
    assert dup.status_code == 409


def test_budget_vs_actual_computes_variance_correctly(headers):
    unique_year = 4100 + uuid.uuid4().int % 1900
    client.post("/api/v1/finance/budgets", headers=headers, json={"category": "maintenance", "period_year": unique_year, "period_month": 3, "budgeted_amount": 10000})
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "maintenance", "amount": 6000, "expense_date": f"{unique_year}-03-10"})
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "maintenance", "amount": 2000, "expense_date": f"{unique_year}-03-20"})

    report = client.get("/api/v1/finance/budget-vs-actual", headers=headers, params={"period_year": unique_year, "period_month": 3}).json()
    line = next(l for l in report["lines"] if l["category"] == "maintenance")
    assert line["budgeted_amount"] == 10000.0
    assert line["actual_amount"] == 8000.0
    assert line["variance"] == 2000.0
    assert line["variance_percent"] == 20.0


def test_deposit_not_counted_as_revenue(headers):
    booking = _make_booking(headers, rate=2000, nights=2)
    check_in = booking["check_in"]
    check_out = booking["check_out"]

    before = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": check_in, "date_to": check_out}).json()
    client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 50000, "notes": "Large deposit test"})
    after = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": check_in, "date_to": check_out}).json()
    assert after["revenue"]["total"] == before["revenue"]["total"]


def test_deposit_lifecycle_full_refund(headers):
    booking = _make_booking(headers)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 5000}).json()
    assert deposit["status"] == "held"
    assert deposit["pending_refund_amount"] == 5000.0

    refund = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 5000})
    assert refund.status_code == 200
    assert refund.json()["status"] == "refunded"
    assert refund.json()["pending_refund_amount"] == 0.0


def test_deposit_partial_refund(headers):
    booking = _make_booking(headers)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 5000}).json()
    refund = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 2000})
    assert refund.json()["status"] == "partially_refunded"
    assert refund.json()["pending_refund_amount"] == 3000.0


def test_deposit_refund_cannot_exceed_original_amount(headers):
    booking = _make_booking(headers)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 1000}).json()
    resp = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 5000})
    assert resp.status_code == 400


def test_pending_refunds_list_excludes_fully_refunded(headers):
    booking = _make_booking(headers)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 1000}).json()
    client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 1000})

    pending = client.get("/api/v1/finance/deposits/pending-refund", headers=headers).json()
    assert not any(d["id"] == deposit["id"] for d in pending)


def test_deposit_refund_logs_audit(headers):
    booking = _make_booking(headers)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 1000}).json()
    client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 500})

    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.deposit.refund"}).json()
    assert any(l["entity_id"] == deposit["id"] for l in logs)


def test_owner_withdrawal_not_deducted_from_revenue(headers):
    booking = _make_booking(headers, rate=2000, nights=2)
    check_in = booking["check_in"]
    check_out = booking["check_out"]
    before = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": check_in, "date_to": check_out}).json()

    client.post("/api/v1/finance/owner-withdrawals", headers=headers, json={"amount": 20000, "withdrawal_date": check_in, "notes": "Personal drawing"})

    after = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": check_in, "date_to": check_out}).json()
    assert after["revenue"]["total"] == before["revenue"]["total"]


def test_owner_withdrawal_appears_in_cash_flow_as_cash_out(headers):
    withdrawal_date = (date.today() + timedelta(days=500)).isoformat()
    client.post("/api/v1/finance/owner-withdrawals", headers=headers, json={"amount": 15000, "withdrawal_date": withdrawal_date, "notes": "test"})

    cash_flow = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": withdrawal_date, "date_to": withdrawal_date}).json()
    assert cash_flow["cash_out_withdrawals"] >= 15000.0


def test_staff_cannot_create_owner_withdrawal():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"finstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=admin_headers, json={"name": "Fin Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.post("/api/v1/finance/owner-withdrawals", headers=staff_headers, json={"amount": 1000, "withdrawal_date": "2026-06-01"})
    assert resp.status_code == 403


def test_ota_commission_creation_and_duplicate_rejected(headers):
    booking = _make_booking(headers)
    resp = client.post("/api/v1/finance/ota-commissions", headers=headers, json={
        "booking_id": booking["id"], "channel": "booking_com", "commission_percent": 15, "commission_amount": 600, "ota_gst_amount": 108,
    })
    assert resp.status_code == 201
    dup = client.post("/api/v1/finance/ota-commissions", headers=headers, json={"booking_id": booking["id"], "channel": "airbnb", "commission_percent": 10})
    assert dup.status_code == 409


def test_ota_commission_invalid_channel_rejected(headers):
    booking = _make_booking(headers)
    resp = client.post("/api/v1/finance/ota-commissions", headers=headers, json={"booking_id": booking["id"], "channel": "expedia"})
    assert resp.status_code == 400


def test_ota_commission_report_groups_by_channel(headers):
    booking = _make_booking(headers, rate=2000, nights=3)
    client.post("/api/v1/finance/ota-commissions", headers=headers, json={
        "booking_id": booking["id"], "channel": "airbnb", "commission_percent": 12, "commission_amount": 720, "ota_gst_amount": 129.6,
    })
    report = client.get("/api/v1/finance/ota-commissions/report", headers=headers, params={"date_from": booking["check_in"], "date_to": booking["check_in"]}).json()
    airbnb_line = next((l for l in report["by_channel"] if l["channel"] == "airbnb"), None)
    assert airbnb_line is not None
    assert airbnb_line["total_commission"] >= 720.0


def test_outstanding_payments_lists_unpaid_booking(headers):
    booking = _make_booking(headers, rate=2000, nights=2)
    outstanding = client.get("/api/v1/finance/outstanding-payments", headers=headers, params={"limit": 1000}).json()
    assert any(o["booking_id"] == booking["id"] for o in outstanding)
    line = next(o for o in outstanding if o["booking_id"] == booking["id"])
    assert line["pending_balance"] == 4000.0
    assert line["is_overdue"] is False


def test_outstanding_payments_excludes_fully_paid_booking(headers):
    booking = _make_booking(headers, rate=2000, nights=2)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 4000, "mode": "upi", "reference_no": "FULLPAY"})
    outstanding = client.get("/api/v1/finance/outstanding-payments", headers=headers, params={"limit": 1000}).json()
    assert not any(o["booking_id"] == booking["id"] for o in outstanding)


def test_due_date_and_overdue_flag(headers):
    booking = _make_booking(headers, rate=2000, nights=2)
    past_due_date = (date.today() - timedelta(days=5)).isoformat()
    resp = client.patch(f"/api/v1/finance/bookings/{booking['id']}/due-date", headers=headers, params={"due_date": past_due_date})
    assert resp.status_code == 200

    outstanding = client.get("/api/v1/finance/outstanding-payments", headers=headers, params={"limit": 1000}).json()
    line = next(o for o in outstanding if o["booking_id"] == booking["id"])
    assert line["due_date"] == past_due_date
    assert line["is_overdue"] is True


def test_cash_flow_bank_vs_hand_split(headers):
    booking = _make_booking(headers, rate=2000, nights=2)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "cash", "reference_no": "CASHTEST"})
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 3000, "mode": "upi", "reference_no": "UPITEST"})

    flow = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": date.today().isoformat(), "date_to": date.today().isoformat()}).json()
    assert flow["cash_in_hand"] >= 1000.0
    assert flow["cash_in_bank"] >= 3000.0


def test_cash_flow_net_calculation(headers):
    d = (date.today() + timedelta(days=600)).isoformat()
    client.post("/api/v1/finance/owner-withdrawals", headers=headers, json={"amount": 500, "withdrawal_date": d})
    flow = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": d, "date_to": d}).json()
    assert flow["net_cash_flow"] == round(flow["cash_in_total"] - flow["cash_out_total"], 2)


def test_gst_dashboard_payable_equals_collected_minus_itc(headers):
    booking = _make_booking(headers, rate=10000, nights=1)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 11800, "mode": "upi", "reference_no": "GSTTEST"})
    client.post(f"/api/v1/invoices/generate/{booking['id']}", headers=headers)

    dashboard = client.get("/api/v1/finance/gst-dashboard", headers=headers, params={"date_from": date.today().isoformat(), "date_to": date.today().isoformat()}).json()
    assert dashboard["gst_payable"] == round(dashboard["gst_collected"] - dashboard["gst_input_credit_eligible"], 2)


def test_unauthenticated_cannot_access_finance_endpoints():
    assert client.get("/api/v1/finance/gst-dashboard").status_code == 401
    assert client.get("/api/v1/finance/cash-flow").status_code == 401
    assert client.post("/api/v1/finance/expenses", json={}).status_code == 401
