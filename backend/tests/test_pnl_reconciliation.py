"""
Phase 19D: regression tests for the canonical P&L formula changes approved
in Phase 19D-A / 19D:

- extra_guest_charge included in apartment_pnl revenue (Decision 1)
- central/property-wide expenses included in property_pnl, never allocated
  to individual apartments (Decision 2)
- full EMI / loan principal never deducted from Net Profit (Decision 3)
- maintenance cost read from the canonical Finance Expense record, not
  MaintenanceTicket.cost directly (Decision 4)
- OTA commission + OTA GST subtracted exactly once, only where an
  OTACommission record actually exists — never invented (Decision 5)
- owner withdrawals / security deposits never touch P&L (unchanged,
  guarded here as a regression check)

Run with: pytest tests/test_pnl_reconciliation.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Booking

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_property_and_unit(headers, rate=3000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"PnLProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"PnLUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return prop["id"], unit["id"]


def _make_booking(headers, unit_id, rate=3000, nights=2, days_offset=200):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "PnL Test Guest", "phone": f"96{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=nights)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
    }).json()
    return booking, check_in


def _set_extra_guest_charge(booking_id: str, amount: float):
    """extra_guest_charge has no write path anywhere in the API (confirmed
    by inspection — BookingCreate/BookingUpdate never expose it), so the
    only way to exercise this field at all is a direct DB write, same
    precedent as test_automation_governance.py's use of SessionLocal."""
    session = SessionLocal()
    try:
        booking = session.get(Booking, uuid.UUID(booking_id))
        booking.extra_guest_charge = amount
        session.commit()
    finally:
        session.close()


# --- Decision 1: extra_guest_charge included exactly once ---

def test_apartment_pnl_includes_extra_guest_charge_exactly_once(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=3000, nights=2)
    _set_extra_guest_charge(booking["id"], 500)

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    # 3000 * 2 nights + 500 extra guest charge = 6500, not 6000 (charge omitted) or 7000 (double-counted)
    assert pnl["revenue"] == 6500.0


def test_apartment_pnl_revenue_matches_reports_summary_formula(headers):
    """Cross-check: apartment_pnl and reports/build_summary must now agree
    on revenue for the identical booking — this is the actual
    reconciliation Decision 1 was meant to achieve. Scoped to this test's
    own property so it isn't polluted by other tests' bookings that happen
    to share the same default check_in date."""
    property_id, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=3000, nights=2)
    _set_extra_guest_charge(booking["id"], 500)

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    date_from = check_in.isoformat()
    date_to = (check_in + timedelta(days=1)).isoformat()
    summary = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": date_from, "date_to": date_to, "property_id": property_id}).json()

    assert pnl["revenue"] == summary["revenue"]["total"] == 6500.0


# --- Decision 2: central costs in property P&L, never allocated to apartments ---

def test_central_expense_included_in_property_pnl(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    client.post("/api/v1/finance/expenses", headers=headers, json={
        "category": "salary", "amount": 12000, "expense_date": today.isoformat(),
    })  # no unit_id => central

    prop_pnl = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    assert prop_pnl["central_expenses"] >= 12000.0
    assert prop_pnl["total_expenses"] >= 12000.0


def test_central_expense_not_allocated_to_any_apartment(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    client.post("/api/v1/finance/expenses", headers=headers, json={
        "category": "salary", "amount": 9999, "expense_date": today.isoformat(),
    })  # central, no unit_id

    apt_pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    categories = [l["category"] for l in apt_pnl["expense_lines"]]
    assert "salary" not in categories
    assert 9999.0 not in [l["amount"] for l in apt_pnl["expense_lines"]]


def test_property_pnl_equals_sum_of_apartments_plus_central(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    client.post("/api/v1/finance/expenses", headers=headers, json={
        "category": "internet", "amount": 1500, "expense_date": today.isoformat(),
    })

    prop_pnl = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    sum_of_units = round(sum(u["total_expenses"] for u in prop_pnl["units"]), 2)
    assert round(sum_of_units + prop_pnl["central_expenses"], 2) == prop_pnl["total_expenses"]


# --- Decision 3: full EMI / loan principal never deducted ---

def test_full_emi_paid_does_not_change_net_profit(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    booking, check_in = _make_booking(headers, unit_id, rate=3000, nights=2, days_offset=1)

    before_apt = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    before_prop = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()

    loan = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": "PnL Test Loan", "lender_name": "Test Bank", "loan_type": "equipment_loan",
        "emi_amount": 25000, "start_date": today.isoformat(),
    }).json()
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": today.isoformat()}).json()
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})

    after_apt = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    after_prop = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()

    assert after_apt["net_profit"] == before_apt["net_profit"]
    assert after_prop["net_profit"] == before_prop["net_profit"]
    assert after_prop["total_expenses"] == before_prop["total_expenses"]


