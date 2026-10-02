import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  api,
  extractErrorMessage,
  type SummaryReport,
  type RevenuePoint,
  type UnitPerformance,
  type PropertyOut,
  type CashFlowReport,
  type OutstandingPaymentLine,
} from "../lib/api";
import { Card, PageHeader, ErrorBanner, Button, EmptyState, Spinner } from "../components/ui";

/**
 * Revenue Dashboard — a dedicated module consolidating revenue figures that
 * otherwise live scattered across Reports Center and Finance. Every number
 * here is read from an existing, already-tested backend calculation:
 *   - /reports/summary            -> total revenue, bookings, occupancy/ADR/RevPAR, revenue-by-source
 *   - /reports/revenue-series     -> daily revenue (aggregated client-side into months for the trend toggle)
 *   - /reports/unit-performance   -> per-unit revenue breakdown
 *   - /finance/cash-flow          -> cash actually collected in the period (payment-date basis)
 *   - /finance/outstanding-payments -> unpaid guest balances (portfolio snapshot, not date-scoped)
 * No new backend endpoint or financial formula is introduced by this page.
 *
 * IMPORTANT — bases don't match, and this page says so rather than hiding
 * it: "Revenue" above is accrual/check-in-date basis; "Cash Collected" and
 * "Outstanding Balance" are Finance's own cash-basis figures. They are
 * shown side by side for context, not as parts of one reconciled total.
 *
 * Revenue-by-source is shown as the raw per-source breakdown (the literal
 * Booking.source values), not collapsed into an OTA-vs-Direct binary —
 * Booking.source and OTACommission.channel are independently-typed,
 * unlinked vocabularies (see reports/service.py and finance/service.py's
 * own ota_missing_commissions docstring), so no reliable rule exists yet
 * to classify an arbitrary source string as OTA vs Direct. Inventing one
 * here would be inventing a business policy, which this module must not do.
 */

