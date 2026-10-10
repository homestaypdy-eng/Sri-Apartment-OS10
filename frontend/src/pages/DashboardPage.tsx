import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart, ReferenceDot, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import {
  api, extractErrorMessage, type OperationalDashboard, type DashboardCalendar,
  type BookingOut, type GuestOut, type UnitOut, type AttentionItem,
} from "../lib/api";
import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner, Table } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useRealtimeRefresh } from "../lib/realtime";

const RECENT_BOOKINGS_LIMIT = 6;
const INITIAL_ALERT_LIMIT = 3;
const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const QUICK_ACTIONS = [
  { label: "New booking", to: "/bookings", variant: "primary" as const },
  { label: "Record payment", to: "/billing", variant: "secondary" as const },
  { label: "Add guest", to: "/guests", variant: "secondary" as const },
  { label: "Manage properties", to: "/properties", variant: "secondary" as const },
  { label: "Raise maintenance ticket", to: "/maintenance", variant: "secondary" as const },
  { label: "Add housekeeping task", to: "/housekeeping", variant: "secondary" as const },
  { label: "New enquiry", to: "/inquiries", variant: "secondary" as const },
];

type AttentionNotice = {
  id: string;
  severity: "critical" | "warning" | "info";
  title: string;
  description?: string;
  to: string;
};

function bookingStatusTone(status: string): "green" | "amber" | "slate" {
  if (status === "confirmed") return "green";
  if (status === "checked_in") return "amber";
  return "slate";
}

function formatCurrency(value: number) {
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

// Explicit "en-GB" locale everywhere on this page — the Dashboard's own
// formatting, not a global app setting — so weekday/month names are always
// English regardless of the browser/OS locale (this previously fell back to
// the system locale via `undefined`, which produced French text such as
// "dimanche 27 septembre 2026" on a fr-* machine).
// Locale-independent formatting: rather than trust one locale's assembled
// string (en-GB gives day-before-month order but a 4-letter "Sept"; en-US
// gives the 3-letter "Sep" but month-before-day order with a comma), the
// weekday/month NAMES are pulled from a fixed "en-US" Intl formatter (always
// 3-letter month, always English regardless of the browser/OS locale) and
// assembled into the exact "Weekday, D Mon YYYY" order this page wants.
const monthShortFormatter = new Intl.DateTimeFormat("en-US", { month: "short" });
const monthLongFormatter = new Intl.DateTimeFormat("en-US", { month: "long" });
const weekdayLongFormatter = new Intl.DateTimeFormat("en-US", { weekday: "long" });

function formatDashboardDate(asOf: string) {
  const [year, month, day] = asOf.split("-").map(Number);
  const date = new Date(year, month - 1, day, 12);
  return `${weekdayLongFormatter.format(date)}, ${day} ${monthShortFormatter.format(date)} ${year}`;
}

function formatShortDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return `${day} ${monthShortFormatter.format(new Date(year, month - 1, 1))}`;
}

function labelForMonth(year: number, month: number) {
  return monthShortFormatter.format(new Date(year, month - 1, 1));
}

function labelForMonthYear(year: number, month: number) {
  return `${monthShortFormatter.format(new Date(year, month - 1, 1))} ${year}`;
}

function createNotices(data: OperationalDashboard): AttentionNotice[] {
  const notices: AttentionNotice[] = data.attention_items.map((item: AttentionItem) => ({
    id: `${item.category}-${item.reference_id}`,
    severity: item.severity,
    title: item.title,
    description: `${item.reason} ${item.action}`,
    to: item.link,
  }));

  // These counts are contextual operational reminders, not critical alerts.
  if (data.follow_ups_due_today > 0) notices.push({
    id: "follow-ups", severity: "info", title: `${data.follow_ups_due_today} enquiry follow-up(s) due today`, to: "/inquiries",
  });
  if (data.staff_absent_today > 0) notices.push({
    id: "staff-absent", severity: "info", title: `${data.staff_absent_today} staff member(s) absent today`, to: "/staff",
  });
  return notices;
}

