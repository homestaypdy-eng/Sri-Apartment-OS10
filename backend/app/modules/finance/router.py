import os
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.notifications.service import trigger_expense_approval, trigger_daily_closing_completed
from app.modules.auth.models import User
from app.modules.bookings.models import Booking
from app.modules.reports.service import export_tabular_pdf, export_tabular_xlsx
from app.modules.properties.models import Unit
from app.shared.file_upload import read_and_validate_upload

EXPENSE_ATTACHMENT_STORAGE_DIR = os.environ.get("EXPENSE_ATTACHMENT_STORAGE_DIR", "/tmp/sri_apartment_expense_attachments")
os.makedirs(EXPENSE_ATTACHMENT_STORAGE_DIR, exist_ok=True)
from app.modules.finance import service
from app.modules.finance.models import (
    EXPENSE_CATEGORIES,
    OTA_CHANNELS_FOR_COMMISSION,
    Budget,
    DailyClosing,
    Expense,
    ExpenseAttachment,
    LaundryEntry,
    OTACommission,
    OwnerWithdrawal,
    PurchaseRecord,
    SecurityDeposit,
    UtilityReading,
    UtilityReadingAllocation,
)
from app.modules.finance.schemas import (
    AdvanceSummaryLine,
    ApartmentPnL,
    BudgetCreate,
    BudgetOut,
    BudgetVsActualReport,
    CashFlowReport,
    DailyCashbook,
    DailyClosingCreate,
    DailyClosingOut,
    ExpenseApprovalAction,
    ExpenseAttachmentOut,
    BulkExpenseApprovalAction,
    BulkApprovalResult,
    ExpenseCreate,
    ExpenseOut,
    GstDashboardReport,
    IncomeRegister,
    LaundryEntryCreate,
    LaundryEntryOut,
    LaundryMonthlySummary,
    OTACommissionCreate,
    OTACommissionOut,
    OTACommissionReport,
    OutstandingPaymentLine,
    OwnerWithdrawalCreate,
    OwnerWithdrawalOut,
    PropertyPnL,
    PurchaseRecordCreate,
    PurchaseRecordOut,
    SecurityDepositCreate,
    SecurityDepositOut,
    SecurityDepositRefund,
    UtilityReadingCreate,
    UtilityReadingOut,
)
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


def _default_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    if date_from and date_to:
        return date_from, date_to
    today = date.today()
    return today.replace(day=1), today


