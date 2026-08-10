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
  type SummaryComparison,
  type MetricComparison,
  type CancellationReport,
  type RevenuePoint,
  type UnitPerformance,
  type PropertyOut,
  type SalesFunnelReport,
} from "../lib/api";
import { Card, PageHeader, ErrorBanner, Button, EmptyState } from "../components/ui";

function firstOfMonth(): string {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}
function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysBetween(dateFromStr: string, dateToStr: string): number {
  const from = new Date(dateFromStr);
  const to = new Date(dateToStr);
  return Math.max(Math.round((to.getTime() - from.getTime()) / 86_400_000), 1);
}

// Same N-day preceding-period convention as Phase 10A-3's build_summary_comparison().
function computePreviousRange(dateFromStr: string, dateToStr: string): { prevFrom: string; prevTo: string } {
  const n = daysBetween(dateFromStr, dateToStr);
  const from = new Date(dateFromStr);
  from.setDate(from.getDate() - n);
  return { prevFrom: from.toISOString().slice(0, 10), prevTo: dateFromStr };
}

// Anchor far enough in the past to capture every booking ever recorded.
// Deliberately NOT used as a basis for Occupancy %/RevPAR (see ScorecardPeriod
// = "all" handling below) — Unit has no inception date, so a fixed decades-wide
// anchor would silently dilute those two percentage-based metrics.
const ALL_TIME_FROM = "2000-01-01";
function allTimeTo(): string {
  return new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
}

function revparFor(revenue: number, days: number): number | null {
  if (!days || days <= 0) return null;
  const v = revenue / days;
  return Number.isFinite(v) ? v : null;
}

