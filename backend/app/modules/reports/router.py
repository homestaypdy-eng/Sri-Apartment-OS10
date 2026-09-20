import os
import uuid
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.modules.reports import service
from app.modules.reports.decision_engine import run_decision_engine
from app.modules.reports.narrative_provider import NarrativeProvider, NarrativeProviderUnavailable, NullNarrativeProvider
from app.modules.reports.narrative_service import NarrativeService
from app.modules.reports.schemas import SummaryReport, RevenuePoint, UnitPerformance, GstBreakdown, OperationalDashboard, DashboardCalendar, SummaryComparison, CancellationReport, AIStrategistBundle, AIStrategistDecisionResult, NarrativeOutput
from app.modules.inquiries.schemas import SalesFunnelReport
from app.shared.rbac import require_role

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _get_narrative_provider() -> NarrativeProvider:
    """Phase 13I: disabled by default (narrative_provider_enabled=False),
    matching Phase 13G's shipped behavior exactly — NullNarrativeProvider,
    every /narrative request returns the deterministic fallback. If
    enabled but misconfigured (e.g. no API key), fall back to
    NullNarrativeProvider rather than raising — a missing/incomplete
    provider configuration must never break the endpoint (section 16)."""
    if not settings.narrative_provider_enabled:
        return NullNarrativeProvider()
    try:
        from app.modules.reports.narrative_provider_anthropic import AnthropicNarrativeProvider
        return AnthropicNarrativeProvider()
    except NarrativeProviderUnavailable:
        return NullNarrativeProvider()


def _default_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Defaults to the current calendar month if no range is given."""
    if date_from and date_to:
        if date_to <= date_from:
            raise HTTPException(400, "date_to must be after date_from")
        return date_from, date_to
    today = date.today()
    start = today.replace(day=1)
    return start, today + timedelta(days=1)


@router.get("/dashboard", response_model=OperationalDashboard, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_operational_dashboard(db: Session = Depends(get_db)):
    return service.operational_dashboard(db)


@router.get("/dashboard/calendar", response_model=DashboardCalendar, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_dashboard_calendar(period_year: int = Query(...), period_month: int = Query(..., ge=1, le=12), db: Session = Depends(get_db)):
    return service.dashboard_calendar(db, period_year, period_month)


@router.get("/summary", response_model=SummaryReport, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_summary(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    d_from, d_to = _default_range(date_from, date_to)
    return service.build_summary(db, d_from, d_to, property_id)


@router.get("/summary-comparison", response_model=SummaryComparison, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_summary_comparison(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    d_from, d_to = _default_range(date_from, date_to)
    return service.build_summary_comparison(db, d_from, d_to, property_id)


@router.get("/cancellations", response_model=CancellationReport, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_cancellation_report(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    d_from, d_to = _default_range(date_from, date_to)
    return service.build_cancellation_report(db, d_from, d_to, property_id)


@router.get("/ai-strategist", response_model=AIStrategistBundle, dependencies=[Depends(require_role("admin"))])
def get_ai_strategist_bundle(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Deterministic, read-only data bundle for the future AI Strategist
    (Phase 13B) — admin-only, unlike every other endpoint in this module,
    since an aggregated cross-domain business-health view is a different
    risk category from any single existing report. Returns facts and
    derived metrics only; never management inference, and never anything
    that could be used to trigger a write.
    """
    d_from, d_to = _default_range(date_from, date_to)
    return service.build_ai_strategist_bundle(db, d_from, d_to, property_id)


