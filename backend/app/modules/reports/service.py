"""
Reporting aggregation logic.

Design notes (read before extending):

- Occupancy / ADR / RevPAR / revenue-by-source / unit performance are all
  BOOKING-based: a booking counts toward a period if its check_in date falls
  within [date_from, date_to). This is a deliberate simplification — it
  avoids prorating revenue for stays that span a period boundary (e.g. a
  booking from Jan 30 to Feb 3 would otherwise need its revenue split across
  two monthly reports). Standard practice varies by PMS; this is the
  simplest defensible choice for a system this size. If exact date-prorated
  revenue recognition matters for your accounting, this is the function to
  change: see `_fetch_bookings_for_period`.
- GST figures are INVOICE-based (Invoice.issued_at), not booking-based,
  because GST liability is tied to when the invoice is issued, not when the
  stay happened — that's a legal distinction, not a style choice.
- "P&L" here is revenue (from invoices) minus maintenance ticket costs only.
  There is no general expense-tracking module in this system yet (rent,
  utilities, staff wages, supplies aren't recorded anywhere), so this is
  NOT a full P&L — it's explicitly labeled `net_revenue_after_maintenance`
  rather than "net profit" so it can't be mistaken for one.
- Cancelled and no-show bookings are excluded from revenue/occupancy
  entirely (no room was actually occupied, no revenue was earned).

Query strategy: booking-derived metrics fetch the (indexed, date-bounded)
rows once via SQL and aggregate in Python, since each metric needs several
different groupings (by day, by unit, by source) from the same row set —
a single filtered fetch is cheaper than several separate SQL aggregate
queries here. GST and maintenance-cost totals, which need only a single
sum with no per-row logic, use SQL-side SUM()/COUNT() directly.
"""
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fpdf import FPDF
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.bookings.models import Booking
from app.modules.finance.models import Expense
from app.modules.finance.service import outstanding_payments
from app.modules.housekeeping.models import HousekeepingTask
from app.modules.inquiries.models import LEAD_STATUSES, Inquiry
from app.modules.maintenance.models import MaintenanceTicket
from app.modules.payments.models import Invoice, Payment
from app.modules.properties.models import Unit

ACTIVE_BOOKING_STATUSES = ("confirmed", "checked_in", "checked_out")

REPORT_STORAGE_DIR = os.environ.get("REPORT_STORAGE_DIR", "/tmp/sri_apartment_reports")
os.makedirs(REPORT_STORAGE_DIR, exist_ok=True)


def _fetch_bookings_for_period(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None):
    query = select(Booking, Unit).join(Unit, Booking.unit_id == Unit.id).where(
        Booking.check_in >= date_from,
        Booking.check_in < date_to,
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
    )
    if property_id:
        query = query.where(Unit.property_id == property_id)
    return db.execute(query).all()


def _get_units(db: Session, property_id: uuid.UUID | None) -> list[Unit]:
    query = select(Unit)
    if property_id:
        query = query.where(Unit.property_id == property_id)
    return db.scalars(query).all()


def gst_totals(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None) -> dict:
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to, time.min, tzinfo=timezone.utc)

    query = select(
        func.coalesce(func.sum(Invoice.cgst), 0),
        func.coalesce(func.sum(Invoice.sgst), 0),
        func.coalesce(func.sum(Invoice.igst), 0),
        func.coalesce(func.sum(Invoice.total), 0),
        func.count(Invoice.id),
    ).join(Booking, Invoice.booking_id == Booking.id).where(
        Invoice.issued_at >= start,
        Invoice.issued_at < end,
    )
    if property_id:
        query = query.join(Unit, Booking.unit_id == Unit.id).where(Unit.property_id == property_id)

    cgst, sgst, igst, total, count = db.execute(query).one()
    return {
        "cgst": float(cgst),
        "sgst": float(sgst),
        "igst": float(igst),
        "total": float(total),
        "invoice_count": count,
    }


def _maintenance_cost_total(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None) -> float:
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to, time.min, tzinfo=timezone.utc)

    query = select(func.coalesce(func.sum(MaintenanceTicket.cost), 0)).where(
        MaintenanceTicket.resolved_at >= start,
        MaintenanceTicket.resolved_at < end,
    )
    if property_id:
        query = query.join(Unit, MaintenanceTicket.unit_id == Unit.id).where(Unit.property_id == property_id)

    return float(db.execute(query).scalar())