def test_loan_interest_never_deducted_because_no_data_exists(headers):
    """Documents the current, truthful state: Loan.interest_rate can be
    set, but nothing reads it into any P&L calculation, because no
    per-installment interest figure exists anywhere to subtract. This is
    the correct implementation of 'deduct interest only when known' given
    today's schema — 'known' is currently never true."""
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    before = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()

    client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": "Interest Rate Set Loan", "lender_name": "Test Bank", "loan_type": "home_loan",
        "emi_amount": 10000, "interest_rate": 9.5, "principal_amount": 500000, "start_date": today.isoformat(),
    })

    after = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    assert after["net_profit"] == before["net_profit"]
    assert after["total_expenses"] == before["total_expenses"]


# --- Decision 4: maintenance cost single canonical source ---

def test_maintenance_cost_not_double_counted_and_matches_reports(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={
        "unit_id": unit_id, "description": "PnL test maintenance",
    }).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 2500})

    apt_pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    maint_lines = [l for l in apt_pnl["expense_lines"] if l["category"] == "maintenance"]
    assert len(maint_lines) == 1
    assert maint_lines[0]["amount"] == 2500.0

    date_from = today.replace(day=1).isoformat()
    date_to = (today + timedelta(days=1)).isoformat()
    summary = client.get("/api/v1/reports/summary", headers=headers, params={"date_from": date_from, "date_to": date_to, "property_id": property_id}).json()
    assert summary["maintenance_cost_total"] == 2500.0


# --- Decision 5: OTA commission/GST subtracted exactly once, never invented ---

def test_ota_commission_and_gst_deducted_exactly_once(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=3000, nights=2)
    booking_total = 6000.0

    client.post("/api/v1/finance/ota-commissions", headers=headers, json={
        "booking_id": booking["id"], "channel": "booking_com",
        "commission_percent": 15, "commission_amount": 900, "ota_gst_amount": 162,
    })

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    ota_lines = [l for l in pnl["expense_lines"] if l["category"] == "ota_commission"]
    assert len(ota_lines) == 1
    assert ota_lines[0]["amount"] == 1062.0  # 900 + 162, once
    assert pnl["net_profit"] == round(booking_total - 1062.0, 2)

    # Calling it again must not accumulate/double the deduction.
    pnl_again = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    assert pnl_again["net_profit"] == pnl["net_profit"]


def test_missing_ota_commission_record_is_not_invented(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=3000, nights=2)
    # No OTACommission created for this booking.

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    categories = [l["category"] for l in pnl["expense_lines"]]
    assert "ota_commission" not in categories
    assert pnl["net_profit"] == 6000.0  # full revenue, nothing invented/estimated


# --- Regression guards: items that must NEVER touch P&L ---

def test_owner_withdrawal_not_treated_as_expense(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    before = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()

    client.post("/api/v1/finance/owner-withdrawals", headers=headers, json={"amount": 50000, "withdrawal_date": today.isoformat()})

    after = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    assert after["net_profit"] == before["net_profit"]
    assert after["total_expenses"] == before["total_expenses"]


def test_security_deposit_not_treated_as_profit(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=3000, nights=2)
    before = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()

    client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking["id"], "amount": 20000, "notes": "PnL test deposit"})

    after = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": check_in.year, "period_month": check_in.month}).json()
    assert after["revenue"] == before["revenue"]
    assert after["net_profit"] == before["net_profit"]


# --- AI Strategist / Autonomous Actions isolation (unchanged, guarded) ---

def test_ai_strategist_endpoints_still_healthy_after_pnl_changes(headers):
    for path in ("/api/v1/reports/ai-strategist", "/api/v1/reports/ai-strategist/decisions", "/api/v1/reports/ai-strategist/narrative"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


# =====================================================================
# Phase 19D-1: Cash Flow EMI integration + Property P&L central-expense
# display reconciliation (backend contract the frontend consumes).
# =====================================================================

def _make_loan_and_due_emi(headers, emi_amount, due_date):
    loan = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": f"19D1 Loan {uuid.uuid4().hex[:6]}", "lender_name": "Test Bank", "loan_type": "equipment_loan",
        "emi_amount": emi_amount, "start_date": due_date,
    }).json()
    emi = client.post(f"/api/v1/finance/loans/{loan['id']}/emi-payments", headers=headers, json={"due_date": due_date}).json()
    return loan, emi


def test_unpaid_emi_schedule_does_not_affect_cash_flow(headers):
    today = date.today()
    before = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()

    _make_loan_and_due_emi(headers, 17000, today.isoformat())  # created but never marked paid

    after = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()
    assert after["cash_out_loan_emi"] == before["cash_out_loan_emi"]
    assert after["cash_out_total"] == before["cash_out_total"]


