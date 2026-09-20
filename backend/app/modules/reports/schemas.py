from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class RevenueBreakdown(BaseModel):
    total: float
    by_source: dict[str, float]


class GstBreakdown(BaseModel):
    cgst: float
    sgst: float
    igst: float
    total: float
    invoice_count: int


class SummaryReport(BaseModel):
    date_from: date
    date_to: date
    revenue: RevenueBreakdown
    total_bookings: int
    room_nights_sold: int
    available_room_nights: int
    occupancy_rate_percent: float
    adr: float
    revpar: float
    gst: GstBreakdown
    maintenance_cost_total: float
    net_revenue_after_maintenance: float


class PeriodRange(BaseModel):
    date_from: date
    date_to: date


class MetricComparison(BaseModel):
    current: float
    previous: float
    change_percent: float | None
    change_percentage_points: float | None = None


class SummaryComparison(BaseModel):
    current_period: PeriodRange
    previous_period: PeriodRange
    occupancy: MetricComparison
    adr: MetricComparison
    revpar: MetricComparison


class CancellationReport(BaseModel):
    """
    "Of the firm bookings scheduled to check in during this period, what
    fraction were cancelled?" — check_in-date basis, same convention as
    every other booking metric in this module. This is NOT "cancellations
    recorded during the period": OS10 has no cancellation timestamp, so a
    booking cancelled today for a stay three months from now counts toward
    that future period, not today. See build_cancellation_report() in
    reports/service.py for the exact rule.

    The firm-booking population (the denominator) is confirmed +
    checked_in + checked_out + cancelled + no_show — "inquiry" bookings
    (unconfirmed holds) are excluded entirely, from both the cancellation
    and no-show rates.
    """
    current_period: PeriodRange
    previous_period: PeriodRange
    total_bookings_in_period: int
    cancelled_count: int
    cancellation_rate_percent: float
    no_show_count: int
    no_show_rate_percent: float
    previous_total_bookings_in_period: int
    previous_cancelled_count: int
    previous_cancellation_rate_percent: float
    previous_no_show_count: int
    previous_no_show_rate_percent: float
    cancellation_rate_change_percent: float | None
    no_show_rate_change_percent: float | None


class ApartmentPerformanceLine(BaseModel):
    unit_id: str
    unit_name: str
    month_revenue: float
    month_expenses: float
    month_profit: float
    month_nights_booked: int
    month_occupancy_percent: float


class MonthlyTrendPoint(BaseModel):
    year: int
    month: int
    revenue: float
    expenses: float
    profit: float


class AttentionItem(BaseModel):
    """One deterministic, rule-based "needs attention today" exception.
    Every field is derived directly from existing operational data — see
    build_attention_items() in reports/service.py for the exact rule
    behind each item; nothing here is predicted or AI-generated."""
    category: Literal["unpaid_balance", "guest_request", "maintenance", "low_stock"]
    severity: Literal["critical", "warning"]
    title: str
    reason: str
    action: str
    reference_id: str
    link: str


class OperationalDashboard(BaseModel):
    as_of: date
    arrivals_today: int
    departures_today: int
    in_house_guests: int
    total_units: int
    total_guests: int
    active_bookings: int
    vacant_units: int
    occupancy_percent: float
    pending_checkins: int
    pending_checkouts: int
    pending_payments_count: int
    pending_payments_amount: float
    housekeeping_pending: int
    maintenance_open: int
    guest_requests_open: int
    low_stock_count: int
    staff_present_today: int
    staff_absent_today: int
    follow_ups_due_today: int
    today_revenue: float
    today_expenses: float
    today_profit: float
    month_revenue: float
    month_expenses: float
    month_profit: float
    apartment_performance: list[ApartmentPerformanceLine]
    monthly_trend: list[MonthlyTrendPoint]
    attention_items: list[AttentionItem]


