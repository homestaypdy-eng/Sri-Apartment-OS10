"""
Aggregation logic for all finance dashboards/reports. Reads from existing
Payment/Invoice/Booking tables plus the new finance tables — never writes
to or recalculates anything in reports/service.py, so existing occupancy/
revenue/GST reports are completely unaffected by anything here.
"""
from collections import defaultdict
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.bookings.models import Booking, Guest
from app.modules.finance.models import EXPENSE_CATEGORIES, Expense, Budget, DailyClosing, OwnerWithdrawal, PurchaseRecord, SecurityDeposit, OTACommission, UtilityReading, UtilityReadingAllocation, LaundryEntry
from app.modules.payments.models import Invoice, Payment
from app.modules.properties.models import Unit


def _range_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
    return start, end


def gst_dashboard(db: Session, date_from: date, date_to: date) -> dict:
    start, end = _range_bounds(date_from, date_to)
    invoices = db.scalars(select(Invoice).where(Invoice.issued_at >= start, Invoice.issued_at <= end)).all()

    gst_collected = round(sum(float(i.cgst) + float(i.sgst) + float(i.igst) for i in invoices), 2)
    gst_input_credit_eligible = round(sum(float(i.cgst) + float(i.sgst) + float(i.igst) for i in invoices if i.itc_available), 2)
    gst_payable = round(gst_collected - gst_input_credit_eligible, 2)
    gst_liability = gst_payable

    monthly: dict[tuple[int, int], dict] = defaultdict(lambda: {"gst": 0.0, "count": 0})
    for inv in invoices:
        key = (inv.issued_at.year, inv.issued_at.month)
        monthly[key]["gst"] += float(inv.cgst) + float(inv.sgst) + float(inv.igst)
        monthly[key]["count"] += 1

    monthly_summary = [
        {"year": y, "month": m, "gst_collected": round(v["gst"], 2), "invoice_count": v["count"]}
        for (y, m), v in sorted(monthly.items())
    ]

    return {
        "date_from": date_from,
        "date_to": date_to,
        "gst_collected": gst_collected,
        "gst_input_credit_eligible": gst_input_credit_eligible,
        "gst_payable": gst_payable,
        "gst_liability": gst_liability,
        "monthly_summary": monthly_summary,
    }


def budget_vs_actual(db: Session, period_year: int, period_month: int) -> dict:
    budgets = {
        b.category: float(b.budgeted_amount)
        for b in db.scalars(select(Budget).where(Budget.period_year == period_year, Budget.period_month == period_month)).all()
    }
    start = date(period_year, period_month, 1)
    end = date(period_year + 1, 1, 1) if period_month == 12 else date(period_year, period_month + 1, 1)
    expenses = db.scalars(select(Expense).where(Expense.expense_date >= start, Expense.expense_date < end)).all()

    actuals: dict[str, float] = defaultdict(float)
    for e in expenses:
        actuals[e.category] += float(e.amount)

    all_categories = set(EXPENSE_CATEGORIES) | set(budgets.keys()) | set(actuals.keys())
    lines = []
    for cat in sorted(all_categories):
        budgeted = round(budgets.get(cat, 0.0), 2)
        actual = round(actuals.get(cat, 0.0), 2)
        variance = round(budgeted - actual, 2)
        variance_percent = round((variance / budgeted) * 100, 2) if budgeted else None
        if budgeted == 0 and actual == 0:
            continue
        lines.append({"category": cat, "budgeted_amount": budgeted, "actual_amount": actual, "variance": variance, "variance_percent": variance_percent})

    return {
        "period_year": period_year,
        "period_month": period_month,
        "lines": lines,
        "total_budgeted": round(sum(l["budgeted_amount"] for l in lines), 2),
        "total_actual": round(sum(l["actual_amount"] for l in lines), 2),
    }


