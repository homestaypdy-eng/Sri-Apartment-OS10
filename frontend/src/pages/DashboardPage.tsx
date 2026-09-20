import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart, Bar, LineChart, Line, Legend, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import {
  api,
  extractErrorMessage,
  type OperationalDashboard,
  type DashboardCalendar,
  type BookingOut,
  type GuestOut,
  type UnitOut,
} from "../lib/api";
import { Card, PageHeader, ErrorBanner, Spinner, Badge, Table, EmptyState, Button, AttentionRow } from "../components/ui";
import { useAuth } from "../context/AuthContext";

// Polling interval for the "keep this reasonably fresh across multiple
// staff using the system at once" behavior. This is honest, simple
// polling — NOT a WebSocket push. Two staff members both looking at the
// dashboard will see each other's changes within this interval, not
// instantly. A true real-time push would need a WebSocket/SSE layer,
// which is a genuinely separate, larger piece of infrastructure.
const REFRESH_INTERVAL_MS = 20_000;
const RECENT_BOOKINGS_LIMIT = 6;

const QUICK_ACTIONS = [
  { label: "+ New Booking", to: "/bookings", variant: "primary" as const },
  { label: "+ Record Payment", to: "/billing", variant: "secondary" as const },
  { label: "+ Add Guest", to: "/guests", variant: "secondary" as const },
  { label: "Manage Properties", to: "/properties", variant: "secondary" as const },
  { label: "+ Raise Maintenance Ticket", to: "/maintenance", variant: "secondary" as const },
  { label: "+ Add Housekeeping Task", to: "/housekeeping", variant: "secondary" as const },
  { label: "+ New Enquiry", to: "/inquiries", variant: "secondary" as const },
];