def test_actual_emi_payment_increases_cash_out_by_exact_amount(headers):
    today = date.today()
    before = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()

    loan, emi = _make_loan_and_due_emi(headers, 17000, today.isoformat())
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})

    after = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()
    assert round(after["cash_out_loan_emi"] - before["cash_out_loan_emi"], 2) == 17000.0
    assert round(after["cash_out_total"] - before["cash_out_total"], 2) == 17000.0
    assert round(after["net_cash_flow"] - before["net_cash_flow"], 2) == -17000.0


def test_repeated_emi_mark_paid_does_not_double_count_in_cash_flow(headers):
    today = date.today()
    loan, emi = _make_loan_and_due_emi(headers, 9000, today.isoformat())
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})
    after_first = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()

    # Idempotent mark-paid (Phase 19C) — repeating it must not create a
    # second effective payment, and cash_flow (a pure read) must not
    # accumulate across repeated GETs either.
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})
    after_second = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()
    after_third_read = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()

    assert after_first["cash_out_loan_emi"] == after_second["cash_out_loan_emi"] == after_third_read["cash_out_loan_emi"]


def test_full_emi_affects_cash_flow_but_not_net_profit(headers):
    """The exact separation this phase exists to prove: one EMI payment,
    checked against both metrics in the same test."""
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()

    net_profit_before = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()["net_profit"]
    cash_out_before = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()["cash_out_total"]

    loan, emi = _make_loan_and_due_emi(headers, 21000, today.isoformat())
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})

    net_profit_after = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()["net_profit"]
    cash_out_after = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}).json()["cash_out_total"]

    assert net_profit_after == net_profit_before  # loan principal/EMI never touches Net Profit
    assert round(cash_out_after - cash_out_before, 2) == 21000.0  # but it's a real cash outflow


def test_loan_principal_never_becomes_operating_expense(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    expenses_before = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()["total_expenses"]

    loan, emi = _make_loan_and_due_emi(headers, 13500, today.isoformat())
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})

    expenses_after = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()["total_expenses"]
    assert expenses_after == expenses_before  # no Expense row, no P&L line — EMI never enters the expense side of P&L


def test_property_pnl_exposes_central_expenses_field_for_display(headers):
    """Backend contract the frontend's 'Central / Property-wide Expenses'
    line (Phase 19D-1 Part 2) consumes — the field must exist and increase
    by exactly the new central expense. Measured as a delta, not an
    absolute value: central_expenses is a date-range query with no
    property_id scoping (Expense has no property_id column — a documented
    Phase 19D limitation), so other test properties' central expenses
    created earlier in this same test run/month are also present in any
    single property_pnl call — a before/after delta on the SAME property
    is the only isolation-safe way to measure this."""
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    before = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()

    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "internet", "amount": 3300, "expense_date": today.isoformat()})

    after = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    assert "central_expenses" in after
    assert round(after["central_expenses"] - before["central_expenses"], 2) == 3300.0


def test_property_pnl_total_reconciles_after_emi_and_central_expense(headers):
    property_id, unit_id = _make_property_and_unit(headers)
    today = date.today()
    before = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()

    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "salary", "amount": 6000, "expense_date": today.isoformat()})
    loan, emi = _make_loan_and_due_emi(headers, 4000, today.isoformat())
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})

    after = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={"period_year": today.year, "period_month": today.month}).json()
    sum_of_units_after = round(sum(u["total_expenses"] for u in after["units"]), 2)
    # Internal consistency always holds regardless of other tests' leakage.
    assert round(sum_of_units_after + after["central_expenses"], 2) == after["total_expenses"]
    # The total increased by exactly the central expense (6000) — EMI
    # (4000) must NOT appear here even though it was paid in the same period.
    assert round(after["total_expenses"] - before["total_expenses"], 2) == 6000.0


def test_ai_strategist_healthy_after_cash_flow_emi_change(headers):
    for path in ("/api/v1/reports/ai-strategist", "/api/v1/reports/ai-strategist/decisions", "/api/v1/reports/ai-strategist/narrative"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def _action_proposals_count() -> int:
    from sqlalchemy import text
    session = SessionLocal()
    try:
        return session.execute(text("SELECT COUNT(*) FROM action_proposals")).scalar()
    finally:
        session.close()


def test_autonomous_actions_unchanged_after_emi_cash_flow_activity(headers):
    before = _action_proposals_count()
    loan, emi = _make_loan_and_due_emi(headers, 2500, date.today().isoformat())
    client.patch(f"/api/v1/finance/loans/emi-payments/{emi['id']}/mark-paid", headers=headers, json={})
    after = _action_proposals_count()
    assert after == before