def build_summary(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None = None) -> dict:
    rows = _fetch_bookings_for_period(db, date_from, date_to, property_id)
    units = _get_units(db, property_id)
    days_in_range = max((date_to - date_from).days, 1)

    total_bookings = 0
    room_nights_sold = 0
    revenue_by_source: dict[str, float] = defaultdict(float)
    total_revenue = 0.0

    for booking, _unit in rows:
        nights = (booking.check_out - booking.check_in).days
        revenue = float(booking.rate_applied) * nights + float(booking.extra_guest_charge)
        total_bookings += 1
        room_nights_sold += nights
        total_revenue += revenue
        revenue_by_source[booking.source] += revenue

    available_room_nights = len(units) * days_in_range
    occupancy_rate = (room_nights_sold / available_room_nights * 100) if available_room_nights else 0.0
    adr = (total_revenue / room_nights_sold) if room_nights_sold else 0.0
    revpar = (total_revenue / available_room_nights) if available_room_nights else 0.0

    gst = gst_totals(db, date_from, date_to, property_id)
    maintenance_cost = _maintenance_cost_total(db, date_from, date_to, property_id)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "revenue": {"total": round(total_revenue, 2), "by_source": {k: round(v, 2) for k, v in revenue_by_source.items()}},
        "total_bookings": total_bookings,
        "room_nights_sold": room_nights_sold,
        "available_room_nights": available_room_nights,
        "occupancy_rate_percent": round(occupancy_rate, 2),
        "adr": round(adr, 2),
        "revpar": round(revpar, 2),
        "gst": gst,
        "maintenance_cost_total": round(maintenance_cost, 2),
        "net_revenue_after_maintenance": round(total_revenue - maintenance_cost, 2),
    }


def build_summary_comparison(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None = None) -> dict:
    """
    Period-over-period comparison for Occupancy / ADR / RevPAR only. Calls
    build_summary() for both the current and previous period rather than
    recomputing these formulas, so the comparison numbers can never drift
    from the numbers shown elsewhere in Reports Center for the same range.

    The comparison period is deterministic and not calendar-aligned: for a
    selected range of N days, the previous period is the immediately
    preceding N-day block ([date_from - N days, date_from)). This is well
    defined for any arbitrary date range, unlike a "previous month/year"
    convention which breaks down for irregular ranges.
    """
    n_days = max((date_to - date_from).days, 1)
    prev_date_to = date_from
    prev_date_from = date_from - timedelta(days=n_days)

    current = build_summary(db, date_from, date_to, property_id)
    previous = build_summary(db, prev_date_from, prev_date_to, property_id)

    def _pct_change(curr: float, prev: float) -> float | None:
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100, 2)

    return {
        "current_period": {"date_from": date_from, "date_to": date_to},
        "previous_period": {"date_from": prev_date_from, "date_to": prev_date_to},
        "occupancy": {
            "current": current["occupancy_rate_percent"],
            "previous": previous["occupancy_rate_percent"],
            "change_percent": _pct_change(current["occupancy_rate_percent"], previous["occupancy_rate_percent"]),
            "change_percentage_points": round(current["occupancy_rate_percent"] - previous["occupancy_rate_percent"], 2),
        },
        "adr": {
            "current": current["adr"],
            "previous": previous["adr"],
            "change_percent": _pct_change(current["adr"], previous["adr"]),
        },
        "revpar": {
            "current": current["revpar"],
            "previous": previous["revpar"],
            "change_percent": _pct_change(current["revpar"], previous["revpar"]),
        },
    }


# The "firm booking" population for cancellation reporting: confirmed,
# checked_in, checked_out, cancelled, no_show. "inquiry" (an unconfirmed
# hold, not yet a real reservation) is deliberately excluded from this
# denominator, per explicit product decision — see build_cancellation_report().
FIRM_BOOKING_STATUSES = ("confirmed", "checked_in", "checked_out", "cancelled", "no_show")


def _fetch_firm_bookings_for_period(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None):
    """
    Same check_in-date-basis period filter as _fetch_bookings_for_period,
    but deliberately WITHOUT excluding cancelled/no_show bookings — those
    are exactly what a cancellation report needs to count. Only "inquiry"
    (not yet a firm booking) is excluded.
    """
    query = select(Booking).join(Unit, Booking.unit_id == Unit.id).where(
        Booking.check_in >= date_from,
        Booking.check_in < date_to,
        Booking.status.in_(FIRM_BOOKING_STATUSES),
    )
    if property_id:
        query = query.where(Unit.property_id == property_id)
    return db.scalars(query).all()


def _cancellation_period_stats(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None) -> dict:
    bookings = _fetch_firm_bookings_for_period(db, date_from, date_to, property_id)
    total = len(bookings)
    cancelled = sum(1 for b in bookings if b.status == "cancelled")
    no_show = sum(1 for b in bookings if b.status == "no_show")
    return {
        "total": total,
        "cancelled": cancelled,
        "no_show": no_show,
        "cancellation_rate": round(cancelled / total * 100, 2) if total else 0.0,
        "no_show_rate": round(no_show / total * 100, 2) if total else 0.0,
    }


