"""
Tests for the Excel-replacement finance extensions: apartment-wise P&L,
utility readings, purchase register, daily cashbook, daily closing, and
advance tracking.
Run with: pytest tests/test_excel_replacement.py -v
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


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"ExcelProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"ExcelUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


def test_utility_reading_computes_consumption_and_amount(headers):
    unit_id = _make_unit(headers)
    resp = client.post("/api/v1/finance/utility-readings", headers=headers, json={
        "unit_id": unit_id, "utility_type": "electricity", "meter_number": "EB123",
        "reading_date": "2030-05-01", "opening_units": 100, "closing_units": 150, "rate_per_unit": 8.5,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["units_consumed"] == 50.0
    assert data["amount"] == 425.0


def test_utility_reading_rejects_closing_less_than_opening(headers):
    unit_id = _make_unit(headers)
    resp = client.post("/api/v1/finance/utility-readings", headers=headers, json={
        "unit_id": unit_id, "reading_date": "2030-05-01", "opening_units": 200, "closing_units": 100, "rate_per_unit": 8,
    })
    assert resp.status_code == 422


def test_purchase_register_create_and_list(headers):
    resp = client.post("/api/v1/finance/purchases", headers=headers, json={
        "purchase_date": "2030-06-01", "item_description": "Bedsheets and pillow covers", "amount": 3500, "supplier_name": "Local Textiles",
    })
    assert resp.status_code == 201
    listing = client.get("/api/v1/finance/purchases", headers=headers, params={"date_from": "2030-06-01", "date_to": "2030-06-01"}).json()
    assert any(p["item_description"] == "Bedsheets and pillow covers" for p in listing)


def test_purchase_creation_logs_audit(headers):
    resp = client.post("/api/v1/finance/purchases", headers=headers, json={"purchase_date": "2030-06-02", "item_description": "Audit test item", "amount": 100})
    purchase_id = resp.json()["id"]
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "finance.purchase.create"}).json()
    assert any(l["entity_id"] == purchase_id for l in logs)


def test_daily_cashbook_reflects_payment_and_expense(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Cashbook Guest", "phone": f"96{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = (date.today() + timedelta(days=50)).isoformat()
    check_out = (date.today() + timedelta(days=52)).isoformat()
    booking_resp = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in, "check_out": check_out})
    assert booking_resp.status_code == 201, booking_resp.text
    booking = booking_resp.json()

    today = date.today().isoformat()
    payment_resp = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 2000, "mode": "cash", "reference_no": "CASHBOOKTEST"})
    assert payment_resp.status_code == 201, payment_resp.text
    expense_resp = client.post("/api/v1/finance/expenses", headers=headers, json={"category": "electricity", "amount": 500, "expense_date": today})
    assert expense_resp.status_code == 201, expense_resp.text
    expense_payment_resp = client.post(
        f"/api/v1/finance/expenses/{expense_resp.json()['id']}/payments",
        headers={**headers, "Idempotency-Key": f"cashbook-{uuid.uuid4().hex}"},
        json={"amount": 500, "payment_date": today, "payment_method": "cash"},
    )
    assert expense_payment_resp.status_code == 201, expense_payment_resp.text

    cashbook = client.get("/api/v1/finance/cashbook", headers=headers, params={"cashbook_date": today}).json()
    descriptions = [l["description"] for l in cashbook["lines"]]
    assert any("Payment received" in d for d in descriptions)
    assert any("Expense payment" in d for d in descriptions)
    assert cashbook["total_in"] >= 2000.0
    assert cashbook["total_out"] >= 500.0


def test_daily_cashbook_running_balance_is_correct(headers):
    """
    Opening balance for a date with no closing yet must equal whatever the
    most recent PRIOR closing's balance is — which is 0 only when no
    closing has ever been recorded before that date. On this long-lived
    shared test database, other tests (and manual validation runs) do
    create real closings, so this asserts the actual invariant rather than
    hardcoding "always zero", which only held in a pristine, empty DB.
    """
    far_future_date = (date.today() + timedelta(days=5000)).isoformat()
    prior_closings = client.get("/api/v1/finance/daily-closing", headers=headers, params={"date_to": far_future_date}).json()
    expected_opening = 0.0
    if prior_closings:
        most_recent = max(prior_closings, key=lambda c: c["closing_date"])
        expected_opening = most_recent["closing_balance"]

    cashbook = client.get("/api/v1/finance/cashbook", headers=headers, params={"cashbook_date": far_future_date}).json()
    assert cashbook["opening_balance"] == expected_opening
    assert cashbook["closing_balance"] == cashbook["opening_balance"] + cashbook["total_in"] - cashbook["total_out"]


def test_daily_closing_create_and_duplicate_rejected(headers):
    closing_date = (date.today() + timedelta(days=1000 + uuid.uuid4().int % 3000)).isoformat()
    resp = client.post("/api/v1/finance/daily-closing", headers=headers, json={"closing_date": closing_date, "opening_balance": 5000, "notes": "Test closing"})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["opening_balance"] == 5000.0
    assert data["closing_balance"] == 5000.0 + data["total_cash_in"] - data["total_cash_out"]

    dup = client.post("/api/v1/finance/daily-closing", headers=headers, json={"closing_date": closing_date, "opening_balance": 6000})
    assert dup.status_code == 409


def test_daily_closing_becomes_next_days_opening_balance(headers):
    base_offset = 4100 + uuid.uuid4().int % 3000
    closing_date = (date.today() + timedelta(days=base_offset)).isoformat()
    next_date = (date.today() + timedelta(days=base_offset + 1)).isoformat()

    closing = client.post("/api/v1/finance/daily-closing", headers=headers, json={"closing_date": closing_date, "opening_balance": 1000}).json()

    next_day_cashbook = client.get("/api/v1/finance/cashbook", headers=headers, params={"cashbook_date": next_date}).json()
    assert next_day_cashbook["opening_balance"] == closing["closing_balance"]


def test_apartment_pnl_computes_revenue_and_unit_specific_expenses(headers):
    unit_id = _make_unit(headers, rate=2000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "PnL Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2031, 3
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-04",
    })

    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "maintenance", "amount": 800, "expense_date": f"{year}-{month:02d}-10", "unit_id": unit_id})
    client.post("/api/v1/finance/utility-readings", headers=headers, json={
        "unit_id": unit_id, "utility_type": "electricity", "reading_date": f"{year}-{month:02d}-15",
        "opening_units": 0, "closing_units": 40, "rate_per_unit": 10,
    })

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert pnl["revenue"] == 6000.0
    assert pnl["total_expenses"] == 800.0 + 400.0
    assert pnl["net_profit"] == 6000.0 - 1200.0


def test_apartment_pnl_excludes_central_expenses_with_no_unit(headers):
    unit_id = _make_unit(headers)
    year, month = 2032, 4
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "salary", "amount": 50000, "expense_date": f"{year}-{month:02d}-05"})

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert pnl["total_expenses"] == 0.0


def test_apartment_pnl_unknown_unit_404s(headers):
    resp = client.get(f"/api/v1/finance/apartment-pnl/{uuid.uuid4()}", headers=headers, params={"period_year": 2030, "period_month": 1})
    assert resp.status_code == 404


def test_advance_summary_lists_partially_paid_booking(headers):
    unit_id = _make_unit(headers, rate=2000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Advance Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = (date.today() + timedelta(days=700)).isoformat()
    check_out = (date.today() + timedelta(days=703)).isoformat()
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in, "check_out": check_out}).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1500, "mode": "upi", "reference_no": "ADVTEST"})

    advances = client.get("/api/v1/finance/advances", headers=headers, params={"limit": 5000}).json()
    match = next((a for a in advances if a["booking_id"] == booking["id"]), None)
    assert match is not None
    assert match["advance_paid"] == 1500.0
    assert match["total_due"] == 6000.0
    assert match["balance_remaining"] == 4500.0


def test_advance_summary_excludes_fully_paid_booking(headers):
    unit_id = _make_unit(headers, rate=2000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Full Pay Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = (date.today() + timedelta(days=710)).isoformat()
    check_out = (date.today() + timedelta(days=712)).isoformat()
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in, "check_out": check_out}).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 4000, "mode": "upi", "reference_no": "FULLADVTEST"})

    advances = client.get("/api/v1/finance/advances", headers=headers, params={"limit": 5000}).json()
    assert not any(a["booking_id"] == booking["id"] for a in advances)


def test_unauthenticated_cannot_access_new_finance_endpoints():
    assert client.get("/api/v1/finance/cashbook").status_code == 401
    assert client.get("/api/v1/finance/advances").status_code == 401
    assert client.post("/api/v1/finance/purchases", json={}).status_code == 401


def test_utility_reading_manual_allocation(headers):
    unit_a = _make_unit(headers)
    unit_b = _make_unit(headers)
    resp = client.post("/api/v1/finance/utility-readings", headers=headers, json={
        "utility_type": "internet", "reading_date": "2030-07-01",
        "opening_units": 0, "closing_units": 1, "rate_per_unit": 1000,
        "manual_allocations": [{"unit_id": unit_a, "share_amount": 600}, {"unit_id": unit_b, "share_amount": 400}],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["amount"] == 1000.0
    allocations = {a["unit_id"]: a["share_amount"] for a in data["allocations"]}
    assert allocations[unit_a] == 600.0
    assert allocations[unit_b] == 400.0


def test_utility_reading_manual_allocation_must_sum_to_total(headers):
    unit_a = _make_unit(headers)
    resp = client.post("/api/v1/finance/utility-readings", headers=headers, json={
        "utility_type": "internet", "reading_date": "2030-07-01",
        "opening_units": 0, "closing_units": 1, "rate_per_unit": 1000,
        "manual_allocations": [{"unit_id": unit_a, "share_amount": 100}],
    })
    assert resp.status_code == 400


def test_apartment_pnl_includes_purchases(headers):
    """
    Regression test for a real bug found during manual end-to-end
    validation: apartment_pnl originally only counted Expense and
    UtilityReading records, silently omitting PurchaseRecord entries
    tagged to the same unit — meaning "Purchase Cost" (an explicit
    requirement) never actually reduced the apartment's profit figure.
    """
    unit_id = _make_unit(headers, rate=3000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "PnL Purchase Guest", "phone": f"89{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2035, 3
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-05",
    })
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "maintenance", "amount": 500, "expense_date": f"{year}-{month:02d}-10", "unit_id": unit_id})
    client.post("/api/v1/finance/utility-readings", headers=headers, json={
        "unit_id": unit_id, "utility_type": "electricity", "reading_date": f"{year}-{month:02d}-10", "opening_units": 0, "closing_units": 50, "rate_per_unit": 9,
    })
    client.post("/api/v1/finance/purchases", headers=headers, json={"purchase_date": f"{year}-{month:02d}-10", "item_description": "Bedsheets", "amount": 300, "unit_id": unit_id})

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert pnl["revenue"] == 12000.0
    purchases_line = next((l for l in pnl["expense_lines"] if l["category"] == "purchases"), None)
    assert purchases_line is not None, "PurchaseRecord entries must appear in apartment P&L expense lines"
    assert purchases_line["amount"] == 300.0
    assert pnl["total_expenses"] == 1250.0
    assert pnl["net_profit"] == 10750.0


def test_apartment_pnl_includes_shared_utility_allocation(headers):
    """A bill shared across multiple units via UtilityReadingAllocation
    must count its allocated share in each unit's own P&L, not just
    bills billed directly to unit_id."""
    unit_a = _make_unit(headers, rate=2000)
    unit_b = _make_unit(headers, rate=2000)
    year, month = 2035, 4
    client.post("/api/v1/finance/utility-readings", headers=headers, json={
        "utility_type": "internet", "reading_date": f"{year}-{month:02d}-05",
        "opening_units": 0, "closing_units": 1, "rate_per_unit": 1000,
        "manual_allocations": [{"unit_id": unit_a, "share_amount": 600}, {"unit_id": unit_b, "share_amount": 400}],
    })

    pnl_a = client.get(f"/api/v1/finance/apartment-pnl/{unit_a}", headers=headers, params={"period_year": year, "period_month": month}).json()
    pnl_b = client.get(f"/api/v1/finance/apartment-pnl/{unit_b}", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert pnl_a["total_expenses"] == 600.0
    assert pnl_b["total_expenses"] == 400.0
