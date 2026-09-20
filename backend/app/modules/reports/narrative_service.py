"""Phase 13G — narrative extraction, orchestration, and deterministic
fallback.

Flow: AIStrategistDecisionResult -> NarrativeInput -> NarrativeProvider ->
validate -> NarrativeOutput (source="llm"), or on any failure ->
deterministic fallback (source="deterministic_fallback") built directly
from the same decision result, with no invented prose.

Zero database queries, zero HTTP calls, zero financial computation happen
anywhere in this module — everything here is a pure, in-memory transform
of an already-computed AIStrategistDecisionResult (Phase 13E).
"""
from __future__ import annotations

import logging

from app.modules.reports.narrative_provider import NarrativeProvider
from app.modules.reports.narrative_validation import validate_raw_narrative_output
from app.modules.reports.schemas import (
    ActionRef,
    AIStrategistDecisionResult,
    NarrativeInput,
    NarrativeInputItem,
    NarrativeInputRecommendation,
    NarrativeOutput,
    ObservationRef,
)

logger = logging.getLogger("sri_apartment_os")


def build_narrative_input(decision_result: AIStrategistDecisionResult) -> NarrativeInput:
    """Pure extraction: same decision_result -> byte-identical
    NarrativeInput, always. Never touches AIStrategistBundle, the
    database, or an HTTP client."""
    items = [
        NarrativeInputItem(
            id=s.signal_id,
            kind="signal",
            classification=s.classification,
            category=s.category,
            severity=s.severity,
            statement=s.statement,
            evidence=s.evidence,
            source_fields=s.source_fields,
            confidence=s.confidence,
        )
        for s in decision_result.signals
    ]
    items += [
        NarrativeInputItem(
            id=i.insight_id,
            kind="insight",
            classification=i.classification,
            category=i.category,
            severity=i.severity,
            statement=i.statement,
            evidence=i.evidence,
            source_fields=i.source_fields,
            confidence=i.confidence,
            period=i.period,
            linked_ids=i.signal_ids,
        )
        for i in decision_result.insights
    ]
    recommendations = [
        NarrativeInputRecommendation(
            recommendation_id=r.recommendation_id,
            priority=r.priority,
            category=r.category,
            action=r.action,
            rationale=r.rationale,
            risk=r.risk,
            evidence=r.evidence,
            source_fields=r.source_fields,
            confidence=r.confidence,
            requires_human_approval=r.requires_human_approval,
            linked_insight_ids=r.linked_insight_ids,
        )
        for r in decision_result.recommendations
    ]
    return NarrativeInput(
        generated_at=decision_result.generated_at,
        items=items,
        recommendations=recommendations,
    )


def deterministic_fallback(narrative_input: NarrativeInput) -> NarrativeOutput:
    """Never fabricates prose: every observation/action/risk text is a
    verbatim copy of a statement/action/risk that Phase 13E already
    produced."""
    observations = [
        ObservationRef(text=item.statement, source_ids=[item.id], classification=item.classification)
        for item in narrative_input.items
    ]
    actions = [
        ActionRef(text=rec.action, source_ids=[rec.recommendation_id], requires_human_approval=rec.requires_human_approval)
        for rec in narrative_input.recommendations
    ]
    risks = [
        ObservationRef(text=rec.risk, source_ids=[rec.recommendation_id], classification="SIGNAL")
        for rec in narrative_input.recommendations
    ]
    summary = (
        f"{len(narrative_input.items)} deterministic observation(s) and "
        f"{len(narrative_input.recommendations)} recommendation(s) are available below."
    )
    return NarrativeOutput(
        executive_summary=summary,
        key_observations=observations,
        priority_actions=actions,
        risks=risks,
        questions_for_management=[],
        generated_at=narrative_input.generated_at,
        source="deterministic_fallback",
    )


class NarrativeService:
    """Orchestrates extraction -> provider call -> validation ->
    fallback. The provider boundary is the one place this module catches
    a broad Exception deliberately: a narrative provider is an external,
    arbitrary-vendor dependency (network, auth, rate limits, malformed
    responses) whose failure modes can't be enumerated the way a specific
    internal dependency's can, and unlike Phase 11C's SMTP case, nothing
    is silently reported as success here — every fallback is explicitly
    labeled source="deterministic_fallback", never hidden."""

    def __init__(self, provider: NarrativeProvider):
        self._provider = provider

    def generate(self, decision_result: AIStrategistDecisionResult) -> NarrativeOutput:
        narrative_input = build_narrative_input(decision_result)
        try:
            raw = self._provider.generate_narrative(narrative_input)
        except Exception as e:
            logger.error(f"Narrative provider failed: {type(e).__name__}: {e}")
            return deterministic_fallback(narrative_input)

        validated = validate_raw_narrative_output(raw, narrative_input)
        if validated is None:
            logger.error("Narrative provider output failed validation; using deterministic fallback.")
            return deterministic_fallback(narrative_input)
        return validated
