import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart, Bar, LineChart, Line, Legend, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { api, extractErrorMessage, type OperationalDashboard, type DashboardCalendar } from "../lib/api";
import { Card, PageHeader, ErrorBanner, Spinner } from "../components/ui";

// Polling interval for the "keep this reasonably fresh across multiple
// staff using the system at once" behavior. This is honest, simple
// polling — NOT a WebSocket push. Two staff members both looking at the
// dashboard will see each other's changes within this interval, not
// instantly. A true real-time push would need a WebSocket/SSE layer,
// which is a genuinely separate, larger piece of infrastructure.
const REFRESH_INTERVAL_MS = 20_000;

export default function DashboardPage() {
  const [data, setData] = useState<OperationalDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [calendar, setCalendar] = useState<DashboardCalendar | null>(null);
  const [calYear, setCalYear] = useState(new Date().getFullYear());
  const [calMonth, setCalMonth] = useState(new Date().getMonth() + 1);

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

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-1">
        <PageHeader title="Dashboard" />
        {lastRefreshed && (
          <p className="text-xs text-slate-400 mb-4">
            Updated {lastRefreshed.toLocaleTimeString()} · auto-refreshes every {REFRESH_INTERVAL_MS / 1000}s
          </p>
        )}
      </div>
      <ErrorBanner message={error} />

      <div className="flex flex-wrap gap-2 mb-6">
        <Link to="/bookings" className="rounded-lg bg-slate-900 text-white text-xs font-medium px-3 py-2 hover:bg-slate-800">+ New Booking</Link>
        <Link to="/billing" className="rounded-lg border border-slate-300 text-xs font-medium px-3 py-2 hover:bg-slate-50">+ Record Payment</Link>
        <Link to="/guests" className="rounded-lg border border-slate-300 text-xs font-medium px-3 py-2 hover:bg-slate-50">+ Add Guest</Link>
        <Link to="/maintenance" className="rounded-lg border border-slate-300 text-xs font-medium px-3 py-2 hover:bg-slate-50">+ Raise Maintenance Ticket</Link>
        <Link to="/housekeeping" className="rounded-lg border border-slate-300 text-xs font-medium px-3 py-2 hover:bg-slate-50">+ Add Housekeeping Task</Link>
        <Link to="/inquiries" className="rounded-lg border border-slate-300 text-xs font-medium px-3 py-2 hover:bg-slate-50">+ New Enquiry</Link>
      </div>

      {(data.low_stock_count > 0 || data.pending_payments_count > 0 || data.maintenance_open > 0 || data.guest_requests_open > 0 || data.follow_ups_due_today > 0 || data.staff_absent_today > 0) && (
        <Card className="mb-6 border-amber-300 bg-amber-50">
          <h2 className="text-sm font-semibold text-amber-900 mb-2">Needs Attention</h2>
          <ul className="text-xs text-amber-900 space-y-1 list-disc list-inside">
            {data.pending_payments_count > 0 && <li><Link to="/finance" className="hover:underline">{data.pending_payments_count} booking(s) with an outstanding balance — ₹{data.pending_payments_amount.toLocaleString()}</Link></li>}
            {data.maintenance_open > 0 && <li><Link to="/maintenance" className="hover:underline">{data.maintenance_open} open maintenance ticket(s)</Link></li>}
            {data.guest_requests_open > 0 && <li><Link to="/guest-requests" className="hover:underline">{data.guest_requests_open} unresolved guest request(s)</Link></li>}
            {data.low_stock_count > 0 && <li><Link to="/inventory" className="hover:underline">{data.low_stock_count} inventory item(s) at or below minimum stock</Link></li>}
            {data.follow_ups_due_today > 0 && <li><Link to="/inquiries" className="hover:underline">{data.follow_ups_due_today} enquiry follow-up(s) due today</Link></li>}
            {data.staff_absent_today > 0 && <li><Link to="/staff" className="hover:underline">{data.staff_absent_today} staff member(s) absent today</Link></li>}
          </ul>
        </Card>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="Arrivals Today" value={data.arrivals_today} to="/bookings" />
        <StatCard label="Departures Today" value={data.departures_today} to="/bookings" />
        <StatCard label="In-House Guests" value={data.in_house_guests} to="/bookings" />
        <StatCard label="Vacant Units" value={data.vacant_units} to="/properties" />
        <StatCard label="Total Guests" value={data.total_guests} to="/guests" />
        <StatCard label="Active Bookings" value={data.active_bookings} to="/bookings" />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="Occupancy" value={`${data.occupancy_percent}%`} />
        <StatCard label="Pending Check-ins" value={data.pending_checkins} to="/bookings" />
        <StatCard label="Pending Check-outs" value={data.pending_checkouts} to="/bookings" />
        <StatCard
          label="Pending Payments"
          value={data.pending_payments_count}
          sub={`₹${data.pending_payments_amount.toLocaleString()}`}
          to="/finance"
        />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-2 gap-4 mb-8">
        <StatCard label="Housekeeping Pending" value={data.housekeeping_pending} to="/housekeeping" tone={data.housekeeping_pending > 0 ? "amber" : undefined} />
        <StatCard label="Maintenance Open" value={data.maintenance_open} to="/maintenance" tone={data.maintenance_open > 0 ? "amber" : undefined} />
        <StatCard label="Guest Requests Open" value={data.guest_requests_open} to="/guest-requests" tone={data.guest_requests_open > 0 ? "amber" : undefined} />
        <StatCard label="Low Stock Items" value={data.low_stock_count} to="/inventory" tone={data.low_stock_count > 0 ? "amber" : undefined} />
        <StatCard label="Staff Present Today" value={data.staff_present_today} to="/staff" />
        <StatCard label="Staff Absent Today" value={data.staff_absent_today} to="/staff" tone={data.staff_absent_today > 0 ? "amber" : undefined} />
        <StatCard label="Follow-ups Due Today" value={data.follow_ups_due_today} to="/inquiries" tone={data.follow_ups_due_today > 0 ? "amber" : undefined} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-3">Today</h2>
          <div className="space-y-2 text-sm">
            <Row label="Revenue" value={`₹${data.today_revenue.toLocaleString()}`} />
            <Row label="Expenses" value={`₹${data.today_expenses.toLocaleString()}`} />
            <Row label="Profit" value={`₹${data.today_profit.toLocaleString()}`} bold tone={data.today_profit >= 0 ? "green" : "red"} />
          </div>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-3">This Month</h2>
          <div className="space-y-2 text-sm">
            <Row label="Revenue" value={`₹${data.month_revenue.toLocaleString()}`} />
            <Row label="Expenses" value={`₹${data.month_expenses.toLocaleString()}`} />
            <Row label="Profit" value={`₹${data.month_profit.toLocaleString()}`} bold tone={data.month_profit >= 0 ? "green" : "red"} />
          </div>
        </Card>
      </div>

      <Card className="mb-8">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Apartment-wise Performance (this month)</h2>
        <p className="text-xs text-slate-400 mb-4">Revenue vs. expenses per unit, sorted by revenue highest to lowest.</p>
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
        <div className="overflow-x-auto mt-4">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-slate-400 border-b border-slate-100">
                <th className="py-1.5 pr-4">Unit</th>
                <th className="py-1.5 pr-4">Revenue</th>
                <th className="py-1.5 pr-4">Expenses</th>
                <th className="py-1.5 pr-4">Profit</th>
                <th className="py-1.5 pr-4">Nights Booked</th>
                <th className="py-1.5">Occupancy</th>
              </tr>
            </thead>
            <tbody>
              {data.apartment_performance.map((a) => (
                <tr key={a.unit_id} className="border-b border-slate-50">
                  <td className="py-1.5 pr-4 font-medium text-slate-700">{a.unit_name}</td>
                  <td className="py-1.5 pr-4 text-slate-500">₹{a.month_revenue.toLocaleString()}</td>
                  <td className="py-1.5 pr-4 text-slate-500">₹{a.month_expenses.toLocaleString()}</td>
                  <td className={`py-1.5 pr-4 font-medium ${a.month_profit >= 0 ? "text-emerald-600" : "text-red-600"}`}>₹{a.month_profit.toLocaleString()}</td>
                  <td className="py-1.5 pr-4 text-slate-500">{a.month_nights_booked}</td>
                  <td className="py-1.5 text-slate-500">{a.month_occupancy_percent}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Monthly Trend (last 6 months)</h2>
        <p className="text-xs text-slate-400 mb-4">Revenue, expenses, and profit — including the current, in-progress month.</p>
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

      <Card className="mt-8">
        <div className="flex items-center justify-between mb-4">
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

function StatCard({ label, value, to, sub, tone }: { label: string; value: string | number; to?: string; sub?: string; tone?: "amber" }) {
  const content = (
    <div className={`bg-white border rounded-xl p-4 h-full ${tone === "amber" ? "border-amber-300 bg-amber-50" : "border-slate-200"}`}>
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className="text-xl font-semibold text-slate-900">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
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