def build_cancellation_report(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None = None) -> dict:
    """
    "Of the firm bookings scheduled to check in during this period, what
    fraction were cancelled?" — NOT "cancellations recorded during the
    period" (OS10 has no cancellation timestamp to measure that). Same
    check_in-date-basis convention, and the same N-day preceding-period
    comparison approach, as build_summary_comparison() above.
    """
    n_days = max((date_to - date_from).days, 1)
    prev_date_to = date_from
    prev_date_from = date_from - timedelta(days=n_days)

    current = _cancellation_period_stats(db, date_from, date_to, property_id)
    previous = _cancellation_period_stats(db, prev_date_from, prev_date_to, property_id)

    def _pct_change(curr: float, prev: float) -> float | None:
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100, 2)

    return {
        "current_period": {"date_from": date_from, "date_to": date_to},
        "previous_period": {"date_from": prev_date_from, "date_to": prev_date_to},
        "total_bookings_in_period": current["total"],
        "cancelled_count": current["cancelled"],
        "cancellation_rate_percent": current["cancellation_rate"],
        "no_show_count": current["no_show"],
        "no_show_rate_percent": current["no_show_rate"],
        "previous_total_bookings_in_period": previous["total"],
        "previous_cancelled_count": previous["cancelled"],
        "previous_cancellation_rate_percent": previous["cancellation_rate"],
        "previous_no_show_count": previous["no_show"],
        "previous_no_show_rate_percent": previous["no_show_rate"],
        "cancellation_rate_change_percent": _pct_change(current["cancellation_rate"], previous["cancellation_rate"]),
        "no_show_rate_change_percent": _pct_change(current["no_show_rate"], previous["no_show_rate"]),
    }


def build_revenue_series(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None = None) -> list[dict]:
    rows = _fetch_bookings_for_period(db, date_from, date_to, property_id)
    by_day: dict[date, dict] = defaultdict(lambda: {"revenue": 0.0, "room_nights_sold": 0})

    for booking, _unit in rows:
        nights = (booking.check_out - booking.check_in).days
        revenue = float(booking.rate_applied) * nights + float(booking.extra_guest_charge)
        by_day[booking.check_in]["revenue"] += revenue
        by_day[booking.check_in]["room_nights_sold"] += nights

    return [
        {"date": d, "revenue": round(v["revenue"], 2), "room_nights_sold": v["room_nights_sold"]}
        for d, v in sorted(by_day.items())
    ]


def build_unit_performance(db: Session, date_from: date, date_to: date, property_id: uuid.UUID | None = None) -> list[dict]:
    rows = _fetch_bookings_for_period(db, date_from, date_to, property_id)
    units = _get_units(db, property_id)
    days_in_range = max((date_to - date_from).days, 1)

    per_unit: dict[uuid.UUID, dict] = {u.id: {"revenue": 0.0, "nights": 0, "name": u.name} for u in units}

    for booking, unit in rows:
        nights = (booking.check_out - booking.check_in).days
        revenue = float(booking.rate_applied) * nights + float(booking.extra_guest_charge)
        if unit.id not in per_unit:
            per_unit[unit.id] = {"revenue": 0.0, "nights": 0, "name": unit.name}
        per_unit[unit.id]["revenue"] += revenue
        per_unit[unit.id]["nights"] += nights

    results = []
    for unit_id, data in per_unit.items():
        occupancy = (data["nights"] / days_in_range * 100) if days_in_range else 0.0
        adr = (data["revenue"] / data["nights"]) if data["nights"] else 0.0
        results.append(
            {
                "unit_id": str(unit_id),
                "unit_name": data["name"],
                "room_nights_sold": data["nights"],
                "revenue": round(data["revenue"], 2),
                "occupancy_rate_percent": round(occupancy, 2),
                "adr": round(adr, 2),
            }
        )
    results.sort(key=lambda r: r["revenue"], reverse=True)
    return results


def build_sales_funnel(db: Session, date_from: date, date_to: date) -> dict:
    """
    Sales funnel counts by lead_status for inquiries CREATED within the
    given range (created_at-based, matching how a sales team would look
    back at "leads generated this month" — not check-in-date-based like the
    booking KPIs above, since a lead's creation date is what matters for
    funnel analysis, not when the eventual stay happens).
    """
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to, time.min, tzinfo=timezone.utc)

    inquiries = db.scalars(
        select(Inquiry).where(Inquiry.created_at >= start, Inquiry.created_at < end)
    ).all()

    counts = {s: 0 for s in LEAD_STATUSES}
    total_quoted_value = 0.0
    total_converted_value = 0.0
    for inq in inquiries:
        counts[inq.lead_status] = counts.get(inq.lead_status, 0) + 1
        total_quoted_value += float(inq.grand_total)
        if inq.lead_status == "converted":
            total_converted_value += float(inq.grand_total)

    total = len(inquiries)
    converted = counts.get("converted", 0)
    lost = counts.get("lost", 0)
    conversion_rate = (converted / total * 100) if total else 0.0

    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_inquiries": total,
        "stages": [{"lead_status": s, "count": counts[s]} for s in LEAD_STATUSES],
        "converted_count": converted,
        "lost_count": lost,
        "conversion_rate_percent": round(conversion_rate, 2),
        "total_quoted_value": round(total_quoted_value, 2),
        "total_converted_value": round(total_converted_value, 2),
    }