def ota_commission_report(db: Session, date_from: date, date_to: date) -> dict:
    commissions = db.scalars(
        select(OTACommission).join(Booking, OTACommission.booking_id == Booking.id).where(Booking.check_in >= date_from, Booking.check_in <= date_to)
    ).all()

    by_channel: dict[str, dict] = defaultdict(lambda: {"count": 0, "revenue": 0.0, "commission": 0.0, "gst": 0.0})
    for c in commissions:
        booking = c.booking
        nights = (booking.check_out - booking.check_in).days
        revenue = float(booking.rate_applied) * nights + float(booking.extra_guest_charge)
        by_channel[c.channel]["count"] += 1
        by_channel[c.channel]["revenue"] += revenue
        by_channel[c.channel]["commission"] += float(c.commission_amount)
        by_channel[c.channel]["gst"] += float(c.ota_gst_amount)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "by_channel": [
            {
                "channel": ch,
                "booking_count": v["count"],
                "total_booking_revenue": round(v["revenue"], 2),
                "total_commission": round(v["commission"], 2),
                "total_ota_gst": round(v["gst"], 2),
            }
            for ch, v in sorted(by_channel.items())
        ],
    }


def outstanding_payments(db: Session) -> list[dict]:
    """
    Was previously 1 query for bookings + 3 queries PER booking (payments,
    guest, unit) — 6,001 queries and ~2.8s measured against a 2,000-row
    benchmark dataset. Now: 4 queries total regardless of booking count
    (bookings, all their payments in one IN-query, all guests in one
    IN-query, all units in one IN-query), with the per-booking payment sum
    computed in Python from an in-memory dict instead of hitting the DB
    again for every row.
    """
    today = date.today()
    # Explicit ordering matters here specifically because this list gets
    # paginated (see the router) — without it, which rows land in the
    # first page is whatever order Postgres happens to return, not
    # necessarily the most urgent ones. Soonest check-in first: the
    # nearest-term outstanding balance is the one that most needs
    # following up before the guest arrives/leaves.
    bookings = db.scalars(select(Booking).where(Booking.status.in_(("confirmed", "checked_in", "checked_out"))).order_by(Booking.check_in)).all()
    if not bookings:
        return []

    booking_ids = [b.id for b in bookings]
    guest_ids = {b.guest_id for b in bookings}
    unit_ids = {b.unit_id for b in bookings}

    payments_by_booking: dict = defaultdict(float)
    for p in db.scalars(select(Payment).where(Payment.booking_id.in_(booking_ids))).all():
        payments_by_booking[p.booking_id] += float(p.amount)

    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}
    units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()}

    lines = []
    for b in bookings:
        nights = (b.check_out - b.check_in).days
        expected_total = float(b.rate_applied) * nights + float(b.extra_guest_charge)
        paid = payments_by_booking.get(b.id, 0.0)
        pending = round(expected_total - paid, 2)
        if pending <= 0:
            continue
        guest = guests_by_id.get(b.guest_id)
        unit = units_by_id.get(b.unit_id)
        lines.append({
            "booking_id": b.id,
            "guest_name": guest.name if guest else "—",
            "unit_name": unit.name if unit else "—",
            "check_in": b.check_in,
            "check_out": b.check_out,
            "expected_total": round(expected_total, 2),
            "paid_amount": round(paid, 2),
            "pending_balance": pending,
            "due_date": b.due_date,
            "is_overdue": bool(b.due_date and b.due_date < today),
        })
    return lines