class AIStrategistBusinessHealth(BaseModel):
    """
    Revenue here is the Reports Center definition (rate_applied x nights +
    extra_guest_charge) — NOT the same as Apartment P&L/Income Register,
    which exclude extra_guest_charge. revenue_definition states this
    explicitly so a future AI consumer never conflates the two.
    """
    current_period: PeriodRange
    previous_period: PeriodRange
    revenue_current: float
    revenue_previous: float
    revenue_definition: str
    occupancy: MetricComparison
    adr: MetricComparison
    revpar: MetricComparison


class AIStrategistCancellations(BaseModel):
    current_period: PeriodRange
    previous_period: PeriodRange
    total_bookings_in_period: int
    cancelled_count: int
    cancellation_rate_percent: float
    no_show_count: int
    no_show_rate_percent: float


class AIStrategistOutstanding(BaseModel):
    total_pending_balance: float
    pending_count: int
    overdue_count: int
    overdue_amount: float


class AIStrategistUnitPerformanceLine(BaseModel):
    unit_id: str
    unit_name: str
    revenue: float
    occupancy_rate_percent: float
    adr: float
    revpar: float


class AIStrategistUnitPerformance(BaseModel):
    current_period: PeriodRange
    top_units: list[AIStrategistUnitPerformanceLine]
    bottom_units: list[AIStrategistUnitPerformanceLine]


class AIStrategistBookingSources(BaseModel):
    current_period: PeriodRange
    revenue_by_source: dict[str, float]
    revenue_definition: str


class AIStrategistGuestSegment(BaseModel):
    guest_id: str
    name: str
    is_repeat_guest: bool
    total_bookings: int
    total_spent: float
    latest_source: str | None
    last_stay: date | None


class AIStrategistMaintenance(BaseModel):
    current_period: PeriodRange
    maintenance_cost_total: float


class AIStrategistGst(BaseModel):
    current_period: PeriodRange
    gst_collected: float
    gst_input_credit_eligible: float
    gst_payable: float


class AIStrategistWeeklyFact(BaseModel):
    """category is backend-assigned and may only be 'fact' or
    'derived_metric' — 'management_inference' is deliberately not a valid
    value here, since 13B must never generate narrative/causal claims."""
    label: str
    value: str
    category: Literal["fact", "derived_metric"]


# --- Phase 19F: Finance data integration (FACTS only — see
# finance/service.py::financial_snapshot for the source-of-truth mapping).
# Every field below is a direct read from an existing canonical Finance/
# Loan/OTA calculation; nothing is recomputed here or in decision_engine.py,
# which does not reference this block at all (it stays untouched — see
# AI ANALYSIS/RECOMMENDATIONS in AIStrategistDecisionResult below).

class AIStrategistFinancePnL(BaseModel):
    period_year: int
    period_month: int
    period_note: str
    revenue: float
    # None whenever property_id is not given: the canonical Property P&L
    # (property_pnl/apartment_pnl) is query-bounded only per single
    # property, not across an entire portfolio in one automatic bundle
    # call — see financial_snapshot()'s docstring. Never estimated as a
    # substitute; revenue alone (a bounded, portfolio-wide figure from
    # build_summary) is still always populated above.
    operating_expenses: float | None
    central_expenses: float | None
    net_profit: float | None
    properties_included: int


class AIStrategistFinanceCashFlow(BaseModel):
    date_from: date
    date_to: date
    cash_in_payments: float
    cash_in_deposits: float
    cash_in_ota_payouts: float
    cash_in_total: float
    cash_out_expenses: float
    cash_out_withdrawals: float
    cash_out_deposit_refunds: float
    cash_out_loan_emi: float
    cash_out_total: float
    net_cash_flow: float
    cash_in_bank: float
    cash_in_hand: float


class AIStrategistFinanceBudgetLine(BaseModel):
    category: str
    budgeted_amount: float
    actual_amount: float
    variance: float
    variance_percent: float | None