# --- Export builders ---

def export_summary_pdf(summary: dict) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Operations Summary Report", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Period: {summary['date_from']} to {summary['date_to']}", ln=True, align="C")
    pdf.ln(6)

    rows = [
        ("Total Revenue", f"Rs {summary['revenue']['total']:.2f}"),
        ("Total Bookings", str(summary["total_bookings"])),
        ("Room Nights Sold", str(summary["room_nights_sold"])),
        ("Available Room Nights", str(summary["available_room_nights"])),
        ("Occupancy Rate", f"{summary['occupancy_rate_percent']:.2f}%"),
        ("ADR", f"Rs {summary['adr']:.2f}"),
        ("RevPAR", f"Rs {summary['revpar']:.2f}"),
        ("GST Collected (CGST+SGST+IGST)", f"Rs {summary['gst']['total']:.2f}"),
        ("Maintenance Cost", f"Rs {summary['maintenance_cost_total']:.2f}"),
        ("Net Revenue After Maintenance", f"Rs {summary['net_revenue_after_maintenance']:.2f}"),
    ]
    pdf.set_font("Helvetica", "", 11)
    for label, value in rows:
        pdf.cell(120, 8, label, border=1)
        pdf.cell(70, 8, value, border=1, new_x="LMARGIN", new_y="NEXT", align="R")

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0, 5,
        "Note: 'Net Revenue After Maintenance' is revenue minus recorded maintenance costs only. "
        "This system does not yet track general operating expenses (rent, utilities, wages, supplies), "
        "so this figure is not a complete profit & loss statement.",
    )

    filename = f"summary_{summary['date_from']}_{summary['date_to']}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(REPORT_STORAGE_DIR, filename)
    pdf.output(filepath)
    return filepath


def export_summary_xlsx(summary: dict) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Metric", "Value"])
    ws.append(["Period", f"{summary['date_from']} to {summary['date_to']}"])
    ws.append(["Total Revenue", summary["revenue"]["total"]])
    for source, amount in summary["revenue"]["by_source"].items():
        ws.append([f"  Revenue ({source})", amount])
    ws.append(["Total Bookings", summary["total_bookings"]])
    ws.append(["Room Nights Sold", summary["room_nights_sold"]])
    ws.append(["Available Room Nights", summary["available_room_nights"]])
    ws.append(["Occupancy Rate (%)", summary["occupancy_rate_percent"]])
    ws.append(["ADR", summary["adr"]])
    ws.append(["RevPAR", summary["revpar"]])
    ws.append(["CGST", summary["gst"]["cgst"]])
    ws.append(["SGST", summary["gst"]["sgst"]])
    ws.append(["IGST", summary["gst"]["igst"]])
    ws.append(["Total GST", summary["gst"]["total"]])
    ws.append(["Invoice Count", summary["gst"]["invoice_count"]])
    ws.append(["Maintenance Cost", summary["maintenance_cost_total"]])
    ws.append(["Net Revenue After Maintenance (not full P&L)", summary["net_revenue_after_maintenance"]])

    filename = f"summary_{summary['date_from']}_{summary['date_to']}_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(REPORT_STORAGE_DIR, filename)
    wb.save(filepath)
    return filepath


def export_unit_performance_xlsx(rows: list[dict], date_from: date, date_to: date) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Unit Performance"
    ws.append(["Unit", "Room Nights Sold", "Revenue", "Occupancy %", "ADR"])
    for r in rows:
        ws.append([r["unit_name"], r["room_nights_sold"], r["revenue"], r["occupancy_rate_percent"], r["adr"]])

    filename = f"unit_performance_{date_from}_{date_to}_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(REPORT_STORAGE_DIR, filename)
    wb.save(filepath)
    return filepath


