"""
Phase 13I — Anthropic narrative provider adapter tests.

All HTTP is mocked (httpx.post is monkeypatched) — this file never makes
a real network call and never requires a real API key. Covers: success,
every documented failure category and its retry/no-retry behavior,
malformed/invalid output routed through the UNCHANGED Phase 13G
validator, data-minimization regression tests (guest_id and raw
ticket-description text must never reach the provider payload), secret
handling (never logged), and router-level provider selection
(disabled-by-default vs. enabled).

Run with: pytest tests/test_narrative_provider_anthropic.py -v
"""
import json
import logging
from datetime import datetime

import httpx
import pytest

from app.core.config import settings
from app.modules.reports import router as reports_router
from app.modules.reports.narrative_provider import NullNarrativeProvider
from app.modules.reports.narrative_provider_anthropic import (
    AnthropicNarrativeProvider,
    _minimize_for_provider,
)
from app.modules.reports.narrative_service import NarrativeService
from app.modules.reports.schemas import (
    AIStrategistDecisionResult,
    AIStrategistEvidence,
    AIStrategistInsight,
    AIStrategistRecommendation,
    AIStrategistSignal,
    NarrativeInput,
    NarrativeInputItem,
)


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


def _narrative_input_with_sensitive_evidence() -> NarrativeInput:
    guest_item = NarrativeInputItem(
        id="guest_segments:supplied", kind="signal", classification="DERIVED",
        category="guest_segments", severity="INFO",
        statement="Among the 2 guest(s) supplied in this bundle, 1 are repeat guests and 1 are new.",
        evidence=[
            AIStrategistEvidence(field="top_guests[0].guest_id", value="11111111-1111-1111-1111-111111111111"),
            AIStrategistEvidence(field="top_guests[0].is_repeat_guest", value="True"),
        ],
        source_fields=["top_guests[0].guest_id", "top_guests[0].is_repeat_guest"],
        confidence="LOW",
    )
    attention_item = NarrativeInputItem(
        id="attention:maintenance:abc123", kind="signal", classification="FACT",
        category="maintenance", severity="WARNING",
        statement="Urgent priority ticket, open 5h.",
        evidence=[
            AIStrategistEvidence(field="needs_attention[0].title", value="Urgent maintenance — Unit 4B: Guest said the AC leaks water everywhere ple"),
            AIStrategistEvidence(field="needs_attention[0].reason", value="Urgent priority ticket, open 5h."),
        ],
        source_fields=["needs_attention[0].title", "needs_attention[0].reason"],
        confidence="HIGH",
    )
    return NarrativeInput(generated_at=datetime(2026, 1, 1), items=[guest_item, attention_item], recommendations=[])


class _FakeResponse:
    def __init__(self, status_code, json_body=None, raise_on_json=False):
        self.status_code = status_code
        self._json_body = json_body
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not valid json")
        return self._json_body


def _make_fake_post(responses):
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        item = responses[calls["n"] - 1]
        if isinstance(item, BaseException):
            raise item
        return item

    fake_post.calls = calls
    return fake_post


def _tool_response(output_dict):
    return _FakeResponse(200, json_body={"content": [{"type": "tool_use", "name": "emit_narrative", "input": output_dict}]})


