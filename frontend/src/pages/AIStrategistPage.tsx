import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  extractErrorMessage,
  type AttentionItem,
  type AIStrategistBundle,
  type MetricComparison,
  type NarrativeOutput,
  type PropertyOut,
} from "../lib/api";
import { Card, PageHeader, ErrorBanner, Button, Table, Badge, EmptyState, Spinner } from "../components/ui";
import { useRealtimeRefresh } from "../lib/realtime";

function localDateValue(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function firstOfMonth(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}
export function today(d = new Date()): string {
  return localDateValue(d);
}
export function toExclusiveDate(inclusiveDate: string): string {
  const [year, month, day] = inclusiveDate.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10);
}
function fromExclusiveDate(exclusiveDate: string): string {
  const [year, month, day] = exclusiveDate.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day - 1)).toISOString().slice(0, 10);
}
function formatDate(dateValue: string): string {
  const [year, month, day] = dateValue.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day, 12)).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  });
}
export function formatPeriod(dateFrom: string, dateToExclusive: string): string {
  return `${formatDate(dateFrom)} – ${formatDate(fromExclusiveDate(dateToExclusive))}`;
}

// Presentation-only formatting — never recomputes a value, only displays
// exactly what the backend bundle already contains. null/undefined are
// always rendered as "—", distinct from an actual 0 the backend returned.
function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}
function fmtPercent(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v}%`;
}
function fmtChange(m: MetricComparison, opts: { points?: boolean; currency?: boolean } = {}): string {
  const arrow = m.current > m.previous ? "▲" : m.current < m.previous ? "▼" : "→";
  const prevLabel = opts.currency ? fmtMoney(m.previous) : `${m.previous}%`;
  if (opts.points && m.change_percentage_points != null) {
    const pts = m.change_percentage_points;
    return `${arrow} ${pts >= 0 ? "+" : ""}${pts} pts vs ${prevLabel} prev.`;
  }
  if (m.change_percent == null) return `${arrow} vs ${prevLabel} prev. — no prior data`;
  return `${arrow} ${m.change_percent >= 0 ? "+" : ""}${m.change_percent}% vs ${prevLabel} prev.`;
}

function StatBox({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className="text-xl font-semibold text-slate-900">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
    </div>
  );
}

const ATTENTION_LIMIT = 5;
const ATTENTION_CATEGORY: Record<AttentionItem["category"], string> = {
  unpaid_balance: "Payments",
  low_stock: "Inventory",
  guest_request: "Operations",
  maintenance: "Operations",
};

export function NeedsAttentionPanel({ items }: { items: AttentionItem[] }) {
  const [showAll, setShowAll] = useState(false);
  const visibleItems = showAll ? items : items.slice(0, ATTENTION_LIMIT);

  return (
    <Card className="mb-6 border-amber-200 bg-amber-50/60">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Needs Attention <span className="ml-1 text-xs font-normal text-slate-500">· current operational alerts</span></h2>
          <p className="mt-1 text-xs text-slate-500">Portfolio-wide current alerts, independent of period/property filters. Ordered by existing OS10 priority; links open records for review only.</p>
        </div>
        {items.length > ATTENTION_LIMIT && (
          <button type="button" aria-expanded={showAll} onClick={() => setShowAll((value) => !value)} className="shrink-0 rounded-md px-2 py-1 text-xs font-semibold text-brand-green hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-green">
            {showAll ? "Show top 5" : `View all (${items.length})`}
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <p role="status" className="rounded-lg border border-slate-200 bg-white/70 px-3 py-3 text-sm text-slate-600">No current items need attention.</p>
      ) : (
        <ul className="space-y-2">
          {visibleItems.map((item) => (
            <li key={`${item.category}-${item.reference_id}`}>
              <Link to={item.link} aria-label={`Review ${ATTENTION_CATEGORY[item.category]}: ${item.title}`} className="flex min-w-0 items-start gap-2 rounded-lg border border-slate-200/80 bg-white px-3 py-2.5 transition hover:border-brand-green/30 hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-green">
                <span className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${item.severity === "critical" ? "bg-red-50 text-red-700" : "bg-amber-100 text-amber-800"}`}>{item.severity}</span>
                <span className="min-w-0 flex-1">
                  <span className="mr-2 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">{ATTENTION_CATEGORY[item.category]}</span>
                  <span className="break-words text-xs font-semibold text-slate-800">{item.title}</span>
                  <span className="mt-1 block break-words text-xs leading-5 text-slate-500">{item.reason} {item.action}</span>
                </span>
                <span className="mt-1 shrink-0 text-[11px] font-semibold text-brand-green">Review →</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function AIStrategistPage() {
  const [dateFrom, setDateFrom] = useState(firstOfMonth());
  const [dateTo, setDateTo] = useState(today());
  const [propertyId, setPropertyId] = useState("");
  const [properties, setProperties] = useState<PropertyOut[]>([]);

  const [bundle, setBundle] = useState<AIStrategistBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [narrative, setNarrative] = useState<NarrativeOutput | null>(null);
  const [narrativeLoading, setNarrativeLoading] = useState(true);
  const [narrativeError, setNarrativeError] = useState<string | null>(null);

  async function load() {
    setError(null);
    setLoading(true);
    try {
      const params: Record<string, string> = { date_from: dateFrom, date_to: toExclusiveDate(dateTo) };
      if (propertyId) params.property_id = propertyId;
      const { data } = await api.get("/reports/ai-strategist", { params });
      setBundle(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  // Independent, best-effort fetch — the narrative panel is optional
  // presentation on top of the deterministic data above; if it fails,
  // the rest of the page (already fetched by load()) stays fully usable.
  async function loadNarrative() {
    setNarrativeError(null);
    setNarrativeLoading(true);
    try {
      const params: Record<string, string> = { date_from: dateFrom, date_to: toExclusiveDate(dateTo) };
      if (propertyId) params.property_id = propertyId;
      const { data } = await api.get("/reports/ai-strategist/narrative", { params });
      setNarrative(data);
    } catch (err) {
      setNarrativeError(extractErrorMessage(err));
    } finally {
      setNarrativeLoading(false);
    }
  }

  useEffect(() => {
    api.get("/properties").then((r) => setProperties(r.data)).catch(() => {});
    load();
    loadNarrative();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Keep deterministic, authoritative metrics fresh without rerunning the
  // optional narrative provider on every business-data invalidation.
  useRealtimeRefresh(["bookings.changed", "availability.changed", "payments.changed", "finance.changed", "properties.changed", "inventory.changed", "housekeeping.changed", "maintenance.changed", "guest_requests.changed"], load);

  return (
    <div>
      <PageHeader
        title="AI Strategist"
        action={
          <Button
            variant="secondary"
            onClick={() => { load(); loadNarrative(); }}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </Button>
        }
      />
      <ErrorBanner message={error} />

      <Card className="mb-6 bg-amber-50 border-amber-200">
        <p className="text-xs font-semibold text-amber-900">READ-ONLY — NOT AUTOMATED</p>
        <p className="text-xs text-amber-800 mt-1">
          Figures and alerts come from OS10 reporting rules; this page does not forecast business results or execute
          operational changes. Links open records for review only. Nothing here changes bookings, prices, payments,
          inventory, or guest communications.
        </p>
      </Card>

      <Card className="mb-6">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="strategist-date-from" className="block text-xs text-slate-500 mb-1">From (inclusive)</label>
            <input id="strategist-date-from" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <div>
            <label htmlFor="strategist-date-to" className="block text-xs text-slate-500 mb-1">Through (inclusive)</label>
            <input id="strategist-date-to" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Property</label>
            <select value={propertyId} onChange={(e) => setPropertyId(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">All properties</option>
              {properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <Button onClick={() => { load(); loadNarrative(); }} disabled={loading}>{loading ? "Loading..." : "Apply"}</Button>
        </div>
      </Card>

      {loading && !bundle && <Spinner label="Loading AI Strategist data..." />}

      {!loading && !bundle && !error && (
        <EmptyState message="No data available for this range." action={<Button variant="secondary" onClick={load}>Retry</Button>} />
      )}

      {error && !bundle && (
        <Card>
          <EmptyState message="Could not load AI Strategist data." action={<Button variant="secondary" onClick={load}>Retry</Button>} />
        </Card>
      )}

      {bundle && (
        <>
          <p className="text-xs text-slate-400 mb-4">Generated {new Date(bundle.generated_at).toLocaleString()}</p>

          {/* A. Business Health */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-1">Business Health</h2>
            <p className="text-xs text-slate-500 mb-1">
              Current period: {formatPeriod(bundle.business_health.current_period.date_from, bundle.business_health.current_period.date_to)} · {propertyId ? properties.find((property) => property.id === propertyId)?.name ?? "Selected property" : "All properties"}
            </p>
            <p className="text-xs text-slate-400 mb-4">
              Revenue definition: {bundle.business_health.revenue_definition} Metrics use bookings whose check-in falls in the selected period; full-stay revenue and nights are counted.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatBox
                label="Revenue"
                value={fmtMoney(bundle.business_health.revenue_current)}
                sub={`prev. ${fmtMoney(bundle.business_health.revenue_previous)}`}
              />
              <StatBox label="Occupancy" value={fmtPercent(bundle.business_health.occupancy.current)} sub={fmtChange(bundle.business_health.occupancy, { points: true })} />
              <StatBox label="ADR" value={fmtMoney(bundle.business_health.adr.current)} sub={fmtChange(bundle.business_health.adr, { currency: true })} />
              <StatBox label="RevPAR" value={fmtMoney(bundle.business_health.revpar.current)} sub={fmtChange(bundle.business_health.revpar, { currency: true })} />
            </div>
            <p className="text-xs text-slate-400 mt-3">
              Compared with {formatPeriod(bundle.business_health.previous_period.date_from, bundle.business_health.previous_period.date_to)}.
            </p>
          </Card>

          {/* G. Attention */}
          <NeedsAttentionPanel items={bundle.needs_attention} />

          {/* B. Cancellations */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Cancellations</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatBox label="Cancelled" value={String(bundle.cancellations.cancelled_count)} sub={fmtPercent(bundle.cancellations.cancellation_rate_percent)} />
              <StatBox label="No-Shows" value={String(bundle.cancellations.no_show_count)} sub={fmtPercent(bundle.cancellations.no_show_rate_percent)} />
              <StatBox label="Firm Bookings in Period" value={String(bundle.cancellations.total_bookings_in_period)} />
              <StatBox
                label="Previous Period"
                value={formatPeriod(bundle.cancellations.previous_period.date_from, bundle.cancellations.previous_period.date_to)}
              />
            </div>
          </Card>

          {/* C. Unit Performance */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-1">Unit Performance</h2>
            <p className="text-xs text-slate-400 mb-4">Selected period: {formatPeriod(bundle.unit_performance.current_period.date_from, bundle.unit_performance.current_period.date_to)}</p>
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Top Units</h3>
            {bundle.unit_performance.top_units.length === 0 ? (
              <EmptyState message="No unit data for this period." />
            ) : (
              <Table headers={["Unit", "Revenue", "Occupancy", "ADR", "RevPAR"]}>
                {bundle.unit_performance.top_units.map((u) => (
                  <tr key={u.unit_id}>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{u.unit_name}</td>
                    <td className="px-4 py-2.5 text-slate-500">{fmtMoney(u.revenue)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{fmtPercent(u.occupancy_rate_percent)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{fmtMoney(u.adr)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{fmtMoney(u.revpar)}</td>
                  </tr>
                ))}
              </Table>
            )}
            {bundle.unit_performance.bottom_units.length > 0 && (
              <>
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2 mt-4">Bottom Units</h3>
                <Table headers={["Unit", "Revenue", "Occupancy", "ADR", "RevPAR"]}>
                  {bundle.unit_performance.bottom_units.map((u) => (
                    <tr key={u.unit_id}>
                      <td className="px-4 py-2.5 font-medium text-slate-800">{u.unit_name}</td>
                      <td className="px-4 py-2.5 text-slate-500">{fmtMoney(u.revenue)}</td>
                      <td className="px-4 py-2.5 text-slate-500">{fmtPercent(u.occupancy_rate_percent)}</td>
                      <td className="px-4 py-2.5 text-slate-500">{fmtMoney(u.adr)}</td>
                      <td className="px-4 py-2.5 text-slate-500">{fmtMoney(u.revpar)}</td>
                    </tr>
                  ))}
                </Table>
              </>
            )}
          </Card>

          {/* D. Booking Sources */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-1">Booking Sources</h2>
            <p className="text-xs text-slate-400 mb-1">Selected period: {formatPeriod(bundle.booking_sources.current_period.date_from, bundle.booking_sources.current_period.date_to)}</p>
            <p className="text-xs text-slate-400 mb-4">Revenue definition: {bundle.booking_sources.revenue_definition}</p>
            {Object.keys(bundle.booking_sources.revenue_by_source).length === 0 ? (
              <EmptyState message="No booking-source data for this period." />
            ) : (
              <div className="flex flex-wrap gap-4">
                {Object.entries(bundle.booking_sources.revenue_by_source).map(([source, amount]) => (
                  <div key={source} className="text-sm">
                    <span className="text-slate-500">{source}:</span>{" "}
                    <span className="font-medium text-slate-800">{fmtMoney(amount)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* E. Guest Segments */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Top Guests</h2>
            {bundle.top_guests.length === 0 ? (
              <EmptyState message="No guest data available." />
            ) : (
              <Table headers={["Guest", "Segment", "Bookings", "Total Spent", "Latest Source", "Last Stay"]}>
                {bundle.top_guests.map((g) => (
                  <tr key={g.guest_id}>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{g.name}</td>
                    <td className="px-4 py-2.5">{g.is_repeat_guest ? <Badge tone="green">Repeat</Badge> : <Badge tone="slate">New</Badge>}</td>
                    <td className="px-4 py-2.5 text-slate-500">{g.total_bookings}</td>
                    <td className="px-4 py-2.5 text-slate-500">{fmtMoney(g.total_spent)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{g.latest_source ?? "—"}</td>
                    <td className="px-4 py-2.5 text-slate-500">{g.last_stay ?? "—"}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>

          {/* F. Outstanding Payments */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Outstanding Payments</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatBox label="Total Pending" value={fmtMoney(bundle.outstanding_payments.total_pending_balance)} />
              <StatBox label="Pending Bookings" value={String(bundle.outstanding_payments.pending_count)} />
              <StatBox label="Overdue Bookings" value={String(bundle.outstanding_payments.overdue_count)} />
              <StatBox label="Overdue Amount" value={fmtMoney(bundle.outstanding_payments.overdue_amount)} />
            </div>
          </Card>

          {/* G. Maintenance */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Maintenance</h2>
            <StatBox label="Maintenance Cost (period)" value={fmtMoney(bundle.maintenance.maintenance_cost_total)} />
          </Card>

          {/* Phase 19F: Finance — P&L / Cash Flow / Budget / Loans / OTA. Every
              figure is a direct read from the existing canonical Finance/Loan/
              OTA services (see finance/service.py::financial_snapshot); nothing
              here is recomputed. FACTS only — no recommendation is implied. */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-1">Finance — P&amp;L</h2>
            <p className="text-xs text-slate-400 mb-4">{bundle.finance.pnl.period_note}</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatBox label="Revenue" value={fmtMoney(bundle.finance.pnl.revenue)} />
              <StatBox label="Operating Expenses" value={fmtMoney(bundle.finance.pnl.operating_expenses)} />
              <StatBox label="Central Expenses" value={fmtMoney(bundle.finance.pnl.central_expenses)} />
              <StatBox label="Net Profit" value={fmtMoney(bundle.finance.pnl.net_profit)} />
            </div>
          </Card>

          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Finance — Cash Flow</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatBox label="Cash In" value={fmtMoney(bundle.finance.cash_flow.cash_in_total)} />
              <StatBox label="Cash Out" value={fmtMoney(bundle.finance.cash_flow.cash_out_total)} />
              <StatBox label="Net Cash Flow" value={fmtMoney(bundle.finance.cash_flow.net_cash_flow)} />
              <StatBox label="OTA Payouts Received" value={fmtMoney(bundle.finance.cash_flow.cash_in_ota_payouts)} />
              <StatBox label="Loan/EMI Paid" value={fmtMoney(bundle.finance.cash_flow.cash_out_loan_emi)} />
              <StatBox label="Owner Withdrawals" value={fmtMoney(bundle.finance.cash_flow.cash_out_withdrawals)} />
              <StatBox label="Cash in Bank" value={fmtMoney(bundle.finance.cash_flow.cash_in_bank)} />
              <StatBox label="Cash in Hand" value={fmtMoney(bundle.finance.cash_flow.cash_in_hand)} />
            </div>
          </Card>

          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Finance — Loans / EMI</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
              <StatBox label="Active Loans" value={String(bundle.finance.loans.total_active_loans)} />
              <StatBox label="Monthly EMI Obligation" value={fmtMoney(bundle.finance.loans.total_monthly_emi_obligation)} />
              <StatBox label="Overdue EMI" value={fmtMoney(bundle.finance.loans.overdue_emi_amount)} sub={`${bundle.finance.loans.overdue_emi_count} installment(s)`} />
              <StatBox label="Due This Month" value={fmtMoney(bundle.finance.loans.outstanding_this_month_amount)} sub={`${bundle.finance.loans.outstanding_this_month_count} installment(s)`} />
            </div>
            {bundle.finance.loans.active_loans.length === 0 ? (
              <EmptyState message="No active loans." />
            ) : (
              <Table headers={["Loan", "Lender", "Principal", "EMI Amount"]}>
                {bundle.finance.loans.active_loans.map((l) => (
                  <tr key={l.loan_id}>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{l.loan_name}</td>
                    <td className="px-4 py-2.5 text-slate-500">{l.lender_name}</td>
                    <td className="px-4 py-2.5 text-slate-500">{fmtMoney(l.principal_amount)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{fmtMoney(l.emi_amount)}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>

          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Finance — OTA Reconciliation</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatBox label="Commission" value={fmtMoney(bundle.finance.ota.commission_total)} />
              <StatBox label="OTA GST" value={fmtMoney(bundle.finance.ota.gst_total)} />
              <StatBox label="Expected Payout" value={fmtMoney(bundle.finance.ota.expected_payout_total)} />
              <StatBox label="Actual Received" value={fmtMoney(bundle.finance.ota.actual_received_total)} />
              <StatBox label="Unreconciled" value={String(bundle.finance.ota.unreconciled_count)} />
              <StatBox label="Discrepancies" value={String(bundle.finance.ota.discrepancy_count)} sub={fmtMoney(bundle.finance.ota.discrepancy_total)} />
              <StatBox label="Missing Commission" value={String(bundle.finance.ota.missing_commission_count)} sub={fmtMoney(bundle.finance.ota.missing_commission_gross_total)} />
            </div>
          </Card>

          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Finance — Payroll / Utilities / Purchases</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
              <StatBox label="Payroll" value={fmtMoney(bundle.finance.expenses.payroll_total)} />
              <StatBox label="Utilities" value={fmtMoney(bundle.finance.expenses.utilities_total)} />
              <StatBox label="Purchases" value={fmtMoney(bundle.finance.expenses.purchases_total)} />
            </div>
          </Card>

          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Finance — Outstanding Obligations</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatBox label="Guest Outstanding" value={fmtMoney(bundle.finance.outstanding_obligations.guest_outstanding_balance)} />
              <StatBox label="EMI Overdue" value={fmtMoney(bundle.finance.outstanding_obligations.emi_overdue_amount)} sub={`${bundle.finance.outstanding_obligations.emi_overdue_count} installment(s)`} />
              <StatBox label="EMI Due This Month" value={fmtMoney(bundle.finance.outstanding_obligations.emi_due_this_month_amount)} />
              <StatBox label="OTA Payout Unreconciled" value={fmtMoney(bundle.finance.outstanding_obligations.ota_unreconciled_expected_payout)} />
            </div>
          </Card>

          {/* H. GST */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">GST</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
              <StatBox label="GST Collected" value={fmtMoney(bundle.gst.gst_collected)} />
              <StatBox label="Input Credit Eligible" value={fmtMoney(bundle.gst.gst_input_credit_eligible)} />
              <StatBox label="GST Payable" value={fmtMoney(bundle.gst.gst_payable)} />
            </div>
          </Card>

          {/* I. Weekly Facts */}
          <Card className="mb-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Selected-period facts</h2>
            {bundle.weekly_facts.length === 0 ? (
              <EmptyState message="No facts available for this period." />
            ) : (
              <ul className="space-y-2 text-sm">
                {bundle.weekly_facts.map((f, i) => (
                  <li key={i} className="flex items-center justify-between border-b border-slate-50 pb-2 last:border-0">
                    <span className="text-slate-600">{f.label}</span>
                    <span className="flex items-center gap-2">
                      <span className="font-medium text-slate-900">{f.value}</span>
                      <Badge tone={f.category === "fact" ? "slate" : "amber"}>{f.category === "fact" ? "fact" : "derived"}</Badge>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* J. Narrative (Phase 13G — explanation layer only, never a new data source) */}
          <Card>
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-sm font-semibold text-slate-900">Narrative</h2>
              {narrative && (
                <Badge tone={narrative.source === "llm" ? "amber" : "slate"}>
                  {narrative.source === "llm" ? "AI-explained" : "Deterministic (no AI available)"}
                </Badge>
              )}
            </div>
            <p className="text-xs text-slate-400 mb-4">
              This section only restates the figures above in plain language, each line linked back to where it
              came from. It is never a new source of numbers — if anything here looks wrong, trust the sections
              above instead.
            </p>

            {narrativeLoading && !narrative && <Spinner label="Loading narrative..." />}
            {narrativeError && !narrative && (
              <EmptyState message="Could not load narrative." action={<Button variant="secondary" onClick={loadNarrative}>Retry</Button>} />
            )}

            {narrative && (
              <div className="space-y-5">
                <p className="text-sm text-slate-700">{narrative.executive_summary}</p>

                {narrative.key_observations.length > 0 && (
                  <div>
                    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Observations</h3>
                    <ul className="space-y-2 text-sm">
                      {narrative.key_observations.map((o, i) => (
                        <li key={i} className="flex items-start gap-2">
                          <Badge tone="slate">{o.classification}</Badge>
                          <span className="text-slate-700">
                            {o.text}
                            <span className="block text-[11px] text-slate-400">source: {o.source_ids.join(", ")}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {narrative.priority_actions.length > 0 && (
                  <div>
                    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Priority Actions</h3>
                    <ul className="space-y-2 text-sm">
                      {narrative.priority_actions.map((a, i) => (
                        <li key={i} className="flex items-start gap-2">
                          <Badge tone="amber">RECOMMENDATION</Badge>
                          <span className="text-slate-700">
                            {a.text}
                            <span className="block text-[11px] text-slate-400">
                              source: {a.source_ids.join(", ")} — requires human approval: {a.requires_human_approval ? "yes" : "no"}
                            </span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {narrative.risks.length > 0 && (
                  <div>
                    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Risks</h3>
                    <ul className="space-y-2 text-sm">
                      {narrative.risks.map((r, i) => (
                        <li key={i} className="flex items-start gap-2">
                          <Badge tone="slate">{r.classification}</Badge>
                          <span className="text-slate-700">
                            {r.text}
                            <span className="block text-[11px] text-slate-400">source: {r.source_ids.join(", ")}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {narrative.questions_for_management.length > 0 && (
                  <div>
                    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Questions for Management</h3>
                    <ul className="list-disc list-inside space-y-1 text-sm text-slate-700">
                      {narrative.questions_for_management.map((q, i) => <li key={i}>{q}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