class AIStrategistFinanceBudget(BaseModel):
    period_year: int
    period_month: int
    total_budgeted: float
    total_actual: float
    lines: list[AIStrategistFinanceBudgetLine]


class AIStrategistFinanceLoanLine(BaseModel):
    loan_id: str
    loan_name: str
    lender_name: str
    principal_amount: float | None
    emi_amount: float


class AIStrategistFinanceLoans(BaseModel):
    total_active_loans: int
    total_monthly_emi_obligation: float
    upcoming_emi_count: int
    upcoming_emi_amount: float
    overdue_emi_count: int
    overdue_emi_amount: float
    paid_this_month_count: int
    paid_this_month_amount: float
    outstanding_this_month_count: int
    outstanding_this_month_amount: float
    active_loans: list[AIStrategistFinanceLoanLine]


class AIStrategistFinanceOTA(BaseModel):
    commission_total: float
    gst_total: float
    expected_payout_total: float
    actual_received_total: float
    reconciled_count: int
    unreconciled_count: int
    discrepancy_count: int
    discrepancy_total: float
    missing_commission_count: int
    missing_commission_gross_total: float


class AIStrategistFinanceExpenseBreakdown(BaseModel):
    payroll_total: float
    utilities_total: float
    purchases_total: float


class AIStrategistFinanceObligations(BaseModel):
    emi_overdue_amount: float
    emi_overdue_count: int
    emi_due_this_month_amount: float
    ota_unreconciled_expected_payout: float
    guest_outstanding_balance: float


class AIStrategistFinance(BaseModel):
    """FACTS only. AI Strategist's decision engine (below) does not read
    this block — no signal, insight, or recommendation is generated from
    it in Phase 19F; it exists purely as validated read-only context."""
    period: PeriodRange
    source_note: str
    pnl: AIStrategistFinancePnL
    cash_flow: AIStrategistFinanceCashFlow
    budget: AIStrategistFinanceBudget
    loans: AIStrategistFinanceLoans
    ota: AIStrategistFinanceOTA
    expenses: AIStrategistFinanceExpenseBreakdown
    outstanding_obligations: AIStrategistFinanceObligations


class AIStrategistBundle(BaseModel):
    generated_at: datetime
    business_health: AIStrategistBusinessHealth
    needs_attention: list[AttentionItem]
    cancellations: AIStrategistCancellations
    outstanding_payments: AIStrategistOutstanding
    unit_performance: AIStrategistUnitPerformance
    booking_sources: AIStrategistBookingSources
    top_guests: list[AIStrategistGuestSegment]
    maintenance: AIStrategistMaintenance
    gst: AIStrategistGst
    weekly_facts: list[AIStrategistWeeklyFact]
    finance: AIStrategistFinance


# --- Phase 13E: deterministic decision engine ---------------------------
#
# These schemas describe the OUTPUT of a pure, in-memory transform over an
# already-built AIStrategistBundle (see reports/decision_engine.py). The
# engine never queries the database, never calls an LLM, and never invents
# a business threshold that has not been explicitly approved — see the
# module docstring in decision_engine.py for the full rule set and why
# each rule requires no arbitrary cutoff.


class AIStrategistEvidence(BaseModel):
    """One (dotted bundle path, stringified value) pair. Every evidence
    value must be traceable back to an exact field already present in the
    AIStrategistBundle — evidence is never computed beyond a direct copy,
    a sign/direction comparison, or a simple count of already-supplied
    items."""
    field: str
    value: str


class AIStrategistSignal(BaseModel):
    """The smallest unit the engine produces: one observation, directly
    traceable to specific bundle fields, with no aggregation across
    categories."""
    signal_id: str
    category: str
    severity: Literal["INFO", "WATCH", "WARNING", "CRITICAL"]
    classification: Literal["FACT", "DERIVED", "SIGNAL"]
    statement: str
    source_fields: list[str]
    evidence: list[AIStrategistEvidence]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]


