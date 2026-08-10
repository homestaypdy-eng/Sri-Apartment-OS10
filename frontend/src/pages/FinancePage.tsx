import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  extractErrorMessage,
  EXPENSE_CATEGORIES,
  LAUNDRY_ITEM_TYPES,
  OTA_CHANNELS,
  type ApartmentPnL,
  type BudgetVsActualReport,
  type CashFlowReport,
  type DailyCashbook,
  type DailyClosingOut,
  type ExpenseOut,
  type GstDashboardReport,
  type IncomeRegister,
  type LaundryEntryOut,
  type LaundryMonthlySummary,
  type OTACommissionReport,
  type OutstandingPaymentLine,
  type OwnerWithdrawalOut,
  type PurchaseRecordOut,
  type AdvanceSummaryLine,
  type SecurityDepositOut,
  type UnitOut,
  type UtilityReadingOut,
} from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState } from "../components/ui";
import { useAuth } from "../context/AuthContext";

type Tab = "gst" | "deposits" | "withdrawals" | "commissions" | "outstanding" | "cashflow" | "budget" | "pnl" | "utility" | "purchases" | "cashbook" | "closing" | "advances" | "expenses" | "laundry" | "income_register";

const todayStr = () => new Date().toISOString().slice(0, 10);
const monthStartStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};

export default function FinancePage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [tab, setTab] = useState<Tab>("gst");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const tabs: { key: Tab; label: string }[] = [
    { key: "income_register", label: "Income Register" },
    { key: "gst", label: "GST Dashboard" },
    { key: "pnl", label: "Apartment P&L" },
    { key: "cashbook", label: "Daily Cashbook" },
    { key: "closing", label: "Daily Closing" },
    { key: "advances", label: "Advances" },
    { key: "deposits", label: "Security Deposits" },
    { key: "withdrawals", label: "Owner Withdrawals" },
    { key: "commissions", label: "OTA Commission" },
    { key: "outstanding", label: "Outstanding Payments" },
    { key: "cashflow", label: "Cash Flow" },
    { key: "expenses", label: "Expenses" },
    { key: "budget", label: "Budget vs Actual" },
    { key: "utility", label: "Utility Bills" },
    { key: "purchases", label: "Purchase Register" },
    { key: "laundry", label: "Laundry Register" },
  ];

  return (
    <div>
      <PageHeader title="Finance" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <div className="flex gap-1 mb-6 border-b border-slate-200 overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px ${
              tab === t.key ? "border-slate-900 text-slate-900" : "border-transparent text-slate-400 hover:text-slate-600"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "income_register" && <IncomeRegisterTab setError={setError} />}
      {tab === "gst" && <GstDashboardTab setError={setError} />}
      {tab === "pnl" && <ApartmentPnLTab setError={setError} />}
      {tab === "cashbook" && <CashbookTab setError={setError} />}
      {tab === "closing" && <ClosingTab isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "advances" && <AdvancesTab setError={setError} />}
      {tab === "deposits" && <DepositsTab setError={setError} setSuccess={setSuccess} />}
      {tab === "withdrawals" && <WithdrawalsTab isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "commissions" && <CommissionsTab isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "outstanding" && <OutstandingTab setError={setError} setSuccess={setSuccess} />}
      {tab === "cashflow" && <CashFlowTab setError={setError} />}
      {tab === "expenses" && <ExpensesTab isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "budget" && <BudgetTab isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "utility" && <UtilityTab setError={setError} setSuccess={setSuccess} />}
      {tab === "purchases" && <PurchasesTab setError={setError} setSuccess={setSuccess} />}
      {tab === "laundry" && <LaundryTab isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
    </div>
  );
}

function DateRangeBar({ from, to, setFrom, setTo }: { from: string; to: string; setFrom: (v: string) => void; setTo: (v: string) => void }) {
  return (
    <div className="flex flex-wrap items-end gap-3 mb-4">
      <div>
        <label className="block text-xs text-slate-500 mb-1">From</label>
        <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
      </div>
      <div>
        <label className="block text-xs text-slate-500 mb-1">To</label>
        <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
      </div>
    </div>
  );
}