function bookingStatusTone(status: string): "green" | "amber" | "slate" {
  if (status === "confirmed") return "green";
  if (status === "checked_in") return "amber";
  return "slate";
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

  const [recentBookings, setRecentBookings] = useState<BookingOut[]>([]);
  const [guests, setGuests] = useState<GuestOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [recentError, setRecentError] = useState<string | null>(null);

  const load = useCallback(async (isBackground = false) => {
    try {
      const { data } = await api.get("/reports/dashboard");
      setData(data);
      setLastRefreshed(new Date());
    } catch (err) {
      if (!isBackground) setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(() => load(true), REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [load]);

  useEffect(() => {
    api.get("/reports/dashboard/calendar", { params: { period_year: calYear, period_month: calMonth } })
      .then(({ data }) => setCalendar(data))
      .catch(() => setCalendar(null));
  }, [calYear, calMonth]);

  // Recent Bookings isn't part of /reports/dashboard — it's the same
  // existing /bookings, /guests, /units list endpoints BookingsPage already
  // uses, just fetched here too and trimmed to the most recently updated
  // few. Kept on its own loading/error state so a slow guest/unit list
  // never blocks the primary operational numbers above from rendering.
  useEffect(() => {
    Promise.all([api.get("/bookings"), api.get("/guests"), api.get("/units")])
      .then(([b, g, u]) => {
        setRecentBookings(b.data);
        setGuests(g.data);
        setUnits(u.data);
      })
      .catch((err) => setRecentError(extractErrorMessage(err)))
      .finally(() => setRecentLoading(false));
  }, []);

  function guestName(id: string) {
    return guests.find((g) => g.id === id)?.name ?? "—";
  }
  function unitName(id: string) {
    return units.find((u) => u.id === id)?.name ?? "—";
  }

  const topRecentBookings = [...recentBookings]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, RECENT_BOOKINGS_LIMIT);

  if (loading) {
    return (
      <div>
        <PageHeader title="Dashboard" />
        <Spinner label="Loading dashboard..." />
      </div>
    );
  }

  if (!data) {
    return (
      <div>
        <PageHeader title="Dashboard" />
        <ErrorBanner message={error} />
      </div>
    );
  }

  const subtitle = `${data.total_units} accommodation${data.total_units === 1 ? "" : "s"} · ${data.active_bookings} active booking${data.active_bookings === 1 ? "" : "s"} · ${data.occupancy_percent}% occupancy`;

  return (
    <div>
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Dashboard</h1>
        {lastRefreshed && (
          <p className="text-xs text-slate-400">
            Updated {lastRefreshed.toLocaleTimeString()} · auto-refreshes every {REFRESH_INTERVAL_MS / 1000}s
          </p>
        )}
      </div>
      <p className="mb-6 text-xs text-slate-500">{subtitle}</p>
      <ErrorBanner message={error} />

      <div className="mb-6 flex flex-wrap gap-2">
        {QUICK_ACTIONS.map((a) => (
          <Link key={a.to + a.label} to={a.to}>
            <Button variant={a.variant} className="text-sm">{a.label}</Button>
          </Link>
        ))}
      </div>

      {(data.attention_items.length > 0 || data.follow_ups_due_today > 0 || data.staff_absent_today > 0) && (
        <Card className="mb-6 border-amber-200 bg-amber-50/60">
          <h2 className="mb-3 text-sm font-semibold text-amber-900">Needs Attention</h2>
          <div className="space-y-2">
            {data.attention_items.map((item, i) => (
              <AttentionRow
                key={i}
                severity={item.severity}
                severityTone={item.severity === "critical" ? "red" : "amber"}
                title={item.title}
                description={`${item.reason} ${item.action}`}
                to={item.link}
              />
            ))}
            {data.follow_ups_due_today > 0 && (
              <AttentionRow
                severity="info"
                severityTone="amber"
                title={`${data.follow_ups_due_today} enquiry follow-up(s) due today`}
                to="/inquiries"
              />
            )}
            {data.staff_absent_today > 0 && (
              <AttentionRow
                severity="info"
                severityTone="amber"
                title={`${data.staff_absent_today} staff member(s) absent today`}
                to="/staff"
              />
            )}
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Overview</h2>
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Occupancy" value={`${data.occupancy_percent}%`} highlight />
        <StatCard label="Active Bookings" value={data.active_bookings} to="/bookings" highlight />
        <StatCard label="Total Guests" value={data.total_guests} to="/guests" highlight />
        <StatCard label="Vacant Units" value={data.vacant_units} to="/properties" highlight />
      </div>

      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Today's Operations</h2>
      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard label="Arrivals Today" value={data.arrivals_today} to="/bookings" />
        <StatCard label="Departures Today" value={data.departures_today} to="/bookings" />
        <StatCard label="Current Stays" value={data.in_house_guests} to="/bookings" />
        <StatCard label="Pending Check-ins" value={data.pending_checkins} to="/bookings" tone={data.pending_checkins > 0 ? "amber" : undefined} />
        <StatCard label="Pending Check-outs" value={data.pending_checkouts} to="/bookings" tone={data.pending_checkouts > 0 ? "amber" : undefined} />
      </div>

      {isAdmin && (
        <Card className="mb-8">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">Today</h2>
          <div className="space-y-2 text-sm">
            <Row label="Revenue" value={`₹${data.today_revenue.toLocaleString()}`} />
            <Row label="Expenses" value={`₹${data.today_expenses.toLocaleString()}`} />
            <Row label="Profit" value={`₹${data.today_profit.toLocaleString()}`} bold tone={data.today_profit >= 0 ? "green" : "red"} />
          </div>
        </Card>
      )}

      {!isAdmin && (
        <p className="mb-8 text-xs text-slate-400">Revenue and P&amp;L are available under Finance and Reports Center.</p>
      )}

      <Card className="mb-8">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Recent Bookings</h2>
            <p className="text-xs text-slate-400">Most recently updated bookings across all units.</p>
          </div>
          <Link to="/bookings"><Button variant="secondary" className="text-xs">View All</Button></Link>
        </div>
        {recentLoading ? (
          <Spinner label="Loading recent bookings..." />
        ) : recentError ? (
          <ErrorBanner message={recentError} />
        ) : topRecentBookings.length === 0 ? (
          <EmptyState message="No bookings yet." />
        ) : (
          <div className="overflow-x-auto">
            <Table headers={["Guest", "Unit", "Check-in", "Check-out", "Status"]}>
              {topRecentBookings.map((b) => (
                <tr key={b.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{guestName(b.guest_id)}</td>
                  <td className="px-4 py-2.5 text-slate-500">{unitName(b.unit_id)}</td>
                  <td className="px-4 py-2.5 text-slate-500">{b.check_in}</td>
                  <td className="px-4 py-2.5 text-slate-500">{b.check_out}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={bookingStatusTone(b.status)}>{b.status}</Badge>
                    {b.is_archived && <Badge tone="amber">Archived</Badge>}
                  </td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>

      {isAdmin && (
        <>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">This Month — Financial Summary</h2>
          <Card className="mb-8">
            <h2 className="mb-3 text-sm font-semibold text-slate-900">This Month</h2>
            <div className="space-y-2 text-sm">
              <Row label="Revenue" value={`₹${data.month_revenue.toLocaleString()}`} />
              <Row label="Expenses" value={`₹${data.month_expenses.toLocaleString()}`} />
              <Row label="Profit" value={`₹${data.month_profit.toLocaleString()}`} bold tone={data.month_profit >= 0 ? "green" : "red"} />
            </div>
          </Card>

          <Card className="mb-8">
            <h2 className="mb-1 text-sm font-semibold text-slate-900">Apartment-wise Performance (this month)</h2>
            <p className="mb-4 text-xs text-slate-400">Revenue vs. expenses per unit, sorted by revenue highest to lowest.</p>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.apartment_performance.slice(0, 12)} margin={{ top: 5, right: 10, left: 0, bottom: 40 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="unit_name" angle={-35} textAnchor="end" interval={0} height={60} tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value: unknown) => `₹${Number(value).toLocaleString()}`} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="month_revenue" name="Revenue" fill="#0f172a" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="month_expenses" name="Expenses" fill="#dc2626" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-4 overflow-x-auto">
              <Table headers={["Unit", "Revenue", "Expenses", "Profit", "Nights Booked", "Occupancy"]}>
                {data.apartment_performance.map((a) => (
                  <tr key={a.unit_id}>
                    <td className="px-4 py-2.5 font-medium text-slate-700">{a.unit_name}</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{a.month_revenue.toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{a.month_expenses.toLocaleString()}</td>
                    <td className={`px-4 py-2.5 font-medium ${a.month_profit >= 0 ? "text-emerald-600" : "text-red-600"}`}>₹{a.month_profit.toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-slate-500">{a.month_nights_booked}</td>
                    <td className="px-4 py-2.5 text-slate-500">{a.month_occupancy_percent}%</td>
                  </tr>
                ))}
              </Table>
            </div>
          </Card>

          <Card className="mb-8">
            <h2 className="mb-1 text-sm font-semibold text-slate-900">Monthly Trend (last 6 months)</h2>
            <p className="mb-4 text-xs text-slate-400">Revenue, expenses, and profit — including the current, in-progress month.</p>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.monthly_trend.map((t) => ({ ...t, label: `${t.month}/${t.year}` }))} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value: unknown) => `₹${Number(value).toLocaleString()}`} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="revenue" name="Revenue" stroke="#0f172a" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="expenses" name="Expenses" stroke="#dc2626" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="profit" name="Profit" stroke="#059669" strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </>
      )}

      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Reference</h2>
      <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          label="Pending Payments"
          value={data.pending_payments_count}
          sub={`₹${data.pending_payments_amount.toLocaleString()}`}
          to="/finance"
        />
        <StatCard label="Housekeeping Pending" value={data.housekeeping_pending} to="/housekeeping" tone={data.housekeeping_pending > 0 ? "amber" : undefined} />
        <StatCard label="Maintenance Open" value={data.maintenance_open} to="/maintenance" tone={data.maintenance_open > 0 ? "amber" : undefined} />
        <StatCard label="Guest Requests Open" value={data.guest_requests_open} to="/guest-requests" tone={data.guest_requests_open > 0 ? "amber" : undefined} />
        <StatCard label="Low Stock Items" value={data.low_stock_count} to="/inventory" tone={data.low_stock_count > 0 ? "amber" : undefined} />
        <StatCard label="Staff Present Today" value={data.staff_present_today} to="/staff" />
        <StatCard label="Staff Absent Today" value={data.staff_absent_today} to="/staff" tone={data.staff_absent_today > 0 ? "amber" : undefined} />
        <StatCard label="Follow-ups Due Today" value={data.follow_ups_due_today} to="/inquiries" tone={data.follow_ups_due_today > 0 ? "amber" : undefined} />
      </div>

      <Card className="mt-8">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Calendar</h2>
            <p className="text-xs text-slate-400">Check-ins, check-outs, and in-house guests by day.</p>
          </div>
          <div className="flex gap-2">
            <input
              type="number"
              value={calYear}
              onChange={(e) => setCalYear(Number(e.target.value))}
              className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
            />
            <select value={calMonth} onChange={(e) => setCalMonth(Number(e.target.value))} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        </div>
        {calendar && (
          <div className="grid grid-cols-7 gap-1.5">
            {calendar.days.map((d) => {
              const dayNum = Number(d.date.split("-")[2]);
              const hasActivity = d.check_ins > 0 || d.check_outs > 0;
              return (
                <div
                  key={d.date}
                  className={`rounded-lg border p-1.5 text-[10px] min-h-[64px] ${hasActivity ? "border-slate-300 bg-slate-50" : "border-slate-100"}`}
                >
                  <p className="font-semibold text-slate-700">{dayNum}</p>
                  {d.check_ins > 0 && <p className="text-emerald-600">↓{d.check_ins} in</p>}
                  {d.check_outs > 0 && <p className="text-amber-600">↑{d.check_outs} out</p>}
                  {d.in_house > 0 && <p className="text-slate-400">{d.in_house} staying</p>}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function StatCard({ label, value, to, sub, tone, highlight }: { label: string; value: string | number; to?: string; sub?: string; tone?: "amber"; highlight?: boolean }) {
  const content = (
    <div
      className={`h-full rounded-xl border p-4 ${
        tone === "amber" ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-white"
      }`}
    >
      <p className="mb-1 text-xs text-slate-500">{label}</p>
      <p className={`font-semibold text-slate-900 ${highlight ? "text-2xl" : "text-xl"}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-400">{sub}</p>}
    </div>
  );
  return to ? <Link to={to}>{content}</Link> : content;
}

function Row({ label, value, bold, tone }: { label: string; value: string; bold?: boolean; tone?: "green" | "red" }) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className={`${bold ? "font-semibold" : ""} ${tone === "green" ? "text-emerald-600" : tone === "red" ? "text-red-600" : "text-slate-800"}`}>{value}</span>
    </div>
  );
}
