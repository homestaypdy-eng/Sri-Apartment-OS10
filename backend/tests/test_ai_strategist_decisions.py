"""
Phase 13E — AI Strategist deterministic decision engine tests.

Covers: auth/RBAC, schema stability, determinism/purity, query-cost
parity with GET /reports/ai-strategist, severity-copy-not-recompute for
attention items, structural (threshold-free) movement/GST/conflict
signals, causal-language and unsupported-claims guardrails, financial
evidence integrity, null/empty-data robustness, and regression
protection for the unmodified Phase 13B endpoint.

Run with: pytest tests/test_ai_strategist_decisions.py -v
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.reports.decision_engine import run_decision_engine
from app.modules.reports.schemas import AIStrategistBundle

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

_CAUSAL_PHRASES = (
    "because", "caused by", "causes", "causing", "due to", "leads to",
    "leading to", "resulting in", "driven by", "as a result of", "so that",
)
_UNSUPPORTED_CLAIM_PHRASES = (
    "concerning", "underperform", "elevated", "declining trend", "risk of",
    "likely to", "will lead", "root cause", "should", "recommend that you",
    "worrying", "alarming",
)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_dec_property_and_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"DecProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"DecUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return prop["id"], unit["id"]


def _make_dec_guest(headers, tag):
    return client.post("/api/v1/guests", headers=headers, json={"name": f"Dec Guest {tag}", "phone": f"9{uuid.uuid4().int % 10**9:09d}"}).json()


def _make_dec_booking(headers, unit_id, guest_id, check_in, nights=2, rate=2000):
    payload = {
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=nights)).isoformat(),
        "rate_applied": rate,
    }
    resp = client.post("/api/v1/bookings", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fetch_bundle(headers, params=None) -> AIStrategistBundle:
    resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return AIStrategistBundle.model_validate(resp.json())


def _all_text_fields(decisions: dict) -> list[str]:
    texts = []
    for s in decisions["signals"]:
        texts.append(s["statement"])
    for i in decisions["insights"]:
        texts.append(i["statement"])
        texts.append(i["title"])
    for r in decisions["recommendations"]:
        texts.append(r["action"])
        texts.append(r["rationale"])
        texts.append(r["risk"])
    return texts


# --- 1/2. Auth and schema -------------------------------------------------


def test_decisions_requires_admin_staff_rejected(headers):
    email = f"decstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Decisions Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=staff_headers)
    assert resp.status_code == 403, resp.text


def test_decisions_unauthenticated_rejected():
    resp = client.get("/api/v1/reports/ai-strategist/decisions")
    assert resp.status_code == 401


def test_decisions_schema_stable(headers):
    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for section in ("generated_at", "signals", "insights", "recommendations"):
        assert section in data, f"decision result missing '{section}'"

    if data["signals"]:
        for field in ("signal_id", "category", "severity", "classification", "statement", "source_fields", "evidence", "confidence"):
            assert field in data["signals"][0], f"signal missing '{field}'"
    if data["insights"]:
        for field in ("insight_id", "category", "title", "severity", "classification", "statement", "evidence", "source_fields", "confidence", "signal_ids"):
            assert field in data["insights"][0], f"insight missing '{field}'"
    if data["recommendations"]:
        for field in ("recommendation_id", "priority", "category", "action", "rationale", "evidence", "source_fields", "risk", "confidence", "requires_human_approval", "linked_insight_ids"):
            assert field in data["recommendations"][0], f"recommendation missing '{field}'"


# --- 3. Determinism / purity ------------------------------------------


def test_engine_is_pure_same_bundle_same_result(headers):
    bundle = _fetch_bundle(headers)
    result_a = run_decision_engine(bundle)
    result_b = run_decision_engine(bundle)
    assert result_a.model_dump() == result_b.model_dump()


def test_engine_never_reads_bundle_generated_at_as_now(headers):
    """generated_at on the decision result must be copied from the
    bundle, not independently derived from the current wall clock."""
    bundle = _fetch_bundle(headers)
    result = run_decision_engine(bundle)
    assert result.generated_at == bundle.generated_at


# --- 4. Query-cost parity with the 13B endpoint -----------------------


def test_decisions_endpoint_adds_no_extra_queries(headers):
    from sqlalchemy import event
    from app.db.session import engine

    def _count_queries_for(path):
        counter = {"n": 0}

        def _before_cursor_execute(*args, **kwargs):
            counter["n"] += 1

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
        try:
            resp = client.get(path, headers=headers)
            assert resp.status_code == 200, resp.text
        finally:
            event.remove(engine, "before_cursor_execute", _before_cursor_execute)
        return counter["n"]

    bundle_only = _count_queries_for("/api/v1/reports/ai-strategist")
    with_decisions = _count_queries_for("/api/v1/reports/ai-strategist/decisions")
    assert with_decisions == bundle_only, (
        f"/decisions issued {with_decisions} queries vs {bundle_only} for the bundle alone — "
        "the decision engine must be pure in-memory and add zero queries"
    )


# --- 5/13/14. Recommendation ordering and structure --------------------


def test_recommendation_priority_orders_critical_before_warning_before_info(headers):
    # Force a critical unpaid_balance attention item (large overdue balance).
    property_id, unit_id = _make_dec_property_and_unit(headers)
    guest = _make_dec_guest(headers, "priority")
    date_from = date.today() - timedelta(days=40)
    booking = _make_dec_booking(headers, unit_id, guest["id"], date_from, nights=2, rate=5000)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_out"})

    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    recs = resp.json()["recommendations"]
    severities = [_severity_of_recommendation(resp.json(), r) for r in recs]
    ranks = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "INFO": 3}
    numeric = [ranks[s] for s in severities]
    assert numeric == sorted(numeric), f"recommendations not sorted by severity: {severities}"
    for idx, r in enumerate(recs):
        assert r["priority"] == idx


def _severity_of_recommendation(data, rec):
    insight_id = rec["linked_insight_ids"][0]
    for insight in data["insights"]:
        if insight["insight_id"] == insight_id:
            return insight["severity"]
    raise AssertionError(f"recommendation {rec['recommendation_id']} links to unknown insight {insight_id}")


def test_all_recommendations_require_human_approval(headers):
    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    for r in resp.json()["recommendations"]:
        assert r["requires_human_approval"] is True


def test_recommendation_linked_insight_ids_resolve(headers):
    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    insight_ids = {i["insight_id"] for i in data["insights"]}
    for r in data["recommendations"]:
        for linked in r["linked_insight_ids"]:
            assert linked in insight_ids, f"recommendation {r['recommendation_id']} links to missing insight {linked}"


# --- 6. Source-field traceability ---------------------------------------


def test_every_signal_and_insight_has_source_fields(headers):
    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for s in data["signals"]:
        assert s["source_fields"], f"signal {s['signal_id']} has no source_fields"
        for f in s["source_fields"]:
            assert "." in f or "[" in f, f"source_field '{f}' does not look like a bundle path"
    for i in data["insights"]:
        assert i["source_fields"], f"insight {i['insight_id']} has no source_fields"


# --- 7. Attention severity copied verbatim, never recomputed -----------


def test_attention_item_severity_copied_not_recomputed(headers):
    property_id, unit_id = _make_dec_property_and_unit(headers)
    guest = _make_dec_guest(headers, "sevcopy")
    date_from = date.today() - timedelta(days=45)
    booking = _make_dec_booking(headers, unit_id, guest["id"], date_from, nights=2, rate=6000)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_out"})

    attention_resp = client.get("/api/v1/reports/dashboard", headers=headers)
    assert attention_resp.status_code == 200
    # OperationalDashboard's field is "attention_items" (not
    # "needs_attention" — that's the AI Strategist bundle's own field
    # name for the same concept). The wrong key here always returned []
    # via .get()'s default, so this test unconditionally skipped on
    # every run regardless of data state.
    attention_items = attention_resp.json().get("attention_items", [])
    matching = [a for a in attention_items if a["reference_id"] == booking["id"]]
    if not matching:
        pytest.skip("attention item for this booking not present in current dashboard window")
    expected_severity = matching[0]["severity"].upper()

    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    signal = next((s for s in data["signals"] if s["signal_id"] == f"attention:unpaid_balance:{booking['id']}"), None)
    assert signal is not None, "expected attention signal for this booking not found"
    assert signal["severity"] == expected_severity
    assert signal["classification"] == "FACT"


# --- 8/10. GST and movement signals: structural, never threshold-based --


def test_gst_signal_always_info_regardless_of_magnitude(headers):
    date_from = date.today() + timedelta(days=11000)
    date_to = date_from + timedelta(days=5)
    params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    gst_signals = [s for s in resp.json()["signals"] if s["category"] == "gst"]
    assert len(gst_signals) == 1
    assert gst_signals[0]["severity"] == "INFO"
    assert gst_signals[0]["classification"] == "FACT"


def test_movement_signals_always_info_never_watch_or_higher(headers):
    """No occupancy/revenue/ADR/RevPAR movement threshold has been
    approved — direction signals must always be INFO regardless of how
    large the swing is."""
    property_id, unit_id = _make_dec_property_and_unit(headers)
    guest = _make_dec_guest(headers, "movement")
    date_from = date.today() + timedelta(days=11100)
    date_to = date_from + timedelta(days=5)
    _make_dec_booking(headers, unit_id, guest["id"], date_from, nights=3, rate=9000)

    params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id}
    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    movement_signals = [s for s in resp.json()["signals"] if s["category"] == "movement"]
    assert len(movement_signals) == 4
    for s in movement_signals:
        assert s["severity"] == "INFO"
        assert s["classification"] == "DERIVED"


def test_movement_signal_evidence_matches_bundle_exactly(headers):
    bundle = _fetch_bundle(headers)
    result = run_decision_engine(bundle)
    revenue_signal = next(s for s in result.signals if s.signal_id == "movement:revenue")
    values = {e.field: e.value for e in revenue_signal.evidence}
    assert values["business_health.revenue_current"] == str(bundle.business_health.revenue_current)
    assert values["business_health.revenue_previous"] == str(bundle.business_health.revenue_previous)


# --- 11. Conflicting-direction insight, non-causal -----------------------


def test_conflicting_direction_insight_is_non_causal_when_present():
    """Directly unit-tests the conflict rule against a hand-built bundle
    where occupancy is supplied as up and revenue as down, avoiding
    dependency on live data happening to move that way."""
    bundle_json = _minimal_bundle_json()
    bundle_json["business_health"]["occupancy"] = {"current": 80.0, "previous": 50.0, "change_percent": 60.0, "change_percentage_points": 30.0}
    bundle_json["business_health"]["revenue_current"] = 1000.0
    bundle_json["business_health"]["revenue_previous"] = 2000.0
    bundle = AIStrategistBundle.model_validate(bundle_json)

    result = run_decision_engine(bundle)
    conflict_insights = [i for i in result.insights if i.category == "movement" and i.insight_id.startswith("insight:conflict:")]
    assert conflict_insights, "expected a conflicting-direction insight for occupancy up / revenue down"
    for i in conflict_insights:
        assert "does not establish why" in i.statement
        for phrase in _CAUSAL_PHRASES:
            assert phrase not in i.statement.lower(), f"causal phrase '{phrase}' found in conflict insight"


# --- 12/15. Guardrails: no causal language, no unsupported claims -------


def test_no_causal_language_anywhere(headers):
    property_id, unit_id = _make_dec_property_and_unit(headers)
    guest = _make_dec_guest(headers, "guardrail")
    date_from = date.today() - timedelta(days=50)
    booking = _make_dec_booking(headers, unit_id, guest["id"], date_from, nights=2, rate=7000)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "checked_out"})

    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    texts = _all_text_fields(resp.json())
    for text in texts:
        lowered = text.lower()
        for phrase in _CAUSAL_PHRASES:
            assert phrase not in lowered, f"forbidden causal phrase '{phrase}' found in: {text!r}"


def test_no_unsupported_claim_language_anywhere(headers):
    resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    texts = _all_text_fields(resp.json())
    for text in texts:
        lowered = text.lower()
        for phrase in _UNSUPPORTED_CLAIM_PHRASES:
            assert phrase not in lowered, f"unsupported-claim phrase '{phrase}' found in: {text!r}"


# --- 16. Financial evidence integrity -----------------------------------


def test_outstanding_and_gst_evidence_exactly_matches_bundle(headers):
    bundle = _fetch_bundle(headers)
    result = run_decision_engine(bundle)

    gst_signal = next(s for s in result.signals if s.signal_id == "gst:payable")
    values = {e.field: e.value for e in gst_signal.evidence}
    assert values["gst.gst_collected"] == str(bundle.gst.gst_collected)
    assert values["gst.gst_input_credit_eligible"] == str(bundle.gst.gst_input_credit_eligible)
    assert values["gst.gst_payable"] == str(bundle.gst.gst_payable)

    outstanding_signal = next((s for s in result.signals if s.signal_id == "outstanding:summary"), None)
    if outstanding_signal is not None:
        values = {e.field: e.value for e in outstanding_signal.evidence}
        assert values["outstanding_payments.total_pending_balance"] == str(bundle.outstanding_payments.total_pending_balance)
        assert values["outstanding_payments.overdue_amount"] == str(bundle.outstanding_payments.overdue_amount)


# --- 17. Null / empty data robustness ------------------------------------


def test_engine_handles_fully_empty_bundle_without_error():
    bundle_json = _minimal_bundle_json()
    bundle = AIStrategistBundle.model_validate(bundle_json)
    result = run_decision_engine(bundle)
    # No attention items, no outstanding balance, no guests, no sources,
    # no unit lines, no weekly facts supplied -> only the always-present
    # gst + cancellations + 4 movement signals, no crash.
    assert len(result.signals) == 6
    assert all(s.severity == "INFO" for s in result.signals)
    assert result.recommendations == [] or all(r.category == "gst" for r in result.recommendations)


# --- 18. Regression: 13B endpoint completely unaffected -----------------


def test_ai_strategist_bundle_endpoint_unchanged_after_decisions(headers):
    resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for section in (
        "generated_at", "business_health", "needs_attention", "cancellations", "outstanding_payments",
        "unit_performance", "booking_sources", "top_guests", "maintenance", "gst", "weekly_facts",
    ):
        assert section in data, f"AI Strategist bundle missing section '{section}' after 13E"
    # Must NOT have grown a 'decisions'/'signals'/'insights'/'recommendations' field —
    # 13E is strictly additive via a separate endpoint, never merged into this response.
    for forbidden in ("signals", "insights", "recommendations", "decisions"):
        assert forbidden not in data, f"/reports/ai-strategist response shape changed to include '{forbidden}'"


# --- helpers ---------------------------------------------------------------


def _minimal_bundle_json() -> dict:
    """A structurally-valid, entirely empty/zeroed AIStrategistBundle for
    direct unit tests of the engine that must not depend on live data."""
    today = date.today().isoformat()
    period = {"date_from": today, "date_to": today}
    return {
        "generated_at": "2026-01-01T00:00:00",
        "business_health": {
            "current_period": period, "previous_period": period,
            "revenue_current": 0.0, "revenue_previous": 0.0,
            "revenue_definition": "rate_applied x nights + extra_guest_charge",
            "occupancy": {"current": 0.0, "previous": 0.0, "change_percent": None, "change_percentage_points": None},
            "adr": {"current": 0.0, "previous": 0.0, "change_percent": None, "change_percentage_points": None},
            "revpar": {"current": 0.0, "previous": 0.0, "change_percent": None, "change_percentage_points": None},
        },
        "needs_attention": [],
        "cancellations": {
            "current_period": period, "previous_period": period,
            "total_bookings_in_period": 0, "cancelled_count": 0, "cancellation_rate_percent": 0.0,
            "no_show_count": 0, "no_show_rate_percent": 0.0,
        },
        "outstanding_payments": {"total_pending_balance": 0.0, "pending_count": 0, "overdue_count": 0, "overdue_amount": 0.0},
        "unit_performance": {"current_period": period, "top_units": [], "bottom_units": []},
        "booking_sources": {"current_period": period, "revenue_by_source": {}, "revenue_definition": "rate_applied x nights + extra_guest_charge"},
        "top_guests": [],
        "maintenance": {"current_period": period, "maintenance_cost_total": 0.0},
        "gst": {"current_period": period, "gst_collected": 0.0, "gst_input_credit_eligible": 0.0, "gst_payable": 0.0},
        "weekly_facts": [],
        "finance": {
            "period": period,
            "source_note": "test fixture",
            "pnl": {
                "period_year": date.today().year, "period_month": date.today().month, "period_note": "test fixture",
                "revenue": 0.0, "operating_expenses": None, "central_expenses": None, "net_profit": None, "properties_included": 0,
            },
            "cash_flow": {
                "date_from": today, "date_to": today,
                "cash_in_payments": 0.0, "cash_in_deposits": 0.0, "cash_in_ota_payouts": 0.0, "cash_in_total": 0.0,
                "cash_out_expenses": 0.0, "cash_out_withdrawals": 0.0, "cash_out_deposit_refunds": 0.0, "cash_out_loan_emi": 0.0,
                "cash_out_total": 0.0, "net_cash_flow": 0.0, "cash_in_bank": 0.0, "cash_in_hand": 0.0,
            },
            "budget": {"period_year": date.today().year, "period_month": date.today().month, "total_budgeted": 0.0, "total_actual": 0.0, "lines": []},
            "loans": {
                "total_active_loans": 0, "total_monthly_emi_obligation": 0.0,
                "upcoming_emi_count": 0, "upcoming_emi_amount": 0.0,
                "overdue_emi_count": 0, "overdue_emi_amount": 0.0,
                "paid_this_month_count": 0, "paid_this_month_amount": 0.0,
                "outstanding_this_month_count": 0, "outstanding_this_month_amount": 0.0,
                "active_loans": [],
            },
            "ota": {
                "commission_total": 0.0, "gst_total": 0.0, "expected_payout_total": 0.0, "actual_received_total": 0.0,
                "reconciled_count": 0, "unreconciled_count": 0, "discrepancy_count": 0, "discrepancy_total": 0.0,
                "missing_commission_count": 0, "missing_commission_gross_total": 0.0,
            },
            "expenses": {"payroll_total": 0.0, "utilities_total": 0.0, "purchases_total": 0.0},
            "outstanding_obligations": {
                "emi_overdue_amount": 0.0, "emi_overdue_count": 0, "emi_due_this_month_amount": 0.0,
                "ota_unreconciled_expected_payout": 0.0, "guest_outstanding_balance": 0.0,
            },
        },
    }
