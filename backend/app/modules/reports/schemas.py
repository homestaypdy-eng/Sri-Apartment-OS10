from datetime import date

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