@router.get(
    "/ai-strategist/decisions",
    response_model=AIStrategistDecisionResult,
    dependencies=[Depends(require_role("admin"))],
)
def get_ai_strategist_decisions(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Phase 13E — deterministic signals/insights/recommendations derived
    from the same bundle as GET /ai-strategist. Builds the bundle exactly
    once per request (identical query cost to /ai-strategist, ~25 fixed
    queries — see reports/service.py::build_ai_strategist_bundle), then
    runs decision_engine.run_decision_engine() as a pure in-memory
    transform with zero additional queries. Admin-only, same as
    /ai-strategist. Never auto-executes anything; every recommendation
    requires human approval.
    """
    d_from, d_to = _default_range(date_from, date_to)
    bundle_dict = service.build_ai_strategist_bundle(db, d_from, d_to, property_id)
    bundle = AIStrategistBundle.model_validate(bundle_dict)
    return run_decision_engine(bundle)


@router.get(
    "/ai-strategist/narrative",
    response_model=NarrativeOutput,
    dependencies=[Depends(require_role("admin"))],
)
def get_ai_strategist_narrative(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Phase 13G/13I — optional narrative explanation of the same decision
    result as GET /ai-strategist/decisions. Builds the bundle exactly
    once per request (identical query cost to /ai-strategist and
    /ai-strategist/decisions — no duplicate bundle construction), runs
    the Phase 13E decision engine, then the Phase 13G/13I narrative
    service (zero additional database queries; the only extra network
    operation is at most one call — plus at most one retry — to the
    configured provider). Admin-only, same as the other two AI
    Strategist endpoints.

    Disabled by default (settings.narrative_provider_enabled=False):
    behaves exactly like Phase 13G — NullNarrativeProvider always raises,
    so the endpoint always returns source="deterministic_fallback".
    When explicitly enabled with a real key, the Anthropic adapter
    (narrative_provider_anthropic.py) is used instead; any provider
    failure still falls back to the same deterministic path, unchanged.
    Never returns raw bundle data; only the validated NarrativeOutput
    shape.
    """
    d_from, d_to = _default_range(date_from, date_to)
    bundle_dict = service.build_ai_strategist_bundle(db, d_from, d_to, property_id)
    bundle = AIStrategistBundle.model_validate(bundle_dict)
    decision_result = run_decision_engine(bundle)
    narrative_service = NarrativeService(provider=_get_narrative_provider())
    return narrative_service.generate(decision_result)


@router.get("/revenue-series", response_model=list[RevenuePoint], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_revenue_series(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    d_from, d_to = _default_range(date_from, date_to)
    return service.build_revenue_series(db, d_from, d_to, property_id)


@router.get("/unit-performance", response_model=list[UnitPerformance], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_unit_performance(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    d_from, d_to = _default_range(date_from, date_to)
    return service.build_unit_performance(db, d_from, d_to, property_id)


@router.get("/gst", response_model=GstBreakdown, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_gst(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    d_from, d_to = _default_range(date_from, date_to)
    return service.gst_totals(db, d_from, d_to, property_id)


@router.get("/sales-funnel", response_model=SalesFunnelReport, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def get_sales_funnel(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
):
    d_from, d_to = _default_range(date_from, date_to)
    return service.build_sales_funnel(db, d_from, d_to)


VALID_REPORTS = ("summary", "unit_performance", "gst", "guest_requests", "maintenance", "housekeeping", "inventory", "enquiries", "guests")
VALID_FORMATS = ("pdf", "xlsx")


@router.get("/export", dependencies=[Depends(require_role("admin", "staff", "manager"))])
def export_report(
    report: str = Query(..., description="summary | unit_performance | gst"),
    format: str = Query(..., description="pdf | xlsx"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    if report not in VALID_REPORTS:
        raise HTTPException(400, f"Invalid report '{report}'. Must be one of {VALID_REPORTS}")
    if format not in VALID_FORMATS:
        raise HTTPException(400, f"Invalid format '{format}'. Must be one of {VALID_FORMATS}")

    d_from, d_to = _default_range(date_from, date_to)

    if report == "summary":
        summary = service.build_summary(db, d_from, d_to, property_id)
        filepath = service.export_summary_pdf(summary) if format == "pdf" else service.export_summary_xlsx(summary)
    elif report == "unit_performance":
        rows = service.build_unit_performance(db, d_from, d_to, property_id)
        filepath = (
            service.export_unit_performance_pdf(rows, d_from, d_to)
            if format == "pdf"
            else service.export_unit_performance_xlsx(rows, d_from, d_to)
        )
    elif report == "guest_requests":
        from app.modules.portal.models import GuestRequest
        from app.modules.bookings.models import Guest
        from app.modules.auth.models import User

        requests = db.scalars(
            select(GuestRequest).where(GuestRequest.created_at >= d_from, GuestRequest.created_at <= d_to).order_by(GuestRequest.created_at.desc())
        ).all()
        guest_ids = {r.guest_id for r in requests}
        staff_ids = {r.assigned_to for r in requests if r.assigned_to}
        guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()} if guest_ids else {}
        staff_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(staff_ids))).all()} if staff_ids else {}

        title = "Guest Requests Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Date", "Guest", "Type", "Priority", "Status", "Assigned To", "Description", "Feedback"]
        rows = []
        for r in requests:
            g = guests_by_id.get(r.guest_id)
            staff = staff_by_id.get(r.assigned_to) if r.assigned_to else None
            feedback = f"{r.guest_feedback_rating}/5" if r.guest_feedback_rating else "—"
            rows.append([r.created_at.date(), g.name if g else "—", r.request_type, r.priority, r.status, staff.name if staff else "Unassigned", r.description[:60], feedback])

        if format == "pdf":
            filepath = service.export_tabular_pdf(title, subtitle, headers, rows, "guest_requests_report")
        else:
            filepath = service.export_tabular_xlsx(title, headers, rows, "guest_requests_report")
    elif report == "guests":
        from app.modules.bookings.models import Guest, Booking as BookingModel
        from app.modules.payments.models import Payment

        # A guest list is naturally an all-time report (who are our
        # guests, how much have they spent in total), not a date-scoped
        # transaction report like Expenses or Purchases — Guest has no
        # created_at to filter by, and filtering by it wouldn't match how
        # this report is actually used anyway.
        guests = db.scalars(select(Guest).order_by(Guest.name)).all()
        guest_ids = [g.id for g in guests]
        # Batch-load every booking and payment for these guests in two
        # queries total, instead of querying per guest — same anti-N+1
        # discipline as every other report in this file.
        bookings_by_guest: dict = defaultdict(list)
        if guest_ids:
            for b in db.scalars(select(BookingModel).where(BookingModel.guest_id.in_(guest_ids))).all():
                bookings_by_guest[b.guest_id].append(b)
        booking_ids = [b.id for blist in bookings_by_guest.values() for b in blist]
        spend_by_booking: dict = defaultdict(float)
        if booking_ids:
            for p in db.scalars(select(Payment).where(Payment.booking_id.in_(booking_ids))).all():
                spend_by_booking[p.booking_id] += float(p.amount)

        title = "Guest Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Name", "Phone", "Email", "VIP", "Blacklisted", "Total Bookings", "Total Spend"]
        rows = []
        for g in guests:
            g_bookings = bookings_by_guest.get(g.id, [])
            total_spend = round(sum(spend_by_booking.get(b.id, 0.0) for b in g_bookings), 2)
            rows.append([
                g.name, g.phone, g.email or "—", "Yes" if g.is_vip else "No", "Yes" if g.is_blacklisted else "No",
                len(g_bookings), total_spend,
            ])

        if format == "pdf":
            filepath = service.export_tabular_pdf(title, subtitle, headers, rows, "guests_report")
        else:
            filepath = service.export_tabular_xlsx(title, headers, rows, "guests_report")
    elif report == "enquiries":
        from app.modules.inquiries.models import Inquiry
        from app.modules.auth.models import User

        inquiries = db.scalars(
            select(Inquiry).where(Inquiry.created_at >= d_from, Inquiry.created_at <= d_to).order_by(Inquiry.created_at.desc())
        ).all()
        staff_ids = {i.assigned_to for i in inquiries if i.assigned_to}
        staff_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(staff_ids))).all()} if staff_ids else {}

        title = "Enquiries Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Date", "Client", "Phone", "Source", "Priority", "Lead Status", "Assigned To", "Grand Total"]
        rows = []
        for i in inquiries:
            staff = staff_by_id.get(i.assigned_to) if i.assigned_to else None
            rows.append([
                i.created_at.date(), i.client_name, i.client_phone, i.source, i.priority,
                i.lead_status, staff.name if staff else "Unassigned", float(i.grand_total),
            ])

        if format == "pdf":
            filepath = service.export_tabular_pdf(title, subtitle, headers, rows, "enquiries_report")
        else:
            filepath = service.export_tabular_xlsx(title, headers, rows, "enquiries_report")
    elif report == "maintenance":
        from app.modules.maintenance.models import MaintenanceTicket
        from app.modules.auth.models import User
        from app.modules.properties.models import Unit

        tickets = db.scalars(
            select(MaintenanceTicket).where(MaintenanceTicket.created_at >= d_from, MaintenanceTicket.created_at <= d_to).order_by(MaintenanceTicket.created_at.desc())
        ).all()
        unit_ids = {t.unit_id for t in tickets}
        staff_ids = {t.assigned_to for t in tickets if t.assigned_to}
        units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()} if unit_ids else {}
        staff_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(staff_ids))).all()} if staff_ids else {}

        title = "Maintenance Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Date", "Unit", "Priority", "Status", "Assigned To", "Cost", "Description", "Resolved"]
        rows = []
        for t in tickets:
            unit = units_by_id.get(t.unit_id)
            staff = staff_by_id.get(t.assigned_to) if t.assigned_to else None
            rows.append([
                t.created_at.date(), unit.name if unit else "—", t.priority, t.status,
                staff.name if staff else "Unassigned", float(t.cost) if t.cost else 0.0,
                t.description[:60], t.resolved_at.date() if t.resolved_at else "—",
            ])

        if format == "pdf":
            filepath = service.export_tabular_pdf(title, subtitle, headers, rows, "maintenance_report")
        else:
            filepath = service.export_tabular_xlsx(title, headers, rows, "maintenance_report")
    elif report == "housekeeping":
        from app.modules.housekeeping.models import HousekeepingTask
        from app.modules.auth.models import User
        from app.modules.properties.models import Unit

        tasks = db.scalars(
            select(HousekeepingTask).where(HousekeepingTask.created_at >= d_from, HousekeepingTask.created_at <= d_to).order_by(HousekeepingTask.created_at.desc())
        ).all()
        unit_ids = {t.unit_id for t in tasks}
        staff_ids = {t.assigned_to for t in tasks if t.assigned_to}
        units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()} if unit_ids else {}
        staff_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(staff_ids))).all()} if staff_ids else {}

        title = "Housekeeping Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Date", "Unit", "Task Type", "Status", "Assigned To", "Completed"]
        rows = []
        for t in tasks:
            unit = units_by_id.get(t.unit_id)
            staff = staff_by_id.get(t.assigned_to) if t.assigned_to else None
            rows.append([
                t.created_at.date(), unit.name if unit else "—", t.task_type, t.status,
                staff.name if staff else "Unassigned", t.completed_at.date() if t.completed_at else "—",
            ])

        if format == "pdf":
            filepath = service.export_tabular_pdf(title, subtitle, headers, rows, "housekeeping_report")
        else:
            filepath = service.export_tabular_xlsx(title, headers, rows, "housekeeping_report")
    elif report == "inventory":
        from app.modules.inventory.models import InventoryItem

        items = db.scalars(select(InventoryItem).order_by(InventoryItem.name)).all()
        title = "Inventory Report"
        subtitle = f"Snapshot as of {date.today()}"
        headers = ["Name", "SKU", "Barcode", "Brand", "Category", "Current Stock", "Minimum Stock", "Unit Cost", "Stock Value", "Low Stock"]
        rows = []
        for item in items:
            stock_value = round(float(item.unit_cost) * item.current_stock, 2) if item.unit_cost else 0.0
            rows.append([
                item.name, item.sku or "—", item.barcode or "—", item.brand or "—", item.category.replace("_", " "),
                item.current_stock, item.minimum_stock, float(item.unit_cost) if item.unit_cost else 0.0,
                stock_value, "Yes" if item.is_low_stock else "No",
            ])

        if format == "pdf":
            filepath = service.export_tabular_pdf(title, subtitle, headers, rows, "inventory_report")
        else:
            filepath = service.export_tabular_xlsx(title, headers, rows, "inventory_report")
    else:  # gst
        gst = service.gst_totals(db, d_from, d_to, property_id)
        if format == "pdf":
            raise HTTPException(400, "GST report export currently supports xlsx only")
        filepath = service.export_gst_xlsx(gst, d_from, d_to)

    media_type = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    filename = os.path.basename(filepath)
    return FileResponse(filepath, media_type=media_type, filename=filename)
