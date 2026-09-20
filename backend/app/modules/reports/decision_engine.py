"""Phase 13E — AI Strategist deterministic decision engine.

Pure, in-memory transform: ``AIStrategistBundle -> AIStrategistDecisionResult``.
This module never opens a database session, never makes an HTTP/LLM call,
and never reads the wall clock or randomness — ``generated_at`` is copied
from the bundle itself, so the same bundle always produces the exact same
result.

Threshold discipline (carried over from the Phase 13D design): this module
must not activate any business rule that depends on an arbitrary,
unapproved numeric cutoff (e.g. "occupancy below 60% is concerning",
"cancellation rate above 15% is elevated"). No such thresholds have been
approved, so every rule below is STRUCTURAL — it fires only on:
  - the presence/absence of already-supplied data (e.g. an overdue count
    being non-zero, a GST payable figure being positive/negative/zero),
  - a severity/classification the backend already assigned (attention
    items, weekly facts), or
  - the sign of a direct current-vs-previous comparison already present
    in the bundle (a "direction", never a magnitude judgment).

"WATCH" is a valid severity in the schema, reserved for a future phase
once a magnitude threshold is explicitly approved. No rule in this module
ever emits it.

Every signal/insight/recommendation's evidence values are direct copies
(or, for booking-source/guest-segment signals, direct counts) of bundle
fields — nothing here recomputes a financial metric.
"""
from __future__ import annotations

import itertools

from app.modules.reports.schemas import (
    AIStrategistBundle,
    AIStrategistDecisionResult,
    AIStrategistEvidence,
    AIStrategistInsight,
    AIStrategistRecommendation,
    AIStrategistSignal,
    AIStrategistUnitPerformanceLine,
)

_SEVERITY_RANK = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "INFO": 3}
_ATTENTION_SEVERITY_MAP = {"critical": "CRITICAL", "warning": "WARNING"}

# The action text for each attention category is deliberately plain and
# advisory — it never claims a fix, only a review, and always requires a
# human to act on it (requires_human_approval is always True downstream).
_ATTENTION_RECOMMENDATION_ACTION = {
    "unpaid_balance": "Review outstanding payments requiring follow-up.",
    "guest_request": "Review open guest requests requiring follow-up.",
    "maintenance": "Review maintenance items marked as critical or warning.",
    "low_stock": "Review low-stock inventory items.",
}


def _ev(field: str, value) -> AIStrategistEvidence:
    return AIStrategistEvidence(field=field, value=str(value))


def _direction(current: float, previous: float) -> str:
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


# --- signal builders ------------------------------------------------------


def _signals_from_attention_items(bundle: AIStrategistBundle) -> list[AIStrategistSignal]:
    signals = []
    for idx, item in enumerate(bundle.needs_attention):
        path = f"needs_attention[{idx}]"
        signals.append(
            AIStrategistSignal(
                signal_id=f"attention:{item.category}:{item.reference_id}",
                category=item.category,
                severity=_ATTENTION_SEVERITY_MAP[item.severity],
                classification="FACT",
                statement=item.reason,
                source_fields=[
                    f"{path}.category",
                    f"{path}.severity",
                    f"{path}.reason",
                    f"{path}.reference_id",
                ],
                evidence=[
                    _ev(f"{path}.title", item.title),
                    _ev(f"{path}.reason", item.reason),
                    _ev(f"{path}.action", item.action),
                    _ev(f"{path}.reference_id", item.reference_id),
                ],
                confidence="HIGH",
            )
        )
    return signals


def _signal_outstanding(bundle: AIStrategistBundle) -> AIStrategistSignal | None:
    o = bundle.outstanding_payments
    if o.pending_count == 0:
        return None
    if o.overdue_count > 0:
        severity = "WARNING"
        statement = (
            f"{o.overdue_count} overdue payment(s) totalling {o.overdue_amount} "
            f"are outstanding, out of {o.pending_count} pending payment(s) "
            f"totalling {o.total_pending_balance}."
        )
    else:
        severity = "INFO"
        statement = (
            f"{o.pending_count} pending payment(s) totalling "
            f"{o.total_pending_balance} exist; none are overdue."
        )
    fields = [
        "outstanding_payments.pending_count",
        "outstanding_payments.overdue_count",
        "outstanding_payments.overdue_amount",
        "outstanding_payments.total_pending_balance",
    ]
    return AIStrategistSignal(
        signal_id="outstanding:summary",
        category="outstanding_payments",
        severity=severity,
        classification="FACT",
        statement=statement,
        source_fields=fields,
        evidence=[
            _ev("outstanding_payments.pending_count", o.pending_count),
            _ev("outstanding_payments.overdue_count", o.overdue_count),
            _ev("outstanding_payments.overdue_amount", o.overdue_amount),
            _ev("outstanding_payments.total_pending_balance", o.total_pending_balance),
        ],
        confidence="HIGH",
    )