class AIStrategistInsight(BaseModel):
    """One or more related signals grouped into a single reportable
    observation. Insights never introduce a claim that is not already
    present in their underlying signals."""
    insight_id: str
    category: str
    title: str
    severity: Literal["INFO", "WATCH", "WARNING", "CRITICAL"]
    classification: Literal["FACT", "DERIVED", "SIGNAL"]
    statement: str
    evidence: list[AIStrategistEvidence]
    source_fields: list[str]
    affected_unit_id: str | None = None
    affected_property_id: str | None = None
    period: PeriodRange | None = None
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    signal_ids: list[str]


class AIStrategistRecommendation(BaseModel):
    """An advisory action tied to one or more insights. Never
    auto-executed; requires_human_approval is always true. There is no
    'classification' field here — a recommendation is never a fact or a
    signal, it is always and only a recommendation."""
    recommendation_id: str
    priority: int
    category: str
    action: str
    rationale: str
    evidence: list[AIStrategistEvidence]
    source_fields: list[str]
    expected_effect: str | None
    risk: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    requires_human_approval: bool
    linked_insight_ids: list[str]


class AIStrategistDecisionResult(BaseModel):
    generated_at: datetime
    signals: list[AIStrategistSignal]
    insights: list[AIStrategistInsight]
    recommendations: list[AIStrategistRecommendation]


# --- Phase 13G: narrative layer infrastructure (mock provider only) -----
#
# These schemas describe the minimal, already-deprivatized input handed to
# a future LLM (see reports/narrative_provider.py) and the strict output
# it must produce. No provider is implemented here beyond a deterministic
# mock used for tests — see reports/narrative_service.py and
# reports/narrative_validation.py for the orchestration and validation
# that keep the decision engine's output as the sole source of truth.


class NarrativeInputItem(BaseModel):
    """One signal or insight, flattened to only what an explanation layer
    needs. Never includes AIStrategistBundle fields beyond what the
    signal/insight itself already carries."""
    id: str
    kind: Literal["signal", "insight"]
    classification: Literal["FACT", "DERIVED", "SIGNAL"]
    category: str
    severity: Literal["INFO", "WATCH", "WARNING", "CRITICAL"]
    statement: str
    evidence: list[AIStrategistEvidence]
    source_fields: list[str]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    period: PeriodRange | None = None
    linked_ids: list[str] = []


class NarrativeInputRecommendation(BaseModel):
    recommendation_id: str
    priority: int
    category: str
    action: str
    rationale: str
    risk: str
    evidence: list[AIStrategistEvidence]
    source_fields: list[str]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    requires_human_approval: bool
    linked_insight_ids: list[str]


class NarrativeInput(BaseModel):
    generated_at: datetime
    items: list[NarrativeInputItem]
    recommendations: list[NarrativeInputRecommendation]


class ObservationRef(BaseModel):
    text: str
    source_ids: list[str]
    classification: Literal["FACT", "DERIVED", "SIGNAL"]


class ActionRef(BaseModel):
    text: str
    source_ids: list[str]
    requires_human_approval: bool


class NarrativeOutput(BaseModel):
    executive_summary: str
    key_observations: list[ObservationRef]
    priority_actions: list[ActionRef]
    risks: list[ObservationRef]
    questions_for_management: list[str]
    generated_at: datetime
    source: Literal["llm", "deterministic_fallback"]


class RevenuePoint(BaseModel):
    date: date
    revenue: float
    room_nights_sold: int


class UnitPerformance(BaseModel):
    unit_id: str
    unit_name: str
    room_nights_sold: int
    revenue: float
    occupancy_rate_percent: float
    adr: float


class CalendarDay(BaseModel):
    date: str
    check_ins: int
    check_outs: int
    in_house: int


class DashboardCalendar(BaseModel):
    period_year: int
    period_month: int
    days: list[CalendarDay]