def _valid_output_dict():
    return {
        "executive_summary": "Summary of the period.",
        "key_observations": [{"text": "GST payable this period is 500.0.", "source_ids": ["sig:1"], "classification": "FACT"}],
        "priority_actions": [{"text": "Review the GST payable figure before filing or reconciliation.", "source_ids": ["rec:1"], "requires_human_approval": True}],
        "risks": [],
        "questions_for_management": [],
    }


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "narrative_provider_api_key", "fake-test-key-do-not-use-9f8e7d")
    monkeypatch.setattr(settings, "narrative_provider_model", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(settings, "narrative_provider_timeout", 5.0)


# --- 1/2. Successful provider response / valid structured output --------


def test_provider_success_returns_expected_dict(monkeypatch):
    fake_post = _make_fake_post([_tool_response(_valid_output_dict())])
    monkeypatch.setattr(httpx, "post", fake_post)

    provider = AnthropicNarrativeProvider()
    narrative_input = NarrativeInput(generated_at=datetime(2026, 1, 1), items=[], recommendations=[])
    raw = provider.generate_narrative(narrative_input)
    assert raw["executive_summary"] == "Summary of the period."
    assert fake_post.calls["n"] == 1


def test_narrative_service_end_to_end_with_valid_provider_output(monkeypatch):
    fake_post = _make_fake_post([_tool_response(_valid_output_dict())])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "llm"
    assert result.key_observations[0].source_ids == ["sig:1"]


# --- 3. Timeout -> fallback -------------------------------------------


def test_timeout_triggers_fallback_after_retry(monkeypatch):
    fake_post = _make_fake_post([httpx.TimeoutException("timed out"), httpx.TimeoutException("timed out")])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"
    assert fake_post.calls["n"] == 2


# --- 4. 429 -> retry once -> fallback if still failing -------------------


def test_rate_limit_retries_once_then_falls_back(monkeypatch):
    fake_post = _make_fake_post([_FakeResponse(429), _FakeResponse(429)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"
    assert fake_post.calls["n"] == 2


def test_rate_limit_succeeds_on_retry(monkeypatch):
    fake_post = _make_fake_post([_FakeResponse(429), _tool_response(_valid_output_dict())])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "llm"
    assert fake_post.calls["n"] == 2


# --- 5. 500 -> retry once -> fallback if still failing --------------------


def test_server_error_retries_once_then_falls_back(monkeypatch):
    fake_post = _make_fake_post([_FakeResponse(500), _FakeResponse(503)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"
    assert fake_post.calls["n"] == 2


# --- 6. Network error -> fallback ------------------------------------------


def test_network_error_triggers_fallback(monkeypatch):
    fake_post = _make_fake_post([httpx.ConnectError("connection refused"), httpx.ConnectError("connection refused")])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"
    assert fake_post.calls["n"] == 2


# --- 7. Authentication failure -> immediate fallback, no retry ------------


def test_auth_failure_falls_back_without_retry(monkeypatch):
    fake_post = _make_fake_post([_FakeResponse(401)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"
    assert fake_post.calls["n"] == 1  # never retried


# --- 8. Malformed JSON -> fallback -----------------------------------------


def test_malformed_json_triggers_fallback(monkeypatch):
    fake_post = _make_fake_post([_FakeResponse(200, raise_on_json=True)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


def test_missing_tool_use_block_triggers_fallback(monkeypatch):
    fake_post = _make_fake_post([_FakeResponse(200, json_body={"content": []})])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


# --- 9. Invalid schema -> fallback (unchanged 13G validator) --------------


def test_invalid_schema_triggers_fallback(monkeypatch):
    bad_output = {"key_observations": [], "priority_actions": [], "risks": [], "questions_for_management": []}  # missing executive_summary
    fake_post = _make_fake_post([_tool_response(bad_output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


# --- 10/11. Unknown insight/recommendation reference -> fallback ----------


def test_unknown_insight_reference_triggers_fallback(monkeypatch):
    output = _valid_output_dict()
    output["key_observations"] = [{"text": "Something.", "source_ids": ["does-not-exist"], "classification": "FACT"}]
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


def test_unknown_recommendation_reference_triggers_fallback(monkeypatch):
    output = _valid_output_dict()
    output["priority_actions"] = [{"text": "Do X.", "source_ids": ["rec:invented"], "requires_human_approval": True}]
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


# --- 12/13/14. Changed severity / classification / approval rejected -----


def test_changed_severity_rejected(monkeypatch):
    output = _valid_output_dict()
    output["key_observations"][0]["severity"] = "CRITICAL"
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


def test_changed_classification_rejected(monkeypatch):
    output = _valid_output_dict()
    output["key_observations"][0]["classification"] = "SIGNAL"
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


def test_weakened_human_approval_rejected(monkeypatch):
    output = _valid_output_dict()
    output["priority_actions"][0]["requires_human_approval"] = False
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


# --- 15. Invented financial number rejected --------------------------------


def test_invented_financial_number_rejected(monkeypatch):
    output = _valid_output_dict()
    output["key_observations"][0]["text"] = "GST payable this period is 999999.0."
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


# --- 16/17. Causal / predictive statement rejected -------------------------


def test_causal_statement_rejected(monkeypatch):
    output = _valid_output_dict()
    output["key_observations"][0]["text"] = "GST payable is high because revenue increased."
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


def test_predictive_statement_rejected(monkeypatch):
    output = _valid_output_dict()
    output["key_observations"][0]["text"] = "GST payable will increase next period."
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


# --- 18. Invented recommendation rejected -----------------------------------


def test_invented_recommendation_rejected(monkeypatch):
    output = _valid_output_dict()
    output["priority_actions"].append({"text": "Raise prices 15%.", "source_ids": ["rec:invented-pricing"], "requires_human_approval": True})
    fake_post = _make_fake_post([_tool_response(output)])
    monkeypatch.setattr(httpx, "post", fake_post)

    result = NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())
    assert result.source == "deterministic_fallback"


# --- 19/20. Data minimization regression tests ------------------------------


def test_provider_payload_excludes_guest_id():
    narrative_input = _narrative_input_with_sensitive_evidence()
    payload = _minimize_for_provider(narrative_input)
    serialized = json.dumps(payload)
    assert "guest_id" not in serialized
    assert "11111111-1111-1111-1111-111111111111" not in serialized


def test_provider_payload_excludes_raw_ticket_description():
    narrative_input = _narrative_input_with_sensitive_evidence()
    payload = _minimize_for_provider(narrative_input)
    serialized = json.dumps(payload)
    assert "AC leaks water" not in serialized
    assert "Guest said" not in serialized


def test_provider_payload_never_includes_evidence_or_source_fields():
    narrative_input = _narrative_input_with_sensitive_evidence()
    payload = _minimize_for_provider(narrative_input)
    for item in payload["items"]:
        assert "evidence" not in item
        assert "source_fields" not in item
        assert set(item.keys()) == {"id", "kind", "classification", "category", "severity", "statement", "confidence"}


# --- 21. API key never logged -----------------------------------------------


def test_api_key_never_appears_in_logs(monkeypatch, caplog):
    fake_post = _make_fake_post([httpx.TimeoutException("timed out"), httpx.TimeoutException("timed out")])
    monkeypatch.setattr(httpx, "post", fake_post)

    with caplog.at_level(logging.ERROR):
        NarrativeService(provider=AnthropicNarrativeProvider()).generate(_sample_decision_result())

    assert "fake-test-key-do-not-use-9f8e7d" not in caplog.text


# --- 22. API key never exposed via the response schema ----------------------


def test_narrative_output_schema_has_no_credential_field():
    from app.modules.reports.schemas import NarrativeOutput
    forbidden = {"api_key", "apikey", "authorization", "token", "secret", "credential"}
    assert forbidden.isdisjoint(set(NarrativeOutput.model_fields.keys()))


# --- 23/24. Router provider selection: disabled by default / enabled -------


def test_provider_disabled_by_default_returns_null_provider():
    assert settings.narrative_provider_enabled is False
    provider = reports_router._get_narrative_provider()
    assert isinstance(provider, NullNarrativeProvider)


def test_provider_enabled_returns_anthropic_adapter(monkeypatch):
    monkeypatch.setattr(settings, "narrative_provider_enabled", True)
    provider = reports_router._get_narrative_provider()
    assert isinstance(provider, AnthropicNarrativeProvider)


def test_provider_enabled_without_key_falls_back_to_null_provider(monkeypatch):
    monkeypatch.setattr(settings, "narrative_provider_enabled", True)
    monkeypatch.setattr(settings, "narrative_provider_api_key", "")
    provider = reports_router._get_narrative_provider()
    assert isinstance(provider, NullNarrativeProvider)