def _signal_gst(bundle: AIStrategistBundle) -> AIStrategistSignal:
    g = bundle.gst
    if g.gst_payable > 0:
        statement = f"GST payable this period is {g.gst_payable}."
    elif g.gst_payable < 0:
        statement = (
            "GST input credit exceeds GST collected this period by "
            f"{abs(g.gst_payable)} (negative payable)."
        )
    else:
        statement = "GST payable this period is zero."
    fields = ["gst.gst_collected", "gst.gst_input_credit_eligible", "gst.gst_payable"]
    return AIStrategistSignal(
        signal_id="gst:payable",
        category="gst",
        severity="INFO",
        classification="FACT",
        statement=statement,
        source_fields=fields,
        evidence=[
            _ev("gst.gst_collected", g.gst_collected),
            _ev("gst.gst_input_credit_eligible", g.gst_input_credit_eligible),
            _ev("gst.gst_payable", g.gst_payable),
        ],
        confidence="HIGH",
    )


def _movement_signals(bundle: AIStrategistBundle) -> dict[str, tuple[str, AIStrategistSignal]]:
    bh = bundle.business_health
    specs = [
        ("Revenue", "revenue", bh.revenue_current, bh.revenue_previous,
         "business_health.revenue_current", "business_health.revenue_previous"),
        ("Occupancy", "occupancy", bh.occupancy.current, bh.occupancy.previous,
         "business_health.occupancy.current", "business_health.occupancy.previous"),
        ("ADR", "adr", bh.adr.current, bh.adr.previous,
         "business_health.adr.current", "business_health.adr.previous"),
        ("RevPAR", "revpar", bh.revpar.current, bh.revpar.previous,
         "business_health.revpar.current", "business_health.revpar.previous"),
    ]
    result: dict[str, tuple[str, AIStrategistSignal]] = {}
    for name, key, current, previous, current_field, previous_field in specs:
        direction = _direction(current, previous)
        verb = "unchanged" if direction == "flat" else direction
        statement = (
            f"{name} is {verb} this period compared to the previous period "
            f"({previous} -> {current})."
        )
        signal = AIStrategistSignal(
            signal_id=f"movement:{key}",
            category="movement",
            severity="INFO",
            classification="DERIVED",
            statement=statement,
            source_fields=[current_field, previous_field],
            evidence=[_ev(current_field, current), _ev(previous_field, previous)],
            confidence="HIGH",
        )
        result[key] = (direction, signal)
    return result


def _conflicting_direction_insights(
    movement: dict[str, tuple[str, AIStrategistSignal]],
) -> list[AIStrategistInsight]:
    insights = []
    for a, b in itertools.combinations(movement.keys(), 2):
        dir_a, sig_a = movement[a]
        dir_b, sig_b = movement[b]
        if {dir_a, dir_b} != {"up", "down"}:
            continue
        insights.append(
            AIStrategistInsight(
                insight_id=f"insight:conflict:{a}_vs_{b}",
                category="movement",
                title=f"{a.capitalize()} and {b.capitalize()} moved in different directions",
                severity="INFO",
                classification="SIGNAL",
                statement=(
                    f"{a.capitalize()} is {dir_a} and {b.capitalize()} is {dir_b} this period. "
                    "The data does not establish why these moved in different directions."
                ),
                evidence=sig_a.evidence + sig_b.evidence,
                source_fields=sig_a.source_fields + sig_b.source_fields,
                confidence="MEDIUM",
                signal_ids=[sig_a.signal_id, sig_b.signal_id],
            )
        )
    return insights


