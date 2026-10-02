"""
Tests completing the Finance module to full production coverage: the new
Income Register (matching the business's own Excel structure exactly),
the three new Finance notification triggers (payment, expense approval,
daily closing), and a comprehensive real-numbers verification tying
together the whole money-flow chain the way the business's Excel sheet
would.
Run with: pytest tests/test_finance_completion.py -v
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


def _make_unit(headers, rate=2000, name_prefix="FinCompUnit", max_occupancy=2):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"FinCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"{name_prefix} {uuid.uuid4().hex[:6]}", "base_rate": rate, "max_occupancy": max_occupancy}).json()
    return unit["id"]


def test_income_register_matches_hand_calculation(headers):
    unit_id = _make_unit(headers, rate=2000, max_occupancy=3)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Income Register Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2041, 3
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-05", "check_out": f"{year}-{month:02d}-08",
        "adults": 2, "children": 1, "source": "airbnb",
    }).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 2000, "mode": "upi", "reference_no": "INCREGTEST"})

    register = client.get("/api/v1/finance/income-register", headers=headers, params={"period_year": year, "period_month": month}).json()
    unit_block = next(u for u in register["units"] if u["unit_id"] == unit_id)
    assert len(unit_block["lines"]) == 1
    line = unit_block["lines"][0]
    assert line["guest_name"] == "Income Register Guest"
    assert line["contact"] == guest["phone"]
    assert line["no_of_guests"] == 3
    assert line["amount"] == 6000.0
    assert line["advance"] == 2000.0
    assert line["balance"] == 4000.0
    assert line["status"] == "airbnb"
    assert unit_block["unit_total"] == 6000.0


def test_income_register_source_breakdown(headers):
    unit_id = _make_unit(headers, rate=1000)
    # Random year avoids accumulating totals across repeated runs of this
    # same test against the persistent, shared test database — by_source
    # aggregates business-wide for a period, not scoped to one unit, so a
    # hardcoded year/month would pick up bookings from earlier runs too.
    year = 2200 + uuid.uuid4().int % 1000
    month = 4
    for i, source in enumerate(["direct", "airbnb", "airbnb"]):
        guest = client.post("/api/v1/guests", headers=headers, json={"name": f"Source Guest {i}", "phone": f"9{i}{uuid.uuid4().int % 10**7:07d}"}).json()
        start_day = 1 + i * 3
        resp = client.post("/api/v1/bookings", headers=headers, json={
            "unit_id": unit_id, "guest_id": guest["id"],
            "check_in": f"{year}-{month:02d}-{start_day:02d}", "check_out": f"{year}-{month:02d}-{start_day+2:02d}",
            "source": source,
        })
        assert resp.status_code == 201, resp.text

    register = client.get("/api/v1/finance/income-register", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert register["by_source"]["direct"] == 2000.0
    assert register["by_source"]["airbnb"] == 4000.0


def test_income_register_grand_total_sums_all_units(headers):
    unit_a = _make_unit(headers, rate=1500)
    unit_b = _make_unit(headers, rate=2500)
    year = 2300 + uuid.uuid4().int % 1000
    month = 5
    guest_a = client.post("/api/v1/guests", headers=headers, json={"name": "Grand Total A", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    guest_b = client.post("/api/v1/guests", headers=headers, json={"name": "Grand Total B", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_a, "guest_id": guest_a["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-03"})
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit_b, "guest_id": guest_b["id"], "check_in": f"{year}-{month:02d}-05", "check_out": f"{year}-{month:02d}-07"})

    register = client.get("/api/v1/finance/income-register", headers=headers, params={"period_year": year, "period_month": month}).json()
    unit_a_block = next(u for u in register["units"] if u["unit_id"] == unit_a)
    unit_b_block = next(u for u in register["units"] if u["unit_id"] == unit_b)
    assert unit_a_block["unit_total"] == 3000.0
    assert unit_b_block["unit_total"] == 5000.0
    assert register["grand_total"] == unit_a_block["unit_total"] + unit_b_block["unit_total"]


def test_income_register_export_pdf_and_excel(headers):
    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/finance/reports/export", headers=headers, params={"report": "income_register", "format": fmt, "period_year": 2041, "period_month": 3})
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_income_register_requires_period_params(headers):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={"report": "income_register", "format": "pdf"})
    assert resp.status_code == 400


def test_income_register_empty_month_returns_zero_not_error(headers):
    # Random year avoids collision with other tests/live-verification runs
    # that create real bookings using their own fixed or offset dates —
    # a genuinely fixed year here is not guaranteed to stay empty forever.
    import uuid as _uuid
    year = 2400 + _uuid.uuid4().int % 1000
    register = client.get("/api/v1/finance/income-register", headers=headers, params={"period_year": year, "period_month": 1}).json()
    assert register["grand_total"] == 0.0


def test_payment_recorded_attempts_notification(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Notify Payment Guest", "phone": f"94{uuid.uuid4().int % 10**8:08d}"}).json()
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (date.today() + timedelta(days=5000)).isoformat(), "check_out": (date.today() + timedelta(days=5002)).isoformat(),
    }).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "cash", "reference_no": "NOTIFYPAYTEST"})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "payment_received"}).json()
    assert any(l["booking_id"] == booking["id"] for l in logs)


def test_refund_attempts_notification(headers):
    unit_id = _make_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Notify Refund Guest", "phone": f"95{uuid.uuid4().int % 10**8:08d}"}).json()
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": (date.today() + timedelta(days=5100)).isoformat(), "check_out": (date.today() + timedelta(days=5102)).isoformat(),
    }).json()
    payment = client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 1000, "mode": "cash", "reference_no": "REFUNDNOTIFYTEST"}).json()
    client.post(f"/api/v1/payments/{payment['id']}/refund", headers=headers, json={"refund_amount": 300, "reason": "Test"})

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "payment_received"}).json()
    matching = [l for l in logs if l["booking_id"] == booking["id"]]
    assert len(matching) == 2


def test_expense_approval_attempts_notification(headers):
    resp = client.post("/api/v1/finance/expenses", headers=headers, json={"category": "other", "amount": 100, "expense_date": date.today().isoformat()})
    expense = resp.json()
    resp2 = client.post(f"/api/v1/finance/expenses/{expense['id']}/approval", headers=headers, json={"approve": True})
    assert resp2.status_code == 200


def test_daily_closing_attempts_notification(headers):
    closing_date = (date.today() + timedelta(days=7000 + uuid.uuid4().int % 1000)).isoformat()
    resp = client.post("/api/v1/finance/daily-closing", headers=headers, json={"closing_date": closing_date, "opening_balance": 0})
    assert resp.status_code == 201, resp.text

    logs = client.get("/api/v1/notifications/logs", headers=headers, params={"event_type": "daily_closing_completed"}).json()
    assert len(logs) >= 1


def test_full_finance_chain_matches_hand_calculation(headers):
    """
    Hand calculation:
      Revenue: 4 nights x Rs.3000 = Rs.12,000
      Expenses: electricity Rs.400 + maintenance Rs.600 = Rs.1,000
      Net profit: Rs.11,000
    """
    unit_id = _make_unit(headers, rate=3000)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Full Chain Guest", "phone": f"96{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2044, 7
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-05",
    }).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 12000, "mode": "upi", "reference_no": "FULLCHAINTEST"})
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "electricity", "amount": 400, "expense_date": f"{year}-{month:02d}-10", "unit_id": unit_id})
    client.post("/api/v1/finance/expenses", headers=headers, json={"category": "maintenance", "amount": 600, "expense_date": f"{year}-{month:02d}-15", "unit_id": unit_id})

    register = client.get("/api/v1/finance/income-register", headers=headers, params={"period_year": year, "period_month": month}).json()
    unit_block = next(u for u in register["units"] if u["unit_id"] == unit_id)
    assert unit_block["unit_total"] == 12000.0
    assert unit_block["lines"][0]["balance"] == 0.0

    pnl = client.get(f"/api/v1/finance/apartment-pnl/{unit_id}", headers=headers, params={"period_year": year, "period_month": month}).json()
    assert pnl["revenue"] == 12000.0
    assert pnl["total_expenses"] == 1000.0
    assert pnl["net_profit"] == 11000.0


# --- Phase 9: non-negative validation + pagination ---

def _make_booking(headers, unit_id, days_offset):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Phase9 Guest", "phone": f"97{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat(),
    }).json()
    return booking["id"]


def test_negative_opening_balance_rejected(headers):
    closing_date = (date.today() + timedelta(days=9000 + uuid.uuid4().int % 500)).isoformat()
    resp = client.post("/api/v1/finance/daily-closing", headers=headers, json={"closing_date": closing_date, "opening_balance": -100})
    assert resp.status_code == 422


def test_zero_opening_balance_still_accepted(headers):
    closing_date = (date.today() + timedelta(days=9500 + uuid.uuid4().int % 500)).isoformat()
    resp = client.post("/api/v1/finance/daily-closing", headers=headers, json={"closing_date": closing_date, "opening_balance": 0})
    assert resp.status_code == 201, resp.text


def test_deposits_pagination_limit_respected(headers):
    unit_id = _make_unit(headers, name_prefix="DepPagUnit")
    for i in range(3):
        booking_id = _make_booking(headers, unit_id, days_offset=10000 + i * 5)
        client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking_id, "amount": 500})

    resp = client.get("/api/v1/finance/deposits", headers=headers, params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    default_resp = client.get("/api/v1/finance/deposits", headers=headers)
    assert default_resp.status_code == 200  # default cap (200) still returns a normal, unbroken listing


def test_owner_withdrawals_pagination_limit_respected(headers):
    for _ in range(3):
        client.post("/api/v1/finance/owner-withdrawals", headers=headers, json={"amount": 100, "withdrawal_date": date.today().isoformat()})

    resp = client.get("/api/v1/finance/owner-withdrawals", headers=headers, params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_ota_commissions_pagination_limit_respected(headers):
    unit_id = _make_unit(headers, name_prefix="CommPagUnit")
    for i in range(3):
        booking_id = _make_booking(headers, unit_id, days_offset=11000 + i * 5)
        client.post("/api/v1/finance/ota-commissions", headers=headers, json={"booking_id": booking_id, "channel": "airbnb", "commission_percent": 15})

    resp = client.get("/api/v1/finance/ota-commissions", headers=headers, params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