def cash_flow(db: Session, date_from: date, date_to: date) -> dict:
    start, end = _range_bounds(date_from, date_to)

    payments = db.scalars(select(Payment).where(Payment.paid_at >= start, Payment.paid_at <= end)).all()
    deposits_received = db.scalars(select(SecurityDeposit).where(SecurityDeposit.received_at >= start, SecurityDeposit.received_at <= end)).all()
    deposits_refunded = db.scalars(
        select(SecurityDeposit).where(SecurityDeposit.refunded_at.isnot(None), SecurityDeposit.refunded_at >= start, SecurityDeposit.refunded_at <= end)
    ).all()
    expenses = db.scalars(select(Expense).where(Expense.expense_date >= date_from, Expense.expense_date <= date_to)).all()
    withdrawals = db.scalars(select(OwnerWithdrawal).where(OwnerWithdrawal.withdrawal_date >= date_from, OwnerWithdrawal.withdrawal_date <= date_to)).all()

    cash_in_payments = round(sum(float(p.amount) for p in payments), 2)
    cash_in_deposits = round(sum(float(d.amount) for d in deposits_received), 2)
    cash_in_total = round(cash_in_payments + cash_in_deposits, 2)

    cash_out_expenses = round(sum(float(e.amount) for e in expenses), 2)
    cash_out_withdrawals = round(sum(float(w.amount) for w in withdrawals), 2)
    cash_out_deposit_refunds = round(sum(float(d.refund_amount) for d in deposits_refunded), 2)
    cash_out_total = round(cash_out_expenses + cash_out_withdrawals + cash_out_deposit_refunds, 2)

    bank_modes = ("upi", "card", "bank_transfer", "razorpay")
    cash_in_bank = round(sum(float(p.amount) for p in payments if p.mode in bank_modes), 2)
    cash_in_hand = round(sum(float(p.amount) for p in payments if p.mode == "cash"), 2)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "cash_in_payments": cash_in_payments,
        "cash_in_deposits": cash_in_deposits,
        "cash_in_total": cash_in_total,
        "cash_out_expenses": cash_out_expenses,
        "cash_out_withdrawals": cash_out_withdrawals,
        "cash_out_deposit_refunds": cash_out_deposit_refunds,
        "cash_out_total": cash_out_total,
        "net_cash_flow": round(cash_in_total - cash_out_total, 2),
        "cash_in_bank": cash_in_bank,
        "cash_in_hand": cash_in_hand,
    }


def apartment_pnl(db: Session, unit_id, period_year: int, period_month: int) -> dict:
    """
    Per-apartment monthly P&L — revenue from that unit's bookings this
    month, minus expenses attributed to that unit (Expense.unit_id) plus
    that unit's utility readings for the month. Central/common expenses
    (Expense.unit_id is null) are deliberately NOT allocated here — they
    belong to the business-wide P&L, not any one apartment's, matching how
    the business's own spreadsheet keeps a separate 'Central' row rather
    than splitting shared costs across flats.
    """
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise ValueError("Unit not found")

    start = date(period_year, period_month, 1)
    end = date(period_year + 1, 1, 1) if period_month == 12 else date(period_year, period_month + 1, 1)

    bookings = db.scalars(
        select(Booking).where(
            Booking.unit_id == unit_id,
            Booking.status.in_(("confirmed", "checked_in", "checked_out")),
            Booking.check_in >= start, Booking.check_in < end,
        )
    ).all()
    revenue = 0.0
    advance_received = 0.0
    outstanding_balance = 0.0
    for b in bookings:
        nights = (b.check_out - b.check_in).days
        booking_total = float(b.rate_applied) * nights
        revenue += booking_total
        paid = sum(float(p.amount) for p in db.scalars(select(Payment).where(Payment.booking_id == b.id)).all())
        advance_received += paid
        outstanding_balance += max(booking_total - paid, 0.0)

    expenses = db.scalars(select(Expense).where(Expense.unit_id == unit_id, Expense.expense_date >= start, Expense.expense_date < end)).all()
    expense_by_category: dict[str, float] = defaultdict(float)
    for e in expenses:
        expense_by_category[e.category] += float(e.amount)

    utility_readings = db.scalars(select(UtilityReading).where(UtilityReading.unit_id == unit_id, UtilityReading.reading_date >= start, UtilityReading.reading_date < end)).all()
    for u in utility_readings:
        expense_by_category[u.utility_type] += u.amount

    # Utility bills SHARED across this unit (via UtilityReadingAllocation)
    # also belong in this apartment's P&L for its allocated share — a bill
    # split 3 ways should count 1/3 of it against each apartment, not be
    # silently excluded just because the reading itself has no single
    # unit_id.
    shared_allocations = db.scalars(
        select(UtilityReadingAllocation)
        .join(UtilityReading, UtilityReadingAllocation.utility_reading_id == UtilityReading.id)
        .where(UtilityReadingAllocation.unit_id == unit_id, UtilityReading.reading_date >= start, UtilityReading.reading_date < end)
    ).all()
    for alloc in shared_allocations:
        reading = db.get(UtilityReading, alloc.utility_reading_id)
        expense_by_category[reading.utility_type] += float(alloc.share_amount)

    purchases = db.scalars(select(PurchaseRecord).where(PurchaseRecord.unit_id == unit_id, PurchaseRecord.purchase_date >= start, PurchaseRecord.purchase_date < end)).all()
    for p in purchases:
        expense_by_category["purchases"] += float(p.amount)

    expense_lines = [{"category": cat, "amount": round(amt, 2)} for cat, amt in sorted(expense_by_category.items())]
    total_expenses = round(sum(e["amount"] for e in expense_lines), 2)

    return {
        "unit_id": unit_id,
        "unit_name": unit.name,
        "period_year": period_year,
        "period_month": period_month,
        "revenue": round(revenue, 2),
        "advance_received": round(advance_received, 2),
        "outstanding_balance": round(outstanding_balance, 2),
        "expense_lines": expense_lines,
        "total_expenses": total_expenses,
        "net_profit": round(revenue - total_expenses, 2),
    }