def _signal_cancellations(bundle: AIStrategistBundle) -> AIStrategistSignal:
    c = bundle.cancellations
    statement = (
        f"Of {c.total_bookings_in_period} firm booking(s) this period, "
        f"{c.cancelled_count} were cancelled ({c.cancellation_rate_percent}%) and "
        f"{c.no_show_count} were no-shows ({c.no_show_rate_percent}%)."
    )
    fields = [
        "cancellations.total_bookings_in_period",
        "cancellations.cancelled_count",
        "cancellations.cancellation_rate_percent",
        "cancellations.no_show_count",
        "cancellations.no_show_rate_percent",
    ]
    return AIStrategistSignal(
        signal_id="cancellations:summary",
        category="cancellations",
        severity="INFO",
        classification="FACT",
        statement=statement,
        source_fields=fields,
        evidence=[
            _ev("cancellations.total_bookings_in_period", c.total_bookings_in_period),
            _ev("cancellations.cancelled_count", c.cancelled_count),
            _ev("cancellations.cancellation_rate_percent", c.cancellation_rate_percent),
            _ev("cancellations.no_show_count", c.no_show_count),
            _ev("cancellations.no_show_rate_percent", c.no_show_rate_percent),
        ],
        confidence="HIGH",
    )


def _unit_signal(bucket: str, idx: int, u: AIStrategistUnitPerformanceLine) -> AIStrategistSignal:
    path = f"unit_performance.{bucket}_units[{idx}]"
    fields = [
        f"{path}.unit_id", f"{path}.unit_name", f"{path}.revenue",
        f"{path}.occupancy_rate_percent", f"{path}.adr", f"{path}.revpar",
    ]
    return AIStrategistSignal(
        signal_id=f"unit_performance:{bucket}:{u.unit_id}",
        category="unit_performance",
        severity="INFO",
        classification="FACT",
        statement=(
            f"Unit '{u.unit_name}' (among the units supplied in this bundle, {bucket} group): "
            f"revenue {u.revenue}, occupancy {u.occupancy_rate_percent}%, "
            f"ADR {u.adr}, RevPAR {u.revpar}."
        ),
        source_fields=fields,
        evidence=[
            _ev(f"{path}.unit_id", u.unit_id),
            _ev(f"{path}.unit_name", u.unit_name),
            _ev(f"{path}.revenue", u.revenue),
            _ev(f"{path}.occupancy_rate_percent", u.occupancy_rate_percent),
            _ev(f"{path}.adr", u.adr),
            _ev(f"{path}.revpar", u.revpar),
        ],
        confidence="HIGH",
    )


def _unit_signals(bundle: AIStrategistBundle) -> list[AIStrategistSignal]:
    up = bundle.unit_performance
    signals = [_unit_signal("top", idx, u) for idx, u in enumerate(up.top_units)]
    signals += [_unit_signal("bottom", idx, u) for idx, u in enumerate(up.bottom_units)]
    return signals


def _signal_booking_sources(bundle: AIStrategistBundle) -> AIStrategistSignal | None:
    bs = bundle.booking_sources
    if not bs.revenue_by_source:
        return None
    sources = sorted(bs.revenue_by_source.items())
    parts = ", ".join(f"{src}: {val}" for src, val in sources)
    return AIStrategistSignal(
        signal_id="booking_sources:supplied",
        category="booking_sources",
        severity="INFO",
        classification="FACT",
        statement=f"Revenue by source this period ({bs.revenue_definition}): {parts}.",
        source_fields=[f"booking_sources.revenue_by_source.{src}" for src, _ in sources],
        evidence=[_ev(f"booking_sources.revenue_by_source.{src}", val) for src, val in sources],
        confidence="HIGH",
    )


def _signal_guest_segments(bundle: AIStrategistBundle) -> AIStrategistSignal | None:
    guests = bundle.top_guests
    if not guests:
        return None
    repeat_count = sum(1 for g in guests if g.is_repeat_guest)
    evidence = []
    fields = []
    for idx, g in enumerate(guests):
        evidence.append(_ev(f"top_guests[{idx}].guest_id", g.guest_id))
        evidence.append(_ev(f"top_guests[{idx}].is_repeat_guest", g.is_repeat_guest))
        fields.append(f"top_guests[{idx}].guest_id")
        fields.append(f"top_guests[{idx}].is_repeat_guest")
    return AIStrategistSignal(
        signal_id="guest_segments:supplied",
        category="guest_segments",
        severity="INFO",
        classification="DERIVED",
        statement=(
            f"Among the {len(guests)} guest(s) supplied in this bundle, {repeat_count} are "
            f"repeat guests and {len(guests) - repeat_count} are new. This reflects only the "
            "guests included in this bundle, not the full guest base."
        ),
        source_fields=fields,
        evidence=evidence,
        confidence="LOW",
    )