def export_unit_performance_pdf(rows: list[dict], date_from: date, date_to: date) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Unit Performance Report", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Period: {date_from} to {date_to}", ln=True, align="C")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 9)
    headers = ["Unit", "Nights", "Revenue", "Occ %", "ADR"]
    widths = [70, 25, 35, 25, 35]
    for h, w in zip(headers, widths):
        pdf.cell(w, 8, h, border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for r in rows:
        pdf.cell(widths[0], 7, r["unit_name"], border=1)
        pdf.cell(widths[1], 7, str(r["room_nights_sold"]), border=1, align="R")
        pdf.cell(widths[2], 7, f"{r['revenue']:.2f}", border=1, align="R")
        pdf.cell(widths[3], 7, f"{r['occupancy_rate_percent']:.1f}", border=1, align="R")
        pdf.cell(widths[4], 7, f"{r['adr']:.2f}", border=1, new_x="LMARGIN", new_y="NEXT", align="R")

    filename = f"unit_performance_{date_from}_{date_to}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(REPORT_STORAGE_DIR, filename)
    pdf.output(filepath)
    return filepath


def export_gst_xlsx(gst: dict, date_from: date, date_to: date) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "GST"
    ws.append(["Period", f"{date_from} to {date_to}"])
    ws.append(["CGST", gst["cgst"]])
    ws.append(["SGST", gst["sgst"]])
    ws.append(["IGST", gst["igst"]])
    ws.append(["Total GST", gst["total"]])
    ws.append(["Invoice Count", gst["invoice_count"]])

    filename = f"gst_{date_from}_{date_to}_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(REPORT_STORAGE_DIR, filename)
    wb.save(filepath)
    return filepath


def operational_dashboard(db: Session) -> dict:
    """
    One consolidated read for the main Dashboard screen — arrivals/
    departures/in-house/vacancy/occupancy/pending work items/today's and
    this month's cash position/apartment performance — built entirely from
    existing tables (Booking, Unit, HousekeepingTask, MaintenanceTicket,
    Payment) plus the new Expense table. Doesn't touch or recompute
    anything in SummaryReport above; this is a second, purpose-built view
    for a different question ("what does today look like") rather than
    a historical range report.
    """
    today = date.today()
    month_start = today.replace(day=1)

    units = db.scalars(select(Unit)).all()
    total_units = len(units)
    unit_by_id = {u.id: u for u in units}

    from app.modules.bookings.models import Guest
    total_guests = db.scalar(select(func.count()).select_from(Guest)) or 0

    active_statuses = ("confirmed", "checked_in", "checked_out")
    all_bookings = db.scalars(select(Booking).where(Booking.status.in_(active_statuses))).all()
    active_bookings = sum(1 for b in all_bookings if b.status in ("confirmed", "checked_in"))

    arrivals_today = sum(1 for b in all_bookings if b.check_in == today and b.status == "confirmed")
    departures_today = sum(1 for b in all_bookings if b.check_out == today and b.status == "checked_in")
    in_house_bookings = [b for b in all_bookings if b.status == "checked_in"]
    in_house_guests = len(in_house_bookings)
    occupied_unit_ids = {b.unit_id for b in in_house_bookings}
    vacant_units = total_units - len(occupied_unit_ids)
    occupancy_percent = round(len(occupied_unit_ids) / total_units * 100, 2) if total_units else 0.0

    pending_checkins = sum(1 for b in all_bookings if b.check_in <= today and b.status == "confirmed")
    pending_checkouts = sum(1 for b in all_bookings if b.check_out <= today and b.status == "checked_in")

    outstanding = outstanding_payments(db)
    pending_payments_count = len(outstanding)
    pending_payments_amount = round(sum(o["pending_balance"] for o in outstanding), 2)

    housekeeping_pending = db.scalar(select(func.count()).select_from(HousekeepingTask).where(HousekeepingTask.status != "completed")) or 0
    # Fetched as full rows (not just count()) so build_attention_items()
    # below can build a WHAT/WHY/action line per ticket without a second
    # round-trip — the count is simply len() of what we already have.
    open_maintenance_tickets = db.scalars(select(MaintenanceTicket).where(MaintenanceTicket.status.in_(("open", "in_progress")))).all()
    maintenance_open = len(open_maintenance_tickets)
    from app.modules.portal.models import GuestRequest
    open_guest_requests = db.scalars(select(GuestRequest).where(GuestRequest.status != "resolved")).all()
    guest_requests_open = len(open_guest_requests)
    from app.modules.inventory.models import InventoryItem
    low_stock_items = db.scalars(select(InventoryItem).where(InventoryItem.current_stock <= InventoryItem.minimum_stock)).all()
    low_stock_count = len(low_stock_items)
    from app.modules.staff.models import Attendance
    staff_present_today = db.scalar(select(func.count()).select_from(Attendance).where(Attendance.date == today, Attendance.status == "present")) or 0
    staff_absent_today = db.scalar(select(func.count()).select_from(Attendance).where(Attendance.date == today, Attendance.status == "absent")) or 0
    from app.modules.inquiries.models import Inquiry as InquiryModel
    follow_ups_due_today = db.scalar(
        select(func.count()).select_from(InquiryModel).where(
            InquiryModel.next_follow_up_date == today,
            InquiryModel.lead_status.in_(("new", "contacted", "follow_up")),
        )
    ) or 0

    def _revenue_for_bookings(bookings_list):
        total = 0.0
        for b in bookings_list:
            nights = (b.check_out - b.check_in).days
            total += float(b.rate_applied) * nights + float(b.extra_guest_charge)
        return total

    today_bookings = [b for b in all_bookings if b.check_in <= today < b.check_out]
    today_revenue = round(sum(float(b.rate_applied) for b in today_bookings), 2)  # one night's worth, per booking active today
    today_expenses = round(sum(float(e.amount) for e in db.scalars(select(Expense).where(Expense.expense_date == today)).all()), 2)
    today_profit = round(today_revenue - today_expenses, 2)

    month_bookings = [b for b in all_bookings if b.check_in >= month_start and b.check_in <= today]
    month_revenue = round(_revenue_for_bookings(month_bookings), 2)
    month_expenses = round(sum(float(e.amount) for e in db.scalars(select(Expense).where(Expense.expense_date >= month_start, Expense.expense_date <= today)).all()), 2)
    month_profit = round(month_revenue - month_expenses, 2)

    apartment_performance = []
    days_elapsed_this_month = (today - month_start).days + 1
    # Per-unit expenses for this month, tagged via Expense.unit_id (same
    # attribution rule as apartment_pnl in finance/service.py — central/
    # shared expenses with no unit_id are excluded from any one apartment's
    # figure here too, for the same reason).
    month_expenses_by_unit: dict = defaultdict(float)
    for e in db.scalars(select(Expense).where(Expense.expense_date >= month_start, Expense.expense_date <= today, Expense.unit_id.isnot(None))).all():
        month_expenses_by_unit[e.unit_id] += float(e.amount)

    # Group month_bookings by unit_id ONCE (O(bookings)) instead of
    # re-scanning the full month_bookings list once per unit inside the
    # loop below (O(units x bookings)) — with 100 units and a few hundred
    # bookings this was tens of thousands of redundant comparisons for
    # what a single grouping pass does in one go.
    bookings_by_unit: dict = defaultdict(list)
    for b in month_bookings:
        bookings_by_unit[b.unit_id].append(b)

    for u in units:
        unit_month_bookings = bookings_by_unit.get(u.id, [])
        unit_revenue = round(_revenue_for_bookings(unit_month_bookings), 2)
        unit_expenses = round(month_expenses_by_unit.get(u.id, 0.0), 2)
        nights_booked = sum((min(b.check_out, today + timedelta(days=1)) - max(b.check_in, month_start)).days for b in unit_month_bookings)
        nights_booked = max(nights_booked, 0)
        occ_pct = round(nights_booked / days_elapsed_this_month * 100, 2) if days_elapsed_this_month else 0.0
        apartment_performance.append({
            "unit_id": str(u.id),
            "unit_name": u.name,
            "month_revenue": unit_revenue,
            "month_expenses": unit_expenses,
            "month_profit": round(unit_revenue - unit_expenses, 2),
            "month_nights_booked": nights_booked,
            "month_occupancy_percent": occ_pct,
        })
    apartment_performance.sort(key=lambda x: x["month_revenue"], reverse=True)

    # Trailing 6-month trend (including the current, in-progress month) for
    # the Dashboard's "Monthly Trend" / "Revenue vs Expense" charts.
    monthly_trend = []
    for i in range(5, -1, -1):
        # Walk back i months from the current month, using day=1 arithmetic
        # to avoid month-length edge cases (e.g. Jan 31 - 1 month).
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        m_start = date(year, month, 1)
        m_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        m_bookings = db.scalars(select(Booking).where(Booking.status.in_(active_statuses), Booking.check_in >= m_start, Booking.check_in < m_end)).all()
        m_revenue = round(_revenue_for_bookings(m_bookings), 2)
        m_expenses = round(sum(float(e.amount) for e in db.scalars(select(Expense).where(Expense.expense_date >= m_start, Expense.expense_date < m_end)).all()), 2)
        monthly_trend.append({"year": year, "month": month, "revenue": m_revenue, "expenses": m_expenses, "profit": round(m_revenue - m_expenses, 2)})

    attention_items = build_attention_items(
        db, today, outstanding=outstanding,
        open_guest_requests=open_guest_requests, open_maintenance_tickets=open_maintenance_tickets,
        low_stock_items=low_stock_items, unit_by_id=unit_by_id,
    )

    return {
        "as_of": today,
        "arrivals_today": arrivals_today,
        "departures_today": departures_today,
        "in_house_guests": in_house_guests,
        "total_units": total_units,
        "total_guests": total_guests,
        "active_bookings": active_bookings,
        "vacant_units": vacant_units,
        "occupancy_percent": occupancy_percent,
        "pending_checkins": pending_checkins,
        "pending_checkouts": pending_checkouts,
        "pending_payments_count": pending_payments_count,
        "pending_payments_amount": pending_payments_amount,
        "housekeeping_pending": housekeeping_pending,
        "maintenance_open": maintenance_open,
        "guest_requests_open": guest_requests_open,
        "low_stock_count": low_stock_count,
        "staff_present_today": staff_present_today,
        "staff_absent_today": staff_absent_today,
        "follow_ups_due_today": follow_ups_due_today,
        "today_revenue": today_revenue,
        "today_expenses": today_expenses,
        "today_profit": today_profit,
        "month_revenue": month_revenue,
        "month_expenses": month_expenses,
        "month_profit": month_profit,
        "apartment_performance": apartment_performance,
        "monthly_trend": monthly_trend,
        "attention_items": attention_items,
    }


# Same priority/response-time convention already used by the escalation
# scheduler (notifications/service.py) — reused here rather than inventing
# a second threshold scheme, so "past its response target" means the same
# thing on the Dashboard as it does in the escalation emails.
_ATTENTION_THRESHOLD_HOURS = {"urgent": 4, "high": 24, "medium": 72, "low": 72}

# Ranking is a fixed, documented two-key sort — not a black-box score:
# 1. severity (critical before warning)
# 2. category, in this fixed order — guest-facing/revenue-affecting
#    concerns before property/supply concerns, since a guest experiencing
#    a problem right now outranks a maintenance ticket or stock level that
#    doesn't have an in-house guest waiting on it.
_ATTENTION_SEVERITY_RANK = {"critical": 0, "warning": 1}
_ATTENTION_CATEGORY_RANK = {"unpaid_balance": 0, "guest_request": 1, "maintenance": 2, "low_stock": 3}


def build_attention_items(
    db: Session, today: date, outstanding: list[dict],
    open_guest_requests: list, open_maintenance_tickets: list, low_stock_items: list, unit_by_id: dict,
) -> list[dict]:
    """
    Deterministic, explainable "what needs attention today" list — every
    item is a plain if/then rule over data the Dashboard (and the pages
    behind each link) already shows to admin and staff alike; nothing
    here is inferred, predicted, or generated. Only genuinely time-
    sensitive exceptions are included (e.g. a future, not-yet-due balance
    is not "today's" problem and is deliberately left out) so this stays
    a short, actionable list rather than a restatement of every open row
    in the system.
    """
    from app.modules.bookings.models import Guest

    items: list[dict] = []

    # --- Unpaid balances: only overdue, or check-in within 3 days ---
    for o in outstanding:
        days_to_checkin = (o["check_in"] - today).days
        if o["is_overdue"]:
            severity = "critical"
            reason = f"Payment was due {o['due_date']} and is still pending."
        elif days_to_checkin <= 0:
            severity = "critical"
            reason = f"Guest has checked in (or checks in today) with ₹{o['pending_balance']:,.0f} still unpaid."
        elif days_to_checkin <= 3:
            severity = "warning"
            reason = f"Guest arrives in {days_to_checkin} day(s) with ₹{o['pending_balance']:,.0f} still unpaid."
        else:
            continue
        items.append({
            "category": "unpaid_balance",
            "severity": severity,
            "title": f"{o['guest_name']} — ₹{o['pending_balance']:,.0f} unpaid ({o['unit_name']})",
            "reason": reason,
            "action": "Follow up with the guest for payment before/at check-in.",
            "reference_id": str(o["booking_id"]),
            "link": "/finance",
        })

    now = datetime.now(timezone.utc)

    # --- Guest requests: past response-time target, or high/urgent priority ---
    guest_ids = {r.guest_id for r in open_guest_requests}
    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()} if guest_ids else {}
    for r in open_guest_requests:
        hours_open = (now - r.created_at).total_seconds() / 3600
        threshold = _ATTENTION_THRESHOLD_HOURS.get(r.priority, 72)
        if hours_open >= threshold:
            severity = "critical"
            reason = f"Open for {hours_open:.0f}h — past the {threshold}h response target for {r.priority} priority."
        elif r.priority in ("urgent", "high"):
            severity = "warning"
            reason = f"{r.priority.capitalize()} priority request, open {hours_open:.0f}h."
        else:
            continue
        guest = guests_by_id.get(r.guest_id)
        items.append({
            "category": "guest_request",
            "severity": severity,
            "title": f"{r.priority.capitalize()} request from {guest.name if guest else 'a guest'}: {r.description[:60]}",
            "reason": reason,
            "action": "Assign and resolve, or update the guest on progress.",
            "reference_id": str(r.id),
            "link": "/guest-requests",
        })

    # --- Maintenance: past response-time target, or high/urgent priority ---
    for t in open_maintenance_tickets:
        hours_open = (now - t.created_at).total_seconds() / 3600
        threshold = _ATTENTION_THRESHOLD_HOURS.get(t.priority, 72)
        if hours_open >= threshold:
            severity = "critical"
            reason = f"Open for {hours_open:.0f}h — past the {threshold}h target for {t.priority} priority."
        elif t.priority in ("urgent", "high"):
            severity = "warning"
            reason = f"{t.priority.capitalize()} priority ticket, open {hours_open:.0f}h."
        else:
            continue
        unit = unit_by_id.get(t.unit_id)
        items.append({
            "category": "maintenance",
            "severity": severity,
            "title": f"{t.priority.capitalize()} maintenance — {unit.name if unit else 'a unit'}: {t.description[:60]}",
            "reason": reason,
            "action": "Assign and resolve.",
            "reference_id": str(t.id),
            "link": "/maintenance",
        })

    # --- Low stock: out of stock is critical, at/below minimum is a warning ---
    for item in low_stock_items:
        out_of_stock = item.current_stock <= 0
        items.append({
            "category": "low_stock",
            "severity": "critical" if out_of_stock else "warning",
            "title": f"{item.name} — {item.current_stock} {item.unit_of_measure} left (min {item.minimum_stock})",
            "reason": "Completely out of stock." if out_of_stock else f"At or below the minimum stock threshold of {item.minimum_stock}.",
            "action": "Reorder or record a stock-in.",
            "reference_id": str(item.id),
            "link": "/inventory",
        })

    items.sort(key=lambda i: (_ATTENTION_SEVERITY_RANK[i["severity"]], _ATTENTION_CATEGORY_RANK[i["category"]]))
    return items