def daily_cashbook(db: Session, cashbook_date: date) -> dict:
    """
    A chronological, running-balance ledger of every cash movement on one
    day — the digital equivalent of the business's paper cashbook. Opening
    balance is taken from the previous day's DailyClosing if one exists,
    else 0 (an honest default when no closing history exists yet).
    """
    start, end = _range_bounds(cashbook_date, cashbook_date)

    previous_closing = db.scalar(select(DailyClosing).where(DailyClosing.closing_date < cashbook_date).order_by(DailyClosing.closing_date.desc()))
    opening_balance = float(previous_closing.closing_balance) if previous_closing else 0.0

    lines = []
    payments = db.scalars(select(Payment).where(Payment.paid_at >= start, Payment.paid_at <= end)).all()
    if payments:
        booking_ids = {p.booking_id for p in payments}
        bookings_by_id = {b.id: b for b in db.scalars(select(Booking).where(Booking.id.in_(booking_ids))).all()}
        guest_ids = {b.guest_id for b in bookings_by_id.values()}
        guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}
        for p in payments:
            booking = bookings_by_id.get(p.booking_id)
            guest = guests_by_id.get(booking.guest_id) if booking else None
            guest_name = guest.name if guest else "guest"
            if float(p.amount) < 0:
                # A refund (see payments/router.py::refund_payment) — a
                # negative Payment row. Shown here as a clear cash-OUT
                # line with a positive display amount, not a confusing
                # negative "in" line, even though the underlying stored
                # value is negative (which is what makes every other
                # "total paid" sum across the codebase net it out
                # automatically).
                lines.append((p.paid_at, f"Refund to {guest_name} ({p.mode}){': ' + p.refund_reason if p.refund_reason else ''}", "out", abs(float(p.amount))))
            else:
                lines.append((p.paid_at, f"Payment received — {guest_name} ({p.mode})", "in", float(p.amount)))

    for d in db.scalars(select(SecurityDeposit).where(SecurityDeposit.received_at >= start, SecurityDeposit.received_at <= end)).all():
        lines.append((d.received_at, "Security deposit received", "in", float(d.amount)))

    for d in db.scalars(select(SecurityDeposit).where(SecurityDeposit.refunded_at.isnot(None), SecurityDeposit.refunded_at >= start, SecurityDeposit.refunded_at <= end)).all():
        lines.append((d.refunded_at, "Security deposit refunded", "out", float(d.refund_amount)))

    for e in db.scalars(select(Expense).where(Expense.expense_date == cashbook_date)).all():
        lines.append((datetime.combine(cashbook_date, time.min, tzinfo=timezone.utc), f"Expense — {e.category}" + (f" ({e.notes})" if e.notes else ""), "out", float(e.amount)))

    for p in db.scalars(select(PurchaseRecord).where(PurchaseRecord.purchase_date == cashbook_date)).all():
        lines.append((datetime.combine(cashbook_date, time.min, tzinfo=timezone.utc), f"Purchase — {p.item_description}", "out", float(p.amount)))

    for w in db.scalars(select(OwnerWithdrawal).where(OwnerWithdrawal.withdrawal_date == cashbook_date)).all():
        lines.append((datetime.combine(cashbook_date, time.min, tzinfo=timezone.utc), "Owner withdrawal" + (f" ({w.notes})" if w.notes else ""), "out", float(w.amount)))

    lines.sort(key=lambda x: x[0])

    running = opening_balance
    cashbook_lines = []
    total_in = 0.0
    total_out = 0.0
    for ts, desc, txn_type, amount in lines:
        if txn_type == "in":
            running += amount
            total_in += amount
        else:
            running -= amount
            total_out += amount
        cashbook_lines.append({"time": ts, "description": desc, "transaction_type": txn_type, "amount": amount, "running_balance": round(running, 2)})

    return {
        "cashbook_date": cashbook_date,
        "opening_balance": round(opening_balance, 2),
        "lines": cashbook_lines,
        "closing_balance": round(running, 2),
        "total_in": round(total_in, 2),
        "total_out": round(total_out, 2),
    }


