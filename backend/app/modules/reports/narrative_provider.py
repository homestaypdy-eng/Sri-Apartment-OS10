"""Phase 13G — narrative provider abstraction.

Defines the vendor-agnostic boundary between the deterministic AI
Strategist pipeline and any future LLM. No real provider (OpenAI,
Anthropic, Gemini, or otherwise) is implemented here — only the
Protocol, an untrusted raw-output type, a deterministic mock used for
tests, and a "no provider configured" default used in production until
a real provider is added in a future phase.

The provider receives ONLY a NarrativeInput (already-minimized signals/
insights/recommendations — see reports/schemas.py). It never receives a
database session, an HTTP client, auth credentials, the raw
AIStrategistBundle, or any operational data.
"""
from __future__ import annotations

from typing import Protocol

from app.modules.reports.schemas import NarrativeInput

# Untrusted, unstructured provider output. Deliberately not typed as
# NarrativeOutput — it must pass through reports/narrative_validation.py
# before it is trusted as that shape.
RawNarrativeOutput = dict[str, object]


class NarrativeProviderUnavailable(Exception):
    """Raised by a provider that cannot produce a narrative right now
    (not configured, unreachable, rate-limited, etc). NarrativeService
    catches this — and any other provider exception — and falls back to
    the deterministic narrative; see narrative_service.py."""


class NarrativeProvider(Protocol):
    def generate_narrative(self, narrative_input: NarrativeInput) -> RawNarrativeOutput: ...


class NullNarrativeProvider:
    """The production default for Phase 13G: no real provider is wired up
    yet, so every call raises, and NarrativeService's fallback path
    always produces the deterministic narrative. This is the honest
    behavior for this phase — it must not be replaced with a real vendor
    SDK, API key, or network call here."""

    def generate_narrative(self, narrative_input: NarrativeInput) -> RawNarrativeOutput:
        raise NarrativeProviderUnavailable("No narrative provider is configured.")


class MockNarrativeProvider:
    """Deterministic, network-free provider for tests only. Produces
    output mechanically from the supplied NarrativeInput — every
    observation/action/risk is a verbatim copy of an existing
    statement/action/risk, tagged with the same id and classification it
    already carries. No randomness, no wall-clock reads, no invented
    prose — this exists to prove the "valid provider output" path
    through validation, not to simulate a real model's writing style."""

    def generate_narrative(self, narrative_input: NarrativeInput) -> RawNarrativeOutput:
        key_observations = [
            {"text": item.statement, "source_ids": [item.id], "classification": item.classification}
            for item in narrative_input.items
        ]
        priority_actions = [
            {
                "text": rec.action,
                "source_ids": [rec.recommendation_id],
                "requires_human_approval": rec.requires_human_approval,
            }
            for rec in narrative_input.recommendations
        ]
        risks = [
            {"text": rec.risk, "source_ids": [rec.recommendation_id], "classification": "SIGNAL"}
            for rec in narrative_input.recommendations
        ]
        executive_summary = (
            f"{len(narrative_input.items)} observation(s) and "
            f"{len(narrative_input.recommendations)} recommendation(s) supplied."
        )
        return {
            "executive_summary": executive_summary,
            "key_observations": key_observations,
            "priority_actions": priority_actions,
            "risks": risks,
            "questions_for_management": [],
        }
