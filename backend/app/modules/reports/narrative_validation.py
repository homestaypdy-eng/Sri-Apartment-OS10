"""Phase 13G — narrative output validation.

Turns an untrusted RawNarrativeOutput (whatever a provider returned) into
a strict NarrativeOutput, or rejects it (returns None) so the caller can
fall back to the deterministic narrative. Nothing here calculates a
financial metric or invents data — every check is a comparison against
NarrativeInput, which is itself a direct copy of Phase 13E's output.

Checks performed, in order:
  A. raw shape is a dict with the expected top-level keys
  B/C/E. every source_id resolves to a real signal/insight/recommendation
     id already present in NarrativeInput (this also rules out invented
     recommendation ids)
  D/N. every observation/action carries at least one source_id, and any
     financial-looking number in its text is traceable to that
     reference's own evidence (see _numbers_are_traceable)
  F/G. if the raw item claims a severity/classification, it must match
     the referenced item's true value
  H. requires_human_approval can never be forced False
  I. unsupported/untraceable financial numbers are rejected outright —
     no fuzzy reconciliation is attempted
  J/K. forbidden causal/predictive language (Phase 13G section 7, plus
     the Phase 13E unsupported-claim guardrails) is rejected
  L. sensitive-field substrings are rejected
  M. output length limits are enforced

Any failure at any step rejects the ENTIRE narrative (returns None) —
this module never assembles a partially-trusted output.
"""
from __future__ import annotations

import re

from app.modules.reports.schemas import (
    ActionRef,
    NarrativeInput,
    NarrativeOutput,
    ObservationRef,
)

_MAX_SUMMARY_LENGTH = 2000
_MAX_ITEM_TEXT_LENGTH = 500
_MAX_QUESTION_LENGTH = 300
_MAX_QUESTIONS = 10
_MAX_LIST_LENGTH = 50

# Phase 13G section 7's explicit minimum guard list, plus the Phase 13E
# causal/unsupported-claim guardrails this module is required to preserve.
_FORBIDDEN_PHRASES = (
    "because", "caused", "causes", "causing", "due to", "driven by", "will",
    "guaranteed", "certain", "definitely", "predict", "forecast",
    "leads to", "leading to", "resulting in", "as a result of", "so that",
    "concerning", "underperform", "elevated", "declining trend", "risk of",
    "likely to", "root cause", "should", "recommend that you",
    "worrying", "alarming",
)

_SENSITIVE_SUBSTRINGS = (
    "phone", "email", "password", "token", "secret", "credential",
    "vip", "blacklist",
)

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _normalize_number(token: str) -> float | None:
    cleaned = token.replace(",", "").strip()
    if cleaned in ("", "-", "+", "."):
        return None
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _text_is_safe(text: str) -> bool:
    lowered = text.lower()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            return False
    for phrase in _SENSITIVE_SUBSTRINGS:
        if phrase in lowered:
            return False
    return True


def _evidence_numbers(referenced) -> set[float]:
    numbers = set()
    for ref in referenced:
        for ev in ref.evidence:
            for token in _NUMBER_RE.findall(ev.value):
                n = _normalize_number(token)
                if n is not None:
                    numbers.add(n)
    return numbers


def _numbers_are_traceable(text: str, referenced) -> bool:
    text_numbers = set()
    for token in _NUMBER_RE.findall(text):
        n = _normalize_number(token)
        if n is not None:
            text_numbers.add(n)
    if not text_numbers:
        return True
    return text_numbers.issubset(_evidence_numbers(referenced))


