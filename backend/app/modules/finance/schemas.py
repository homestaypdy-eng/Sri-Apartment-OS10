import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ExpenseCreate(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    amount: float = Field(gt=0)
    expense_date: date
    unit_id: uuid.UUID | None = None
    notes: str | None = None
    # Historical expenses continue to be recorded as approved by default;
    # new expenses can explicitly enter the existing approval workflow.
    approval_status: Literal["pending", "approved"] = "approved"

    @field_validator("category")
    @classmethod
    def category_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("category cannot be empty")
        return value


class ExpenseUpdate(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    amount: float = Field(gt=0)
    expense_date: date
    unit_id: uuid.UUID | None = None
    notes: str | None = None

    @field_validator("category")
    @classmethod
    def category_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("category cannot be empty")
        return value


class ExpenseOut(BaseModel):
    id: uuid.UUID
    category: str
    amount: float
    expense_date: date
    unit_id: uuid.UUID | None
    notes: str | None
    approval_status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    created_by: uuid.UUID | None = None
    paid_amount: float
    remaining_amount: float
    payment_status: Literal["unpaid", "partially_paid", "paid"]

    class Config:
        from_attributes = True


class ExpensePaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_date: date
    payment_method: Literal["cash", "bank_transfer", "upi", "card", "cheque"]
    reference_no: str | None = Field(default=None, max_length=100)


class ExpensePaymentOut(BaseModel):
    id: uuid.UUID
    expense_id: uuid.UUID
    amount: float
    payment_date: date
    payment_method: Literal["cash", "bank_transfer", "upi", "card", "cheque"]
    reference_no: str | None
    created_by: uuid.UUID | None
    created_by_name: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ExpenseApprovalAction(BaseModel):
    approve: bool
    notes: str | None = None


class BulkExpenseApprovalAction(BaseModel):
    expense_ids: list[uuid.UUID]
    approve: bool
    notes: str | None = None


class BulkApprovalResult(BaseModel):
    approved_count: int
    rejected_count: int
    not_found_ids: list[uuid.UUID]


class ExpenseAttachmentOut(BaseModel):
    id: uuid.UUID
    expense_id: uuid.UUID
    filename: str
    content_type: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class UtilityAllocationInput(BaseModel):
    unit_id: uuid.UUID
    share_amount: float = Field(gt=0)


class UtilityReadingCreate(BaseModel):
    unit_id: uuid.UUID | None = None  # null = shared bill; use shared_unit_ids OR manual_allocations to split it
    utility_type: str = "electricity"
    meter_number: str | None = None
    reading_date: date
    opening_units: float = Field(ge=0)
    closing_units: float = Field(ge=0)
    rate_per_unit: float = Field(gt=0)
    notes: str | None = None
    shared_unit_ids: list[uuid.UUID] | None = None  # equal split across these units
    manual_allocations: list[UtilityAllocationInput] | None = None  # exact amount per unit — the frontend computes these for both "percentage" and "manual" modes; this is just "here are the final numbers"

    @field_validator("closing_units")
    @classmethod
    def closing_not_less_than_opening(cls, v, info):
        opening = info.data.get("opening_units")
        if opening is not None and v < opening:
            raise ValueError("closing_units cannot be less than opening_units")
        return v


class UtilityAllocationOut(BaseModel):
    unit_id: uuid.UUID
    unit_name: str
    share_amount: float


class UtilityReadingOut(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID | None
    utility_type: str
    meter_number: str | None
    reading_date: date
    opening_units: float
    closing_units: float
    rate_per_unit: float
    notes: str | None
    created_at: datetime
    units_consumed: float
    amount: float
    allocations: list[UtilityAllocationOut] = []

    class Config:
        from_attributes = True


class PurchaseRecordCreate(BaseModel):
    purchase_date: date
    item_description: str
    category: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    unit_price: float | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, gt=0)  # optional if quantity+unit_price given; computed then
    invoice_number: str | None = None
    supplier_name: str | None = None
    unit_id: uuid.UUID | None = None
    notes: str | None = None

    @field_validator("amount")
    @classmethod
    def amount_or_qty_and_price_required(cls, v, info):
        qty = info.data.get("quantity")
        price = info.data.get("unit_price")
        if v is None and (qty is None or price is None):
            raise ValueError("Provide either amount, or both quantity and unit_price")
        return v


class PurchaseRecordOut(BaseModel):
    id: uuid.UUID
    purchase_date: date
    item_description: str
    category: str | None
    quantity: float | None
    unit_price: float | None
    amount: float
    invoice_number: str | None
    supplier_name: str | None
    unit_id: uuid.UUID | None
    notes: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class DailyClosingCreate(BaseModel):
    closing_date: date
    opening_balance: float = Field(ge=0)
    notes: str | None = None



class DailyClosingOut(BaseModel):
    id: uuid.UUID
    closing_date: date
    opening_balance: float
    total_cash_in: float
    total_cash_out: float
    closing_balance: float
    notes: str | None
    closed_by: uuid.UUID | None
    closed_at: datetime

    class Config:
        from_attributes = True


class CashbookLine(BaseModel):
    time: datetime
    description: str
    transaction_type: str  # "in" | "out"
    amount: float
    running_balance: float


class DailyCashbook(BaseModel):
    cashbook_date: date
    opening_balance: float
    lines: list[CashbookLine]
    closing_balance: float
    total_in: float
    total_out: float


class ApartmentPnLLine(BaseModel):
    category: str
    amount: float


class ApartmentPnL(BaseModel):
    unit_id: uuid.UUID
    unit_name: str
    period_year: int
    period_month: int
    revenue: float
    advance_received: float
    outstanding_balance: float
    expense_lines: list[ApartmentPnLLine]
    total_expenses: float
    net_profit: float


class AdvanceSummaryLine(BaseModel):
    booking_id: uuid.UUID
    guest_name: str
    unit_name: str
    check_in: date
    check_out: date
    advance_paid: float
    total_due: float
    balance_remaining: float


class BudgetCreate(BaseModel):
    category: str
    period_year: int
    period_month: int = Field(ge=1, le=12)
    budgeted_amount: float = Field(gt=0)


class BudgetOut(BudgetCreate):
    id: uuid.UUID

    class Config:
        from_attributes = True


class BudgetVsActualLine(BaseModel):
    category: str
    budgeted_amount: float
    actual_amount: float
    variance: float
    variance_percent: float | None


class BudgetVsActualReport(BaseModel):
    period_year: int
    period_month: int
    lines: list[BudgetVsActualLine]
    total_budgeted: float
    total_actual: float


class SecurityDepositCreate(BaseModel):
    booking_id: uuid.UUID
    amount: float = Field(gt=0)
    notes: str | None = None


class SecurityDepositRefund(BaseModel):
    refund_amount: float = Field(gt=0)
    notes: str | None = None


class SecurityDepositOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    amount: float
    status: str
    received_at: datetime
    refund_amount: float
    refunded_at: datetime | None
    notes: str | None
    pending_refund_amount: float

    class Config:
        from_attributes = True


class OwnerWithdrawalCreate(BaseModel):
    amount: float = Field(gt=0)
    withdrawal_date: date
    notes: str | None = None


class OwnerWithdrawalOut(OwnerWithdrawalCreate):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class OTACommissionCreate(BaseModel):
    booking_id: uuid.UUID
    channel: str
    commission_percent: float = Field(ge=0, le=100, default=0)
    commission_amount: float = Field(ge=0, default=0)
    ota_gst_amount: float = Field(ge=0, default=0)


class OTACommissionOut(OTACommissionCreate):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class OTACommissionChannelSummary(BaseModel):
    channel: str
    booking_count: int
    total_booking_revenue: float
    total_commission: float
    total_ota_gst: float


class OTACommissionReport(BaseModel):
    date_from: date
    date_to: date
    by_channel: list[OTACommissionChannelSummary]


class OTAPayoutReceiptRecord(BaseModel):
    actual_received_amount: float = Field(gt=0)
    receipt_date: date
    status: str  # matched / partial / discrepancy — staff-declared, compared against the computed expected payout
    reference_number: str | None = None
    notes: str | None = None
    expected_updated_at: datetime | None = None  # optimistic concurrency; only checked when a receipt already exists


class OTAPayoutReceiptOut(BaseModel):
    id: uuid.UUID
    ota_commission_id: uuid.UUID
    actual_received_amount: float
    receipt_date: date
    reference_number: str | None
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OTAReconciliationLine(BaseModel):
    ota_commission_id: uuid.UUID
    booking_id: uuid.UUID
    channel: str
    guest_name: str
    unit_name: str
    check_in: date
    check_out: date
    gross_amount: float
    commission_amount: float
    ota_gst_amount: float
    expected_payout: float
    actual_received_amount: float | None
    difference: float | None
    receipt_date: date | None
    reference_number: str | None
    reconciliation_status: str  # unreconciled / matched / partial / discrepancy
    receipt_id: uuid.UUID | None
    receipt_updated_at: datetime | None


class OTAMissingCommissionLine(BaseModel):
    booking_id: uuid.UUID
    guest_name: str
    unit_name: str
    check_in: date
    check_out: date
    source: str
    gross_amount: float


class OutstandingPaymentLine(BaseModel):
    booking_id: uuid.UUID
    guest_name: str
    unit_name: str
    check_in: date
    check_out: date
    expected_total: float
    paid_amount: float
    pending_balance: float
    due_date: date | None
    is_overdue: bool


class CashFlowReport(BaseModel):
    date_from: date
    date_to: date
    cash_in_payments: float
    cash_in_deposits: float
    cash_in_ota_payouts: float
    # Informational only, excluded from cash_in_total — see cash_flow()'s
    # docstring comment. A Zotel-recorded payment is the guest's payment
    # status per Zotel, not confirmed cash in the business's own bank/hand;
    # the actual cash event is cash_in_ota_payouts, once reconciled.
    cash_in_zotel_pending: float
    cash_in_total: float
    cash_out_expenses: float
    cash_out_withdrawals: float
    cash_out_deposit_refunds: float
    cash_out_loan_emi: float
    cash_out_total: float
    net_cash_flow: float
    cash_in_bank: float
    cash_in_hand: float


class GstMonthlySummaryLine(BaseModel):
    year: int
    month: int
    gst_collected: float
    invoice_count: int


class GstDashboardReport(BaseModel):
    date_from: date
    date_to: date
    gst_collected: float
    gst_input_credit_eligible: float
    gst_payable: float
    gst_liability: float
    monthly_summary: list[GstMonthlySummaryLine]


class LaundryEntryCreate(BaseModel):
    entry_date: date
    item_type: str
    quantity: int = Field(gt=0)
    rate_per_item: float = Field(gt=0)
    unit_id: uuid.UUID | None = None
    notes: str | None = None


class LaundryEntryOut(LaundryEntryCreate):
    id: uuid.UUID
    created_at: datetime
    total_amount: float

    class Config:
        from_attributes = True


class LaundrySummaryLine(BaseModel):
    item_type: str
    total_quantity: int
    total_amount: float


class LaundryMonthlySummary(BaseModel):
    period_year: int
    period_month: int
    lines: list[LaundrySummaryLine]
    grand_total: float


class IncomeRegisterLine(BaseModel):
    booking_id: uuid.UUID
    check_in: date
    guest_name: str
    no_of_guests: int
    contact: str
    amount: float
    advance: float
    balance: float
    status: str


class IncomeRegisterUnit(BaseModel):
    unit_id: uuid.UUID
    unit_name: str
    lines: list[IncomeRegisterLine]
    unit_total: float


class IncomeRegister(BaseModel):
    period_year: int
    period_month: int
    units: list[IncomeRegisterUnit]
    grand_total: float
    by_source: dict[str, float]


class LoanCreate(BaseModel):
    loan_name: str
    lender_name: str
    loan_type: str = "other"
    principal_amount: float | None = Field(default=None, gt=0)
    interest_rate: float | None = Field(default=None, ge=0, le=100)
    emi_amount: float = Field(gt=0)
    start_date: date
    end_date: date | None = None
    unit_id: uuid.UUID | None = None
    notes: str | None = None

    @field_validator("end_date")
    @classmethod
    def end_not_before_start(cls, v, info):
        start = info.data.get("start_date")
        if v is not None and start is not None and v < start:
            raise ValueError("end_date cannot be before start_date")
        return v


class LoanUpdate(BaseModel):
    loan_name: str | None = None
    lender_name: str | None = None
    loan_type: str | None = None
    principal_amount: float | None = Field(default=None, gt=0)
    interest_rate: float | None = Field(default=None, ge=0, le=100)
    emi_amount: float | None = Field(default=None, gt=0)
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None
    unit_id: uuid.UUID | None = None
    notes: str | None = None


class LoanOut(BaseModel):
    id: uuid.UUID
    loan_name: str
    lender_name: str
    loan_type: str
    principal_amount: float | None
    interest_rate: float | None
    emi_amount: float
    start_date: date
    end_date: date | None
    status: str
    unit_id: uuid.UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LoanEmiPaymentCreate(BaseModel):
    due_date: date
    emi_amount: float | None = Field(default=None, gt=0)  # defaults to the loan's current emi_amount if omitted
    notes: str | None = None


class LoanEmiMarkPaid(BaseModel):
    paid_date: date | None = None  # defaults to today
    paid_amount: float | None = Field(default=None, gt=0)  # defaults to the schedule row's emi_amount
    reference_number: str | None = None
    notes: str | None = None


class LoanEmiPaymentOut(BaseModel):
    id: uuid.UUID
    loan_id: uuid.UUID
    due_date: date
    emi_amount: float
    status: str
    paid_date: date | None
    paid_amount: float | None
    reference_number: str | None
    notes: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class LoanSummary(BaseModel):
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


class PropertyPnL(BaseModel):
    property_id: uuid.UUID
    property_name: str
    period_year: int
    period_month: int
    units: list[ApartmentPnL]
    total_revenue: float
    central_expenses: float
    total_expenses: float
    net_profit: float