function StatBox({ label, value, tone }: { label: string; value: string; tone?: "green" | "red" }) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-xl font-semibold ${tone === "green" ? "text-emerald-600" : tone === "red" ? "text-red-600" : "text-slate-900"}`}>{value}</p>
    </div>
  );
}

// --- GST Dashboard ---
function GstDashboardTab({ setError }: { setError: (e: string | null) => void }) {
  const [from, setFrom] = useState(monthStartStr());
  const [to, setTo] = useState(todayStr());
  const [report, setReport] = useState<GstDashboardReport | null>(null);

  async function load() {
    try {
      const { data } = await api.get("/finance/gst-dashboard", { params: { date_from: from, date_to: to } });
      setReport(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to]);

  return (
    <div>
      <DateRangeBar from={from} to={to} setFrom={setFrom} setTo={setTo} />
      <Card className="mb-6 bg-amber-50 border-amber-200">
        <p className="text-xs text-amber-800">
          "GST Input Credit" here reflects GST collected on ITC-eligible sales (the &gt;₹7,500/night slab) — this
          system doesn't track GST paid on vendor/inventory purchases, so this is a starting point for your
          accountant, not a filing-ready input-tax-credit reconciliation.
        </p>
      </Card>
      {report && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <StatBox label="GST Collected" value={`₹${report.gst_collected.toLocaleString()}`} />
            <StatBox label="GST Input Credit (eligible)" value={`₹${report.gst_input_credit_eligible.toLocaleString()}`} tone="green" />
            <StatBox label="GST Payable" value={`₹${report.gst_payable.toLocaleString()}`} tone="red" />
          </div>
          <Card>
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Monthly GST Summary</h2>
            <div className="overflow-x-auto">
              <Table headers={["Month", "GST Collected", "Invoices"]}>
                {report.monthly_summary.map((m) => (
                  <tr key={`${m.year}-${m.month}`}>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{m.month}/{m.year}</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{m.gst_collected.toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-slate-500">{m.invoice_count}</td>
                  </tr>
                ))}
              </Table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

// --- Security Deposits ---
function DepositsTab({ setError, setSuccess }: { setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [deposits, setDeposits] = useState<SecurityDepositOut[]>([]);
  const [form, setForm] = useState({ booking_id: "", amount: "", notes: "" });
  const [refundDrafts, setRefundDrafts] = useState<Record<string, string>>({});

  async function load() {
    try {
      const { data } = await api.get("/finance/deposits");
      setDeposits(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/deposits", { booking_id: form.booking_id, amount: Number(form.amount), notes: form.notes || undefined });
      setSuccess("Deposit recorded.");
      setForm({ booking_id: "", amount: "", notes: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleRefund(id: string) {
    setError(null);
    setSuccess(null);
    const amount = Number(refundDrafts[id]);
    if (!amount) {
      setError("Enter a refund amount first.");
      return;
    }
    try {
      await api.post(`/finance/deposits/${id}/refund`, { refund_amount: amount });
      setSuccess("Refund recorded.");
      setRefundDrafts({ ...refundDrafts, [id]: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <Card className="mb-6 max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Record Deposit Received</h2>
        <p className="text-xs text-slate-400 mb-4">Deposits are never counted as revenue — they're held on the guest's behalf.</p>
        <form onSubmit={handleCreate} className="space-y-3">
          <input required placeholder="Booking ID" value={form.booking_id} onChange={(e) => setForm({ ...form, booking_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input required type="number" placeholder="Amount" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input placeholder="Notes (optional)" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <Button type="submit">Record Deposit</Button>
        </form>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Deposit Ledger</h2>
        <div className="overflow-x-auto">
          <Table headers={["Booking", "Amount", "Status", "Pending Refund", "Action"]}>
            {deposits.map((d) => (
              <tr key={d.id}>
                <td className="px-4 py-2.5 font-mono text-xs text-slate-500">{d.booking_id.slice(0, 8)}...</td>
                <td className="px-4 py-2.5 text-slate-500">₹{d.amount.toLocaleString()}</td>
                <td className="px-4 py-2.5">
                  <Badge tone={d.status === "refunded" ? "green" : d.status === "partially_refunded" ? "amber" : "slate"}>{d.status.replace("_", " ")}</Badge>
                </td>
                <td className="px-4 py-2.5 text-slate-500">₹{d.pending_refund_amount.toLocaleString()}</td>
                <td className="px-4 py-2.5">
                  {d.pending_refund_amount > 0 && (
                    <div className="flex gap-2">
                      <input
                        type="number"
                        placeholder="Amount"
                        value={refundDrafts[d.id] || ""}
                        onChange={(e) => setRefundDrafts({ ...refundDrafts, [d.id]: e.target.value })}
                        className="w-24 rounded-lg border border-slate-300 px-2 py-1 text-xs"
                      />
                      <Button variant="secondary" onClick={() => handleRefund(d.id)}>Refund</Button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

// --- Owner Withdrawals ---
function WithdrawalsTab({ isAdmin, setError, setSuccess }: { isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [withdrawals, setWithdrawals] = useState<OwnerWithdrawalOut[]>([]);
  const [form, setForm] = useState({ amount: "", withdrawal_date: todayStr(), notes: "" });

  async function load() {
    try {
      const { data } = await api.get("/finance/owner-withdrawals");
      setWithdrawals(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    if (isAdmin) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/owner-withdrawals", { amount: Number(form.amount), withdrawal_date: form.withdrawal_date, notes: form.notes || undefined });
      setSuccess("Withdrawal recorded.");
      setForm({ amount: "", withdrawal_date: todayStr(), notes: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  if (!isAdmin) {
    return <Card><p className="text-sm text-slate-400">Owner Withdrawals are admin-only.</p></Card>;
  }

  return (
    <div>
      <Card className="mb-6 max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Record Owner Withdrawal</h2>
        <p className="text-xs text-slate-400 mb-4">Tracked independently — never deducted from profit calculations, shown only as a cash movement.</p>
        <form onSubmit={handleCreate} className="space-y-3">
          <input required type="number" placeholder="Amount" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input required type="date" value={form.withdrawal_date} onChange={(e) => setForm({ ...form, withdrawal_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input placeholder="Notes (optional)" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <Button type="submit">Record Withdrawal</Button>
        </form>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Withdrawal Ledger</h2>
        <div className="overflow-x-auto">
          <Table headers={["Date", "Amount", "Notes"]}>
            {withdrawals.map((w) => (
              <tr key={w.id}>
                <td className="px-4 py-2.5 text-slate-500">{w.withdrawal_date}</td>
                <td className="px-4 py-2.5 font-medium text-slate-800">₹{w.amount.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">{w.notes ?? "—"}</td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

// --- OTA Commission ---
function CommissionsTab({ isAdmin, setError, setSuccess }: { isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [report, setReport] = useState<OTACommissionReport | null>(null);
  const [from, setFrom] = useState(monthStartStr());
  const [to, setTo] = useState(todayStr());
  const [form, setForm] = useState({ booking_id: "", channel: "booking_com", commission_percent: "", commission_amount: "", ota_gst_amount: "" });

  async function load() {
    try {
      const { data } = await api.get("/finance/ota-commissions/report", { params: { date_from: from, date_to: to } });
      setReport(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/ota-commissions", {
        booking_id: form.booking_id,
        channel: form.channel,
        commission_percent: Number(form.commission_percent || 0),
        commission_amount: Number(form.commission_amount || 0),
        ota_gst_amount: Number(form.ota_gst_amount || 0),
      });
      setSuccess("Commission recorded.");
      setForm({ booking_id: "", channel: "booking_com", commission_percent: "", commission_amount: "", ota_gst_amount: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      {isAdmin && (
        <Card className="mb-6 max-w-xl">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Record Commission for a Booking</h2>
          <form onSubmit={handleCreate} className="space-y-3">
            <input required placeholder="Booking ID" value={form.booking_id} onChange={(e) => setForm({ ...form, booking_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {OTA_CHANNELS.map((c) => <option key={c} value={c}>{c.replace("_", ".")}</option>)}
            </select>
            <div className="grid grid-cols-3 gap-3">
              <input type="number" placeholder="Commission %" value={form.commission_percent} onChange={(e) => setForm({ ...form, commission_percent: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              <input type="number" placeholder="Commission ₹" value={form.commission_amount} onChange={(e) => setForm({ ...form, commission_amount: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              <input type="number" placeholder="OTA GST ₹" value={form.ota_gst_amount} onChange={(e) => setForm({ ...form, ota_gst_amount: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <Button type="submit">Record Commission</Button>
          </form>
        </Card>
      )}

      <DateRangeBar from={from} to={to} setFrom={setFrom} setTo={setTo} />
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Commission by Channel</h2>
        <div className="overflow-x-auto">
          <Table headers={["Channel", "Bookings", "Revenue", "Commission", "OTA GST"]}>
            {report?.by_channel.map((c) => (
              <tr key={c.channel}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{c.channel.replace("_", ".")}</td>
                <td className="px-4 py-2.5 text-slate-500">{c.booking_count}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{c.total_booking_revenue.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{c.total_commission.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{c.total_ota_gst.toLocaleString()}</td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

// --- Outstanding Payments ---
function OutstandingTab({ setError, setSuccess }: { setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [lines, setLines] = useState<OutstandingPaymentLine[]>([]);
  const [dueDrafts, setDueDrafts] = useState<Record<string, string>>({});

  async function load() {
    try {
      const { data } = await api.get("/finance/outstanding-payments");
      setLines(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSetDueDate(bookingId: string) {
    setError(null);
    setSuccess(null);
    const due = dueDrafts[bookingId];
    if (!due) return;
    try {
      await api.patch(`/finance/bookings/${bookingId}/due-date`, null, { params: { due_date: due } });
      setSuccess("Due date set.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-900 mb-4">Outstanding Payments</h2>
      <div className="overflow-x-auto">
        <Table headers={["Guest", "Unit", "Stay", "Pending Balance", "Due Date", "Overdue", "Reminder"]}>
          {lines.map((l) => (
            <tr key={l.booking_id}>
              <td className="px-4 py-2.5 font-medium text-slate-800">{l.guest_name}</td>
              <td className="px-4 py-2.5 text-slate-500">{l.unit_name}</td>
              <td className="px-4 py-2.5 text-slate-500">{l.check_in} → {l.check_out}</td>
              <td className="px-4 py-2.5 text-slate-500">₹{l.pending_balance.toLocaleString()}</td>
              <td className="px-4 py-2.5">
                {l.due_date ?? (
                  <div className="flex gap-1">
                    <input type="date" value={dueDrafts[l.booking_id] || ""} onChange={(e) => setDueDrafts({ ...dueDrafts, [l.booking_id]: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1 text-xs" />
                    <Button variant="secondary" onClick={() => handleSetDueDate(l.booking_id)}>Set</Button>
                  </div>
                )}
              </td>
              <td className="px-4 py-2.5">{l.is_overdue && <Badge tone="red">overdue</Badge>}</td>
              <td className="px-4 py-2.5 text-xs text-slate-400">See Notifications to send a payment reminder</td>
            </tr>
          ))}
        </Table>
      </div>
    </Card>
  );
}

// --- Cash Flow ---
function CashFlowTab({ setError }: { setError: (e: string | null) => void }) {
  const [from, setFrom] = useState(monthStartStr());
  const [to, setTo] = useState(todayStr());
  const [report, setReport] = useState<CashFlowReport | null>(null);

  async function load() {
    try {
      const { data } = await api.get("/finance/cash-flow", { params: { date_from: from, date_to: to } });
      setReport(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to]);

  return (
    <div>
      <DateRangeBar from={from} to={to} setFrom={setFrom} setTo={setTo} />
      {report && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <StatBox label="Cash In" value={`₹${report.cash_in_total.toLocaleString()}`} tone="green" />
            <StatBox label="Cash Out" value={`₹${report.cash_out_total.toLocaleString()}`} tone="red" />
            <StatBox label="Cash in Bank" value={`₹${report.cash_in_bank.toLocaleString()}`} />
            <StatBox label="Cash in Hand" value={`₹${report.cash_in_hand.toLocaleString()}`} />
          </div>
          <Card>
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Breakdown</h2>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-slate-500">Payments received</span><span>₹{report.cash_in_payments.toLocaleString()}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">Deposits received</span><span>₹{report.cash_in_deposits.toLocaleString()}</span></div>
              <div className="flex justify-between border-t border-slate-100 pt-2"><span className="text-slate-500">Expenses paid</span><span>₹{report.cash_out_expenses.toLocaleString()}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">Owner withdrawals</span><span>₹{report.cash_out_withdrawals.toLocaleString()}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">Deposit refunds</span><span>₹{report.cash_out_deposit_refunds.toLocaleString()}</span></div>
              <div className="flex justify-between font-semibold pt-2 border-t border-slate-200"><span>Net cash flow</span><span className={report.net_cash_flow >= 0 ? "text-emerald-600" : "text-red-600"}>₹{report.net_cash_flow.toLocaleString()}</span></div>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

// --- Budget vs Actual ---
function BudgetTab({ isAdmin, setError, setSuccess }: { isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [report, setReport] = useState<BudgetVsActualReport | null>(null);
  const [budgetForm, setBudgetForm] = useState({ category: "electricity", budgeted_amount: "" });

  async function load() {
    try {
      const { data } = await api.get("/finance/budget-vs-actual", { params: { period_year: year, period_month: month } });
      setReport(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month]);

  async function handleSetBudget(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/budgets", { category: budgetForm.category, period_year: year, period_month: month, budgeted_amount: Number(budgetForm.budgeted_amount) });
      setSuccess("Budget set.");
      setBudgetForm({ ...budgetForm, budgeted_amount: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <div className="flex gap-3 mb-6">
        <input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28 rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
        <select value={month} onChange={(e) => setMonth(Number(e.target.value))} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm">
          {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      {isAdmin && (
        <Card className="mb-6 max-w-lg">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Set Budget ({month}/{year})</h2>
          <p className="text-xs text-slate-400 mb-4">Actual expenses are recorded from the Expenses tab — this only sets the target amount to compare against.</p>
          <form onSubmit={handleSetBudget} className="space-y-3">
            <select value={budgetForm.category} onChange={(e) => setBudgetForm({ ...budgetForm, category: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {EXPENSE_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <input required type="number" placeholder="Budgeted amount" value={budgetForm.budgeted_amount} onChange={(e) => setBudgetForm({ ...budgetForm, budgeted_amount: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <Button type="submit">Set Budget</Button>
          </form>
        </Card>
      )}

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Budget vs Actual</h2>
        <div className="overflow-x-auto">
          <Table headers={["Category", "Budgeted", "Actual", "Variance", "Variance %"]}>
            {report?.lines.map((l) => (
              <tr key={l.category}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{l.category}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{l.budgeted_amount.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{l.actual_amount.toLocaleString()}</td>
                <td className={`px-4 py-2.5 ${l.variance < 0 ? "text-red-600" : "text-emerald-600"}`}>₹{l.variance.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">{l.variance_percent !== null ? `${l.variance_percent}%` : "—"}</td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

// --- Apartment P&L ---
function ApartmentPnLTab({ setError }: { setError: (e: string | null) => void }) {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [unitId, setUnitId] = useState("");
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [pnl, setPnl] = useState<ApartmentPnL | null>(null);

  useEffect(() => {
    api.get("/units").then((r) => setUnits(r.data)).catch((err) => setError(extractErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!unitId) {
      setPnl(null);
      return;
    }
    api.get(`/finance/apartment-pnl/${unitId}`, { params: { period_year: year, period_month: month } })
      .then((r) => setPnl(r.data))
      .catch((err) => setError(extractErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unitId, year, month]);

  return (
    <div>
      <div className="flex flex-wrap items-end gap-3 mb-6">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Unit</label>
          <select value={unitId} onChange={(e) => setUnitId(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm min-w-[200px]">
            <option value="">Select a unit...</option>
            {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">Year</label>
          <input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-24 rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">Month</label>
          <select value={month} onChange={(e) => setMonth(Number(e.target.value))} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm">
            {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </div>

      {pnl && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-6">
            <StatBox label="Revenue (room rate only)" value={`₹${pnl.revenue.toLocaleString()}`} tone="green" />
            <StatBox label="Advance Received" value={`₹${pnl.advance_received.toLocaleString()}`} />
            <StatBox label="Outstanding" value={`₹${pnl.outstanding_balance.toLocaleString()}`} tone={pnl.outstanding_balance > 0 ? "red" : undefined} />
            <StatBox label="Total Expenses (this unit)" value={`₹${pnl.total_expenses.toLocaleString()}`} tone="red" />
            <StatBox label="Net Profit (this unit)" value={`₹${pnl.net_profit.toLocaleString()}`} tone={pnl.net_profit >= 0 ? "green" : "red"} />
          </div>
          <Card>
            <h2 className="text-sm font-semibold text-slate-900 mb-1 flex items-center justify-between">
              <span>{pnl.unit_name} — {pnl.period_month}/{pnl.period_year}</span>
              <ExportButtons report="apartment_pnl" params={{ unit_id: unitId, period_year: year, period_month: month }} />
            </h2>
            <p className="text-xs text-slate-400 mb-4">
              Only expenses explicitly tagged to this unit are included — central/shared expenses (staff salaries, GST, etc.) belong to the business-wide books, not any one apartment's.
            </p>
            <div className="overflow-x-auto">
              <Table headers={["Expense Category", "Amount"]}>
                {pnl.expense_lines.length === 0 ? (
                  <tr><td className="px-4 py-2.5 text-slate-400" colSpan={2}>No unit-specific expenses recorded for this period.</td></tr>
                ) : (
                  pnl.expense_lines.map((l) => (
                    <tr key={l.category}>
                      <td className="px-4 py-2.5 font-medium text-slate-800">{l.category}</td>
                      <td className="px-4 py-2.5 text-slate-500">₹{l.amount.toLocaleString()}</td>
                    </tr>
                  ))
                )}
              </Table>
            </div>
          </Card>
        </>
      )}
      {!pnl && unitId && <p className="text-sm text-slate-400">Loading...</p>}
      {!unitId && <p className="text-sm text-slate-400">Select a unit to see its monthly P&L.</p>}
    </div>
  );
}

// --- Daily Cashbook ---
function CashbookTab({ setError }: { setError: (e: string | null) => void }) {
  const [cashbookDate, setCashbookDate] = useState(todayStr());
  const [cashbook, setCashbook] = useState<DailyCashbook | null>(null);

  useEffect(() => {
    api.get("/finance/cashbook", { params: { cashbook_date: cashbookDate } })
      .then((r) => setCashbook(r.data))
      .catch((err) => setError(extractErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cashbookDate]);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Date</label>
          <input type="date" value={cashbookDate} onChange={(e) => setCashbookDate(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
        </div>
        <ExportButtons report="cashbook" params={{ date_to: cashbookDate }} />
      </div>

      {cashbook && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
            <StatBox label="Opening Balance" value={`₹${cashbook.opening_balance.toLocaleString()}`} />
            <StatBox label="Total In" value={`₹${cashbook.total_in.toLocaleString()}`} tone="green" />
            <StatBox label="Total Out" value={`₹${cashbook.total_out.toLocaleString()}`} tone="red" />
            <StatBox label="Closing Balance" value={`₹${cashbook.closing_balance.toLocaleString()}`} />
          </div>
          <Card>
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Transactions</h2>
            <div className="overflow-x-auto">
              <Table headers={["Time", "Description", "Type", "Amount", "Running Balance"]}>
                {cashbook.lines.length === 0 ? (
                  <tr><td className="px-4 py-2.5 text-slate-400" colSpan={5}>No transactions on this date.</td></tr>
                ) : (
                  cashbook.lines.map((l, i) => (
                    <tr key={i}>
                      <td className="px-4 py-2.5 text-slate-500">{new Date(l.time).toLocaleTimeString()}</td>
                      <td className="px-4 py-2.5 text-slate-700">{l.description}</td>
                      <td className="px-4 py-2.5"><Badge tone={l.transaction_type === "in" ? "green" : "red"}>{l.transaction_type}</Badge></td>
                      <td className="px-4 py-2.5 text-slate-500">₹{l.amount.toLocaleString()}</td>
                      <td className="px-4 py-2.5 font-medium text-slate-800">₹{l.running_balance.toLocaleString()}</td>
                    </tr>
                  ))
                )}
              </Table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

// --- Daily Closing ---
function ClosingTab({ isAdmin, setError, setSuccess }: { isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [closings, setClosings] = useState<DailyClosingOut[]>([]);
  const [form, setForm] = useState({ closing_date: todayStr(), opening_balance: "", notes: "" });

  async function load() {
    try {
      const { data } = await api.get("/finance/daily-closing");
      setClosings(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleClose(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/daily-closing", { closing_date: form.closing_date, opening_balance: Number(form.opening_balance), notes: form.notes || undefined });
      setSuccess(`${form.closing_date} closed.`);
      setForm({ closing_date: todayStr(), opening_balance: "", notes: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      {isAdmin && (
        <Card className="mb-6 max-w-lg">
          <h2 className="text-sm font-semibold text-slate-900 mb-1">Close a Day's Books</h2>
          <p className="text-xs text-slate-400 mb-4">A day can only be closed once — this locks in that day's cash position.</p>
          <form onSubmit={handleClose} className="space-y-3">
            <input required type="date" value={form.closing_date} onChange={(e) => setForm({ ...form, closing_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required type="number" placeholder="Opening balance (₹)" value={form.opening_balance} onChange={(e) => setForm({ ...form, opening_balance: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input placeholder="Notes (optional)" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <Button type="submit">Close Day</Button>
          </form>
        </Card>
      )}
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Closing History</h2>
        <div className="overflow-x-auto">
          <Table headers={["Date", "Opening", "Cash In", "Cash Out", "Closing", "Notes"]}>
            {closings.map((c) => (
              <tr key={c.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{c.closing_date}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{c.opening_balance.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-emerald-600">₹{c.total_cash_in.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-red-600">₹{c.total_cash_out.toLocaleString()}</td>
                <td className="px-4 py-2.5 font-medium text-slate-800">₹{c.closing_balance.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-400">{c.notes ?? "—"}</td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

// --- Advances ---
function AdvancesTab({ setError }: { setError: (e: string | null) => void }) {
  const [advances, setAdvances] = useState<AdvanceSummaryLine[]>([]);

  useEffect(() => {
    api.get("/finance/advances").then((r) => setAdvances(r.data)).catch((err) => setError(extractErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-900 mb-1">Advance Tracking</h2>
      <p className="text-xs text-slate-400 mb-4">Bookings with an advance paid but a balance still remaining, sourced from actual payment records.</p>
      <div className="overflow-x-auto">
        <Table headers={["Guest", "Unit", "Stay", "Advance Paid", "Total Due", "Balance Remaining"]}>
          {advances.length === 0 ? (
            <tr><td className="px-4 py-2.5 text-slate-400" colSpan={6}>No outstanding advances.</td></tr>
          ) : (
            advances.map((a) => (
              <tr key={a.booking_id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{a.guest_name}</td>
                <td className="px-4 py-2.5 text-slate-500">{a.unit_name}</td>
                <td className="px-4 py-2.5 text-slate-500">{a.check_in} → {a.check_out}</td>
                <td className="px-4 py-2.5 text-emerald-600">₹{a.advance_paid.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{a.total_due.toLocaleString()}</td>
                <td className="px-4 py-2.5 font-medium text-red-600">₹{a.balance_remaining.toLocaleString()}</td>
              </tr>
            ))
          )}
        </Table>
      </div>
    </Card>
  );
}

// --- Utility Bills ---
function UtilityTab({ setError, setSuccess }: { setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [readings, setReadings] = useState<UtilityReadingOut[]>([]);
  const [billingMode, setBillingMode] = useState<"single" | "split">("single");
  const [splitMode, setSplitMode] = useState<"equal" | "percentage" | "manual">("equal");
  const [selectedUnits, setSelectedUnits] = useState<string[]>([]);
  const [percentages, setPercentages] = useState<Record<string, string>>({});
  const [manualAmounts, setManualAmounts] = useState<Record<string, string>>({});
  const [form, setForm] = useState({ unit_id: "", meter_number: "", utility_type: "electricity", reading_date: todayStr(), opening_units: "", closing_units: "", rate_per_unit: "" });

  async function load() {
    try {
      const [u, r] = await Promise.all([api.get("/units"), api.get("/finance/utility-readings")]);
      setUnits(u.data);
      setReadings(r.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function unitName(id: string | null) {
    if (!id) return "Shared";
    return units.find((u) => u.id === id)?.name ?? "—";
  }

  const totalAmount = (Number(form.closing_units) - Number(form.opening_units) || 0) * (Number(form.rate_per_unit) || 0);

  function toggleUnit(unitId: string) {
    setSelectedUnits((prev) => (prev.includes(unitId) ? prev.filter((id) => id !== unitId) : [...prev, unitId]));
  }

  // Live preview of the split, regardless of mode — this is what actually
  // gets submitted for percentage/manual modes (equal mode is computed
  // server-side from shared_unit_ids instead, since it's exact division).
  function computedShare(unitId: string): number {
    if (splitMode === "equal") {
      return selectedUnits.length ? Math.round((totalAmount / selectedUnits.length) * 100) / 100 : 0;
    }
    if (splitMode === "percentage") {
      const pct = Number(percentages[unitId] || 0);
      return Math.round(totalAmount * (pct / 100) * 100) / 100;
    }
    return Number(manualAmounts[unitId] || 0);
  }

  const previewTotal = selectedUnits.reduce((sum, id) => sum + computedShare(id), 0);
  const previewMismatch = billingMode === "split" && splitMode !== "equal" && selectedUnits.length > 0 && Math.abs(previewTotal - totalAmount) > 1;

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      const basePayload = {
        meter_number: form.meter_number || undefined,
        utility_type: form.utility_type,
        reading_date: form.reading_date,
        opening_units: Number(form.opening_units),
        closing_units: Number(form.closing_units),
        rate_per_unit: Number(form.rate_per_unit),
      };

      if (billingMode === "single") {
        await api.post("/finance/utility-readings", { ...basePayload, unit_id: form.unit_id });
      } else if (splitMode === "equal") {
        await api.post("/finance/utility-readings", { ...basePayload, shared_unit_ids: selectedUnits });
      } else {
        await api.post("/finance/utility-readings", {
          ...basePayload,
          manual_allocations: selectedUnits.map((id) => ({ unit_id: id, share_amount: computedShare(id) })),
        });
      }
      setSuccess("Utility reading recorded.");
      setForm({ ...form, opening_units: "", closing_units: "" });
      setSelectedUnits([]);
      setPercentages({});
      setManualAmounts({});
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <Card className="mb-6 max-w-2xl">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Record Utility Bill</h2>
        <form onSubmit={handleCreate} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <select value={form.utility_type} onChange={(e) => setForm({ ...form, utility_type: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {["electricity", "water", "internet", "laundry", "gas", "generator", "waste_collection", "other"].map((t) => (
                <option key={t} value={t}>{t.replace("_", " ")}</option>
              ))}
            </select>
            <input placeholder="EB / Meter number (optional)" value={form.meter_number} onChange={(e) => setForm({ ...form, meter_number: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <input required type="date" value={form.reading_date} onChange={(e) => setForm({ ...form, reading_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <div className="grid grid-cols-3 gap-3">
            <input required type="number" placeholder="Opening units" value={form.opening_units} onChange={(e) => setForm({ ...form, opening_units: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required type="number" placeholder="Closing units" value={form.closing_units} onChange={(e) => setForm({ ...form, closing_units: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required type="number" placeholder="Rate/unit (₹)" value={form.rate_per_unit} onChange={(e) => setForm({ ...form, rate_per_unit: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          {totalAmount > 0 && <p className="text-xs text-slate-500">Bill total: <span className="font-semibold text-slate-800">₹{totalAmount.toLocaleString()}</span></p>}

          <div className="flex gap-2 pt-2 border-t border-slate-100">
            <button type="button" onClick={() => setBillingMode("single")} className={`flex-1 rounded-lg border px-3 py-1.5 text-xs font-medium ${billingMode === "single" ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 text-slate-600"}`}>
              Single Apartment
            </button>
            <button type="button" onClick={() => setBillingMode("split")} className={`flex-1 rounded-lg border px-3 py-1.5 text-xs font-medium ${billingMode === "split" ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 text-slate-600"}`}>
              Split Across Apartments
            </button>
          </div>

          {billingMode === "single" && (
            <select required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">Select unit...</option>
              {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          )}

          {billingMode === "split" && (
            <div className="space-y-3">
              <div className="flex gap-2">
                {(["equal", "percentage", "manual"] as const).map((m) => (
                  <button key={m} type="button" onClick={() => setSplitMode(m)} className={`flex-1 rounded-lg border px-3 py-1.5 text-xs font-medium capitalize ${splitMode === m ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 text-slate-600"}`}>
                    {m}
                  </button>
                ))}
              </div>

              <div className="border border-slate-200 rounded-lg p-3 max-h-56 overflow-y-auto space-y-2">
                {units.map((u) => (
                  <div key={u.id} className="flex items-center gap-2">
                    <input type="checkbox" checked={selectedUnits.includes(u.id)} onChange={() => toggleUnit(u.id)} />
                    <span className="text-xs text-slate-700 flex-1">{u.name}</span>
                    {selectedUnits.includes(u.id) && splitMode === "percentage" && (
                      <input
                        type="number"
                        placeholder="%"
                        value={percentages[u.id] ?? ""}
                        onChange={(e) => setPercentages({ ...percentages, [u.id]: e.target.value })}
                        className="w-16 rounded-md border border-slate-300 px-2 py-1 text-xs"
                      />
                    )}
                    {selectedUnits.includes(u.id) && splitMode === "manual" && (
                      <input
                        type="number"
                        placeholder="₹"
                        value={manualAmounts[u.id] ?? ""}
                        onChange={(e) => setManualAmounts({ ...manualAmounts, [u.id]: e.target.value })}
                        className="w-20 rounded-md border border-slate-300 px-2 py-1 text-xs"
                      />
                    )}
                    {selectedUnits.includes(u.id) && splitMode === "equal" && (
                      <span className="text-xs text-slate-400">₹{computedShare(u.id).toLocaleString()}</span>
                    )}
                  </div>
                ))}
              </div>

              {selectedUnits.length > 0 && (
                <div className={`text-xs rounded-lg p-2 ${previewMismatch ? "bg-red-50 text-red-700" : "bg-slate-50 text-slate-600"}`}>
                  Allocated: ₹{previewTotal.toLocaleString()} of ₹{totalAmount.toLocaleString()} bill total
                  {previewMismatch && " — adjust so these add up before submitting"}
                </div>
              )}
            </div>
          )}

          <Button type="submit">Record Reading</Button>
        </form>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4 flex items-center justify-between">
          Reading History
          <ExportButtons report="utility" />
        </h2>
        {readings.length === 0 ? (
          <EmptyState message="No utility readings recorded yet." />
        ) : (
          <div className="overflow-x-auto">
            <Table headers={["Unit", "Type", "Meter", "Date", "Consumed", "Amount", "Split"]}>
              {readings.map((r) => (
                <tr key={r.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{unitName(r.unit_id)}</td>
                  <td className="px-4 py-2.5 text-slate-500">{r.utility_type}</td>
                  <td className="px-4 py-2.5 text-slate-500">{r.meter_number ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{r.reading_date}</td>
                  <td className="px-4 py-2.5 text-slate-500">{r.units_consumed}</td>
                  <td className="px-4 py-2.5 font-medium text-slate-800">₹{r.amount.toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-slate-400">
                    {r.allocations.length > 0 ? r.allocations.map((a) => `${a.unit_name}: ₹${a.share_amount}`).join(", ") : "—"}
                  </td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}


// --- Purchase Register ---
function PurchasesTab({ setError, setSuccess }: { setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [purchases, setPurchases] = useState<PurchaseRecordOut[]>([]);
  const [form, setForm] = useState({ purchase_date: todayStr(), item_description: "", amount: "", supplier_name: "" });
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sortKey, setSortKey] = useState<"purchase_date" | "amount">("purchase_date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  async function load() {
    try {
      const params: Record<string, string> = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const { data } = await api.get("/finance/purchases", { params });
      setPurchases(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFrom, dateTo]);

  function toggleSort(key: "purchase_date" | "amount") {
    if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const filtered = purchases
    .filter((p) => !search || p.item_description.toLowerCase().includes(search.toLowerCase()) || (p.supplier_name ?? "").toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      const cmp = sortKey === "purchase_date" ? a.purchase_date.localeCompare(b.purchase_date) : a.amount - b.amount;
      return sortDir === "asc" ? cmp : -cmp;
    });

  function sortIndicator(key: "purchase_date" | "amount") {
    return sortKey === key ? (sortDir === "asc" ? " ▲" : " ▼") : "";
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/purchases", { purchase_date: form.purchase_date, item_description: form.item_description, amount: Number(form.amount), supplier_name: form.supplier_name || undefined });
      setSuccess("Purchase recorded.");
      setForm({ purchase_date: todayStr(), item_description: "", amount: "", supplier_name: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <Card className="mb-6 max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Record Purchase</h2>
        <p className="text-xs text-slate-400 mb-4">For ad-hoc purchases with a free-text description — not tied to a standing Inventory item.</p>
        <form onSubmit={handleCreate} className="space-y-3">
          <input required type="date" value={form.purchase_date} onChange={(e) => setForm({ ...form, purchase_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input required placeholder="Item description" value={form.item_description} onChange={(e) => setForm({ ...form, item_description: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <div className="grid grid-cols-2 gap-3">
            <input required type="number" placeholder="Amount (₹)" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input placeholder="Supplier (optional)" value={form.supplier_name} onChange={(e) => setForm({ ...form, supplier_name: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <Button type="submit">Record Purchase</Button>
        </form>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4 flex items-center justify-between">
          Purchase Register
          <ExportButtons report="purchases" params={{ date_from: dateFrom, date_to: dateTo }} />
        </h2>
        <div className="flex flex-wrap gap-2 mb-4">
          <input placeholder="Search item or supplier..." value={search} onChange={(e) => setSearch(e.target.value)} className="flex-1 min-w-[200px] rounded-lg border border-slate-300 px-3 py-1.5 text-xs" />
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        </div>
        {filtered.length === 0 ? (
          <EmptyState message="No purchases match these filters." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-400 border-b border-slate-100">
                  <th className="py-2 pr-4 cursor-pointer select-none" onClick={() => toggleSort("purchase_date")}>Date{sortIndicator("purchase_date")}</th>
                  <th className="py-2 pr-4">Item</th>
                  <th className="py-2 pr-4">Supplier</th>
                  <th className="py-2 cursor-pointer select-none" onClick={() => toggleSort("amount")}>Amount{sortIndicator("amount")}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p) => (
                  <tr key={p.id} className="border-b border-slate-50">
                    <td className="py-2 pr-4 text-slate-500">{p.purchase_date}</td>
                    <td className="py-2 pr-4 font-medium text-slate-800">{p.item_description}</td>
                    <td className="py-2 pr-4 text-slate-500">{p.supplier_name ?? "—"}</td>
                    <td className="py-2 text-slate-500">₹{p.amount.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

// --- Reusable report export buttons (PDF/Excel) ---
function ExportButtons({ report, params }: { report: string; params?: Record<string, string | number> }) {
  async function handleExport(format: "pdf" | "xlsx") {
    try {
      const response = await api.get("/finance/reports/export", {
        params: { report, format, ...params },
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `${report}_report.${format}`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      // Export failures here are non-critical (the on-screen data is
      // already visible) — silently ignoring keeps this button simple
      // rather than needing its own error banner wired through every tab.
    }
  }

  return (
    <div className="flex gap-2">
      <Button variant="secondary" onClick={() => handleExport("pdf")}>Export PDF</Button>
      <Button variant="secondary" onClick={() => handleExport("xlsx")}>Export Excel</Button>
    </div>
  );
}

// --- Expenses (search, filter, sort, bulk approval) ---
type ExpenseSortKey = "expense_date" | "amount" | "category";

function ExpensesTab({ isAdmin, setError, setSuccess }: { isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [expenses, setExpenses] = useState<ExpenseOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [approvalFilter, setApprovalFilter] = useState("");
  const [sortKey, setSortKey] = useState<ExpenseSortKey>("expense_date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [form, setForm] = useState({ category: "electricity", amount: "", expense_date: todayStr(), unit_id: "", notes: "" });

  async function load() {
    try {
      const params: Record<string, string> = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      if (categoryFilter) params.category = categoryFilter;
      const [e, u] = await Promise.all([api.get("/finance/expenses", { params }), api.get("/units")]);
      setExpenses(e.data);
      setUnits(u.data);
      setSelected(new Set());
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFrom, dateTo, categoryFilter]);

  function unitName(id: string | null) {
    if (!id) return "Central";
    return units.find((u) => u.id === id)?.name ?? "—";
  }

  function toggleSort(key: ExpenseSortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const filtered = expenses
    .filter((e) => !approvalFilter || e.approval_status === approvalFilter)
    .filter((e) => {
      if (!search) return true;
      const q = search.toLowerCase();
      return e.category.toLowerCase().includes(q) || (e.notes ?? "").toLowerCase().includes(q) || unitName(e.unit_id).toLowerCase().includes(q);
    })
    .sort((a, b) => {
      let cmp = 0;
      if (sortKey === "expense_date") cmp = a.expense_date.localeCompare(b.expense_date);
      else if (sortKey === "amount") cmp = a.amount - b.amount;
      else cmp = a.category.localeCompare(b.category);
      return sortDir === "asc" ? cmp : -cmp;
    });

  function sortIndicator(key: ExpenseSortKey) {
    if (sortKey !== key) return "";
    return sortDir === "asc" ? " ▲" : " ▼";
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selected.size === filtered.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filtered.map((e) => e.id)));
    }
  }

  async function handleBulkAction(approve: boolean) {
    if (selected.size === 0) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post("/finance/expenses/bulk-approval", {
        expense_ids: Array.from(selected),
        approve,
      });
      setSuccess(`${approve ? "Approved" : "Rejected"} ${data.approved_count || data.rejected_count} expense(s).`);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleSingleAction(id: string, approve: boolean) {
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/finance/expenses/${id}/approval`, { approve });
      setSuccess(approve ? "Approved." : "Rejected.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/expenses", {
        category: form.category, amount: Number(form.amount), expense_date: form.expense_date,
        unit_id: form.unit_id || undefined, notes: form.notes || undefined,
      });
      setSuccess("Expense recorded.");
      setForm({ ...form, amount: "", notes: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function approvalTone(status: string): "green" | "red" | "amber" {
    if (status === "approved") return "green";
    if (status === "rejected") return "red";
    return "amber";
  }

  return (
    <div>
      {isAdmin && (
        <Card className="mb-6 max-w-lg">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Record Expense</h2>
          <form onSubmit={handleCreate} className="space-y-3">
            <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {EXPENSE_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <div className="grid grid-cols-2 gap-3">
              <input required type="number" placeholder="Amount (₹)" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              <input required type="date" value={form.expense_date} onChange={(e) => setForm({ ...form, expense_date: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <select value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">Central / shared expense (no apartment)</option>
              {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <input placeholder="Notes (optional)" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <Button type="submit">Record Expense</Button>
          </form>
        </Card>
      )}

      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Expenses</h2>
          <ExportButtons report="expenses" params={{ date_from: dateFrom, date_to: dateTo }} />
        </div>

        <div className="flex flex-wrap gap-2 mb-4">
          <input
            placeholder="Search category, notes, or apartment..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 min-w-[200px] rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
          />
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
          <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
            <option value="">All categories</option>
            {EXPENSE_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select value={approvalFilter} onChange={(e) => setApprovalFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
            <option value="">All statuses</option>
            <option value="approved">Approved</option>
            <option value="pending">Pending</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>

        {isAdmin && selected.size > 0 && (
          <div className="flex items-center gap-2 mb-4 bg-slate-50 rounded-lg px-3 py-2">
            <span className="text-xs text-slate-600">{selected.size} selected</span>
            <Button variant="secondary" onClick={() => handleBulkAction(true)}>Approve Selected</Button>
            <Button variant="secondary" onClick={() => handleBulkAction(false)}>Reject Selected</Button>
          </div>
        )}

        {filtered.length === 0 ? (
          <EmptyState message="No expenses match these filters." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-400 border-b border-slate-100">
                  {isAdmin && (
                    <th className="py-2 pr-2">
                      <input type="checkbox" checked={selected.size === filtered.length && filtered.length > 0} onChange={toggleSelectAll} />
                    </th>
                  )}
                  <th className="py-2 pr-4 cursor-pointer select-none" onClick={() => toggleSort("expense_date")}>Date{sortIndicator("expense_date")}</th>
                  <th className="py-2 pr-4 cursor-pointer select-none" onClick={() => toggleSort("category")}>Category{sortIndicator("category")}</th>
                  <th className="py-2 pr-4">Apartment</th>
                  <th className="py-2 pr-4 cursor-pointer select-none" onClick={() => toggleSort("amount")}>Amount{sortIndicator("amount")}</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Notes</th>
                  {isAdmin && <th className="py-2">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {filtered.map((e) => (
                  <tr key={e.id} className="border-b border-slate-50">
                    {isAdmin && (
                      <td className="py-2 pr-2">
                        <input type="checkbox" checked={selected.has(e.id)} onChange={() => toggleSelect(e.id)} />
                      </td>
                    )}
                    <td className="py-2 pr-4 text-slate-500">{e.expense_date}</td>
                    <td className="py-2 pr-4 font-medium text-slate-800">{e.category}</td>
                    <td className="py-2 pr-4 text-slate-500">{unitName(e.unit_id)}</td>
                    <td className="py-2 pr-4 text-slate-500">₹{e.amount.toLocaleString()}</td>
                    <td className="py-2 pr-4"><Badge tone={approvalTone(e.approval_status)}>{e.approval_status}</Badge></td>
                    <td className="py-2 pr-4 text-slate-400 max-w-[200px] truncate">{e.notes ?? "—"}</td>
                    {isAdmin && (
                      <td className="py-2 space-x-2 whitespace-nowrap">
                        {e.approval_status !== "approved" && (
                          <button onClick={() => handleSingleAction(e.id, true)} className="text-xs text-emerald-600 hover:underline">Approve</button>
                        )}
                        {e.approval_status !== "rejected" && (
                          <button onClick={() => handleSingleAction(e.id, false)} className="text-xs text-red-500 hover:underline">Reject</button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

// --- Laundry Register ---
function LaundryTab({ isAdmin, setError, setSuccess }: { isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [entries, setEntries] = useState<LaundryEntryOut[]>([]);
  const [summary, setSummary] = useState<LaundryMonthlySummary | null>(null);
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [form, setForm] = useState({ entry_date: todayStr(), item_type: "bedsheet", quantity: "", rate_per_item: "", unit_id: "" });
  const [search, setSearch] = useState("");

  async function load() {
    try {
      const [e, s, u] = await Promise.all([
        api.get("/finance/laundry"),
        api.get("/finance/laundry/summary", { params: { period_year: year, period_month: month } }),
        api.get("/units"),
      ]);
      setEntries(e.data);
      setSummary(s.data);
      setUnits(u.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month]);

  function unitName(id: string | null) {
    if (!id) return "Central";
    return units.find((u) => u.id === id)?.name ?? "—";
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/finance/laundry", {
        entry_date: form.entry_date, item_type: form.item_type, quantity: Number(form.quantity),
        rate_per_item: Number(form.rate_per_item), unit_id: form.unit_id || undefined,
      });
      setSuccess("Laundry entry recorded.");
      setForm({ ...form, quantity: "", rate_per_item: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDelete(entry: LaundryEntryOut) {
    if (!window.confirm(`Delete this laundry entry — ${entry.item_type.replace("_", " ")} x${entry.quantity} on ${entry.entry_date} (₹${entry.total_amount.toLocaleString()})? This cannot be undone.`)) return;
    setError(null);
    try {
      await api.delete(`/finance/laundry/${entry.id}`);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const filtered = entries.filter((e) => !search || e.item_type.includes(search.toLowerCase()) || unitName(e.unit_id).toLowerCase().includes(search.toLowerCase()));

  return (
    <div>
      <Card className="mb-6 max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Record Laundry Entry</h2>
        <p className="text-xs text-slate-400 mb-4">Matches the business's own laundry sheet — item, quantity, and rate per item.</p>
        <form onSubmit={handleCreate} className="space-y-3">
          <input required type="date" value={form.entry_date} onChange={(e) => setForm({ ...form, entry_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <select value={form.item_type} onChange={(e) => setForm({ ...form, item_type: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            {LAUNDRY_ITEM_TYPES.map((t: string) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
          </select>
          <div className="grid grid-cols-2 gap-3">
            <input required type="number" placeholder="Quantity" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required type="number" placeholder="Rate per item (₹)" value={form.rate_per_item} onChange={(e) => setForm({ ...form, rate_per_item: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <select value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Central / shared (no apartment)</option>
            {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          {form.quantity && form.rate_per_item && (
            <p className="text-xs text-slate-500">Total: <span className="font-semibold text-slate-800">₹{(Number(form.quantity) * Number(form.rate_per_item)).toLocaleString()}</span></p>
          )}
          <Button type="submit">Record Entry</Button>
        </form>
      </Card>

      <div className="flex gap-3 mb-4">
        <input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28 rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
        <select value={month} onChange={(e) => setMonth(Number(e.target.value))} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm">
          {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      {summary && (
        <Card className="mb-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Monthly Summary — {summary.period_month}/{summary.period_year}</h2>
          {summary.lines.length === 0 ? (
            <EmptyState message="No laundry entries for this period." />
          ) : (
            <div className="overflow-x-auto">
              <Table headers={["Item Type", "Total Quantity", "Total Amount"]}>
                {summary.lines.map((l) => (
                  <tr key={l.item_type}>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{l.item_type.replace("_", " ")}</td>
                    <td className="px-4 py-2.5 text-slate-500">{l.total_quantity}</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{l.total_amount.toLocaleString()}</td>
                  </tr>
                ))}
              </Table>
              <p className="text-sm font-semibold text-slate-900 mt-3 text-right">Grand Total: ₹{summary.grand_total.toLocaleString()}</p>
            </div>
          )}
        </Card>
      )}

      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">All Entries</h2>
          <input placeholder="Search item or apartment..." value={search} onChange={(e) => setSearch(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs" />
        </div>
        {filtered.length === 0 ? (
          <EmptyState message="No laundry entries recorded yet." />
        ) : (
          <div className="overflow-x-auto">
            <Table headers={["Date", "Item", "Apartment", "Qty", "Rate", "Total", ""]}>
              {filtered.map((e) => (
                <tr key={e.id}>
                  <td className="px-4 py-2.5 text-slate-500">{e.entry_date}</td>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{e.item_type.replace("_", " ")}</td>
                  <td className="px-4 py-2.5 text-slate-500">{unitName(e.unit_id)}</td>
                  <td className="px-4 py-2.5 text-slate-500">{e.quantity}</td>
                  <td className="px-4 py-2.5 text-slate-500">₹{e.rate_per_item}</td>
                  <td className="px-4 py-2.5 font-medium text-slate-800">₹{e.total_amount.toLocaleString()}</td>
                  <td className="px-4 py-2.5">
                    {isAdmin && (
                      <button onClick={() => handleDelete(e)} className="text-xs text-red-500 hover:underline">Delete</button>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}

// --- Income Register ---
function IncomeRegisterTab({ setError }: { setError: (e: string | null) => void }) {
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [register, setRegister] = useState<IncomeRegister | null>(null);
  const [expandedUnits, setExpandedUnits] = useState<Set<string>>(new Set());

  async function load() {
    try {
      const { data } = await api.get("/finance/income-register", { params: { period_year: year, period_month: month } });
      setRegister(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month]);

  function toggleUnit(unitId: string) {
    setExpandedUnits((prev) => {
      const next = new Set(prev);
      if (next.has(unitId)) next.delete(unitId);
      else next.add(unitId);
      return next;
    });
  }

  const sourceLabels: Record<string, string> = {
    direct: "Direct", walk_in: "Walk-in", airbnb: "Airbnb", agoda: "Agoda", booking_com: "Booking.com",
  };

  return (
    <div>
      <div className="flex items-end justify-between flex-wrap gap-3 mb-6">
        <div className="flex gap-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Year</label>
            <input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28 rounded-lg border border-slate-300 px-3 py-1.5 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Month</label>
            <select value={month} onChange={(e) => setMonth(Number(e.target.value))} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm">
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        </div>
        <ExportButtons report="income_register" params={{ period_year: year, period_month: month }} />
      </div>

      {register && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
            <StatBox label="Grand Total" value={`₹${register.grand_total.toLocaleString()}`} tone="green" />
            {Object.entries(register.by_source).slice(0, 3).map(([source, amount]) => (
              <StatBox key={source} label={sourceLabels[source] ?? source} value={`₹${amount.toLocaleString()}`} />
            ))}
          </div>

          {Object.keys(register.by_source).length > 0 && (
            <Card className="mb-6">
              <h2 className="text-sm font-semibold text-slate-900 mb-3">Revenue by Source (room rate only)</h2>
              <div className="flex flex-wrap gap-4">
                {Object.entries(register.by_source).map(([source, amount]) => (
                  <div key={source} className="text-sm">
                    <span className="text-slate-500">{sourceLabels[source] ?? source}:</span>{" "}
                    <span className="font-medium text-slate-800">₹{amount.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          <Card>
            <h2 className="text-sm font-semibold text-slate-900 mb-1">Flat-wise Income Register — {register.period_month}/{register.period_year}</h2>
            <p className="text-xs text-slate-400 mb-4">Matches the business's own monthly income sheet — one block per apartment, with guest, contact, amount, advance, balance, and booking source.</p>
            {register.units.every((u) => u.lines.length === 0) ? (
              <EmptyState message="No bookings recorded for this period." />
            ) : (
              <div className="space-y-3">
                {register.units.filter((u) => u.lines.length > 0).map((unit) => (
                  <div key={unit.unit_id} className="border border-slate-100 rounded-lg overflow-hidden">
                    <button
                      onClick={() => toggleUnit(unit.unit_id)}
                      className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-50 hover:bg-slate-100 text-left"
                    >
                      <span className="text-sm font-medium text-slate-800">{unit.unit_name}</span>
                      <span className="text-sm text-slate-600">₹{unit.unit_total.toLocaleString()} ({unit.lines.length} booking{unit.lines.length !== 1 ? "s" : ""})</span>
                    </button>
                    {expandedUnits.has(unit.unit_id) && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-left text-slate-400 border-b border-slate-100">
                              <th className="py-2 px-4">Date</th>
                              <th className="py-2 px-4">Guest</th>
                              <th className="py-2 px-4">Guests</th>
                              <th className="py-2 px-4">Contact</th>
                              <th className="py-2 px-4">Amount (room rate only)</th>
                              <th className="py-2 px-4">Advance</th>
                              <th className="py-2 px-4">Balance</th>
                              <th className="py-2 px-4">Source</th>
                            </tr>
                          </thead>
                          <tbody>
                            {unit.lines.map((l) => (
                              <tr key={l.booking_id} className="border-b border-slate-50">
                                <td className="py-2 px-4 text-slate-500">{l.check_in}</td>
                                <td className="py-2 px-4 font-medium text-slate-800">{l.guest_name}</td>
                                <td className="py-2 px-4 text-slate-500">{l.no_of_guests}</td>
                                <td className="py-2 px-4 text-slate-500">{l.contact}</td>
                                <td className="py-2 px-4 text-slate-500">₹{l.amount.toLocaleString()}</td>
                                <td className="py-2 px-4 text-emerald-600">₹{l.advance.toLocaleString()}</td>
                                <td className={`py-2 px-4 font-medium ${l.balance > 0 ? "text-red-600" : "text-slate-500"}`}>₹{l.balance.toLocaleString()}</td>
                                <td className="py-2 px-4"><Badge tone="slate">{sourceLabels[l.status] ?? l.status}</Badge></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
