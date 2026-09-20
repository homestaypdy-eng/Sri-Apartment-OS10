"""
Phase 13G — AI Strategist narrative layer tests (mock provider only).

Covers: extraction purity/determinism, sensitive-field exclusion, the
mock provider, the validation pipeline's rejection paths (unknown ids,
changed severity/classification, weakened human-approval, invented
recommendations, untraceable financial numbers, causal/predictive
language), deterministic fallback on provider/validation failure, the
live endpoint's RBAC, schema stability, regression protection for the
13B/13E endpoints, and query-cost parity.

Run with: pytest tests/test_ai_strategist_narrative.py -v
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.reports.narrative_provider import MockNarrativeProvider
from app.modules.reports.narrative_service import (
    NarrativeService,
    build_narrative_input,
    deterministic_fallback,
)
from app.modules.reports.narrative_validation import validate_raw_narrative_output
from app.modules.reports.schemas import (
    AIStrategistDecisionResult,
    AIStrategistEvidence,
    AIStrategistInsight,
    AIStrategistRecommendation,
    AIStrategistSignal,
    NarrativeInputItem,
    NarrativeInputRecommendation,
)

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _sample_decision_result() -> AIStrategistDecisionResult:
    signal = AIStrategistSignal(
        signal_id="sig:1", category="gst", severity="INFO", classification="FACT",
        statement="GST payable this period is 500.0.",
        source_fields=["gst.gst_payable"],
        evidence=[AIStrategistEvidence(field="gst.gst_payable", value="500.0")],
        confidence="HIGH",
    )
    insight = AIStrategistInsight(
        insight_id="insight:1", category="gst", title="GST payable summary",
        severity="INFO", classification="FACT", statement="GST payable this period is 500.0.",
        evidence=[AIStrategistEvidence(field="gst.gst_payable", value="500.0")],
        source_fields=["gst.gst_payable"], confidence="HIGH", signal_ids=["sig:1"],
    )
    recommendation = AIStrategistRecommendation(
        recommendation_id="rec:1", priority=0, category="gst",
        action="Review the GST payable figure before filing or reconciliation.",
        rationale="GST payable this period is 500.0.",
        evidence=[AIStrategistEvidence(field="gst.gst_payable", value="500.0")],
        source_fields=["gst.gst_payable"], expected_effect=None,
        risk="Filing without review may misstate GST payable.",
        confidence="HIGH", requires_human_approval=True, linked_insight_ids=["insight:1"],
    )
    return AIStrategistDecisionResult(
        generated_at=datetime(2026, 1, 1),
        signals=[signal], insights=[insight], recommendations=[recommendation],
    )


def _base_raw(**overrides) -> dict:
    raw = {
        "executive_summary": "Summary.",
        "key_observations": [],
        "priority_actions": [],
        "risks": [],
        "questions_for_management": [],
    }
    raw.update(overrides)
    return raw


class _FailingProvider:
    def generate_narrative(self, narrative_input):
        raise RuntimeError("provider unavailable")


class _MalformedNumberProvider:
    def generate_narrative(self, narrative_input):
        return _base_raw(key_observations=[
            {"text": "GST payable this period is 999999.0.", "source_ids": ["sig:1"], "classification": "FACT"},
        ])


# --- 1/2. NarrativeInput field set and sensitive-field exclusion --------


def test_narrative_input_contains_only_approved_fields():
    item_fields = set(NarrativeInputItem.model_fields.keys())
    assert item_fields == {
        "id", "kind", "classification", "category", "severity", "statement",
        "evidence", "source_fields", "confidence", "period", "linked_ids",
    }
    rec_fields = set(NarrativeInputRecommendation.model_fields.keys())
    assert rec_fields == {
        "recommendation_id", "priority", "category", "action", "rationale", "risk",
        "evidence", "source_fields", "confidence", "requires_human_approval", "linked_insight_ids",
    }


def test_narrative_input_excludes_sensitive_fields():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw_text = narrative_input.model_dump_json().lower()
    for forbidden in ("phone", "password", "token", "secret", "credential", "blacklist", "\"vip\""):
        assert forbidden not in raw_text, f"forbidden field/value '{forbidden}' leaked into NarrativeInput"


# --- 3. Deterministic extraction ------------------------------------------


def test_narrative_input_extraction_deterministic():
    decision_result = _sample_decision_result()
    a = build_narrative_input(decision_result)
    b = build_narrative_input(decision_result)
    assert a.model_dump() == b.model_dump()


# --- 4. Mock provider determinism -----------------------------------------


def test_mock_provider_deterministic():
    narrative_input = build_narrative_input(_sample_decision_result())
    provider = MockNarrativeProvider()
    assert provider.generate_narrative(narrative_input) == provider.generate_narrative(narrative_input)


# --- 5. Valid provider output passes --------------------------------------


def test_narrative_service_valid_provider_output_passes():
    result = NarrativeService(provider=MockNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "llm"
    assert result.key_observations and result.priority_actions
    assert result.key_observations[0].source_ids == ["sig:1"]
    assert result.priority_actions[0].source_ids == ["rec:1"]


# --- 6. Invalid schema rejected --------------------------------------------


def test_validate_rejects_non_dict_and_missing_fields():
    narrative_input = build_narrative_input(_sample_decision_result())
    assert validate_raw_narrative_output("not a dict", narrative_input) is None
    assert validate_raw_narrative_output({}, narrative_input) is None


# --- 7. Unknown insight/signal id rejected ---------------------------------


def test_validate_rejects_unknown_source_id():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(key_observations=[
        {"text": "Something.", "source_ids": ["does-not-exist"], "classification": "FACT"},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 8. Unknown recommendation id rejected ---------------------------------


def test_validate_rejects_unknown_recommendation_id():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(priority_actions=[
        {"text": "Do X.", "source_ids": ["rec:does-not-exist"], "requires_human_approval": True},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 9. Changed severity rejected -------------------------------------------


def test_validate_rejects_changed_severity():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(key_observations=[
        {"text": "GST payable this period is 500.0.", "source_ids": ["sig:1"], "classification": "FACT", "severity": "CRITICAL"},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 10. Changed classification rejected ------------------------------------


def test_validate_rejects_changed_classification():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(key_observations=[
        {"text": "GST payable this period is 500.0.", "source_ids": ["sig:1"], "classification": "SIGNAL"},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 11. requires_human_approval=false rejected -----------------------------


def test_validate_rejects_weakened_human_approval():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(priority_actions=[
        {"text": "Review the GST payable figure before filing or reconciliation.",
         "source_ids": ["rec:1"], "requires_human_approval": False},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 12. Invented recommendation rejected -----------------------------------


def test_validate_rejects_invented_recommendation():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(priority_actions=[
        {"text": "Raise prices 15%.", "source_ids": ["rec:invented-pricing-action"], "requires_human_approval": True},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 13. Unsupported financial number rejected ------------------------------


def test_validate_rejects_untraceable_financial_number():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(key_observations=[
        {"text": "GST payable this period is 999999.0.", "source_ids": ["sig:1"], "classification": "FACT"},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 14. Unsupported causal language rejected -------------------------------


def test_validate_rejects_causal_language():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(key_observations=[
        {"text": "GST payable is high because revenue increased.", "source_ids": ["sig:1"], "classification": "FACT"},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 15. Unsupported predictive language rejected ---------------------------


def test_validate_rejects_predictive_language():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(key_observations=[
        {"text": "GST payable will increase next period.", "source_ids": ["sig:1"], "classification": "FACT"},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 16. Missing evidence reference rejected --------------------------------


def test_validate_rejects_empty_source_ids():
    narrative_input = build_narrative_input(_sample_decision_result())
    raw = _base_raw(key_observations=[
        {"text": "Some observation.", "source_ids": [], "classification": "FACT"},
    ])
    assert validate_raw_narrative_output(raw, narrative_input) is None


# --- 17. Provider failure triggers fallback ---------------------------------


def test_provider_failure_triggers_deterministic_fallback():
    result = NarrativeService(provider=_FailingProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"
    assert result.key_observations[0].text == "GST payable this period is 500.0."


# --- 18. Validation failure triggers fallback -------------------------------


def test_validation_failure_triggers_deterministic_fallback():
    result = NarrativeService(provider=_MalformedNumberProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


def test_deterministic_fallback_never_fabricates_prose():
    narrative_input = build_narrative_input(_sample_decision_result())
    fallback = deterministic_fallback(narrative_input)
    assert fallback.key_observations[0].text == narrative_input.items[0].statement
    assert fallback.priority_actions[0].text == narrative_input.recommendations[0].action
    assert fallback.risks[0].text == narrative_input.recommendations[0].risk


# --- 19/20/21. RBAC on the live endpoint ------------------------------------


def test_narrative_endpoint_admin_allowed(headers):
    resp = client.get("/api/v1/reports/ai-strategist/narrative", headers=headers)
    assert resp.status_code == 200, resp.text
    # No real provider is configured in this phase — must be honest about it.
    assert resp.json()["source"] == "deterministic_fallback"


def test_narrative_endpoint_staff_rejected(headers):
    email = f"narrstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Narrative Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}
    resp = client.get("/api/v1/reports/ai-strategist/narrative", headers=staff_headers)
    assert resp.status_code == 403, resp.text


def test_narrative_endpoint_unauthenticated_rejected():
    resp = client.get("/api/v1/reports/ai-strategist/narrative")
    assert resp.status_code == 401


# --- 22. Narrative endpoint response schema stable --------------------------


def test_narrative_endpoint_schema_stable(headers):
    resp = client.get("/api/v1/reports/ai-strategist/narrative", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for field in (
        "executive_summary", "key_observations", "priority_actions", "risks",
        "questions_for_management", "generated_at", "source",
    ):
        assert field in data, f"narrative response missing '{field}'"
    if data["key_observations"]:
        for field in ("text", "source_ids", "classification"):
            assert field in data["key_observations"][0]
    if data["priority_actions"]:
        for field in ("text", "source_ids", "requires_human_approval"):
            assert field in data["priority_actions"][0]
            assert data["priority_actions"][0]["requires_human_approval"] is True


# --- 23. Regression: 13B/13E endpoints unaffected ----------------------------


def test_ai_strategist_bundle_and_decisions_unchanged_after_narrative(headers):
    bundle_resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert bundle_resp.status_code == 200
    bundle_data = bundle_resp.json()
    for forbidden in ("narrative", "executive_summary", "key_observations"):
        assert forbidden not in bundle_data

    decisions_resp = client.get("/api/v1/reports/ai-strategist/decisions", headers=headers)
    assert decisions_resp.status_code == 200
    decisions_data = decisions_resp.json()
    for field in ("generated_at", "signals", "insights", "recommendations"):
        assert field in decisions_data
    for forbidden in ("narrative", "executive_summary", "key_observations"):
        assert forbidden not in decisions_data


# --- 24. Query-cost parity ---------------------------------------------------


def test_narrative_endpoint_adds_no_extra_queries(headers):
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

    decisions_count = _count_queries_for("/api/v1/reports/ai-strategist/decisions")
    narrative_count = _count_queries_for("/api/v1/reports/ai-strategist/narrative")
    assert narrative_count == decisions_count, (
        f"/narrative issued {narrative_count} queries vs {decisions_count} for /decisions — "
        "the narrative layer must add zero additional queries"
    )
