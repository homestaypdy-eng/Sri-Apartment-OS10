from datetime import date
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