function MetricIcon({ kind }: { kind: "occupancy" | "revenue" | "bookings" | "units" | "payments" }) {
  const paths: Record<typeof kind, React.ReactNode> = {
    occupancy: <><path d="M3 20h18M5 20V9l7-5 7 5v11" /><path d="M9 20v-6h6v6" /></>,
    revenue: <><path d="M12 2v20M17 5.5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6" /></>,
    bookings: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /></>,
    units: <><path d="M3 21h18M5 21V5a2 2 0 012-2h10a2 2 0 012 2v16M9 7h2m2 0h2m-6 4h2m2 0h2m-6 4h2m2 0h2" /></>,
    payments: <><rect x="2" y="5" width="20" height="14" rx="2" /><path d="M2 10h20M7 15h3" /></>,
  };
  return <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-[10px] ${kind === "payments" ? "bg-amber-50 text-amber-700" : "bg-brand-green/[0.06] text-brand-green"}`} aria-hidden="true">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{paths[kind]}</svg>
  </span>;
}

export default function DashboardPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [data, setData] = useState<OperationalDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [calendar, setCalendar] = useState<DashboardCalendar | null>(null);
  const [calYear, setCalYear] = useState(new Date().getFullYear());
  const [calMonth, setCalMonth] = useState(new Date().getMonth() + 1);
  const [showAllNotices, setShowAllNotices] = useState(false);
  const [recentBookings, setRecentBookings] = useState<BookingOut[]>([]);
  const [guests, setGuests] = useState<GuestOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [recentError, setRecentError] = useState<string | null>(null);

  const load = useCallback(async (isBackground = false) => {
    try {
      const response = await api.get("/reports/dashboard");
      setData(response.data);
      setLastRefreshed(new Date());
      setError(null);
    } catch (err) {
      if (!isBackground) setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRecent = useCallback(async () => {
    try {
      const [bookingsResponse, guestsResponse, unitsResponse] = await Promise.all([api.get("/bookings"), api.get("/guests"), api.get("/units")]);
      setRecentBookings(bookingsResponse.data);
      setGuests(guestsResponse.data);
      setUnits(unitsResponse.data);
      setRecentError(null);
    } catch (err) {
      setRecentError(extractErrorMessage(err));
    } finally {
      setRecentLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useRealtimeRefresh(
    ["bookings.changed", "availability.changed", "guests.changed", "payments.changed", "finance.changed", "properties.changed", "notifications.changed"],
    () => {
      void load(true);
      void loadRecent();
      void api.get("/reports/dashboard/calendar", { params: { period_year: calYear, period_month: calMonth } })
        .then(({ data: responseData }) => setCalendar(responseData))
        .catch(() => setCalendar(null));
    },
  );

  useEffect(() => {
    api.get("/reports/dashboard/calendar", { params: { period_year: calYear, period_month: calMonth } })
      .then(({ data: responseData }) => setCalendar(responseData))
      .catch(() => setCalendar(null));
  }, [calYear, calMonth]);

  useEffect(() => { void loadRecent(); }, [loadRecent]);

  const topRecentBookings = useMemo(() => [...recentBookings]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, RECENT_BOOKINGS_LIMIT), [recentBookings]);

  const currentMonthLabel = useMemo(() => {
    if (!data) return "";
    const [year, month] = data.as_of.split("-").map(Number);
    return labelForMonthYear(year, month);
  }, [data]);
  const notices = useMemo(() => data ? createNotices(data) : [], [data]);
  const visibleNotices = showAllNotices ? notices : notices.slice(0, INITIAL_ALERT_LIMIT);
  const revenueTrend = useMemo(() => data?.monthly_trend.map((point) => ({
    ...point, month_label: labelForMonth(point.year, point.month),
  })) ?? [], [data]);
  const occupancyByUnit = useMemo(() => data?.apartment_performance.slice(0, 8) ?? [], [data]);

  function guestName(id: string) { return guests.find((guest) => guest.id === id)?.name ?? "—"; }
  function unitName(id: string) { return units.find((unit) => unit.id === id)?.name ?? "—"; }

  if (loading) return <div><Spinner label="Loading dashboard..." /></div>;
  if (!data) return <div><ErrorBanner message={error} /><EmptyState message="Dashboard data is not available right now." action={<Button onClick={() => { setLoading(true); void load(); }}>Try again</Button>} /></div>;

  return (
    <div className="mx-auto w-full max-w-[1600px] space-y-7 pb-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brand-green-2">Sri Apartment OS</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 sm:text-[28px]">Dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">{formatDashboardDate(data.as_of)} <span className="px-1 text-slate-300">·</span> Live operational overview</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {lastRefreshed && <p className="text-xs text-slate-500" aria-live="polite">Updated {lastRefreshed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · refreshes every 20 sec</p>}
          <Link to="/notifications" aria-label="Open notifications" className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-green">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/></svg>
          </Link>
          <div className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-white py-1.5 pl-1.5 pr-3 shadow-sm" aria-label={`Signed in as ${role ?? "user"}`}>
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-green text-xs font-semibold uppercase text-white" aria-hidden="true">{(role ?? "U").slice(0, 1)}</span>
            <span className="text-sm font-medium capitalize text-slate-700">{role ?? "User"}</span>
          </div>
        </div>
      </header>

      <ErrorBanner message={error} />

      <section aria-labelledby="kpi-heading">
        <div className="mb-3 flex items-center justify-between">
          <h2 id="kpi-heading" className="text-sm font-semibold text-slate-800">Business snapshot</h2>
          <span className="text-xs text-slate-500">As of {data.as_of}</span>
        </div>
        <div className={`grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 ${isAdmin ? "xl:grid-cols-6" : "xl:grid-cols-4"}`}>
          <MetricCard label="Today's occupancy" value={`${data.occupancy_percent}%`} context={`${data.total_units - data.vacant_units} / ${data.total_units} units occupied today`} icon="occupancy" />
          {isAdmin && <MetricCard label="Today's revenue" value={formatCurrency(data.today_revenue)} context="Current day · operating total" icon="revenue" />}
          {isAdmin && <MetricCard label="Monthly revenue" value={formatCurrency(data.month_revenue)} context={`Month to date · ${currentMonthLabel}`} icon="revenue" />}
          <MetricCard label="Active bookings" value={data.active_bookings.toLocaleString()} context="Confirmed and in-house" icon="bookings" to="/bookings" />
          <MetricCard label="Available units" value={`${data.vacant_units.toLocaleString()} / ${data.total_units.toLocaleString()}`} context="units available" icon="units" to="/properties" />
          <MetricCard label="Pending payments" value={data.pending_payments_count.toLocaleString()} context={`${formatCurrency(data.pending_payments_amount)} outstanding`} icon="payments" to="/finance" attention />
        </div>
        {!isAdmin && <p className="mt-2 text-xs text-slate-500">Revenue details are available to administrators in Finance and Reports.</p>}
      </section>

      <section aria-labelledby="revenue-occupancy-heading">
        <div className="mb-3 flex items-center justify-between">
          <div><h2 id="revenue-occupancy-heading" className="text-sm font-semibold text-slate-800">Revenue &amp; occupancy</h2><p className="mt-0.5 text-xs text-slate-500">Existing monthly report and unit occupancy figures.</p></div>
          {isAdmin && <Link className="text-xs font-semibold text-brand-green hover:underline" to="/reports">Reports Center <span aria-hidden="true">→</span></Link>}
        </div>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Card className="!rounded-[14px] !p-4 sm:!p-5 shadow-sm">
            <div className="mb-4 flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-slate-900">Revenue overview</h3><p className="mt-1 text-xs text-slate-500">Six-month trend, including the current month</p></div>{isAdmin && <Badge tone="green">Month to date {formatCurrency(data.month_revenue)}</Badge>}</div>
            {isAdmin ? (revenueTrend.length ? <div className="h-64 w-full" role="img" aria-label="Monthly revenue, expenses, and profit chart">
              <ResponsiveContainer width="100%" height="100%"><LineChart data={revenueTrend} margin={{ top: 8, right: 10, left: -15, bottom: 0 }}>
                <CartesianGrid stroke="#edf1f0" vertical={false} />
                <XAxis dataKey="month_label" tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                <YAxis tick={false} axisLine={false} tickLine={false} tickFormatter={(value: number) => value >= 1000 ? `${Math.round(value / 1000)}k` : String(value)} />
                <Tooltip formatter={(value: unknown) => formatCurrency(Number(value))} labelFormatter={(label, payload) => {
                  const point = payload?.[0]?.payload as { year?: number; month?: number } | undefined;
                  return point?.year && point.month ? labelForMonthYear(point.year, point.month) : label;
                }} contentStyle={{ borderRadius: 12, borderColor: "#e2e8f0", fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="revenue" name="Revenue" stroke="#0f3d3a" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} />
                <Line type="monotone" dataKey="expenses" name="Expenses" stroke="#c58a3a" strokeWidth={1.75} strokeDasharray="5 3" dot={false} activeDot={{ r: 4 }} />
                <Line type="monotone" dataKey="profit" name="Profit" stroke="#1e6b65" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                {revenueTrend.length > 0 && <>
                  <ReferenceDot x={revenueTrend[revenueTrend.length - 1].month_label} y={revenueTrend[revenueTrend.length - 1].revenue} r={4} fill="#0f3d3a" stroke="white" strokeWidth={2} />
                  <ReferenceDot x={revenueTrend[revenueTrend.length - 1].month_label} y={revenueTrend[revenueTrend.length - 1].expenses} r={4} fill="#c58a3a" stroke="white" strokeWidth={2} />
                  <ReferenceDot x={revenueTrend[revenueTrend.length - 1].month_label} y={revenueTrend[revenueTrend.length - 1].profit} r={4} fill="#1e6b65" stroke="white" strokeWidth={2} />
                </>}
              </LineChart></ResponsiveContainer>
            </div> : <EmptyState message="No revenue trend is available." />) : <div className="flex h-64 flex-col items-center justify-center text-center"><span className="mb-3 grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-500" aria-hidden="true">₹</span><p className="text-sm font-medium text-slate-700">Financial chart access is restricted</p><p className="mt-1 max-w-xs text-xs text-slate-500">Your current role can review operations and occupancy. Financial reporting is available in authorized Finance views.</p></div>}
          </Card>

          <Card className="!rounded-[14px] !p-4 sm:!p-5 shadow-sm">
            <div className="mb-4 flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-slate-900">Occupancy overview</h3><p className="mt-1 text-xs text-slate-500">Monthly occupancy by unit · {currentMonthLabel}</p></div><span className="shrink-0 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm font-semibold text-slate-800">Today · {data.occupancy_percent}%</span></div>
            {occupancyByUnit.length ? <div className="h-64 w-full" role="img" aria-label="Monthly occupancy by unit chart">
              <ResponsiveContainer width="100%" height="100%"><BarChart data={occupancyByUnit} layout="vertical" margin={{ top: 4, right: 30, left: 4, bottom: 4 }}>
                <XAxis type="number" domain={[0, 100]} hide />
                <YAxis type="category" dataKey="unit_name" width={60} tick={{ fontSize: 11, fill: "#475569" }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(value: unknown) => `${Number(value)}%`} contentStyle={{ borderRadius: 12, borderColor: "#e2e8f0", fontSize: 12 }} />
                <Bar dataKey="month_occupancy_percent" name="Occupancy" radius={[0, 7, 7, 0]} barSize={14} background={{ fill: "#fbfcfb", stroke: "#e2e8e0", strokeWidth: 1, radius: 7 }}>
                  {occupancyByUnit.map((unit) => <Cell key={unit.unit_id} fill={unit.month_occupancy_percent === 0 ? "#e2e8f0" : unit.month_occupancy_percent >= 70 ? "#0f3d3a" : unit.month_occupancy_percent >= 35 ? "#4b817b" : "#a8c5c0"} />)}
                  <LabelList dataKey="month_occupancy_percent" position="right" formatter={(value: unknown) => `${Number(value)}%`} fill="#475569" fontSize={10} />
                </Bar>
              </BarChart></ResponsiveContainer>
            </div> : <EmptyState message="No unit occupancy data is available." />}
            {data.apartment_performance.length > occupancyByUnit.length && <p className="mt-2 text-center text-[11px] text-slate-500">Showing 8 of {data.apartment_performance.length} units · ranked by revenue</p>}
          </Card>
        </div>
      </section>

      <section aria-labelledby="operations-heading">
        <div className="mb-3"><h2 id="operations-heading" className="text-sm font-semibold text-slate-800">Today’s operations</h2><p className="mt-0.5 text-xs text-slate-500">Key movements and open work at a glance.</p></div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
          <OperationCard label="Check-ins" value={data.arrivals_today} detail={`${data.pending_checkins} awaiting check-in`} to="/bookings" />
          <OperationCard label="Check-outs" value={data.departures_today} detail={`${data.pending_checkouts} awaiting check-out`} to="/bookings" />
          <OperationCard label="Housekeeping" value={data.housekeeping_pending} detail="Tasks not completed" to="/housekeeping" />
          <OperationCard label="Maintenance" value={data.maintenance_open} detail="Open or in progress" to="/maintenance" />
          <OperationCard label="Pending payments" value={data.pending_payments_count} detail={formatCurrency(data.pending_payments_amount)} to="/finance" />
        </div>
      </section>

      {(notices.length > 0) && <section aria-labelledby="attention-heading">
        <Card className="!rounded-[14px] !p-4 sm:!p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><div className="flex items-center gap-2"><h2 id="attention-heading" className="text-sm font-semibold text-slate-900">Needs attention</h2><span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">{notices.length}</span></div><p className="mt-1 text-xs text-slate-500">Priority items from the operations dashboard.</p></div>
            {notices.length > INITIAL_ALERT_LIMIT && <button type="button" aria-expanded={showAllNotices} onClick={() => setShowAllNotices((shown) => !shown)} className="rounded-md text-xs font-semibold text-brand-green hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-green">{showAllNotices ? "Show fewer ↑" : "View all →"}</button>}
          </div>
          <div className="mt-4 space-y-2">
            {visibleNotices.map((notice) => <NoticeRow key={notice.id} notice={notice} />)}
          </div>
        </Card>
      </section>}

      <div className="flex flex-wrap gap-2" aria-label="Quick actions">
        {QUICK_ACTIONS.map((action) => <Link key={action.to + action.label} to={action.to} className={`inline-flex items-center rounded-[10px] px-3.5 py-2 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-green ${action.variant === "primary" ? "bg-brand-green text-white hover:bg-brand-green-2" : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>{action.label}</Link>)}
      </div>

      <section aria-labelledby="recent-bookings-heading">
        <Card className="!rounded-[14px] !p-4 sm:!p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 id="recent-bookings-heading" className="text-sm font-semibold text-slate-900">Recent bookings</h2><p className="mt-1 text-xs text-slate-500">Most recently updated bookings across all units.</p></div><Link to="/bookings" className="text-xs font-semibold text-brand-green hover:underline">All bookings <span aria-hidden="true">→</span></Link></div>
          {recentLoading ? <Spinner label="Loading recent bookings..." /> : recentError ? <ErrorBanner message={recentError} /> : topRecentBookings.length === 0 ? <EmptyState message="No bookings yet." /> : (
            <div className="overflow-x-auto"><Table headers={["Guest", "Unit", "Check-in", "Check-out", "Status"]}>{topRecentBookings.map((booking) => <tr key={booking.id}>
              <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-800">{guestName(booking.guest_id)}</td><td className="whitespace-nowrap px-4 py-3 text-slate-500">{unitName(booking.unit_id)}</td><td className="whitespace-nowrap px-4 py-3 tabular-nums text-slate-500">{formatShortDate(booking.check_in)}</td><td className="whitespace-nowrap px-4 py-3 tabular-nums text-slate-500">{formatShortDate(booking.check_out)}</td><td className="whitespace-nowrap px-4 py-3"><span className="inline-flex flex-wrap items-center gap-1.5"><Badge tone={bookingStatusTone(booking.status)}>{booking.status}</Badge>{booking.is_archived && <Badge tone="amber">Archived</Badge>}</span></td>
            </tr>)}</Table>
            </div>
          )}
        </Card>
      </section>

      {isAdmin && <section className="space-y-4" aria-labelledby="financial-details-heading">
        <div><h2 id="financial-details-heading" className="text-sm font-semibold text-slate-800">Financial performance</h2><p className="mt-0.5 text-xs text-slate-500">Figures provided by the existing operational dashboard report.</p></div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <FinancialCard title="Today" revenue={data.today_revenue} expenses={data.today_expenses} profit={data.today_profit} />
          <FinancialCard title="Month to date" revenue={data.month_revenue} expenses={data.month_expenses} profit={data.month_profit} />
          <Card className="!rounded-[14px] !p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-900">Monthly trend</h3>
            <p className="mt-1 text-xs text-slate-500">Revenue, expenses and profit.</p>
            <div className="mt-3 divide-y divide-slate-100">
              {revenueTrend.slice(-3).map((point) => (
                <div key={`${point.year}-${point.month}`} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                  <span className="w-16 shrink-0 text-xs font-semibold text-slate-600">{point.month_label} {point.year}</span>
                  <div className="flex flex-1 flex-wrap justify-end gap-x-3 gap-y-0.5 text-right text-[11px]">
                    <span className="text-slate-500">{formatCurrency(point.revenue)} <span className="text-slate-400">rev</span></span>
                    <span className="text-amber-700">{formatCurrency(point.expenses)} <span className="text-amber-600/70">exp</span></span>
                    <span className="font-semibold text-brand-green">{formatCurrency(point.profit)} <span className="font-normal text-brand-green/70">profit</span></span>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
        <Card className="!rounded-[14px] !p-4 sm:!p-5 shadow-sm">
          <div className="mb-4"><h3 className="text-sm font-semibold text-slate-900">Apartment-wise performance</h3><p className="mt-1 text-xs text-slate-500">This month · revenue vs expenses per unit, ranked by revenue.</p></div>
          <div className="h-72" role="img" aria-label="Monthly revenue and expenses by unit chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.apartment_performance.slice(0, 12)} margin={{ top: 5, right: 10, left: 0, bottom: 40 }}><CartesianGrid stroke="#edf1f0" vertical={false} /><XAxis dataKey="unit_name" angle={-35} textAnchor="end" interval={0} height={60} tick={{ fontSize: 10, fill: "#64748b" }} /><YAxis tick={{ fontSize: 10, fill: "#64748b" }} /><Tooltip formatter={(value: unknown) => formatCurrency(Number(value))} /><Legend wrapperStyle={{ fontSize: 11 }} /><Bar dataKey="month_revenue" name="Revenue" fill="#0f3d3a" radius={[4, 4, 0, 0]} /><Bar dataKey="month_expenses" name="Expenses" fill="#c58a3a" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div>
          <div className="mt-4 overflow-x-auto"><Table headers={["Unit", "Revenue", "Expenses", "Profit", "Nights booked", "Occupancy"]}>{data.apartment_performance.map((unit) => <tr key={unit.unit_id}><td className="px-4 py-3 font-medium text-slate-700">{unit.unit_name}</td><td className="px-4 py-3 text-slate-600">{formatCurrency(unit.month_revenue)}</td><td className="px-4 py-3 text-slate-600">{formatCurrency(unit.month_expenses)}</td><td className={`px-4 py-3 font-medium ${unit.month_profit >= 0 ? "text-brand-green" : "text-red-700"}`}>{formatCurrency(unit.month_profit)}</td><td className="px-4 py-3 text-slate-600">{unit.month_nights_booked}</td><td className="px-4 py-3 text-slate-600">{unit.month_occupancy_percent}%</td></tr>)}</Table></div>
        </Card>
      </section>}

      <section aria-labelledby="operations-detail-heading">
        <div className="mb-3"><h2 id="operations-detail-heading" className="text-sm font-semibold text-slate-800">Operations detail</h2><p className="mt-0.5 text-xs text-slate-500">Supporting workload and staffing counts.</p></div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <OperationCard label="Registered guests" value={data.total_guests} detail="Guest records" to="/guests" />
          <OperationCard label="Current stays" value={data.in_house_guests} detail="Guests checked in" to="/bookings" />
          <OperationCard label="Guest requests" value={data.guest_requests_open} detail="Open requests" to="/guest-requests" />
          <OperationCard label="Low stock" value={data.low_stock_count} detail="At or below minimum" to="/inventory" tone={data.low_stock_count > 0 ? "amber" : "slate"} />
          <OperationCard label="Staff present" value={data.staff_present_today} detail="Today" to="/staff" />
          <OperationCard label="Staff absent" value={data.staff_absent_today} detail="Today" to="/staff" />
          <OperationCard label="Follow-ups due" value={data.follow_ups_due_today} detail="Enquiries today" to="/inquiries" />
        </div>
      </section>

      <section aria-labelledby="calendar-heading">
        <Card className="!rounded-[14px] !p-4 sm:!p-5 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 id="calendar-heading" className="text-sm font-semibold text-slate-900">Calendar</h2><p className="mt-1 text-xs text-slate-500">Check-ins, check-outs and in-house guests by day.</p></div><div className="flex gap-2"><label className="sr-only" htmlFor="calendar-year">Calendar year</label><input id="calendar-year" type="number" value={calYear} onChange={(event) => setCalYear(Number(event.target.value))} className="w-24 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-green" /><label className="sr-only" htmlFor="calendar-month">Calendar month</label><select id="calendar-month" value={calMonth} onChange={(event) => setCalMonth(Number(event.target.value))} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-green">{Array.from({ length: 12 }, (_, index) => index + 1).map((month) => <option key={month} value={month}>{monthLongFormatter.format(new Date(2000, month - 1, 1))}</option>)}</select></div></div>
          {calendar ? (
            <>
              <div className="mb-1.5 grid grid-cols-7 gap-1.5 sm:gap-2">
                {WEEKDAY_LABELS.map((label) => (
                  <div key={label} className="text-center text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-1.5 sm:gap-2">
                {/* Leading blank cells so day 1 lands under its real weekday
                    (calendar.days is a flat list with no week/weekday info of
                    its own) — the previous version placed day 1 in column 1
                    unconditionally, misaligning every day after it. */}
                {Array.from({ length: new Date(calendar.period_year, calendar.period_month - 1, 1).getDay() }).map((_, index) => (
                  <div key={`lead-${index}`} aria-hidden="true" />
                ))}
                {calendar.days.map((day) => {
                  const dayNum = Number(day.date.split("-")[2]);
                  const hasActivity = day.check_ins > 0 || day.check_outs > 0;
                  const isToday = day.date === data.as_of;
                  return (
                    <div
                      key={day.date}
                      className={`min-h-[70px] rounded-lg border p-1.5 text-[10px] sm:min-h-[82px] sm:p-2 ${
                        isToday ? "border-brand-green ring-1 ring-brand-green" : hasActivity ? "border-brand-green/20 bg-brand-green/[0.035]" : "border-slate-100 bg-white"
                      }`}
                    >
                      <p className="font-semibold text-slate-700">{dayNum}</p>
                      <div className="mt-1 space-y-0.5">
                        {day.check_ins > 0 && <p className="flex items-center gap-1 text-blue-800"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500" aria-hidden="true" />{day.check_ins} in</p>}
                        {day.check_outs > 0 && <p className="flex items-center gap-1 text-amber-800"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden="true" />{day.check_outs} out</p>}
                        {day.in_house > 0 && <p className="flex items-center gap-1 text-brand-green"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-green-4" aria-hidden="true" />{day.in_house} stay</p>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          ) : <Spinner label="Loading calendar..." />}
        </Card>
      </section>
    </div>
  );
}

function MetricCard({ label, value, context, icon, to, attention = false }: { label: string; value: string; context: string; icon: "occupancy" | "revenue" | "bookings" | "units" | "payments"; to?: string; attention?: boolean }) {
  const content = <div className={`relative h-full min-h-[112px] rounded-[12px] border bg-white p-3.5 shadow-[0_2px_8px_rgba(15,61,58,0.045)] sm:p-4 ${attention ? "border-amber-200/80" : "border-slate-200/80"}`}><div className="min-w-0"><h3 className="pr-9 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</h3><p className="mt-2 truncate text-2xl font-semibold leading-8 tracking-tight text-slate-900">{value}</p><p className={`mt-1 truncate text-xs ${attention ? "text-amber-800" : "text-slate-500"}`}>{context}</p></div><span className="absolute right-3.5 top-3.5 sm:right-4 sm:top-4"><MetricIcon kind={icon} /></span></div>;
  return to ? <Link to={to} className="rounded-[12px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-green">{content}</Link> : content;
}

function OperationCard({ label, value, detail, to, tone = "slate" }: { label: string; value: number; detail: string; to: string; tone?: "slate" | "amber" }) {
  return <Link to={to} className={`rounded-[12px] border bg-white px-4 py-3 shadow-[0_2px_8px_rgba(15,61,58,0.035)] transition hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-green ${tone === "amber" ? "border-amber-200 hover:border-amber-300" : "border-slate-200/80 hover:border-brand-green/30"}`}><p className={`text-xs font-medium ${tone === "amber" ? "text-amber-800" : "text-slate-500"}`}>{label}</p><p className={`mt-1 text-xl font-semibold tabular-nums ${tone === "amber" ? "text-amber-800" : "text-slate-900"}`}>{value.toLocaleString()}</p><p className="mt-0.5 truncate text-[11px] text-slate-500">{detail}</p></Link>;
}

function NoticeRow({ notice }: { notice: AttentionNotice }) {
  const tones = {
    critical: { label: "Urgent", badge: "bg-red-50 text-red-700", accent: "border-l-red-400", icon: "bg-red-50 text-red-600" },
    warning: { label: "Attention", badge: "bg-amber-50 text-amber-800", accent: "border-l-amber-400", icon: "bg-amber-50 text-amber-700" },
    info: { label: "Info", badge: "bg-slate-100 text-slate-600", accent: "border-l-slate-300", icon: "bg-slate-100 text-slate-500" },
  } as const;
  const tone = tones[notice.severity];
  return (
    <Link
      to={notice.to}
      className={`group flex items-start gap-3 rounded-xl border-l-[3px] bg-slate-50 px-3.5 py-3 transition hover:bg-slate-100 ${tone.accent}`}
    >
      <span className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg ${tone.icon}`} aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 9v4M12 17v.01" /><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" /></svg>
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tone.badge}`}>{tone.label}</span>
          <span className="text-xs font-semibold text-slate-800 group-hover:text-brand-green">{notice.title}</span>
        </div>
        {notice.description && <p className="mt-1 text-xs leading-5 text-slate-500">{notice.description}</p>}
      </div>
      <svg className="mt-1.5 shrink-0 text-slate-300 group-hover:text-brand-green" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6" /></svg>
    </Link>
  );
}

function FinancialCard({ title, revenue, expenses, profit }: { title: string; revenue: number; expenses: number; profit: number }) {
  return <Card className="!rounded-[14px] !p-5 shadow-sm"><h3 className="text-sm font-semibold text-slate-900">{title}</h3><div className="mt-4 space-y-3 text-xs"><FinancialRow label="Revenue" value={revenue} tone="revenue"/><FinancialRow label="Expenses" value={expenses} tone="expenses"/><div className="border-t border-slate-100 pt-3"><FinancialRow label="Profit" value={profit} strong tone="profit"/></div></div></Card>;
}

function FinancialRow({ label, value, strong, tone }: { label: string; value: number; strong?: boolean; tone: "revenue" | "expenses" | "profit" }) {
  const valueTone = tone === "expenses" ? "text-amber-800" : tone === "profit" && value < 0 ? "text-red-700" : "text-brand-green";
  return <div className="flex items-center justify-between gap-3"><span className="text-slate-500">{label}</span><span className={`tabular-nums ${strong ? "font-semibold" : "font-medium"} ${valueTone}`}>{formatCurrency(value)}</span></div>;
}