@router.post("/expenses", response_model=ExpenseOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_expense(payload: ExpenseCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not payload.category or not payload.category.strip():
        raise HTTPException(400, "category cannot be empty")
    expense = Expense(**payload.model_dump(), created_by=user.id)
    db.add(expense)
    db.flush()
    record_audit_log(db, user.id, "finance.expense.create", entity_type="expense", entity_id=str(expense.id), details=f"{payload.category}: Rs.{payload.amount} on {payload.expense_date}")
    db.commit()
    db.refresh(expense)
    return expense


@router.get("/expense-categories", dependencies=[Depends(require_role("admin", "staff"))])
def list_expense_categories(db: Session = Depends(get_db)):
    """Suggested categories for the UI's dropdown: the default set plus any
    custom category that's actually been used, so a typed-in custom
    category shows up as a quick-pick next time rather than needing to be
    retyped every time."""
    used = {row[0] for row in db.execute(select(Expense.category).distinct()).all()}
    return sorted(set(EXPENSE_CATEGORIES) | used)


@router.post("/expenses/{expense_id}/approval", response_model=ExpenseOut, dependencies=[Depends(require_role("admin"))])
def approve_or_reject_expense(expense_id: uuid.UUID, payload: ExpenseApprovalAction, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(404, "Expense not found")

    expense.approval_status = "approved" if payload.approve else "rejected"
    expense.approved_by = user.id
    expense.approved_at = datetime.now(timezone.utc)
    if payload.notes:
        expense.notes = f"{expense.notes or ''}\n[{'Approved' if payload.approve else 'Rejected'}] {payload.notes}".strip()

    record_audit_log(db, user.id, "finance.expense.approval", entity_type="expense", entity_id=str(expense.id), details=f"{expense.approval_status}: {payload.notes or 'no notes'}")
    trigger_expense_approval(db, expense, payload.approve)
    db.commit()
    db.refresh(expense)
    return expense


@router.post("/expenses/bulk-approval", response_model=BulkApprovalResult, dependencies=[Depends(require_role("admin"))])
def bulk_approve_or_reject_expenses(payload: BulkExpenseApprovalAction, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Applies the same approve/reject action to many expenses at once —
    the actual mutation and audit logging per row is identical to the
    single-item endpoint above, just looped, so a bulk action is never
    silently different from doing the same thing one at a time.
    """
    new_status = "approved" if payload.approve else "rejected"
    found_ids = set()
    for expense_id in payload.expense_ids:
        expense = db.get(Expense, expense_id)
        if expense is None:
            continue
        found_ids.add(expense_id)
        expense.approval_status = new_status
        expense.approved_by = user.id
        expense.approved_at = datetime.now(timezone.utc)
        if payload.notes:
            expense.notes = f"{expense.notes or ''}\n[{'Approved' if payload.approve else 'Rejected'}] {payload.notes}".strip()
        record_audit_log(db, user.id, "finance.expense.approval", entity_type="expense", entity_id=str(expense.id), details=f"{new_status} (bulk): {payload.notes or 'no notes'}")

    not_found = [eid for eid in payload.expense_ids if eid not in found_ids]
    db.commit()
    return {
        "approved_count": len(found_ids) if payload.approve else 0,
        "rejected_count": len(found_ids) if not payload.approve else 0,
        "not_found_ids": not_found,
    }


@router.post("/expenses/{expense_id}/attachments", response_model=ExpenseAttachmentOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
async def upload_expense_attachment(expense_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(404, "Expense not found")

    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{expense_id}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(EXPENSE_ATTACHMENT_STORAGE_DIR, stored_name)
    contents = await read_and_validate_upload(file)
    with open(stored_path, "wb") as f:
        f.write(contents)

    attachment = ExpenseAttachment(
        expense_id=expense_id,
        filename=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream",
        file_path=stored_path,
        uploaded_by=user.id,
    )
    db.add(attachment)
    record_audit_log(db, user.id, "finance.expense.attachment_upload", entity_type="expense", entity_id=str(expense_id), details=f"Uploaded: {attachment.filename}")
    db.commit()
    db.refresh(attachment)
    return attachment


@router.get("/expenses/{expense_id}/attachments", response_model=list[ExpenseAttachmentOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_expense_attachments(expense_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(ExpenseAttachment).where(ExpenseAttachment.expense_id == expense_id)).all()


@router.get("/expenses/{expense_id}/attachments/{attachment_id}/download", dependencies=[Depends(require_role("admin", "staff"))])
def download_expense_attachment(expense_id: uuid.UUID, attachment_id: uuid.UUID, db: Session = Depends(get_db)):
    attachment = db.get(ExpenseAttachment, attachment_id)
    if attachment is None or attachment.expense_id != expense_id:
        raise HTTPException(404, "Attachment not found")
    if not os.path.exists(attachment.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(attachment.file_path, media_type=attachment.content_type, filename=attachment.filename)


@router.get("/expenses", response_model=list[ExpenseOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_expenses(
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(Expense).order_by(Expense.expense_date.desc(), Expense.id)
    if category:
        query = query.where(Expense.category == category)
    if date_from:
        query = query.where(Expense.expense_date >= date_from)
    if date_to:
        query = query.where(Expense.expense_date <= date_to)
    query = query.offset(offset).limit(limit)
    return db.scalars(query).all()


@router.post("/budgets", response_model=BudgetOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_budget(payload: BudgetCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not payload.category or not payload.category.strip():
        raise HTTPException(400, "category cannot be empty")
    budget = Budget(**payload.model_dump())
    db.add(budget)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"A budget for '{payload.category}' in {payload.period_month}/{payload.period_year} already exists")
    record_audit_log(db, user.id, "finance.budget.create", entity_type="budget", entity_id=str(budget.id), details=f"{payload.category} {payload.period_month}/{payload.period_year}: Rs.{payload.budgeted_amount}")
    db.commit()
    db.refresh(budget)
    return budget


@router.get("/budgets", response_model=list[BudgetOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_budgets(period_year: int | None = None, period_month: int | None = None, db: Session = Depends(get_db)):
    query = select(Budget)
    if period_year:
        query = query.where(Budget.period_year == period_year)
    if period_month:
        query = query.where(Budget.period_month == period_month)
    return db.scalars(query).all()


@router.get("/budget-vs-actual", response_model=BudgetVsActualReport, dependencies=[Depends(require_role("admin", "staff"))])
def get_budget_vs_actual(period_year: int = Query(...), period_month: int = Query(..., ge=1, le=12), db: Session = Depends(get_db)):
    return service.budget_vs_actual(db, period_year, period_month)


@router.post("/deposits", response_model=SecurityDepositOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def create_deposit(payload: SecurityDepositCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = db.get(Booking, payload.booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    deposit = SecurityDeposit(**payload.model_dump(), status="held")
    db.add(deposit)
    db.flush()
    record_audit_log(db, user.id, "finance.deposit.received", entity_type="security_deposit", entity_id=str(deposit.id), details=f"Rs.{payload.amount} for booking {booking.id}")
    db.commit()
    db.refresh(deposit)
    return deposit


@router.get("/deposits", response_model=list[SecurityDepositOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_deposits(
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(SecurityDeposit).order_by(SecurityDeposit.received_at.desc())
    if status:
        query = query.where(SecurityDeposit.status == status)
    return db.scalars(query.offset(offset).limit(limit)).all()


@router.get("/deposits/pending-refund", response_model=list[SecurityDepositOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_pending_refunds(db: Session = Depends(get_db)):
    return db.scalars(select(SecurityDeposit).where(SecurityDeposit.status.in_(("held", "partially_refunded"))).order_by(SecurityDeposit.received_at)).all()


@router.post("/deposits/{deposit_id}/refund", response_model=SecurityDepositOut, dependencies=[Depends(require_role("admin", "staff"))])
def refund_deposit(deposit_id: uuid.UUID, payload: SecurityDepositRefund, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # with_for_update locks the row for the rest of this transaction, so two
    # near-simultaneous partial-refund requests on the same deposit can't
    # both read a stale refund_amount and lose one of the updates.
    deposit = db.get(SecurityDeposit, deposit_id, with_for_update=True)
    if deposit is None:
        raise HTTPException(404, "Deposit not found")

    new_total_refunded = float(deposit.refund_amount) + payload.refund_amount
    if new_total_refunded > float(deposit.amount):
        raise HTTPException(400, f"Refund would exceed the original deposit amount ({deposit.amount}); already refunded {deposit.refund_amount}")

    deposit.refund_amount = new_total_refunded
    deposit.refunded_at = datetime.now(timezone.utc)
    deposit.status = "refunded" if new_total_refunded == float(deposit.amount) else "partially_refunded"
    if payload.notes:
        deposit.notes = f"{deposit.notes or ''}\n[Refund] {payload.notes}".strip()

    record_audit_log(db, user.id, "finance.deposit.refund", entity_type="security_deposit", entity_id=str(deposit.id), details=f"Refunded Rs.{payload.refund_amount}, new status: {deposit.status}")
    db.commit()
    db.refresh(deposit)
    return deposit


@router.post("/owner-withdrawals", response_model=OwnerWithdrawalOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_withdrawal(payload: OwnerWithdrawalCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    withdrawal = OwnerWithdrawal(**payload.model_dump(), created_by=user.id)
    db.add(withdrawal)
    db.flush()
    record_audit_log(db, user.id, "finance.owner_withdrawal.create", entity_type="owner_withdrawal", entity_id=str(withdrawal.id), details=f"Rs.{payload.amount} on {payload.withdrawal_date}")
    db.commit()
    db.refresh(withdrawal)
    return withdrawal


@router.get("/owner-withdrawals", response_model=list[OwnerWithdrawalOut], dependencies=[Depends(require_role("admin"))])
def list_withdrawals(
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(OwnerWithdrawal).order_by(OwnerWithdrawal.withdrawal_date.desc())
    if date_from:
        query = query.where(OwnerWithdrawal.withdrawal_date >= date_from)
    if date_to:
        query = query.where(OwnerWithdrawal.withdrawal_date <= date_to)
    return db.scalars(query.offset(offset).limit(limit)).all()


@router.post("/ota-commissions", response_model=OTACommissionOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_commission(payload: OTACommissionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.channel not in OTA_CHANNELS_FOR_COMMISSION:
        raise HTTPException(400, f"Invalid channel '{payload.channel}'. Must be one of {OTA_CHANNELS_FOR_COMMISSION}")
    booking = db.get(Booking, payload.booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")

    existing = db.scalar(select(OTACommission).where(OTACommission.booking_id == payload.booking_id))
    if existing:
        raise HTTPException(409, "A commission record already exists for this booking")

    commission = OTACommission(**payload.model_dump())
    db.add(commission)
    db.flush()
    record_audit_log(db, user.id, "finance.ota_commission.create", entity_type="ota_commission", entity_id=str(commission.id), details=f"{payload.channel} {payload.commission_percent}% = Rs.{payload.commission_amount} for booking {booking.id}")
    db.commit()
    db.refresh(commission)
    return commission


@router.get("/ota-commissions", response_model=list[OTACommissionOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_commissions(
    channel: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(OTACommission)
    if channel:
        query = query.where(OTACommission.channel == channel)
    return db.scalars(query.offset(offset).limit(limit)).all()


@router.get("/ota-commissions/report", response_model=OTACommissionReport, dependencies=[Depends(require_role("admin", "staff"))])
def get_ota_commission_report(date_from: date | None = Query(None), date_to: date | None = Query(None), db: Session = Depends(get_db)):
    d_from, d_to = _default_range(date_from, date_to)
    return service.ota_commission_report(db, d_from, d_to)


@router.get("/outstanding-payments", response_model=list[OutstandingPaymentLine], dependencies=[Depends(require_role("admin", "staff"))])
def get_outstanding_payments(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    """
    Pending-balance filtering happens after computing each booking's
    balance (it's derived, not a raw column), so this can't be pushed down
    into the SQL WHERE clause without restructuring the underlying
    calculation — which risks changing the numbers themselves, and this
    pass is optimizing performance, not business logic. Slicing the
    already-computed result list is a safe, zero-risk way to still cap
    response payload size for a business with a very large number of
    currently-outstanding bookings.
    """
    all_lines = service.outstanding_payments(db)
    return all_lines[offset:offset + limit]


@router.patch("/bookings/{booking_id}/due-date", dependencies=[Depends(require_role("admin", "staff"))])
def set_booking_due_date(booking_id: uuid.UUID, due_date: date = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    booking.due_date = due_date
    record_audit_log(db, user.id, "finance.booking.due_date_set", entity_type="booking", entity_id=str(booking.id), details=f"Due date set to {due_date}")
    db.commit()
    return {"booking_id": booking.id, "due_date": booking.due_date}


@router.get("/cash-flow", response_model=CashFlowReport, dependencies=[Depends(require_role("admin", "staff"))])
def get_cash_flow(date_from: date | None = Query(None), date_to: date | None = Query(None), db: Session = Depends(get_db)):
    d_from, d_to = _default_range(date_from, date_to)
    return service.cash_flow(db, d_from, d_to)


@router.get("/gst-dashboard", response_model=GstDashboardReport, dependencies=[Depends(require_role("admin", "staff"))])
def get_gst_dashboard(date_from: date | None = Query(None), date_to: date | None = Query(None), db: Session = Depends(get_db)):
    d_from, d_to = _default_range(date_from, date_to)
    return service.gst_dashboard(db, d_from, d_to)


# --- Utility Readings (electricity, etc, per unit) ---

@router.post("/utility-readings", response_model=UtilityReadingOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def create_utility_reading(payload: UtilityReadingCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    split_options_given = sum([bool(payload.unit_id), bool(payload.shared_unit_ids), bool(payload.manual_allocations)])
    if split_options_given == 0:
        raise HTTPException(400, "One of unit_id (single apartment), shared_unit_ids (equal split), or manual_allocations (manual/percentage split) is required")
    if split_options_given > 1:
        raise HTTPException(400, "Provide only one of unit_id, shared_unit_ids, or manual_allocations")

    data = payload.model_dump(exclude={"shared_unit_ids", "manual_allocations"})
    reading = UtilityReading(**data)
    db.add(reading)
    db.flush()

    if payload.shared_unit_ids:
        share = round(reading.amount / len(payload.shared_unit_ids), 2)
        for uid in payload.shared_unit_ids:
            if db.get(Unit, uid) is None:
                db.rollback()
                raise HTTPException(404, f"Unit {uid} not found")
            db.add(UtilityReadingAllocation(utility_reading_id=reading.id, unit_id=uid, share_amount=share))

    elif payload.manual_allocations:
        allocated_total = round(sum(a.share_amount for a in payload.manual_allocations), 2)
        # Allow a small rounding tolerance (paise-level rounding when the
        # frontend converts percentages to amounts) rather than demanding
        # exact-to-the-paisa equality, which would reject entirely
        # reasonable splits like three-way 33.33/33.33/33.34%.
        if abs(allocated_total - reading.amount) > 1.0:
            db.rollback()
            raise HTTPException(400, f"Manual allocation amounts (Rs.{allocated_total}) must add up to the bill total (Rs.{reading.amount})")
        for alloc in payload.manual_allocations:
            if db.get(Unit, alloc.unit_id) is None:
                db.rollback()
                raise HTTPException(404, f"Unit {alloc.unit_id} not found")
            db.add(UtilityReadingAllocation(utility_reading_id=reading.id, unit_id=alloc.unit_id, share_amount=alloc.share_amount))

    split_description = (
        f" split equally across {len(payload.shared_unit_ids)} units" if payload.shared_unit_ids
        else f" manually allocated across {len(payload.manual_allocations)} units" if payload.manual_allocations
        else f" for unit {payload.unit_id}"
    )
    record_audit_log(
        db, user.id, "finance.utility_reading.create", entity_type="utility_reading", entity_id=str(reading.id),
        details=f"{payload.utility_type}: {reading.units_consumed} units = Rs.{reading.amount}" + split_description,
    )
    db.commit()
    db.refresh(reading)
    return _utility_reading_out(db, reading)


def _utility_reading_out(db: Session, reading: UtilityReading) -> dict:
    allocations = db.scalars(select(UtilityReadingAllocation).where(UtilityReadingAllocation.utility_reading_id == reading.id)).all()
    return {
        "id": reading.id, "unit_id": reading.unit_id, "utility_type": reading.utility_type, "meter_number": reading.meter_number,
        "reading_date": reading.reading_date, "opening_units": float(reading.opening_units), "closing_units": float(reading.closing_units),
        "rate_per_unit": float(reading.rate_per_unit), "notes": reading.notes, "created_at": reading.created_at,
        "units_consumed": reading.units_consumed, "amount": reading.amount,
        "allocations": [{"unit_id": a.unit_id, "unit_name": (db.get(Unit, a.unit_id).name if db.get(Unit, a.unit_id) else "—"), "share_amount": float(a.share_amount)} for a in allocations],
    }


@router.get("/utility-readings", response_model=list[UtilityReadingOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_utility_readings(unit_id: uuid.UUID | None = None, limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    query = select(UtilityReading).order_by(UtilityReading.reading_date.desc(), UtilityReading.id)
    if unit_id:
        # Include readings billed directly to this unit AND shared readings allocated to it
        direct_ids = {r.id for r in db.scalars(query.where(UtilityReading.unit_id == unit_id)).all()}
        shared_ids = {a.utility_reading_id for a in db.scalars(select(UtilityReadingAllocation).where(UtilityReadingAllocation.unit_id == unit_id)).all()}
        all_ids = direct_ids | shared_ids
        readings = db.scalars(select(UtilityReading).where(UtilityReading.id.in_(all_ids)).order_by(UtilityReading.reading_date.desc(), UtilityReading.id).offset(offset).limit(limit)).all()
    else:
        readings = db.scalars(query.offset(offset).limit(limit)).all()
    return [_utility_reading_out(db, r) for r in readings]


# --- Purchase Register ---

@router.post("/purchases", response_model=PurchaseRecordOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def create_purchase(payload: PurchaseRecordCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = payload.model_dump()
    if data["amount"] is None:
        data["amount"] = round(payload.quantity * payload.unit_price, 2)
    purchase = PurchaseRecord(**data, created_by=user.id)
    db.add(purchase)
    db.flush()
    record_audit_log(db, user.id, "finance.purchase.create", entity_type="purchase_record", entity_id=str(purchase.id), details=f"{payload.item_description}: Rs.{purchase.amount}")
    db.commit()
    db.refresh(purchase)
    return purchase


@router.get("/purchases", response_model=list[PurchaseRecordOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_purchases(
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(PurchaseRecord).order_by(PurchaseRecord.purchase_date.desc(), PurchaseRecord.id)
    if date_from:
        query = query.where(PurchaseRecord.purchase_date >= date_from)
    if date_to:
        query = query.where(PurchaseRecord.purchase_date <= date_to)
    query = query.offset(offset).limit(limit)
    return db.scalars(query).all()


# --- Daily Cashbook & Closing ---

@router.get("/cashbook", response_model=DailyCashbook, dependencies=[Depends(require_role("admin", "staff"))])
def get_daily_cashbook(cashbook_date: date | None = Query(None), db: Session = Depends(get_db)):
    return service.daily_cashbook(db, cashbook_date or date.today())


@router.post("/daily-closing", response_model=DailyClosingOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def create_daily_closing(payload: DailyClosingCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    existing = db.scalar(select(DailyClosing).where(DailyClosing.closing_date == payload.closing_date))
    if existing:
        raise HTTPException(409, f"{payload.closing_date} has already been closed — a day can only be closed once")

    cashbook = service.daily_cashbook(db, payload.closing_date)
    closing = DailyClosing(
        closing_date=payload.closing_date,
        opening_balance=payload.opening_balance,
        total_cash_in=cashbook["total_in"],
        total_cash_out=cashbook["total_out"],
        closing_balance=round(payload.opening_balance + cashbook["total_in"] - cashbook["total_out"], 2),
        notes=payload.notes,
        closed_by=user.id,
    )
    db.add(closing)
    db.flush()
    record_audit_log(db, user.id, "finance.daily_closing.create", entity_type="daily_closing", entity_id=str(closing.id), details=f"{payload.closing_date}: opening Rs.{payload.opening_balance}, closing Rs.{closing.closing_balance}")
    trigger_daily_closing_completed(db, closing)
    db.commit()
    db.refresh(closing)
    return closing


@router.get("/daily-closing", response_model=list[DailyClosingOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_daily_closings(date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db)):
    query = select(DailyClosing).order_by(DailyClosing.closing_date.desc())
    if date_from:
        query = query.where(DailyClosing.closing_date >= date_from)
    if date_to:
        query = query.where(DailyClosing.closing_date <= date_to)
    return db.scalars(query).all()


# --- Apartment-wise P&L ---

@router.get("/apartment-pnl/{unit_id}", response_model=ApartmentPnL, dependencies=[Depends(require_role("admin", "staff"))])
def get_apartment_pnl(unit_id: uuid.UUID, period_year: int = Query(...), period_month: int = Query(..., ge=1, le=12), db: Session = Depends(get_db)):
    try:
        return service.apartment_pnl(db, unit_id, period_year, period_month)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/property-pnl/{property_id}", response_model=PropertyPnL, dependencies=[Depends(require_role("admin", "staff"))])
def get_property_pnl(property_id: uuid.UUID, period_year: int = Query(...), period_month: int = Query(..., ge=1, le=12), db: Session = Depends(get_db)):
    try:
        return service.property_pnl(db, property_id, period_year, period_month)
    except ValueError as e:
        raise HTTPException(404, str(e))


# --- Advances ---

@router.get("/advances", response_model=list[AdvanceSummaryLine], dependencies=[Depends(require_role("admin", "staff"))])
def get_advance_summary(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    all_lines = service.advance_summary(db)
    return all_lines[offset:offset + limit]


# --- Report exports (PDF/Excel) — Cash Book, Apartment P&L, Expense, Purchase, Utility, Salary ---

VALID_FINANCE_REPORTS = ("cashbook", "apartment_pnl", "property_pnl", "expenses", "purchases", "utility", "salary", "income_register")
VALID_EXPORT_FORMATS = ("pdf", "xlsx")


@router.get("/reports/export", dependencies=[Depends(require_role("admin", "staff"))])
def export_finance_report(
    report: str = Query(..., description="cashbook | apartment_pnl | property_pnl | expenses | purchases | utility | salary | income_register"),
    format: str = Query(..., description="pdf | xlsx"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    unit_id: uuid.UUID | None = Query(None),
    property_id: uuid.UUID | None = Query(None),
    period_year: int | None = Query(None),
    period_month: int | None = Query(None),
    db: Session = Depends(get_db),
):
    if report not in VALID_FINANCE_REPORTS:
        raise HTTPException(400, f"Invalid report '{report}'. Must be one of {VALID_FINANCE_REPORTS}")
    if format not in VALID_EXPORT_FORMATS:
        raise HTTPException(400, f"Invalid format '{format}'. Must be one of {VALID_EXPORT_FORMATS}")

    d_from, d_to = _default_range(date_from, date_to)

    if report == "cashbook":
        cb_date = date_to or date.today()
        data = service.daily_cashbook(db, cb_date)
        title = "Daily Cash Book"
        subtitle = f"{cb_date} — Opening: Rs.{data['opening_balance']} | Closing: Rs.{data['closing_balance']}"
        headers = ["Time", "Description", "Type", "Amount", "Running Balance"]
        rows = [[l["time"].strftime("%H:%M:%S"), l["description"], l["transaction_type"], l["amount"], l["running_balance"]] for l in data["lines"]]

    elif report == "apartment_pnl":
        if unit_id is None or period_year is None or period_month is None:
            raise HTTPException(400, "apartment_pnl report requires unit_id, period_year, and period_month")
        try:
            data = service.apartment_pnl(db, unit_id, period_year, period_month)
        except ValueError as e:
            raise HTTPException(404, str(e))
        title = f"Apartment P&L — {data['unit_name']}"
        subtitle = f"{period_month}/{period_year} — Revenue: Rs.{data['revenue']} | Net Profit: Rs.{data['net_profit']}"
        headers = ["Expense Category", "Amount"]
        rows = [[l["category"], l["amount"]] for l in data["expense_lines"]]
        rows.append(["Advance Received", data["advance_received"]])
        rows.append(["Outstanding Balance", data["outstanding_balance"]])

    elif report == "property_pnl":
        if property_id is None or period_year is None or period_month is None:
            raise HTTPException(400, "property_pnl report requires property_id, period_year, and period_month")
        try:
            data = service.property_pnl(db, property_id, period_year, period_month)
        except ValueError as e:
            raise HTTPException(404, str(e))
        title = f"Property P&L — {data['property_name']}"
        subtitle = f"{period_month}/{period_year} — Total Revenue: Rs.{data['total_revenue']} | Net Profit: Rs.{data['net_profit']}"
        headers = ["Unit", "Revenue", "Expenses", "Net Profit"]
        rows = [[u["unit_name"], u["revenue"], u["total_expenses"], u["net_profit"]] for u in data["units"]]
        rows.append(["TOTAL", data["total_revenue"], data["total_expenses"], data["net_profit"]])

    elif report == "expenses":
        expenses = db.scalars(select(Expense).where(Expense.expense_date >= d_from, Expense.expense_date <= d_to).order_by(Expense.expense_date)).all()
        title = "Expense Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Date", "Category", "Amount", "Unit", "Approval", "Notes"]
        unit_ids = {e.unit_id for e in expenses if e.unit_id}
        units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()} if unit_ids else {}
        rows = []
        for e in expenses:
            unit = units_by_id.get(e.unit_id) if e.unit_id else None
            rows.append([e.expense_date, e.category, float(e.amount), unit.name if unit else "Central", e.approval_status, e.notes or ""])

    elif report == "purchases":
        purchases = db.scalars(select(PurchaseRecord).where(PurchaseRecord.purchase_date >= d_from, PurchaseRecord.purchase_date <= d_to).order_by(PurchaseRecord.purchase_date)).all()
        title = "Purchase Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Date", "Item", "Category", "Qty", "Unit Price", "Amount", "Vendor", "Invoice #"]
        rows = [[p.purchase_date, p.item_description, p.category or "", p.quantity or "", p.unit_price or "", float(p.amount), p.supplier_name or "", p.invoice_number or ""] for p in purchases]

    elif report == "utility":
        readings = db.scalars(select(UtilityReading).where(UtilityReading.reading_date >= d_from, UtilityReading.reading_date <= d_to).order_by(UtilityReading.reading_date)).all()
        title = "Utility Bill Report"
        subtitle = f"{d_from} to {d_to}"
        headers = ["Date", "Unit", "Type", "Meter #", "Opening", "Closing", "Consumed", "Amount"]
        unit_ids = {r.unit_id for r in readings if r.unit_id}
        units_by_id = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()} if unit_ids else {}
        rows = []
        for r in readings:
            unit = units_by_id.get(r.unit_id) if r.unit_id else None
            rows.append([r.reading_date, unit.name if unit else "Shared", r.utility_type, r.meter_number or "", float(r.opening_units), float(r.closing_units), r.units_consumed, r.amount])

    elif report == "salary":
        from app.modules.staff.models import Payroll
        payrolls = db.scalars(select(Payroll).where(Payroll.period_year == (period_year or date.today().year), Payroll.period_month == (period_month or date.today().month))).all()
        title = "Salary Report"
        subtitle = f"{period_month or date.today().month}/{period_year or date.today().year}"
        headers = ["Staff", "Basic Salary", "Deductions", "Bonus", "Net Pay", "Status"]
        staff_ids = {p.user_id for p in payrolls}
        staff_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(staff_ids))).all()} if staff_ids else {}
        rows = []
        for p in payrolls:
            staff = staff_by_id.get(p.user_id)
            rows.append([staff.name if staff else "—", float(p.basic_salary), float(p.deductions), float(p.bonus), float(p.net_pay), p.status])

    elif report == "income_register":
        if period_year is None or period_month is None:
            raise HTTPException(400, "income_register report requires period_year and period_month")
        data = service.income_register(db, period_year, period_month)
        title = "Income Register"
        subtitle = f"{period_month}/{period_year} — Grand Total: Rs.{data['grand_total']}"
        headers = ["Apartment", "Date", "Guest", "Contact", "Amount", "Advance", "Balance", "Source"]
        rows = []
        for unit_block in data["units"]:
            for line in unit_block["lines"]:
                rows.append([
                    unit_block["unit_name"], line["check_in"], line["guest_name"], line["contact"],
                    line["amount"], line["advance"], line["balance"], line["status"],
                ])
            if unit_block["lines"]:
                rows.append([f"{unit_block['unit_name']} TOTAL", "", "", "", unit_block["unit_total"], "", "", ""])

    filename_prefix = f"{report}_report"
    if format == "pdf":
        filepath = export_tabular_pdf(title, subtitle, headers, rows, filename_prefix)
        media_type = "application/pdf"
    else:
        filepath = export_tabular_xlsx(title, headers, rows, filename_prefix)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return FileResponse(filepath, media_type=media_type, filename=os.path.basename(filepath))


# --- Laundry Register ---

@router.post("/laundry", response_model=LaundryEntryOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
def create_laundry_entry(payload: LaundryEntryCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.modules.finance.models import LAUNDRY_ITEM_TYPES
    if payload.item_type not in LAUNDRY_ITEM_TYPES:
        raise HTTPException(400, f"Invalid item_type '{payload.item_type}'. Must be one of {LAUNDRY_ITEM_TYPES}")
    entry = LaundryEntry(**payload.model_dump(), created_by=user.id)
    db.add(entry)
    db.flush()
    record_audit_log(db, user.id, "finance.laundry.create", entity_type="laundry_entry", entity_id=str(entry.id), details=f"{payload.item_type} x{payload.quantity} = Rs.{entry.total_amount}")
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/laundry", response_model=list[LaundryEntryOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_laundry_entries(
    date_from: date | None = None,
    date_to: date | None = None,
    unit_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(LaundryEntry).order_by(LaundryEntry.entry_date.desc(), LaundryEntry.id)
    if date_from:
        query = query.where(LaundryEntry.entry_date >= date_from)
    if date_to:
        query = query.where(LaundryEntry.entry_date <= date_to)
    if unit_id:
        query = query.where(LaundryEntry.unit_id == unit_id)
    return db.scalars(query.offset(offset).limit(limit)).all()


@router.get("/laundry/summary", response_model=LaundryMonthlySummary, dependencies=[Depends(require_role("admin", "staff"))])
def get_laundry_summary(period_year: int = Query(...), period_month: int = Query(..., ge=1, le=12), db: Session = Depends(get_db)):
    return service.laundry_monthly_summary(db, period_year, period_month)


@router.delete("/laundry/{entry_id}", status_code=204, dependencies=[Depends(require_role("admin"))])
def delete_laundry_entry(entry_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    entry = db.get(LaundryEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Laundry entry not found")
    record_audit_log(db, user.id, "finance.laundry.delete", entity_type="laundry_entry", entity_id=str(entry_id), details=f"{entry.item_type} x{entry.quantity}")
    db.delete(entry)
    db.commit()


# --- Income Register ---

@router.get("/income-register", response_model=IncomeRegister, dependencies=[Depends(require_role("admin", "staff"))])
def get_income_register(period_year: int = Query(...), period_month: int = Query(..., ge=1, le=12), db: Session = Depends(get_db)):
    return service.income_register(db, period_year, period_month)