def advance_summary(db: Session) -> list[dict]:
    """Every active booking with an advance recorded but a balance still
    remaining. Same batch-loading fix as outstanding_payments above —
    payments/guests/units all fetched in one query each instead of per row.
    Ordered by check_in for the same reason outstanding_payments is: this
    list gets paginated, so an explicit, meaningful order (soonest first)
    determines which rows actually get seen, not arbitrary DB return order."""
    bookings = db.scalars(select(Booking).where(Booking.status.in_(("confirmed", "checked_in"))).order_by(Booking.check_in)).all()
    if not bookings:
        return []

    booking_ids = [b.id for b in bookings]
    guest_ids = {b.guest_id for b in bookings}
    unit_ids = {b.unit_id for b in bookings}

    payments_by_booking: dict = defaultdict(float)
    for p in db.scalars(select(Payment).where(Payment.booking_id.in_(booking_ids))).all():
        payments_by_booking[p.booking_id] += float(p.amount)

    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}
    units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()}

    lines = []
    for b in bookings:
        paid = payments_by_booking.get(b.id, 0.0)
        if paid <= 0:
            continue
        nights = (b.check_out - b.check_in).days
        total_due = float(b.rate_applied) * nights
        remaining = round(total_due - paid, 2)
        if remaining <= 0:
            continue
        guest = guests_by_id.get(b.guest_id)
        unit = units_by_id.get(b.unit_id)
        lines.append({
            "booking_id": b.id,
            "guest_name": guest.name if guest else "—",
            "unit_name": unit.name if unit else "—",
            "check_in": b.check_in,
            "check_out": b.check_out,
            "advance_paid": round(paid, 2),
            "total_due": round(total_due, 2),
            "balance_remaining": remaining,
        })
    return lines


def laundry_monthly_summary(db: Session, period_year: int, period_month: int) -> dict:
    """The Laundry Register's monthly summary — item-level totals matching
    the business's own Excel structure (quantity and amount per item type:
    bedsheet, pillow cover, bath towel, etc.), not just one lump sum."""
    start = date(period_year, period_month, 1)
    end = date(period_year + 1, 1, 1) if period_month == 12 else date(period_year, period_month + 1, 1)

    entries = db.scalars(select(LaundryEntry).where(LaundryEntry.entry_date >= start, LaundryEntry.entry_date < end)).all()

    by_item: dict = defaultdict(lambda: {"qty": 0, "amount": 0.0})
    for e in entries:
        by_item[e.item_type]["qty"] += e.quantity
        by_item[e.item_type]["amount"] += e.total_amount

    lines = [{"item_type": k, "total_quantity": v["qty"], "total_amount": round(v["amount"], 2)} for k, v in sorted(by_item.items())]
    return {
        "period_year": period_year,
        "period_month": period_month,
        "lines": lines,
        "grand_total": round(sum(l["total_amount"] for l in lines), 2),
    }