def _weekly_fact_signals(bundle: AIStrategistBundle) -> list[AIStrategistSignal]:
    signals = []
    for idx, wf in enumerate(bundle.weekly_facts):
        classification = "FACT" if wf.category == "fact" else "DERIVED"
        signals.append(
            AIStrategistSignal(
                signal_id=f"weekly_fact:{idx}",
                category="weekly_fact",
                severity="INFO",
                classification=classification,
                statement=f"{wf.label}: {wf.value}",
                source_fields=[
                    f"weekly_facts[{idx}].label",
                    f"weekly_facts[{idx}].value",
                    f"weekly_facts[{idx}].category",
                ],
                evidence=[
                    _ev(f"weekly_facts[{idx}].label", wf.label),
                    _ev(f"weekly_facts[{idx}].value", wf.value),
                ],
                confidence="HIGH",
            )
        )
    return signals


# --- insight builders ------------------------------------------------------


def _attention_insights(attention_signals: list[AIStrategistSignal]) -> list[AIStrategistInsight]:
    by_category: dict[str, list[AIStrategistSignal]] = {}
    for s in attention_signals:
        by_category.setdefault(s.category, []).append(s)
    insights = []
    for category, sigs in by_category.items():
        severity = "CRITICAL" if any(s.severity == "CRITICAL" for s in sigs) else "WARNING"
        label = category.replace("_", " ")
        insights.append(
            AIStrategistInsight(
                insight_id=f"insight:attention:{category}",
                category=category,
                title=f"{len(sigs)} {label} item(s) need attention",
                severity=severity,
                classification="FACT",
                statement=(
                    f"{len(sigs)} {label} item(s) are flagged in the existing "
                    "Needs Attention list."
                ),
                evidence=[e for s in sigs for e in s.evidence],
                source_fields=[f for s in sigs for f in s.source_fields],
                confidence="HIGH",
                signal_ids=[s.signal_id for s in sigs],
            )
        )
    return insights


def _wrap_single_insight(
    signal: AIStrategistSignal,
    title: str,
    insight_id: str,
    period=None,
) -> AIStrategistInsight:
    return AIStrategistInsight(
        insight_id=insight_id,
        category=signal.category,
        title=title,
        severity=signal.severity,
        classification=signal.classification,
        statement=signal.statement,
        evidence=signal.evidence,
        source_fields=signal.source_fields,
        period=period,
        confidence=signal.confidence,
        signal_ids=[signal.signal_id],
    )


def _unit_performance_insight(unit_signals: list[AIStrategistSignal]) -> AIStrategistInsight | None:
    if not unit_signals:
        return None
    return AIStrategistInsight(
        insight_id="insight:unit_performance:supplied",
        category="unit_performance",
        title="Unit performance as supplied in this bundle",
        severity="INFO",
        classification="FACT",
        statement=(
            f"{len(unit_signals)} unit performance line(s) were supplied in this bundle "
            "(top and bottom performers from the existing Unit Performance report). This "
            "is a direct listing, not a portfolio-wide ranking or a comparative judgment "
            "about any unit."
        ),
        evidence=[e for s in unit_signals for e in s.evidence],
        source_fields=[f for s in unit_signals for f in s.source_fields],
        confidence="HIGH",
        signal_ids=[s.signal_id for s in unit_signals],
    )


# --- recommendation builder --------------------------------------------


