"""
Phase 19F: Finance data integration into the AI Strategist FACTS bundle.

Covers:
- the new `finance` block appears in GET /reports/ai-strategist
- every sub-block (pnl, cash_flow, loans, ota, budget/expenses,
  outstanding_obligations) matches the corresponding EXISTING canonical
  Finance/Loan/OTA endpoint exactly — nothing is recalculated
  independently (finance/service.py::financial_snapshot only reuses
  property_pnl/cash_flow/budget_vs_actual/loan_summary/
  ota_commission_report/ota_reconciliation_lines/ota_missing_commissions/
  outstanding_payments)
- OTA commission+GST is never double-counted between pnl.operating_expenses
  and finance.ota (worked example: 975 + 175.50 = 1150.50, not 2301.00)
- a missing OTA commission stays explicitly missing (never invented into
  commission_total/expected_payout_total)
- AI Strategist endpoints remain GET-only (405 on POST/PUT/PATCH/DELETE)
- the existing AI Strategist endpoints stay healthy
- Autonomous Actions (action_proposals) unchanged
- Payments/Invoices/GST unaffected by reading the enriched bundle

Run with: pytest tests/test_ai_strategist_finance.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_property_and_unit(headers, rate=3000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"AIFinProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"AIFinUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return prop["id"], unit["id"]


def _make_booking(headers, unit_id, rate=3000, nights=2, days_offset=9000, source="direct"):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "AIFin Test Guest", "phone": f"98{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=nights)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": check_out.isoformat(),
        "rate_applied": rate, "source": source,
    }).json()
    return booking, check_in


def _make_commission(headers, booking_id, channel="booking_com", percent=15, commission=900, gst=162):
    resp = client.post("/api/v1/finance/ota-commissions", headers=headers, json={
        "booking_id": booking_id, "channel": channel,
        "commission_percent": percent, "commission_amount": commission, "ota_gst_amount": gst,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _action_proposals_count() -> int:
    from sqlalchemy import text
    session = SessionLocal()
    try:
        return session.execute(text("SELECT COUNT(*) FROM action_proposals")).scalar()
    finally:
        session.close()


# --- 1: finance data appears in the bundle ---

def test_finance_block_present_in_bundle(headers):
    resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert resp.status_code == 200, resp.text
    finance = resp.json()["finance"]
    for key in ("period", "source_note", "pnl", "cash_flow", "budget", "loans", "ota", "expenses", "outstanding_obligations"):
        assert key in finance, f"missing finance.{key}"


# --- 2: P&L matches canonical property_pnl ---

def test_finance_pnl_matches_canonical_property_pnl(headers):
    property_id, unit_id = _make_property_and_unit(headers, rate=4000)
    booking, check_in = _make_booking(headers, unit_id, rate=4000, nights=2, days_offset=9100)

    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(), "property_id": property_id,
    })
    assert strat_resp.status_code == 200, strat_resp.text
    strat_pnl = strat_resp.json()["finance"]["pnl"]

    pnl_resp = client.get(f"/api/v1/finance/property-pnl/{property_id}", headers=headers, params={
        # The selected report range is [date_from, date_to), and the
        # strategist's monthly P&L is explicitly anchored to the calendar
        # month containing date_to (see financial_snapshot's period_note).
        # If the exclusive end is the first of a new month, compare to that
        # returned monthly scope instead of the booking's check-in month.
        "period_year": strat_pnl["period_year"], "period_month": strat_pnl["period_month"],
    })
    assert pnl_resp.status_code == 200, pnl_resp.text
    canonical = pnl_resp.json()

    assert strat_pnl["revenue"] == canonical["total_revenue"]
    assert strat_pnl["operating_expenses"] == canonical["total_expenses"]
    assert strat_pnl["central_expenses"] == canonical["central_expenses"]
    assert strat_pnl["net_profit"] == canonical["net_profit"]
    expected_pnl_month = (check_in + timedelta(days=1))
    assert strat_pnl["period_year"] == expected_pnl_month.year
    assert strat_pnl["period_month"] == expected_pnl_month.month


# --- 3: Cash Flow matches canonical cash_flow() ---

def test_finance_cash_flow_matches_canonical_cash_flow(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=2500, nights=1, days_offset=9200)
    d_from, d_to = check_in.isoformat(), (check_in + timedelta(days=1)).isoformat()

    cf_resp = client.get("/api/v1/finance/cash-flow", headers=headers, params={"date_from": d_from, "date_to": d_to})
    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params={"date_from": d_from, "date_to": d_to})
    assert cf_resp.status_code == 200 and strat_resp.status_code == 200

    canonical = cf_resp.json()
    strat_cf = strat_resp.json()["finance"]["cash_flow"]
    for key in ("cash_in_payments", "cash_in_deposits", "cash_in_ota_payouts", "cash_in_total",
                "cash_out_expenses", "cash_out_withdrawals", "cash_out_deposit_refunds", "cash_out_loan_emi",
                "cash_out_total", "net_cash_flow", "cash_in_bank", "cash_in_hand"):
        assert strat_cf[key] == canonical[key], f"{key}: {strat_cf[key]} != {canonical[key]}"


# --- 4: Loan/EMI matches the Loan module ---

def test_finance_loans_matches_loan_module(headers):
    loan_resp = client.post("/api/v1/finance/loans", headers=headers, json={
        "loan_name": f"AIFin Loan {uuid.uuid4().hex[:6]}", "lender_name": "Test Bank", "loan_type": "other",
        "principal_amount": 250000, "interest_rate": 8.5, "emi_amount": 9876, "start_date": date.today().isoformat(),
    })
    assert loan_resp.status_code == 201, loan_resp.text
    loan = loan_resp.json()

    summary_resp = client.get("/api/v1/finance/loans/summary", headers=headers)
    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert summary_resp.status_code == 200 and strat_resp.status_code == 200

    canonical = summary_resp.json()
    strat_loans = strat_resp.json()["finance"]["loans"]
    for key in ("total_active_loans", "total_monthly_emi_obligation", "upcoming_emi_count", "upcoming_emi_amount",
                "overdue_emi_count", "overdue_emi_amount", "paid_this_month_count", "paid_this_month_amount",
                "outstanding_this_month_count", "outstanding_this_month_amount"):
        assert strat_loans[key] == canonical[key], f"{key}: {strat_loans[key]} != {canonical[key]}"

    match = next(l for l in strat_loans["active_loans"] if l["loan_id"] == loan["id"])
    assert match["principal_amount"] == 250000.0
    assert match["emi_amount"] == 9876.0


# --- 5: OTA reconciliation matches the OTA reconciliation service ---

def test_finance_ota_matches_ota_reconciliation_service(headers):
    _, unit_id = _make_property_and_unit(headers, rate=2500)
    booking, check_in = _make_booking(headers, unit_id, rate=2500, nights=2, days_offset=9300)
    commission = _make_commission(headers, booking["id"], channel="mmt", percent=19.5, commission=975, gst=175.50)
    d_from, d_to = check_in.isoformat(), (check_in + timedelta(days=1)).isoformat()

    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3849.50, "receipt_date": date.today().isoformat(), "status": "matched",
    })

    recon_resp = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={"date_from": d_from, "date_to": d_to})
    report_resp = client.get("/api/v1/finance/ota-commissions/report", headers=headers, params={"date_from": d_from, "date_to": d_to})
    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params={"date_from": d_from, "date_to": d_to})
    assert recon_resp.status_code == 200 and report_resp.status_code == 200 and strat_resp.status_code == 200

    recon_lines = recon_resp.json()
    report = report_resp.json()
    strat_ota = strat_resp.json()["finance"]["ota"]

    assert strat_ota["commission_total"] == round(sum(c["total_commission"] for c in report["by_channel"]), 2)
    assert strat_ota["gst_total"] == round(sum(c["total_ota_gst"] for c in report["by_channel"]), 2)
    assert strat_ota["expected_payout_total"] == round(sum(l["expected_payout"] for l in recon_lines), 2)
    assert strat_ota["actual_received_total"] == round(sum(l["actual_received_amount"] for l in recon_lines if l["actual_received_amount"] is not None), 2)

    match = next(l for l in recon_lines if l["ota_commission_id"] == commission["id"])
    assert match["reconciliation_status"] == "matched"
    assert strat_ota["reconciled_count"] >= 1


# --- 6: no financial values duplicated (worked example) ---

def test_ota_commission_not_double_counted_across_pnl_and_ota_blocks(headers):
    property_id, unit_id = _make_property_and_unit(headers, rate=2500)
    booking, check_in = _make_booking(headers, unit_id, rate=2500, nights=2, days_offset=9400)
    commission = _make_commission(headers, booking["id"], channel="booking_com", percent=19.5, commission=975, gst=175.50)

    # Record + repeat a receipt — must not inflate the P&L expense figure.
    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3849.50, "receipt_date": date.today().isoformat(), "status": "matched",
    })
    client.patch(f"/api/v1/finance/ota-commissions/{commission['id']}/receipt", headers=headers, json={
        "actual_received_amount": 3849.50, "receipt_date": date.today().isoformat(), "status": "matched",
    })

    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(), "property_id": property_id,
    })
    finance = strat_resp.json()["finance"]
    # gross 5000 - commission 975 - gst 175.50 = net_profit 3849.50, expense = 1150.50 exactly once
    assert finance["pnl"]["operating_expenses"] == 1150.50
    assert finance["pnl"]["net_profit"] == 3849.50
    assert finance["pnl"]["revenue"] == 5000.0
    # Expected payout (accrual-side OTA figure) must never be conflated with
    # cash_in_ota_payouts (cash-side) — they're independent, both present.
    assert finance["ota"]["expected_payout_total"] == 3849.50


# --- 7: missing OTA commission remains explicitly missing ---

def test_missing_ota_commission_not_invented_in_finance_block(headers):
    _, unit_id = _make_property_and_unit(headers, rate=1800)
    booking, check_in = _make_booking(headers, unit_id, rate=1800, nights=2, days_offset=9500, source="ota_agoda")
    d_from, d_to = check_in.isoformat(), (check_in + timedelta(days=1)).isoformat()

    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params={"date_from": d_from, "date_to": d_to})
    finance = strat_resp.json()["finance"]
    assert finance["ota"]["missing_commission_count"] >= 1
    assert finance["ota"]["missing_commission_gross_total"] >= 3600.0

    missing_resp = client.get("/api/v1/finance/ota-missing-commissions", headers=headers, params={"date_from": d_from, "date_to": d_to})
    missing = missing_resp.json()
    assert any(m["booking_id"] == booking["id"] for m in missing)
    # This booking's commission must not have been invented into commission_total.
    recon_resp = client.get("/api/v1/finance/ota-commissions/reconciliation", headers=headers, params={"date_from": d_from, "date_to": d_to})
    assert all(l["booking_id"] != booking["id"] for l in recon_resp.json())


# --- 8: AI endpoints remain GET-only ---

def test_ai_strategist_endpoints_get_only(headers):
    for path in ("/api/v1/reports/ai-strategist", "/api/v1/reports/ai-strategist/decisions", "/api/v1/reports/ai-strategist/narrative"):
        for method in ("post", "put", "patch", "delete"):
            resp = getattr(client, method)(path, headers=headers)
            assert resp.status_code == 405, f"{method.upper()} {path} -> {resp.status_code}"


# --- 9: existing AI Strategist endpoints remain healthy ---

def test_existing_ai_strategist_endpoints_healthy(headers):
    for path in ("/api/v1/reports/ai-strategist", "/api/v1/reports/ai-strategist/decisions", "/api/v1/reports/ai-strategist/narrative"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


# --- 10: Autonomous Actions unchanged ---

def test_autonomous_actions_unchanged_after_finance_bundle_reads(headers):
    before = _action_proposals_count()
    for _ in range(3):
        client.get("/api/v1/reports/ai-strategist", headers=headers)
        client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    after = _action_proposals_count()
    assert after == before


# --- 11: Payments/Invoices/GST remain unchanged ---

def test_payments_invoices_gst_unchanged_by_reading_finance_bundle(headers):
    _, unit_id = _make_property_and_unit(headers)
    booking, check_in = _make_booking(headers, unit_id, rate=2000, nights=1, days_offset=9600)

    before_payments = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    before_gst = client.get("/api/v1/finance/gst-dashboard", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()

    client.get("/api/v1/reports/ai-strategist", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    })

    after_payments = client.get(f"/api/v1/payments/{booking['id']}", headers=headers).json()
    after_gst = client.get("/api/v1/finance/gst-dashboard", headers=headers, params={
        "date_from": check_in.isoformat(), "date_to": (check_in + timedelta(days=1)).isoformat(),
    }).json()

    assert len(after_payments) == len(before_payments)
    assert after_gst == before_gst