def income_register(db: Session, period_year: int, period_month: int) -> dict:
    """
    The Income Register — the direct digital equivalent of the business's
    own Excel sheet (one column block per flat: Date, Name, No Guest,
    Contact, Amounts, Advance, Balance, status), rebuilt from real booking
    and payment data rather than manual entry. "status" here is the
    booking's source (direct/airbnb/agoda/booking_com/walk_in), matching
    the business's own use of that column to record where the booking
    came from, not the booking's confirmed/cancelled state.
    """
    from app.modules.bookings.models import Booking, Guest
    from app.modules.properties.models import Unit

    start = date(period_year, period_month, 1)
    end = date(period_year + 1, 1, 1) if period_month == 12 else date(period_year, period_month + 1, 1)

    units = db.scalars(select(Unit)).all()
    bookings = db.scalars(
        select(Booking).where(
            Booking.status.in_(("confirmed", "checked_in", "checked_out")),
            Booking.check_in >= start, Booking.check_in < end,
        ).order_by(Booking.check_in)
    ).all()

    if not bookings:
        return {"period_year": period_year, "period_month": period_month, "units": [
            {"unit_id": u.id, "unit_name": u.name, "lines": [], "unit_total": 0.0} for u in units
        ], "grand_total": 0.0, "by_source": {}}

    booking_ids = [b.id for b in bookings]
    guest_ids = {b.guest_id for b in bookings}
    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}

    payments_by_booking: dict = defaultdict(float)
    for p in db.scalars(select(Payment).where(Payment.booking_id.in_(booking_ids))).all():
        payments_by_booking[p.booking_id] += float(p.amount)

    bookings_by_unit: dict = defaultdict(list)
    for b in bookings:
        bookings_by_unit[b.unit_id].append(b)

    by_source: dict = defaultdict(float)
    unit_blocks = []
    grand_total = 0.0
    for u in units:
        lines = []
        unit_total = 0.0
        for b in bookings_by_unit.get(u.id, []):
            guest = guests_by_id.get(b.guest_id)
            nights = (b.check_out - b.check_in).days
            amount = round(float(b.rate_applied) * nights, 2)
            advance = round(payments_by_booking.get(b.id, 0.0), 2)
            balance = round(amount - advance, 2)
            lines.append({
                "booking_id": b.id, "check_in": b.check_in,
                "guest_name": guest.name if guest else "—",
                "no_of_guests": b.adults + b.children,
                "contact": guest.phone if guest else "—",
                "amount": amount, "advance": advance, "balance": balance, "status": b.source,
            })
            unit_total += amount
            by_source[b.source] += amount
        unit_blocks.append({"unit_id": u.id, "unit_name": u.name, "lines": lines, "unit_total": round(unit_total, 2)})
        grand_total += unit_total

    return {
        "period_year": period_year, "period_month": period_month,
        "units": unit_blocks, "grand_total": round(grand_total, 2),
        "by_source": {k: round(v, 2) for k, v in by_source.items()},
    }


def property_pnl(db: Session, property_id, period_year: int, period_month: int) -> dict:
    """
    Property-wide P&L — sums apartment_pnl across every unit under one
    property. Reuses apartment_pnl's own logic per unit rather than
    re-deriving revenue/expense rules a second time, so the two can never
    silently drift apart.
    """
    from app.modules.properties.models import Property, Unit
    prop = db.get(Property, property_id)
    if prop is None:
        raise ValueError("Property not found")

    units = db.scalars(select(Unit).where(Unit.property_id == property_id)).all()
    unit_lines = [apartment_pnl(db, u.id, period_year, period_month) for u in units]

    total_revenue = round(sum(u["revenue"] for u in unit_lines), 2)
    total_expenses = round(sum(u["total_expenses"] for u in unit_lines), 2)
    return {
        "property_id": property_id,
        "property_name": prop.name,
        "period_year": period_year,
        "period_month": period_month,
        "units": unit_lines,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": round(total_revenue - total_expenses, 2),
    }