def _pdf_safe(text) -> str:
    """
    FPDF's base Helvetica font only supports Latin-1 — common characters
    like em-dashes (—) or curly quotes crash PDF generation outright
    (FPDFUnicodeEncodingException), not just render oddly. Rather than
    hunting down every string that might contain one across every report
    type (present and future), every value going into a PDF cell in this
    module is sanitized here once, centrally.
    """
    s = str(text)
    replacements = {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "..."}
    for bad, good in replacements.items():
        s = s.replace(bad, good)
    return s.encode("latin-1", "replace").decode("latin-1")


def export_tabular_pdf(title: str, subtitle: str, headers: list[str], rows: list[list], filename_prefix: str) -> str:
    """
    Generic tabular PDF export — used for every new report type (Cash
    Book, Apartment P&L, Expense/Purchase/Utility/Salary reports) instead
    of writing a bespoke FPDF layout per report. Landscape orientation
    since these reports tend to have several columns.
    """
    pdf = FPDF(orientation="L")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _pdf_safe(title), new_x="LMARGIN", new_y="NEXT", align="C")
    if subtitle:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, _pdf_safe(subtitle), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    page_width = pdf.w - 2 * pdf.l_margin
    col_width = page_width / max(len(headers), 1)

    pdf.set_font("Helvetica", "B", 9)
    for h in headers:
        pdf.cell(col_width, 8, _pdf_safe(h), border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for row in rows:
        for cell in row:
            text = _pdf_safe(cell)
            if len(text) > 40:
                text = text[:37] + "..."
            pdf.cell(col_width, 7, text, border=1)
        pdf.ln()

    filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(REPORT_STORAGE_DIR, filename)
    pdf.output(filepath)
    return filepath


def export_tabular_xlsx(title: str, headers: list[str], rows: list[list], filename_prefix: str) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for row in rows:
        ws.append(row)

    filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(REPORT_STORAGE_DIR, filename)
    wb.save(filepath)
    return filepath


def dashboard_calendar(db: Session, period_year: int, period_month: int) -> dict:
    """
    Day-by-day check-in/check-out counts for one calendar month — the
    data behind the Dashboard's Calendar view. Built from the same
    Booking table every other Dashboard figure uses; no separate
    calendar-specific storage, so it can never drift from the real
    booking data the rest of the Dashboard already shows.
    """
    from app.modules.bookings.models import Booking as BookingModel

    month_start = date(period_year, period_month, 1)
    month_end = date(period_year + 1, 1, 1) if period_month == 12 else date(period_year, period_month + 1, 1)

    bookings = db.scalars(
        select(BookingModel).where(
            BookingModel.status.in_(("confirmed", "checked_in", "checked_out")),
            BookingModel.check_in < month_end,
            BookingModel.check_out >= month_start,
        )
    ).all()

    days: dict = {}
    d = month_start
    while d < month_end:
        days[d.isoformat()] = {"date": d.isoformat(), "check_ins": 0, "check_outs": 0, "in_house": 0}
        d += timedelta(days=1)

    for b in bookings:
        if month_start <= b.check_in < month_end:
            days[b.check_in.isoformat()]["check_ins"] += 1
        if month_start <= b.check_out < month_end:
            days[b.check_out.isoformat()]["check_outs"] += 1
        d = max(b.check_in, month_start)
        end = min(b.check_out, month_end)
        while d < end:
            days[d.isoformat()]["in_house"] += 1
            d += timedelta(days=1)

    return {"period_year": period_year, "period_month": period_month, "days": list(days.values())}