def _build_recommendations(
    attention_insights: list[AIStrategistInsight],
    gst_signal: AIStrategistSignal,
    gst_payable: float,
) -> list[AIStrategistRecommendation]:
    candidates: list[tuple[str, str, str, dict]] = []
    for insight in attention_insights:
        candidates.append((
            insight.severity,
            insight.category,
            f"rec:attention:{insight.category}",
            dict(
                category=insight.category,
                action=_ATTENTION_RECOMMENDATION_ACTION[insight.category],
                rationale=insight.statement,
                evidence=insight.evidence,
                source_fields=insight.source_fields,
                expected_effect=None,
                risk="Not reviewing may delay resolution of an already-flagged operational item.",
                confidence="HIGH",
                requires_human_approval=True,
                linked_insight_ids=[insight.insight_id],
            ),
        ))
    if gst_payable > 0:
        candidates.append((
            "INFO",
            "gst",
            "rec:gst:payable",
            dict(
                category="gst",
                action="Review the GST payable figure before filing or reconciliation.",
                rationale=gst_signal.statement,
                evidence=gst_signal.evidence,
                source_fields=gst_signal.source_fields,
                expected_effect=None,
                risk="Filing without review may misstate GST payable.",
                confidence="HIGH",
                requires_human_approval=True,
                linked_insight_ids=["insight:gst:payable"],
            ),
        ))
    # Deterministic ordering only: severity, then category, then id.
    # Deliberately not weighted by any financial amount — no such
    # weighting has been approved (see decision_engine module docstring).
    candidates.sort(key=lambda c: (_SEVERITY_RANK[c[0]], c[1], c[2]))
    return [
        AIStrategistRecommendation(recommendation_id=rec_id, priority=idx, **kwargs)
        for idx, (_severity, _category, rec_id, kwargs) in enumerate(candidates)
    ]


# --- entry point ------------------------------------------------------


def run_decision_engine(bundle: AIStrategistBundle) -> AIStrategistDecisionResult:
    """Pure transform: same bundle in, same AIStrategistDecisionResult out.

    No database session, no HTTP client, no LLM, no wall-clock or random
    access — generated_at is copied from the bundle itself.
    """
    current_period = bundle.business_health.current_period

    signals: list[AIStrategistSignal] = []
    insights: list[AIStrategistInsight] = []

    attention_signals = _signals_from_attention_items(bundle)
    signals.extend(attention_signals)
    attention_insights = _attention_insights(attention_signals)
    insights.extend(attention_insights)

    outstanding_signal = _signal_outstanding(bundle)
    if outstanding_signal is not None:
        signals.append(outstanding_signal)
        insights.append(_wrap_single_insight(
            outstanding_signal, "Outstanding payments summary",
            "insight:outstanding:summary", period=current_period,
        ))

    gst_signal = _signal_gst(bundle)
    signals.append(gst_signal)
    insights.append(_wrap_single_insight(
        gst_signal, "GST payable summary", "insight:gst:payable", period=current_period,
    ))

    movement = _movement_signals(bundle)
    for key, (_direction_value, sig) in movement.items():
        signals.append(sig)
        insights.append(_wrap_single_insight(
            sig, f"{key.capitalize()} movement", f"insight:movement:{key}", period=current_period,
        ))
    insights.extend(_conflicting_direction_insights(movement))

    cancellations_signal = _signal_cancellations(bundle)
    signals.append(cancellations_signal)
    insights.append(_wrap_single_insight(
        cancellations_signal, "Cancellations and no-shows summary",
        "insight:cancellations:summary", period=bundle.cancellations.current_period,
    ))

    unit_signals = _unit_signals(bundle)
    signals.extend(unit_signals)
    unit_insight = _unit_performance_insight(unit_signals)
    if unit_insight is not None:
        insights.append(unit_insight)

    booking_sources_signal = _signal_booking_sources(bundle)
    if booking_sources_signal is not None:
        signals.append(booking_sources_signal)
        insights.append(_wrap_single_insight(
            booking_sources_signal, "Booking source distribution",
            "insight:booking_sources:supplied", period=bundle.booking_sources.current_period,
        ))

    guest_segments_signal = _signal_guest_segments(bundle)
    if guest_segments_signal is not None:
        signals.append(guest_segments_signal)
        insights.append(_wrap_single_insight(
            guest_segments_signal, "Guest segment distribution (top guests supplied)",
            "insight:guest_segments:supplied",
        ))

    weekly_signals = _weekly_fact_signals(bundle)
    signals.extend(weekly_signals)
    for idx, (wf, sig) in enumerate(zip(bundle.weekly_facts, weekly_signals)):
        insights.append(_wrap_single_insight(sig, wf.label, f"insight:weekly_fact:{idx}"))

    recommendations = _build_recommendations(attention_insights, gst_signal, bundle.gst.gst_payable)

    return AIStrategistDecisionResult(
        generated_at=bundle.generated_at,
        signals=signals,
        insights=insights,
        recommendations=recommendations,
    )