def _validate_observation(raw_obs, items_by_id, recs_by_id) -> ObservationRef | None:
    if not isinstance(raw_obs, dict):
        return None
    text = raw_obs.get("text")
    source_ids = raw_obs.get("source_ids")
    if not isinstance(text, str) or not text or len(text) > _MAX_ITEM_TEXT_LENGTH:
        return None
    if not isinstance(source_ids, list) or not source_ids:
        return None

    referenced = []
    for sid in source_ids:
        if not isinstance(sid, str):
            return None
        if sid in items_by_id:
            referenced.append(items_by_id[sid])
        elif sid in recs_by_id:
            referenced.append(recs_by_id[sid])
        else:
            return None  # unknown id: invented or stale reference

    true_classifications = {getattr(r, "classification", "SIGNAL") for r in referenced}
    claimed_classification = raw_obs.get("classification")
    if claimed_classification is not None and claimed_classification not in true_classifications:
        return None
    classification = claimed_classification or next(iter(true_classifications))

    claimed_severity = raw_obs.get("severity")
    if claimed_severity is not None:
        true_severities = {getattr(r, "severity") for r in referenced if hasattr(r, "severity")}
        if true_severities and claimed_severity not in true_severities:
            return None

    if not _text_is_safe(text):
        return None
    if not _numbers_are_traceable(text, referenced):
        return None

    return ObservationRef(text=text, source_ids=source_ids, classification=classification)


def _validate_action(raw_action, recs_by_id) -> ActionRef | None:
    if not isinstance(raw_action, dict):
        return None
    text = raw_action.get("text")
    source_ids = raw_action.get("source_ids")
    if not isinstance(text, str) or not text or len(text) > _MAX_ITEM_TEXT_LENGTH:
        return None
    if not isinstance(source_ids, list) or not source_ids:
        return None

    referenced = []
    for sid in source_ids:
        if not isinstance(sid, str) or sid not in recs_by_id:
            return None  # invented or unknown recommendation id
        referenced.append(recs_by_id[sid])

    true_approval = all(r.requires_human_approval for r in referenced)
    if not true_approval:
        return None  # 13E always sets this True; never trust a claim otherwise
    claimed_approval = raw_action.get("requires_human_approval")
    if claimed_approval is not None and claimed_approval is not True:
        return None  # cannot be weakened to False

    claimed_priority = raw_action.get("priority")
    if claimed_priority is not None:
        true_priorities = {r.priority for r in referenced}
        if claimed_priority not in true_priorities:
            return None

    if not _text_is_safe(text):
        return None
    if not _numbers_are_traceable(text, referenced):
        return None

    return ActionRef(text=text, source_ids=source_ids, requires_human_approval=True)


def validate_raw_narrative_output(raw: object, narrative_input: NarrativeInput) -> NarrativeOutput | None:
    if not isinstance(raw, dict):
        return None

    items_by_id = {item.id: item for item in narrative_input.items}
    recs_by_id = {r.recommendation_id: r for r in narrative_input.recommendations}

    raw_observations = raw.get("key_observations", [])
    raw_risks = raw.get("risks", [])
    raw_actions = raw.get("priority_actions", [])
    raw_summary = raw.get("executive_summary")
    raw_questions = raw.get("questions_for_management", [])

    if not isinstance(raw_observations, list) or len(raw_observations) > _MAX_LIST_LENGTH:
        return None
    if not isinstance(raw_risks, list) or len(raw_risks) > _MAX_LIST_LENGTH:
        return None
    if not isinstance(raw_actions, list) or len(raw_actions) > _MAX_LIST_LENGTH:
        return None
    if not isinstance(raw_summary, str) or not raw_summary or len(raw_summary) > _MAX_SUMMARY_LENGTH:
        return None
    if not isinstance(raw_questions, list) or len(raw_questions) > _MAX_QUESTIONS:
        return None
    for q in raw_questions:
        if not isinstance(q, str) or len(q) > _MAX_QUESTION_LENGTH or not _text_is_safe(q):
            return None

    if not _text_is_safe(raw_summary):
        return None

    observations = []
    for raw_obs in raw_observations:
        validated = _validate_observation(raw_obs, items_by_id, recs_by_id)
        if validated is None:
            return None
        observations.append(validated)

    risks = []
    for raw_risk in raw_risks:
        validated = _validate_observation(raw_risk, items_by_id, recs_by_id)
        if validated is None:
            return None
        risks.append(validated)

    actions = []
    for raw_action in raw_actions:
        validated = _validate_action(raw_action, recs_by_id)
        if validated is None:
            return None
        actions.append(validated)

    return NarrativeOutput(
        executive_summary=raw_summary,
        key_observations=observations,
        priority_actions=actions,
        risks=risks,
        questions_for_management=raw_questions,
        generated_at=narrative_input.generated_at,
        source="llm",
    )