function firstOfMonth(): string {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}
function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function fmtCurrency(n: number): string {
  return `₹${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPercent(n: number): string {
  return `${n.toFixed(1)}%`;
}

type TrendGranularity = "daily" | "monthly";

interface TrendPoint {
  key: string;
  label: string;
  revenue: number;
}

// Pure aggregation of the same daily rows /reports/revenue-series already
// returns — sums revenue per calendar month, introduces no new figure.
function aggregateMonthly(points: RevenuePoint[]): TrendPoint[] {
  const byMonth = new Map<string, number>();
  for (const p of points) {
    const key = p.date.slice(0, 7); // "YYYY-MM"
    byMonth.set(key, round2((byMonth.get(key) ?? 0) + p.revenue));
  }
  return [...byMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, revenue]) => ({ key, label: key, revenue }));
}

export default function RevenueDashboardPage() {
  const [dateFrom, setDateFrom] = useState(firstOfMonth());
  const [dateTo, setDateTo] = useState(today());
  const [propertyId, setPropertyId] = useState("");
  const [properties, setProperties] = useState<PropertyOut[]>([]);

  const [summary, setSummary] = useState<SummaryReport | null>(null);
  const [revenueSeries, setRevenueSeries] = useState<RevenuePoint[]>([]);
  const [unitPerformance, setUnitPerformance] = useState<UnitPerformance[]>([]);
  const [cashFlow, setCashFlow] = useState<CashFlowReport | null>(null);
  const [outstandingLines, setOutstandingLines] = useState<OutstandingPaymentLine[]>([]);

  const [trendGranularity, setTrendGranularity] = useState<TrendGranularity>("daily");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    setLoading(true);
    try {
      const params: Record<string, string> = { date_from: dateFrom, date_to: dateTo };
      if (propertyId) params.property_id = propertyId;
      // Finance's cash-flow endpoint has no property filter — it is
      // always portfolio-wide, a pre-existing Finance limitation, not
      // something this page can fix (see the ambiguity note below).
      const financeParams: Record<string, string> = { date_from: dateFrom, date_to: dateTo };

      const [s, r, u, cf, outstanding] = await Promise.all([
        api.get("/reports/summary", { params }),
        api.get("/reports/revenue-series", { params }),
        api.get("/reports/unit-performance", { params }),
        api.get("/finance/cash-flow", { params: financeParams }),
        api.get("/finance/outstanding-payments", { params: { limit: 5000 } }),
      ]);
      setSummary(s.data);
      setRevenueSeries(r.data);
      setUnitPerformance(u.data);
      setCashFlow(cf.data);
      setOutstandingLines(outstanding.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    api.get("/properties").then((r) => setProperties(r.data)).catch(() => {});
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalRevenue = summary?.revenue.total ?? 0;

  const totalOutstanding = round2(outstandingLines.reduce((sum, l) => sum + l.pending_balance, 0));
  const overdueOutstanding = round2(
    outstandingLines.filter((l) => l.is_overdue).reduce((sum, l) => sum + l.pending_balance, 0)
  );

  const sourceRows = summary
    ? Object.entries(summary.revenue.by_source)
        .map(([source, amount]) => ({
          source,
          amount,
          percent: totalRevenue > 0 ? (amount / totalRevenue) * 100 : 0,
        }))
        .sort((a, b) => b.amount - a.amount)
    : [];

  const unitRows = [...unitPerformance]
    .sort((a, b) => b.revenue - a.revenue)
    .map((u) => ({
      ...u,
      percentOfTotal: totalRevenue > 0 ? (u.revenue / totalRevenue) * 100 : 0,
    }));

  const dailyTrend: TrendPoint[] = revenueSeries.map((p) => ({ key: p.date, label: p.date, revenue: p.revenue }));
  const monthlyTrend = aggregateMonthly(revenueSeries);
  const trendData = trendGranularity === "daily" ? dailyTrend : monthlyTrend;

  const hasAnyData = !!summary;
  const isZeroPeriod = hasAnyData && summary!.total_bookings === 0 && revenueSeries.length === 0;

  return (
    <div>
      <PageHeader title="Revenue Dashboard" />
      <ErrorBanner message={error} />

      <Card className="mb-6">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="revdash-date-from" className="block text-xs text-slate-500 mb-1">
              From
            </label>
            <input
              id="revdash-date-from"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="revdash-date-to" className="block text-xs text-slate-500 mb-1">
              To
            </label>
            <input
              id="revdash-date-to"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="revdash-property" className="block text-xs text-slate-500 mb-1">
              Property
            </label>
            <select
              id="revdash-property"
              value={propertyId}
              onChange={(e) => setPropertyId(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">All properties</option>
              {properties.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <Button onClick={load} disabled={loading}>
            {loading ? "Loading..." : "Apply Filters"}
          </Button>
        </div>
        <div className="flex gap-2 mt-3 pt-3 border-t border-slate-100">
          <span className="text-xs text-slate-400 self-center mr-1">Quick range:</span>
          <button
            onClick={() => {
              setDateFrom(today());
              setDateTo(today());
            }}
            className="text-xs rounded-lg border border-slate-300 px-3 py-1 text-slate-600 hover:bg-slate-50"
          >
            Today
          </button>
          <button
            onClick={() => {
              const d = new Date();
              const weekAgo = new Date(d);
              weekAgo.setDate(d.getDate() - 6);
              setDateFrom(weekAgo.toISOString().slice(0, 10));
              setDateTo(today());
            }}
            className="text-xs rounded-lg border border-slate-300 px-3 py-1 text-slate-600 hover:bg-slate-50"
          >
            This Week
          </button>
          <button
            onClick={() => {
              setDateFrom(firstOfMonth());
              setDateTo(today());
            }}
            className="text-xs rounded-lg border border-slate-300 px-3 py-1 text-slate-600 hover:bg-slate-50"
          >
            This Month
          </button>
          <button
            onClick={() => {
              const d = new Date();
              setDateFrom(`${d.getFullYear()}-01-01`);
              setDateTo(today());
            }}
            className="text-xs rounded-lg border border-slate-300 px-3 py-1 text-slate-600 hover:bg-slate-50"
          >
            This Year
          </button>
        </div>
      </Card>

      {loading && !hasAnyData && <Spinner label="Loading revenue dashboard..." />}

      {!loading && !hasAnyData && !error && (
        <EmptyState message="No revenue data available. Try adjusting the date range." />
      )}

      {hasAnyData && (
        <>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Revenue — {dateFrom} to {dateTo}
          </h2>
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Kpi label="Total Revenue" value={fmtCurrency(totalRevenue)} />
            <Kpi label="Bookings" value={summary!.total_bookings} />
            <Kpi label="Occupancy" value={fmtPercent(summary!.occupancy_rate_percent)} />
            <Kpi label="ADR" value={fmtCurrency(summary!.adr)} />
            <Kpi label="RevPAR" value={fmtCurrency(summary!.revpar)} />
          </div>
          <p className="text-xs text-slate-400 -mt-3 mb-6">
            Room/accommodation revenue (rate × nights + extra guest charge) — this system tracks no other revenue
            category, so Total Revenue above is entirely accommodation revenue.
          </p>

          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Cash Position</h2>
          <div className="mb-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card>
              <p className="text-xs text-slate-500 mb-1">Cash Collected (this period)</p>
              <p className="text-xl font-semibold text-slate-900">
                {cashFlow ? fmtCurrency(cashFlow.cash_in_total) : "—"}
              </p>
              {cashFlow && (
                <p className="text-xs text-slate-400 mt-1">
                  {fmtCurrency(cashFlow.cash_in_bank)} bank/UPI/card · {fmtCurrency(cashFlow.cash_in_hand)} cash
                  {cashFlow.cash_in_zotel_pending > 0 && (
                    <> · {fmtCurrency(cashFlow.cash_in_zotel_pending)} Zotel-reported (pending OTA payout reconciliation)</>
                  )}
                </p>
              )}
            </Card>
            <Card>
              <p className="text-xs text-slate-500 mb-1">Outstanding Guest Balance (all active bookings)</p>
              <p className="text-xl font-semibold text-slate-900">{fmtCurrency(totalOutstanding)}</p>
              <p className={`text-xs mt-1 ${overdueOutstanding > 0 ? "text-red-500" : "text-slate-400"}`}>
                {fmtCurrency(overdueOutstanding)} overdue · {outstandingLines.length} booking(s) with a balance, as of today
              </p>
            </Card>
          </div>
          <Card className="mb-6 bg-amber-50 border-amber-200">
            <p className="text-xs text-amber-800">
              Cash Collected and Outstanding Balance are Finance's existing cash-basis figures (by payment date, and a
              portfolio snapshot as of today) — they are <strong>not</strong> the same basis as the accrual Revenue
              total above (by check-in date), so the three numbers do not add up to one another. Cash Collected also
              cannot be filtered by property (a pre-existing Finance limitation).
            </p>
          </Card>

          {isZeroPeriod ? (
            <Card className="mb-6">
              <EmptyState message="No bookings or revenue recorded for this date range." />
            </Card>
          ) : (
            <>
              <Card className="mb-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-sm font-semibold text-slate-900">Revenue Trend</h2>
                  <div className="flex rounded-lg border border-slate-300 overflow-hidden text-xs">
                    {(["daily", "monthly"] as const).map((g) => (
                      <button
                        key={g}
                        onClick={() => setTrendGranularity(g)}
                        aria-pressed={trendGranularity === g}
                        className={`px-3 py-1.5 ${
                          trendGranularity === g ? "bg-brand-green text-white" : "bg-white text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        {g === "daily" ? "Daily" : "Monthly"}
                      </button>
                    ))}
                  </div>
                </div>
                {trendData.length === 0 ? (
                  <EmptyState message="No revenue recorded in this period." />
                ) : (
                  <ResponsiveContainer width="100%" height={260}>
                    <LineChart data={trendData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                      <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip formatter={(value: unknown) => fmtCurrency(Number(value))} />
                      <Line type="monotone" dataKey="revenue" stroke="#0f3d3a" strokeWidth={2} dot={trendGranularity === "monthly"} />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </Card>

              <Card className="mb-6">
                <h2 className="text-sm font-semibold text-slate-900 mb-1">Unit / Apartment Revenue Breakdown</h2>
                <p className="text-xs text-slate-400 mb-4">Ranked by revenue, highest to lowest, for the selected period.</p>
                {unitRows.length === 0 ? (
                  <EmptyState message="No unit revenue data available." />
                ) : (
                  <>
                    <ResponsiveContainer width="100%" height={240}>
                      <BarChart data={unitRows}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="unit_name" tick={{ fontSize: 10 }} interval={0} angle={-20} textAnchor="end" height={60} />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: unknown) => fmtCurrency(Number(value))} />
                        <Bar dataKey="revenue" fill="#0f3d3a" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                    <div className="mt-4 overflow-x-auto border border-slate-200 rounded-xl">
                      <table className="min-w-full text-sm">
                        <thead className="bg-slate-50 border-b border-slate-200">
                          <tr>
                            {["Unit", "Revenue", "% of Total", "Room-Nights", "Occupancy %", "ADR"].map((h) => (
                              <th key={h} scope="col" className="text-left px-4 py-2.5 font-medium text-slate-500">
                                {h}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {unitRows.map((u) => (
                            <tr key={u.unit_id}>
                              <th scope="row" className="px-4 py-2.5 text-left font-medium text-slate-800">
                                {u.unit_name}
                              </th>
                              <td className="px-4 py-2.5 text-slate-500">{fmtCurrency(u.revenue)}</td>
                              <td className="px-4 py-2.5 text-slate-500">{fmtPercent(u.percentOfTotal)}</td>
                              <td className="px-4 py-2.5 text-slate-500">{u.room_nights_sold}</td>
                              <td className="px-4 py-2.5 text-slate-500">{fmtPercent(u.occupancy_rate_percent)}</td>
                              <td className="px-4 py-2.5 text-slate-500">{fmtCurrency(u.adr)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </Card>

              <Card>
                <h2 className="text-sm font-semibold text-slate-900 mb-1">Revenue by Source</h2>
                <p className="text-xs text-slate-400 mb-4">
                  Shown per literal booking source rather than an OTA-vs-Direct grouping — see the note below.
                </p>
                {sourceRows.length === 0 ? (
                  <EmptyState message="No source-attributed revenue in this period." />
                ) : (
                  <div className="overflow-x-auto border border-slate-200 rounded-xl">
                    <table className="min-w-full text-sm">
                      <thead className="bg-slate-50 border-b border-slate-200">
                        <tr>
                          {["Source", "Revenue", "% of Total", ""].map((h) => (
                            <th key={h} scope="col" className="text-left px-4 py-2.5 font-medium text-slate-500">
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {sourceRows.map((row) => (
                          <tr key={row.source}>
                            <th scope="row" className="px-4 py-2.5 text-left font-medium text-slate-800 capitalize">
                              {row.source.replace(/_/g, " ")}
                            </th>
                            <td className="px-4 py-2.5 text-slate-500">{fmtCurrency(row.amount)}</td>
                            <td className="px-4 py-2.5 text-slate-500">{fmtPercent(row.percent)}</td>
                            <td className="px-4 py-2.5">
                              <div className="h-2 w-32 rounded-full bg-slate-100 overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-brand-green"
                                  style={{ width: `${Math.min(row.percent, 100)}%` }}
                                />
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="text-xs text-slate-400 mt-3">
                  A reliable OTA-vs-Direct split isn't shown because Booking source and OTA commission channel are
                  independently recorded and don't always match (e.g. a Zotel-imported booking is sourced "zotel",
                  not a per-OTA-channel label) — see Finance → OTA Reconciliation for channel-level commission data.
                </p>
              </Card>
            </>
          )}
        </>
      )}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className="text-xl font-semibold text-slate-900">{value}</p>
    </Card>
  );
}