function fmtRevPar(v: number | null): string {
  if (v === null) return "—";
  return `₹${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

type ScorecardPeriod = "this" | "last" | "all";
type ScorecardSortBy = "revenue" | "occupancy" | "adr" | "revpar";

interface ScorecardRow {
  unit_id: string;
  unit_name: string;
  revenue: number;
  occupancy_rate_percent: number | null;
  adr: number;
  room_nights_sold: number;
  revpar: number | null;
  trend: "up" | "down" | "flat" | null;
}

// Reuses the existing /reports/unit-performance rows verbatim (current,
// previous, or all-time — whichever the active tab needs) — no new formula,
// just re-presenting and re-ranking data OS10 already computes. RevPAR is
// the only derived value, using the same revenue/days_in_range identity
// build_summary() already uses at portfolio scale, applied per unit.
function buildScorecardRows(
  period: ScorecardPeriod,
  current: UnitPerformance[],
  previous: UnitPerformance[],
  allTime: UnitPerformance[],
  periodDays: number
): ScorecardRow[] {
  if (period === "all") {
    return allTime.map((u) => ({
      unit_id: u.unit_id,
      unit_name: u.unit_name,
      revenue: u.revenue,
      occupancy_rate_percent: null,
      adr: u.adr,
      room_nights_sold: u.room_nights_sold,
      revpar: null,
      trend: null,
    }));
  }

  const source = period === "this" ? current : previous;
  const previousByUnit = new Map(previous.map((u) => [u.unit_id, u]));

  return source.map((u) => {
    let trend: ScorecardRow["trend"] = null;
    if (period === "this") {
      const prevRevenue = previousByUnit.get(u.unit_id)?.revenue ?? 0;
      trend = u.revenue > prevRevenue ? "up" : u.revenue < prevRevenue ? "down" : "flat";
    }
    return {
      unit_id: u.unit_id,
      unit_name: u.unit_name,
      revenue: u.revenue,
      occupancy_rate_percent: u.occupancy_rate_percent,
      adr: u.adr,
      room_nights_sold: u.room_nights_sold,
      revpar: revparFor(u.revenue, periodDays),
      trend,
    };
  });
}

export default function ReportsPage() {
  const [dateFrom, setDateFrom] = useState(firstOfMonth());
  const [dateTo, setDateTo] = useState(today());
  const [propertyId, setPropertyId] = useState("");
  const [properties, setProperties] = useState<PropertyOut[]>([]);

  const [summary, setSummary] = useState<SummaryReport | null>(null);
  const [comparison, setComparison] = useState<SummaryComparison | null>(null);
  const [cancellation, setCancellation] = useState<CancellationReport | null>(null);
  const [revenueSeries, setRevenueSeries] = useState<RevenuePoint[]>([]);
  const [unitPerformance, setUnitPerformance] = useState<UnitPerformance[]>([]);
  const [unitPerformancePrevious, setUnitPerformancePrevious] = useState<UnitPerformance[]>([]);
  const [unitPerformanceAllTime, setUnitPerformanceAllTime] = useState<UnitPerformance[]>([]);
  const [salesFunnel, setSalesFunnel] = useState<SalesFunnelReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [scorecardPeriod, setScorecardPeriod] = useState<ScorecardPeriod>("this");
  const [scorecardSortBy, setScorecardSortBy] = useState<ScorecardSortBy>("revenue");

  async function loadReports() {
    setError(null);
    setLoading(true);
    try {
      const params: Record<string, string> = { date_from: dateFrom, date_to: dateTo };
      if (propertyId) params.property_id = propertyId;

      const { prevFrom, prevTo } = computePreviousRange(dateFrom, dateTo);
      const prevParams: Record<string, string> = { date_from: prevFrom, date_to: prevTo };
      if (propertyId) prevParams.property_id = propertyId;
      const allTimeParams: Record<string, string> = { date_from: ALL_TIME_FROM, date_to: allTimeTo() };
      if (propertyId) allTimeParams.property_id = propertyId;

      const [s, c, cx, r, u, uPrev, uAll] = await Promise.all([
        api.get("/reports/summary", { params }),
        api.get("/reports/summary-comparison", { params }),
        api.get("/reports/cancellations", { params }),
        api.get("/reports/revenue-series", { params }),
        api.get("/reports/unit-performance", { params }),
        api.get("/reports/unit-performance", { params: prevParams }),
        api.get("/reports/unit-performance", { params: allTimeParams }),
      ]);
      setSummary(s.data);
      setComparison(c.data);
      setCancellation(cx.data);
      setRevenueSeries(r.data);
      setUnitPerformance(u.data);
      setUnitPerformancePrevious(uPrev.data);
      setUnitPerformanceAllTime(uAll.data);

      // Sales funnel doesn't take a property filter (it's lead-based, not booking-based)
      const funnelParams = { date_from: dateFrom, date_to: dateTo };
      const funnel = await api.get("/reports/sales-funnel", { params: funnelParams });
      setSalesFunnel(funnel.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    api.get("/properties").then((r) => setProperties(r.data)).catch(() => {});
    loadReports();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleExport(report: "summary" | "unit_performance" | "gst" | "guest_requests" | "maintenance" | "housekeeping" | "inventory" | "enquiries" | "guests", format: "pdf" | "xlsx") {
    setError(null);
    setExporting(`${report}-${format}`);
    try {
      const params: Record<string, string> = { report, format, date_from: dateFrom, date_to: dateTo };
      if (propertyId) params.property_id = propertyId;
      const res = await api.get("/reports/export", { params, responseType: "blob" });
      const contentType = format === "pdf" ? "application/pdf" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
      const url = window.URL.createObjectURL(new Blob([res.data], { type: contentType }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${report}_${dateFrom}_${dateTo}.${format}`;
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setExporting(null);
    }
  }

  async function handleFinanceExport(report: "cashbook" | "expenses" | "purchases" | "utility" | "salary", format: "pdf" | "xlsx") {
    setError(null);
    setExporting(`${report}-${format}`);
    try {
      const params: Record<string, string> = { report, format, date_from: dateFrom, date_to: dateTo };
      const res = await api.get("/finance/reports/export", { params, responseType: "blob" });
      const contentType = format === "pdf" ? "application/pdf" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
      const url = window.URL.createObjectURL(new Blob([res.data], { type: contentType }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${report}_${dateFrom}_${dateTo}.${format}`;
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setExporting(null);
    }
  }

  const periodDays = daysBetween(dateFrom, dateTo);
  const scorecardPrevRange = computePreviousRange(dateFrom, dateTo);
  const scorecardRows = buildScorecardRows(scorecardPeriod, unitPerformance, unitPerformancePrevious, unitPerformanceAllTime, periodDays);
  // Occupancy/RevPAR aren't shown in All-Time mode, so those sort keys aren't
  // meaningful there — fall back to Revenue rather than sorting by nulls.
  const effectiveSortBy: ScorecardSortBy =
    scorecardPeriod === "all" && (scorecardSortBy === "occupancy" || scorecardSortBy === "revpar") ? "revenue" : scorecardSortBy;
  const scorecardSortKey: Record<ScorecardSortBy, keyof ScorecardRow> = {
    revenue: "revenue", occupancy: "occupancy_rate_percent", adr: "adr", revpar: "revpar",
  };
  const sortedScorecardRows = [...scorecardRows].sort((a, b) => {
    const key = scorecardSortKey[effectiveSortBy];
    const av = (a[key] as number | null) ?? -Infinity;
    const bv = (b[key] as number | null) ?? -Infinity;
    return bv - av;
  });

  return (
    <div>
      <PageHeader title="Reports Center" />
      <ErrorBanner message={error} />

      <Card className="mb-6">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1">From</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">To</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Property</label>
            <select
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
          <Button onClick={loadReports} disabled={loading}>
            {loading ? "Loading..." : "Apply Filters"}
          </Button>
        </div>
        <div className="flex gap-2 mt-3 pt-3 border-t border-slate-100">
          <span className="text-xs text-slate-400 self-center mr-1">Quick range:</span>
          <button
            onClick={() => { setDateFrom(today()); setDateTo(today()); }}
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
            onClick={() => { setDateFrom(firstOfMonth()); setDateTo(today()); }}
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

      {summary && (
        <>
          <div id="printable-summary-report">
          <div className="flex items-center justify-between mb-3 print:hidden">
            <h2 className="text-sm font-semibold text-slate-900">Owner Dashboard — {dateFrom} to {dateTo}</h2>
            <Button variant="secondary" onClick={() => window.print()}>Print</Button>
          </div>
          <h2 className="hidden print:block text-sm font-semibold text-slate-900 mb-3">Owner Dashboard — {dateFrom} to {dateTo}</h2>
          <div className="grid grid-cols-4 gap-4 mb-4">
            <Kpi label="Revenue" value={`₹${summary.revenue.total.toLocaleString()}`} />
            <Kpi
              label="Occupancy"
              value={`${summary.occupancy_rate_percent}%`}
              sub={comparison ? formatComparison(comparison.occupancy, { suffix: "%", percentagePoints: true }) : undefined}
            />
            <Kpi
              label="ADR"
              value={`₹${summary.adr.toLocaleString()}`}
              sub={comparison ? formatComparison(comparison.adr, { currency: true }) : undefined}
            />
            <Kpi
              label="RevPAR"
              value={`₹${summary.revpar.toLocaleString()}`}
              sub={comparison ? formatComparison(comparison.revpar, { currency: true }) : undefined}
            />
          </div>
          {comparison && (
            <p className="text-xs text-slate-400 -mt-2 mb-4">
              Compared to previous period: {comparison.previous_period.date_from} to {comparison.previous_period.date_to}
            </p>
          )}
          <div className="grid grid-cols-4 gap-4 mb-6">
            <Kpi label="Total Bookings" value={summary.total_bookings} />
            <Kpi label="Room Nights Sold" value={summary.room_nights_sold} />
            <Kpi label="GST Collected" value={`₹${summary.gst.total.toLocaleString()}`} />
            <Kpi label="Net Rev. After Maintenance" value={`₹${summary.net_revenue_after_maintenance.toLocaleString()}`} />
          </div>

          <Card className="mb-4 bg-amber-50 border-amber-200">
            <p className="text-xs text-amber-800">
              "Net Revenue After Maintenance" is revenue minus recorded maintenance costs only — this system doesn't
              yet track general operating expenses (rent, utilities, wages), so it is <strong>not</strong> a full P&amp;L.
            </p>
          </Card>

          {cancellation && (
            <Card className="mb-6">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-slate-900">Cancellations</h2>
                <span className="text-xs text-slate-400">
                  vs. {cancellation.previous_period.date_from} to {cancellation.previous_period.date_to}
                </span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div>
                  <p className="text-xs text-slate-500 mb-1">Cancellation Rate</p>
                  <p className="text-xl font-semibold text-slate-900">{cancellation.cancellation_rate_percent}%</p>
                  <p className="text-xs text-slate-400 mt-1">
                    {formatRateChange(cancellation.cancellation_rate_percent, cancellation.previous_cancellation_rate_percent, cancellation.cancellation_rate_change_percent)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">Cancelled / Total</p>
                  <p className="text-xl font-semibold text-slate-900">
                    {cancellation.cancelled_count} / {cancellation.total_bookings_in_period}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">
                    Previous: {cancellation.previous_cancelled_count} / {cancellation.previous_total_bookings_in_period}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">No-Show Rate</p>
                  <p className="text-xl font-semibold text-slate-900">{cancellation.no_show_rate_percent}%</p>
                  <p className="text-xs text-slate-400 mt-1">
                    {formatRateChange(cancellation.no_show_rate_percent, cancellation.previous_no_show_rate_percent, cancellation.no_show_rate_change_percent)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">No-Shows</p>
                  <p className="text-xl font-semibold text-slate-900">{cancellation.no_show_count}</p>
                  <p className="text-xs text-slate-400 mt-1">Previous: {cancellation.previous_no_show_count}</p>
                </div>
              </div>
              <p className="text-xs text-slate-400 mt-3">
                Of firm bookings (confirmed, checked-in, checked-out, cancelled, or no-show) scheduled to check in
                during this period — not cancellations recorded during the period, since OS10 doesn't track a
                cancellation timestamp. "Inquiry" holds are excluded from these counts.
              </p>
            </Card>
          )}
          </div>
        </>
      )}

      <div className="grid grid-cols-2 gap-6 mb-6">
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Revenue by Day</h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={revenueSeries}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Line type="monotone" dataKey="revenue" stroke="#0f172a" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Revenue by Unit</h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={unitPerformance}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="unit_name" tick={{ fontSize: 10 }} interval={0} angle={-20} textAnchor="end" height={60} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="revenue" fill="#0f172a" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      {salesFunnel && (
        <Card className="mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-slate-900">Sales Funnel (Inquiries → Bookings)</h2>
            <span className="text-xs text-slate-500">
              {salesFunnel.total_inquiries} leads · {salesFunnel.conversion_rate_percent}% conversion
            </span>
          </div>
          <div className="grid grid-cols-3 gap-6">
            <div className="col-span-2">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={salesFunnel.stages} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                  <YAxis
                    type="category"
                    dataKey="lead_status"
                    tick={{ fontSize: 11 }}
                    width={80}
                    tickFormatter={(v: string) => v.replace("_", " ")}
                  />
                  <Tooltip />
                  <Bar dataKey="count" fill="#b08d3e" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="space-y-3">
              <div>
                <p className="text-xs text-slate-500">Converted</p>
                <p className="text-lg font-semibold text-emerald-600">{salesFunnel.converted_count}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Lost</p>
                <p className="text-lg font-semibold text-red-500">{salesFunnel.lost_count}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Total Quoted Value</p>
                <p className="text-sm font-medium text-slate-800">₹{salesFunnel.total_quoted_value.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Converted Value</p>
                <p className="text-sm font-medium text-slate-800">₹{salesFunnel.total_converted_value.toLocaleString()}</p>
              </div>
            </div>
          </div>
          <p className="text-xs text-slate-400 mt-3">
            Based on when each inquiry was created (not booking-property-filtered), for the same date range as above.
          </p>
        </Card>
      )}

      <Card className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Export Reports</h2>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => handleExport("summary", "pdf")} disabled={exporting === "summary-pdf"}>
            {exporting === "summary-pdf" ? "Exporting..." : "Summary (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("summary", "xlsx")} disabled={exporting === "summary-xlsx"}>
            {exporting === "summary-xlsx" ? "Exporting..." : "Summary (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("unit_performance", "pdf")} disabled={exporting === "unit_performance-pdf"}>
            {exporting === "unit_performance-pdf" ? "Exporting..." : "Unit Performance (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("unit_performance", "xlsx")} disabled={exporting === "unit_performance-xlsx"}>
            {exporting === "unit_performance-xlsx" ? "Exporting..." : "Unit Performance (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("gst", "xlsx")} disabled={exporting === "gst-xlsx"}>
            {exporting === "gst-xlsx" ? "Exporting..." : "GST Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("guest_requests", "pdf")} disabled={exporting === "guest_requests-pdf"}>
            {exporting === "guest_requests-pdf" ? "Exporting..." : "Guest Requests (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("guest_requests", "xlsx")} disabled={exporting === "guest_requests-xlsx"}>
            {exporting === "guest_requests-xlsx" ? "Exporting..." : "Guest Requests (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("maintenance", "pdf")} disabled={exporting === "maintenance-pdf"}>
            {exporting === "maintenance-pdf" ? "Exporting..." : "Maintenance Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("maintenance", "xlsx")} disabled={exporting === "maintenance-xlsx"}>
            {exporting === "maintenance-xlsx" ? "Exporting..." : "Maintenance Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("housekeeping", "pdf")} disabled={exporting === "housekeeping-pdf"}>
            {exporting === "housekeeping-pdf" ? "Exporting..." : "Housekeeping Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("housekeeping", "xlsx")} disabled={exporting === "housekeeping-xlsx"}>
            {exporting === "housekeeping-xlsx" ? "Exporting..." : "Housekeeping Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("inventory", "pdf")} disabled={exporting === "inventory-pdf"}>
            {exporting === "inventory-pdf" ? "Exporting..." : "Inventory Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("inventory", "xlsx")} disabled={exporting === "inventory-xlsx"}>
            {exporting === "inventory-xlsx" ? "Exporting..." : "Inventory Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("enquiries", "pdf")} disabled={exporting === "enquiries-pdf"}>
            {exporting === "enquiries-pdf" ? "Exporting..." : "Enquiries Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("enquiries", "xlsx")} disabled={exporting === "enquiries-xlsx"}>
            {exporting === "enquiries-xlsx" ? "Exporting..." : "Enquiries Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("guests", "pdf")} disabled={exporting === "guests-pdf"}>
            {exporting === "guests-pdf" ? "Exporting..." : "Guest Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleExport("guests", "xlsx")} disabled={exporting === "guests-xlsx"}>
            {exporting === "guests-xlsx" ? "Exporting..." : "Guest Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("cashbook", "pdf")} disabled={exporting === "cashbook-pdf"}>
            {exporting === "cashbook-pdf" ? "Exporting..." : "Cash Book (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("cashbook", "xlsx")} disabled={exporting === "cashbook-xlsx"}>
            {exporting === "cashbook-xlsx" ? "Exporting..." : "Cash Book (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("expenses", "pdf")} disabled={exporting === "expenses-pdf"}>
            {exporting === "expenses-pdf" ? "Exporting..." : "Expense Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("expenses", "xlsx")} disabled={exporting === "expenses-xlsx"}>
            {exporting === "expenses-xlsx" ? "Exporting..." : "Expense Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("purchases", "pdf")} disabled={exporting === "purchases-pdf"}>
            {exporting === "purchases-pdf" ? "Exporting..." : "Purchase Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("purchases", "xlsx")} disabled={exporting === "purchases-xlsx"}>
            {exporting === "purchases-xlsx" ? "Exporting..." : "Purchase Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("utility", "pdf")} disabled={exporting === "utility-pdf"}>
            {exporting === "utility-pdf" ? "Exporting..." : "Utility Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("utility", "xlsx")} disabled={exporting === "utility-xlsx"}>
            {exporting === "utility-xlsx" ? "Exporting..." : "Utility Report (Excel)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("salary", "pdf")} disabled={exporting === "salary-pdf"}>
            {exporting === "salary-pdf" ? "Exporting..." : "Salary Report (PDF)"}
          </Button>
          <Button variant="secondary" onClick={() => handleFinanceExport("salary", "xlsx")} disabled={exporting === "salary-xlsx"}>
            {exporting === "salary-xlsx" ? "Exporting..." : "Salary Report (Excel)"}
          </Button>
        </div>
        <p className="text-xs text-slate-400 mt-3">
          Apartment P&L reports (per-unit, with revenue/expense/profit breakdown) are exportable from Finance → Apartment P&L, since they need a specific unit and month selected first.
        </p>
      </Card>

      <Card className="mb-6">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-slate-900">Unit Scorecard</h2>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex rounded-lg border border-slate-300 overflow-hidden text-xs">
              {(["this", "last", "all"] as const).map((p) => (
                <button
                  key={p}
                  onClick={() => setScorecardPeriod(p)}
                  className={`px-3 py-1.5 ${scorecardPeriod === p ? "bg-slate-900 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}
                >
                  {p === "this" ? "This Period" : p === "last" ? "Last Period" : "All Time"}
                </button>
              ))}
            </div>
            <select
              value={effectiveSortBy}
              onChange={(e) => setScorecardSortBy(e.target.value as ScorecardSortBy)}
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
            >
              <option value="revenue">Sort: Revenue</option>
              {scorecardPeriod !== "all" && <option value="occupancy">Sort: Occupancy</option>}
              <option value="adr">Sort: ADR</option>
              {scorecardPeriod !== "all" && <option value="revpar">Sort: RevPAR</option>}
            </select>
          </div>
        </div>
        <p className="text-xs text-slate-400 mb-3">
          {scorecardPeriod === "this" &&
            `Ranked by ${effectiveSortBy === "revenue" ? "revenue" : effectiveSortBy}, ${dateFrom} to ${dateTo}. Trend compares against the immediately preceding ${periodDays}-day period (${scorecardPrevRange.prevFrom} to ${scorecardPrevRange.prevTo}).`}
          {scorecardPeriod === "last" &&
            `Ranked by ${effectiveSortBy === "revenue" ? "revenue" : effectiveSortBy} for the preceding period: ${scorecardPrevRange.prevFrom} to ${scorecardPrevRange.prevTo}.`}
          {scorecardPeriod === "all" &&
            "Ranked by revenue across all recorded history. Occupancy % and RevPAR aren't shown here — OS10 has no record of when each unit became available, so an all-time percentage would be misleading."}
        </p>
        {sortedScorecardRows.length === 0 ? (
          <EmptyState message="No unit data available." />
        ) : (
          <div className="overflow-x-auto border border-slate-200 rounded-xl">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {[
                    "Rank",
                    "Unit",
                    "Revenue",
                    ...(scorecardPeriod === "all" ? ["Room-Nights", "ADR"] : ["Occupancy %", "ADR", "RevPAR"]),
                    ...(scorecardPeriod === "this" ? ["Trend"] : []),
                  ].map((h) => (
                    <th key={h} className="text-left px-4 py-2.5 font-medium text-slate-500">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sortedScorecardRows.map((row, i) => (
                  <tr key={row.unit_id}>
                    <td className="px-4 py-2.5 text-slate-400">{i + 1}</td>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{row.unit_name}</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{row.revenue.toLocaleString()}</td>
                    {scorecardPeriod === "all" ? (
                      <>
                        <td className="px-4 py-2.5 text-slate-500">{row.room_nights_sold}</td>
                        <td className="px-4 py-2.5 text-slate-500">₹{row.adr.toLocaleString()}</td>
                      </>
                    ) : (
                      <>
                        <td className="px-4 py-2.5 text-slate-500">{row.occupancy_rate_percent}%</td>
                        <td className="px-4 py-2.5 text-slate-500">₹{row.adr.toLocaleString()}</td>
                        <td className="px-4 py-2.5 text-slate-500">{fmtRevPar(row.revpar)}</td>
                      </>
                    )}
                    {scorecardPeriod === "this" && (
                      <td className="px-4 py-2.5">
                        {row.trend === "up" && <span className="text-emerald-600" title="Revenue up vs. last period">▲</span>}
                        {row.trend === "down" && <span className="text-red-600" title="Revenue down vs. last period">▼</span>}
                        {row.trend === "flat" && <span className="text-slate-400" title="Revenue unchanged vs. last period">→</span>}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Unit Performance Detail</h2>
        {unitPerformance.length === 0 ? (
          <EmptyState message="No report data available for this period." />
        ) : (
          <div className="overflow-x-auto border border-slate-200 rounded-xl">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Unit", "Nights Sold", "Revenue", "Occupancy %", "ADR"].map((h) => (
                    <th key={h} className="text-left px-4 py-2.5 font-medium text-slate-500">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {unitPerformance.map((u) => (
                  <tr key={u.unit_id}>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{u.unit_name}</td>
                    <td className="px-4 py-2.5 text-slate-500">{u.room_nights_sold}</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{u.revenue.toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-slate-500">{u.occupancy_rate_percent}%</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{u.adr.toLocaleString()}</td>
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

function Kpi({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <Card>
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className="text-xl font-semibold text-slate-900">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
    </Card>
  );
}

function formatComparison(
  m: MetricComparison,
  opts: { currency?: boolean; suffix?: string; percentagePoints?: boolean } = {}
): string {
  const format = (v: number) => (opts.currency ? `₹${v.toLocaleString()}` : `${v}${opts.suffix ?? ""}`);
  const arrow = m.current > m.previous ? "▲" : m.current < m.previous ? "▼" : "→";
  const prevLabel = `${format(m.previous)} prev.`;

  if (opts.percentagePoints && m.change_percentage_points != null) {
    const pts = m.change_percentage_points;
    return `${arrow} ${pts >= 0 ? "+" : ""}${pts} pts vs ${prevLabel}`;
  }
  if (m.change_percent == null) {
    return `${arrow} vs ${prevLabel} — no prior data`;
  }
  return `${arrow} ${m.change_percent >= 0 ? "+" : ""}${m.change_percent}% vs ${prevLabel}`;
}

function formatRateChange(current: number, previous: number, changePercent: number | null): string {
  const arrow = current > previous ? "▲" : current < previous ? "▼" : "→";
  if (changePercent == null) {
    return `${arrow} vs ${previous}% prev. — no prior data`;
  }
  return `${arrow} ${changePercent >= 0 ? "+" : ""}${changePercent}% vs ${previous}% prev.`;
}
