"""
Aggregation logic for all finance dashboards/reports. Reads from existing
Payment/Invoice/Booking tables plus the new finance tables — never writes
to or recalculates anything in reports/service.py, so existing occupancy/
revenue/GST reports are completely unaffected by anything here.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.bookings.models import Booking, Guest
from app.modules.finance.models import EXPENSE_CATEGORIES, EXPENSE_PNL_STATUSES, Expense, ExpensePayment, Budget, DailyClosing, OwnerWithdrawal, PurchaseRecord, SecurityDeposit, OTACommission, OTAPayoutReceipt, UtilityReading, UtilityReadingAllocation, LaundryEntry, Loan, LoanEmiPayment
from app.modules.payments.models import PAYMENT_MODES, Invoice, Payment
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
    expenses = db.scalars(select(Expense).where(Expense.expense_date >= start, Expense.expense_date < end, Expense.approval_status.in_(EXPENSE_PNL_STATUSES))).all()

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
    # Cash-ledger figures below (cash_in_payments/cash_in_bank/cash_in_hand/
    # cash_in_total) are scoped to PAYMENT_MODES (upi/card/bank_transfer/
    # cash/razorpay) only — known_payments — not every Payment row: this is
    # a ledger of cash that has actually moved into one of the business's
    # own bank/hand instruments, and PAYMENT_MODES is exactly that set (see
    # payments/models.py). mode="zotel" is deliberately excluded from it,
    # not merely uncategorised: a Zotel-recorded Payment row means "the
    # guest's payment status as reported by Zotel," not "cash has landed in
    # our bank or hand" — Zotel's own payment_method field (pay_at_hotel/
    # pay_now/part_payments) describes a billing arrangement, not a
    # specific instrument, so there is nothing honest to bucket it into
    # here (see zotel/service.py's _import_one docstring for the original
    # reasoning). The actual cash event for OTA-sourced revenue is tracked
    # separately — cash_in_ota_payouts below, sourced from OTAPayoutReceipt,
    # a staff-confirmed record of money the OTA has actually paid out — so
    # folding zotel-mode payments into cash_in_total too would double-count
    # that revenue once both rows eventually exist for the same booking.
    # Zotel-mode payments are NOT simply dropped, though: cash_in_zotel_
    # pending below still surfaces them, informationally, so a real
    # recorded payment never silently vanishes from this report — it's
    # just correctly kept out of the bank/hand/total cash figures until an
    # actual OTAPayoutReceipt reconciles it.
    known_payments = [p for p in payments if p.mode in PAYMENT_MODES]
    zotel_payments = [p for p in payments if p.mode == "zotel"]
    deposits_received = db.scalars(select(SecurityDeposit).where(SecurityDeposit.received_at >= start, SecurityDeposit.received_at <= end)).all()
    deposits_refunded = db.scalars(
        select(SecurityDeposit).where(SecurityDeposit.refunded_at.isnot(None), SecurityDeposit.refunded_at >= start, SecurityDeposit.refunded_at <= end)
    ).all()
    expense_payments = db.scalars(select(ExpensePayment).where(ExpensePayment.payment_date >= date_from, ExpensePayment.payment_date <= date_to)).all()
    withdrawals = db.scalars(select(OwnerWithdrawal).where(OwnerWithdrawal.withdrawal_date >= date_from, OwnerWithdrawal.withdrawal_date <= date_to)).all()
    # Phase 19D-1: only ACTUALLY RECORDED EMI payments count as a cash
    # outflow — an EMI schedule row that's still "due" is not a payment,
    # it's an obligation, and must not be assumed paid. paid_amount is the
    # authoritative recorded amount (falls back to emi_amount only if a
    # payment was marked paid without an explicit override — see
    # mark_loan_emi_paid in finance/router.py). This does not touch Net
    # Profit anywhere — cash_flow() is a separate function from
    # apartment_pnl/property_pnl, which still never reference Loan/EMI.
    loan_emi_paid = db.scalars(
        select(LoanEmiPayment).where(LoanEmiPayment.status == "paid", LoanEmiPayment.paid_date >= date_from, LoanEmiPayment.paid_date <= date_to)
    ).all()
    # Phase 19E: only an actually-recorded OTA payout receipt counts as cash
    # in — the expected payout (gross - commission - GST) is never treated
    # as cash, since it's a computed estimate of what the OTA owes us, not
    # money that has moved. See OTAPayoutReceipt docstring: this table only
    # ever holds actual-received amounts, so summing it here can't conflate
    # expected with actual.
    ota_payouts_received = db.scalars(
        select(OTAPayoutReceipt).where(OTAPayoutReceipt.receipt_date >= date_from, OTAPayoutReceipt.receipt_date <= date_to)
    ).all()

    cash_in_payments = round(sum(float(p.amount) for p in known_payments), 2)
    cash_in_deposits = round(sum(float(d.amount) for d in deposits_received), 2)
    cash_in_ota_payouts = round(sum(float(r.actual_received_amount) for r in ota_payouts_received), 2)
    cash_in_total = round(cash_in_payments + cash_in_deposits + cash_in_ota_payouts, 2)
    # Informational only — deliberately NOT part of cash_in_total (see the
    # comment above known_payments/zotel_payments for why folding it in
    # would double-count against cash_in_ota_payouts).
    cash_in_zotel_pending = round(sum(float(p.amount) for p in zotel_payments), 2)

    cash_out_expenses = round(sum(float(p.amount) for p in expense_payments), 2)
    cash_out_withdrawals = round(sum(float(w.amount) for w in withdrawals), 2)
    cash_out_deposit_refunds = round(sum(float(d.refund_amount) for d in deposits_refunded), 2)
    cash_out_loan_emi = round(sum(float(p.paid_amount if p.paid_amount is not None else p.emi_amount) for p in loan_emi_paid), 2)
    cash_out_total = round(cash_out_expenses + cash_out_withdrawals + cash_out_deposit_refunds + cash_out_loan_emi, 2)

    bank_modes = ("upi", "card", "bank_transfer", "razorpay")
    cash_in_bank = round(sum(float(p.amount) for p in known_payments if p.mode in bank_modes), 2)
    cash_in_hand = round(sum(float(p.amount) for p in known_payments if p.mode == "cash"), 2)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "cash_in_payments": cash_in_payments,
        "cash_in_deposits": cash_in_deposits,
        "cash_in_ota_payouts": cash_in_ota_payouts,
        "cash_in_zotel_pending": cash_in_zotel_pending,
        "cash_in_total": cash_in_total,
        "cash_out_expenses": cash_out_expenses,
        "cash_out_withdrawals": cash_out_withdrawals,
        "cash_out_deposit_refunds": cash_out_deposit_refunds,
        "cash_out_loan_emi": cash_out_loan_emi,
        "cash_out_total": cash_out_total,
        "net_cash_flow": round(cash_in_total - cash_out_total, 2),
        "cash_in_bank": cash_in_bank,
        "cash_in_hand": cash_in_hand,
    }


def _ota_cost_for_bookings(db: Session, booking_ids: list) -> float:
    """
    Phase 19D Decision 5: OTA commission + OTA-charged GST are real costs
    against revenue, but ONLY for bookings that actually have an
    OTACommission row. A booking with no commission record contributes 0 —
    this deliberately does not attempt to detect/estimate a "missing"
    commission from Booking.source, since that field and
    OTACommission.channel are independently-typed, unlinked vocabularies
    (see the Phase 19D-A analysis). Reconciling that gap is Phase 19E's job.
    """
    if not booking_ids:
        return 0.0
    rows = db.scalars(select(OTACommission).where(OTACommission.booking_id.in_(booking_ids))).all()
    return round(sum(float(r.commission_amount) + float(r.ota_gst_amount) for r in rows), 2)


def _gross_booking_amount(booking: Booking) -> float:
    """Same formula as outstanding_payments/apartment_pnl/ota_commission_report — the one canonical gross-revenue-per-booking calculation, never re-derived differently here."""
    nights = (booking.check_out - booking.check_in).days
    return float(booking.rate_applied) * nights + float(booking.extra_guest_charge)


def ota_reconciliation_lines(db: Session, date_from: date, date_to: date, channel: str | None = None) -> list[dict]:
    """
    Phase 19E: one row per existing OTACommission (never invented — a
    booking with no commission record simply doesn't appear here, see
    ota_missing_commissions for that gap instead). expected_payout is
    computed fresh every call from Booking + OTACommission, the same two
    tables apartment_pnl/ota_commission_report already read — no new
    revenue or commission source is introduced. reconciliation_status is
    "unreconciled" whenever no OTAPayoutReceipt row exists yet, otherwise
    it's exactly the status the receipt was recorded/updated with.
    """
    query = select(OTACommission).join(Booking, OTACommission.booking_id == Booking.id).where(Booking.check_in >= date_from, Booking.check_in <= date_to)
    if channel:
        query = query.where(OTACommission.channel == channel)
    commissions = db.scalars(query).all()
    if not commissions:
        return []

    commission_ids = [c.id for c in commissions]
    receipts_by_commission = {
        r.ota_commission_id: r for r in db.scalars(select(OTAPayoutReceipt).where(OTAPayoutReceipt.ota_commission_id.in_(commission_ids))).all()
    }
    guest_ids = {c.booking.guest_id for c in commissions}
    unit_ids = {c.booking.unit_id for c in commissions}
    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}
    units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()}

    lines = []
    for c in commissions:
        booking = c.booking
        gross = _gross_booking_amount(booking)
        expected_payout = round(gross - float(c.commission_amount) - float(c.ota_gst_amount), 2)
        receipt = receipts_by_commission.get(c.id)
        guest = guests_by_id.get(booking.guest_id)
        unit = units_by_id.get(booking.unit_id)
        actual = float(receipt.actual_received_amount) if receipt else None

        lines.append({
            "ota_commission_id": c.id,
            "booking_id": booking.id,
            "channel": c.channel,
            "guest_name": guest.name if guest else "—",
            "unit_name": unit.name if unit else "—",
            "check_in": booking.check_in,
            "check_out": booking.check_out,
            "gross_amount": round(gross, 2),
            "commission_amount": round(float(c.commission_amount), 2),
            "ota_gst_amount": round(float(c.ota_gst_amount), 2),
            "expected_payout": expected_payout,
            "actual_received_amount": actual,
            "difference": round(expected_payout - actual, 2) if actual is not None else None,
            "receipt_date": receipt.receipt_date if receipt else None,
            "reference_number": receipt.reference_number if receipt else None,
            "reconciliation_status": receipt.status if receipt else "unreconciled",
            "receipt_id": receipt.id if receipt else None,
            "receipt_updated_at": receipt.updated_at if receipt else None,
        })
    return lines


def ota_missing_commissions(db: Session, date_from: date, date_to: date) -> list[dict]:
    """
    Bookings whose source looks OTA-sourced (the 'ota_<channel>' naming
    convention documented inline on Booking.source) but have no
    OTACommission row at all. gross_amount is shown since it doesn't
    depend on a commission record — but no commission, GST, or expected
    payout is ever invented for these; Booking.source and
    OTACommission.channel remain independently-typed, unlinked
    vocabularies (Phase 19D-A finding). This is the gap list, not a
    reconciliation — see ota_reconciliation_lines for bookings that do
    have a commission record.
    """
    bookings = db.scalars(
        select(Booking).where(Booking.source.like("ota_%"), Booking.check_in >= date_from, Booking.check_in <= date_to)
    ).all()
    if not bookings:
        return []

    booking_ids = [b.id for b in bookings]
    already_commissioned = {row for (row,) in db.execute(select(OTACommission.booking_id).where(OTACommission.booking_id.in_(booking_ids)))}
    missing = [b for b in bookings if b.id not in already_commissioned]
    if not missing:
        return []

    guest_ids = {b.guest_id for b in missing}
    unit_ids = {b.unit_id for b in missing}
    guests_by_id = {g.id: g for g in db.scalars(select(Guest).where(Guest.id.in_(guest_ids))).all()}
    units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()}

    lines = []
    for b in missing:
        guest = guests_by_id.get(b.guest_id)
        unit = units_by_id.get(b.unit_id)
        lines.append({
            "booking_id": b.id,
            "guest_name": guest.name if guest else "—",
            "unit_name": unit.name if unit else "—",
            "check_in": b.check_in,
            "check_out": b.check_out,
            "source": b.source,
            "gross_amount": round(_gross_booking_amount(b), 2),
        })
    return lines


def apartment_pnl(db: Session, unit_id, period_year: int, period_month: int) -> dict:
    """
    Per-apartment monthly P&L — revenue from that unit's bookings this
    month, minus expenses attributed to that unit (Expense.unit_id) plus
    that unit's utility readings, purchases, and OTA commission/GST for the
    month. Central/common expenses (Expense.unit_id is null) are
    deliberately NOT allocated here — they belong to the property-wide P&L
    (see property_pnl), not any one apartment's, matching how the
    business's own spreadsheet keeps a separate 'Central' row rather than
    splitting shared costs across flats. This is the canonical Net Profit
    calculation as of Phase 19D — see the Phase 19D-A/19D reports for the
    reconciliation this formula resolves.

    Loan/EMI is deliberately NOT referenced anywhere in this function
    (Phase 19D Decision 3): the full EMI amount is never an expense (it
    mixes a real cost — interest — with a balance-sheet item — principal —
    that isn't a cost at all), and no interest-only figure exists anywhere
    in the Loan/LoanEmiPayment schema to subtract instead. The moment a
    real interest figure becomes available, this is the function to change.
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
        # Phase 19D Decision 1: extra_guest_charge is part of room revenue,
        # consistent with build_summary/outstanding_payments/ota_commission_report
        # — this was the one formula in the codebase that omitted it.
        booking_total = float(b.rate_applied) * nights + float(b.extra_guest_charge)
        revenue += booking_total
        paid = sum(float(p.amount) for p in db.scalars(select(Payment).where(Payment.booking_id == b.id)).all())
        advance_received += paid
        outstanding_balance += max(booking_total - paid, 0.0)

    expenses = db.scalars(select(Expense).where(Expense.unit_id == unit_id, Expense.expense_date >= start, Expense.expense_date < end, Expense.approval_status.in_(EXPENSE_PNL_STATUSES))).all()
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

    # Phase 19D Decision 5: OTA commission + OTA-charged GST are real costs
    # against this apartment's revenue, for whichever of its bookings
    # actually have a recorded OTACommission — never estimated for bookings
    # without one.
    ota_cost = _ota_cost_for_bookings(db, [b.id for b in bookings])
    if ota_cost:
        expense_by_category["ota_commission"] += ota_cost

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

    expense_payments = db.scalars(
        select(ExpensePayment).options(joinedload(ExpensePayment.expense)).where(ExpensePayment.payment_date == cashbook_date)
    ).all()
    for payment in expense_payments:
        lines.append((
            datetime.combine(payment.payment_date, time.min, tzinfo=timezone.utc),
            f"Expense payment — {payment.expense.category} ({payment.payment_method})",
            "out", float(payment.amount),
        ))

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
    property, PLUS this property's central/shared expenses (Phase 19D
    Decision 2) — costs with Expense.unit_id IS NULL, which apartment_pnl
    deliberately excludes from every individual apartment's figure. Shown
    as its own `central_expenses` line, never distributed across the
    apartments.

    IMPORTANT LIMITATION (documented, not silently worked around): Expense
    has no property_id column, only a nullable unit_id. A central expense
    therefore cannot be attributed to one specific property if more than
    one Property row exists — this function currently attributes EVERY
    central expense in the period to whichever property_id is asked for,
    which is only correct because exactly one Property exists in this
    deployment today (verified before this change shipped). If a second
    property is ever added, Expense needs a property_id column before this
    function's central-expense figure can be trusted — flagged in the
    Phase 19D report as a required follow-up, not fixed here.
    """
    from app.modules.properties.models import Property, Unit
    prop = db.get(Property, property_id)
    if prop is None:
        raise ValueError("Property not found")

    units = db.scalars(select(Unit).where(Unit.property_id == property_id)).all()
    unit_lines = [apartment_pnl(db, u.id, period_year, period_month) for u in units]

    start = date(period_year, period_month, 1)
    end = date(period_year + 1, 1, 1) if period_month == 12 else date(period_year, period_month + 1, 1)
    central_expenses = round(sum(float(e.amount) for e in db.scalars(
        select(Expense).where(Expense.unit_id.is_(None), Expense.expense_date >= start, Expense.expense_date < end, Expense.approval_status.in_(EXPENSE_PNL_STATUSES))
    ).all()), 2)

    total_revenue = round(sum(u["revenue"] for u in unit_lines), 2)
    total_expenses = round(sum(u["total_expenses"] for u in unit_lines) + central_expenses, 2)
    return {
        "property_id": property_id,
        "property_name": prop.name,
        "period_year": period_year,
        "period_month": period_month,
        "units": unit_lines,
        "total_revenue": total_revenue,
        "central_expenses": central_expenses,
        "total_expenses": total_expenses,
        "net_profit": round(total_revenue - total_expenses, 2),
    }


def loan_summary(db: Session, today: date | None = None) -> dict:
    """
    Phase 19C. Reads only Loan/LoanEmiPayment — never touches Expense,
    Payment, Invoice, or any P&L function, so this cannot change the
    numbers those already-existing reports produce.
    """
    today = today or date.today()
    month_start = today.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    active_loans = db.scalars(select(Loan).where(Loan.status == "active")).all()
    total_monthly_emi_obligation = round(sum(float(l.emi_amount) for l in active_loans), 2)

    all_emis = db.scalars(select(LoanEmiPayment)).all()
    upcoming = [e for e in all_emis if e.status == "due" and e.due_date >= today]
    overdue = [e for e in all_emis if e.status == "due" and e.due_date < today]
    paid_this_month = [e for e in all_emis if e.status == "paid" and e.paid_date and month_start <= e.paid_date < next_month]
    outstanding_this_month = [e for e in all_emis if e.status == "due" and month_start <= e.due_date < next_month]

    return {
        "total_active_loans": len(active_loans),
        "total_monthly_emi_obligation": total_monthly_emi_obligation,
        "upcoming_emi_count": len(upcoming),
        "upcoming_emi_amount": round(sum(float(e.emi_amount) for e in upcoming), 2),
        "overdue_emi_count": len(overdue),
        "overdue_emi_amount": round(sum(float(e.emi_amount) for e in overdue), 2),
        "paid_this_month_count": len(paid_this_month),
        "paid_this_month_amount": round(sum(float(e.paid_amount or e.emi_amount) for e in paid_this_month), 2),
        "outstanding_this_month_count": len(outstanding_this_month),
        "outstanding_this_month_amount": round(sum(float(e.emi_amount) for e in outstanding_this_month), 2),
    }


def financial_snapshot(db: Session, date_from: date, date_to: date, revenue_total: float, property_id=None) -> dict:
    """
    Phase 19F: read-only, self-contained Finance aggregation for callers
    that need a cross-domain snapshot (P&L, cash flow, budget, loans/EMI,
    OTA reconciliation) built entirely from already-existing canonical
    Finance calculations — nothing here is a new formula:

      - pnl.revenue: when a single property_id is given, this is
        property_pnl's own total_revenue for the calendar month containing
        date_to — the SAME figure and SAME time window as
        operating_expenses/central_expenses/net_profit below, so the four
        numbers stay internally consistent (never revenue for one window
        sitting next to expenses/profit for a different one). Only in the
        portfolio-wide (property_id=None) case, where property_pnl is not
        called at all (see below), does this fall back to the caller-
        supplied `revenue_total` for the exact [date_from, date_to] range —
        the Finance module intentionally has no dependency on any revenue
        calculation that lives outside it, so a caller that already has a
        bounded, canonical portfolio-wide revenue figure passes it in
        rather than this function deriving one itself.
      - pnl.operating_expenses/central_expenses/net_profit: property_pnl(),
        anchored to the calendar month containing date_to, since
        property_pnl is itself month-scoped (see its own docstring) — but
        ONLY when a single property_id is given. property_pnl sums
        apartment_pnl() across every unit in a property, and apartment_pnl
        issues a bounded-per-UNIT but NOT bounded-per-PORTFOLIO set of
        queries (a query per booking for its payments, among others) — so
        calling it across every Property row for every caller by default
        would make this function's query count scale with the whole
        portfolio's cumulative unit/booking history. Rather than silently
        estimating a substitute figure, these three fields are simply None
        for the portfolio-wide (property_id=None) case — never invented.
        Passing an explicit property_id (the same one Finance's own
        Property P&L screen requires) computes them exactly, at that one
        property's existing, accepted query cost.
      - cash_flow: cash_flow(), passed the exact date_from/date_to given —
        untouched, not re-derived. Every one of its internal queries is a
        single bounded SELECT, so this never scales with entity count.
      - budget / payroll / utilities / purchases: budget_vs_actual() for
        the same calendar month as pnl — payroll/utilities/purchases are
        read directly off its already-computed per-category `lines`,
        never a new Expense query. Also bounded (two flat SELECTs).
      - loans/EMI: loan_summary(), plus a plain field-read of each active
        Loan's recorded principal_amount (never estimated/amortized).
        Bounded (two flat SELECTs).
      - ota: ota_commission_report() / ota_reconciliation_lines() /
        ota_missing_commissions(), passed the exact date_from/date_to —
        the exact same Phase 19E reconciliation data the Finance OTA tab
        shows, just totaled for a caller-consumable summary. These
        functions are untouched here and inherit whatever query
        characteristics they already have.
      - outstanding_obligations: reassembles figures already computed
        above (loan_summary, ota reconciliation) plus outstanding_payments()
        — no new calculation.
    """
    from app.modules.properties.models import Property

    pnl_year, pnl_month = date_to.year, date_to.month

    if property_id is not None:
        prop = db.get(Property, property_id)
        if prop is not None:
            p = property_pnl(db, property_id, pnl_year, pnl_month)
            pnl = {
                "period_year": pnl_year, "period_month": pnl_month,
                "period_note": (
                    "Anchored to the calendar month containing date_to — property_pnl is month-scoped, the same convention "
                    "Finance's own Property P&L screen uses. Revenue here is property_pnl's own figure for that full "
                    "calendar month (not the narrower date_from/date_to range business_health uses above), so it stays "
                    "internally consistent with operating_expenses/central_expenses/net_profit alongside it."
                ),
                "revenue": p["total_revenue"],
                "operating_expenses": p["total_expenses"], "central_expenses": p["central_expenses"], "net_profit": p["net_profit"],
                "properties_included": 1,
            }
        else:
            pnl = {
                "period_year": pnl_year, "period_month": pnl_month, "period_note": "property_id not found.",
                "revenue": revenue_total, "operating_expenses": None, "central_expenses": None, "net_profit": None,
                "properties_included": 0,
            }
    else:
        pnl = {
            "period_year": pnl_year, "period_month": pnl_month,
            "period_note": (
                "Operating expenses/central expenses/net profit require a single property_id — computing the canonical "
                "Property P&L across every property in one automatic bundle call is not query-bounded (property_pnl scales "
                "with that property's unit/booking count) and is intentionally not attempted here. Call with an explicit "
                "property_id, or use Finance > Property P&L, for those three figures."
            ),
            "revenue": revenue_total, "operating_expenses": None, "central_expenses": None, "net_profit": None,
            "properties_included": 0,
        }

    cf = cash_flow(db, date_from, date_to)

    budget = budget_vs_actual(db, pnl_year, pnl_month)
    budget_lines_by_category = {l["category"]: l for l in budget["lines"]}
    utility_categories = ("electricity", "water", "internet")
    expense_breakdown = {
        "payroll_total": budget_lines_by_category.get("salary", {}).get("actual_amount", 0.0),
        "utilities_total": round(sum(budget_lines_by_category.get(c, {}).get("actual_amount", 0.0) for c in utility_categories), 2),
        "purchases_total": budget_lines_by_category.get("purchases", {}).get("actual_amount", 0.0),
    }

    ls = loan_summary(db)
    active_loans = db.scalars(select(Loan).where(Loan.status == "active")).all()
    loans = {
        **ls,
        "active_loans": [{
            "loan_id": str(l.id), "loan_name": l.loan_name, "lender_name": l.lender_name,
            "principal_amount": float(l.principal_amount) if l.principal_amount is not None else None,
            "emi_amount": float(l.emi_amount),
        } for l in active_loans],
    }

    ota_report = ota_commission_report(db, date_from, date_to)
    recon_lines = ota_reconciliation_lines(db, date_from, date_to)
    unreconciled = [l for l in recon_lines if l["reconciliation_status"] == "unreconciled"]
    discrepancies = [l for l in recon_lines if l["reconciliation_status"] == "discrepancy"]
    missing = ota_missing_commissions(db, date_from, date_to)
    ota = {
        "commission_total": round(sum(c["total_commission"] for c in ota_report["by_channel"]), 2),
        "gst_total": round(sum(c["total_ota_gst"] for c in ota_report["by_channel"]), 2),
        "expected_payout_total": round(sum(l["expected_payout"] for l in recon_lines), 2),
        "actual_received_total": round(sum(l["actual_received_amount"] for l in recon_lines if l["actual_received_amount"] is not None), 2),
        "reconciled_count": len(recon_lines) - len(unreconciled),
        "unreconciled_count": len(unreconciled),
        "discrepancy_count": len(discrepancies),
        "discrepancy_total": round(sum(abs(l["difference"] or 0.0) for l in discrepancies), 2),
        "missing_commission_count": len(missing),
        "missing_commission_gross_total": round(sum(m["gross_amount"] for m in missing), 2),
    }

    guest_outstanding = outstanding_payments(db)
    outstanding_obligations = {
        "emi_overdue_amount": ls["overdue_emi_amount"],
        "emi_overdue_count": ls["overdue_emi_count"],
        "emi_due_this_month_amount": ls["outstanding_this_month_amount"],
        "ota_unreconciled_expected_payout": round(sum(l["expected_payout"] for l in unreconciled), 2),
        "guest_outstanding_balance": round(sum(o["pending_balance"] for o in guest_outstanding), 2),
    }

    return {
        "period": {"date_from": date_from, "date_to": date_to},
        "source_note": "Every figure is a direct read from the existing canonical Finance/OTA/Loan services — nothing here is recalculated independently. FACTS only; no recommendation is implied.",
        "pnl": pnl,
        "cash_flow": cf,
        "budget": {"period_year": pnl_year, "period_month": pnl_month, "total_budgeted": budget["total_budgeted"], "total_actual": budget["total_actual"], "lines": budget["lines"]},
        "loans": loans,
        "ota": ota,
        "expenses": expense_breakdown,
        "outstanding_obligations": outstanding_obligations,
    }
