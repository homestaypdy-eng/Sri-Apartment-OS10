import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  api,
  extractErrorMessage,
  type BookingOut,
  type CheckInLine,
  type CheckOutLine,
  type GuestOut,
  type StaffDashboardOut,
  type StaffTaskCategory,
  type StaffTaskOut,
  type UnitOut,
  type UserOut,
} from "../lib/api";
import { Card, PageHeader, SuccessBanner, Button, Badge, EmptyState, SkeletonLine } from "../components/ui";
import { useAuth } from "../context/AuthContext";

type Tab = "mine" | "today" | "check-ins" | "check-outs" | "housekeeping" | "maintenance" | "guest_request" | "all";
type StatusFilter = "all" | "pending" | "in_progress" | "completed";

const OPEN_STATUSES = new Set(["pending", "open"]);
const DONE_STATUSES = new Set(["completed", "resolved", "closed"]);

const TABS: { key: Tab; label: string }[] = [
  { key: "mine", label: "My Tasks" },
  { key: "today", label: "Today" },
  { key: "check-ins", label: "Check-ins" },
  { key: "check-outs", label: "Check-outs" },
  { key: "housekeeping", label: "Housekeeping" },
  { key: "maintenance", label: "Maintenance" },
  { key: "guest_request", label: "Guest Requests" },
  { key: "all", label: "All Tasks" },
];

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "in_progress", label: "In Progress" },
  { key: "completed", label: "Completed" },
];

function decodeUserId(): string | null {
  const token = localStorage.getItem("access_token");
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload.sub ?? null;
  } catch {
    return null;
  }
}

const NEXT_ACTION: Record<StaffTaskCategory, Record<string, { next: string; label: string } | undefined>> = {
  housekeeping: {
    pending: { next: "in_progress", label: "Start" },
    in_progress: { next: "completed", label: "Complete" },
  },
  maintenance: {
    open: { next: "in_progress", label: "Start" },
    in_progress: { next: "resolved", label: "Resolve" },
  },
  guest_request: {
    open: { next: "in_progress", label: "Start" },
    in_progress: { next: "resolved", label: "Complete" },
  },
};

const SUCCESS_MESSAGE: Record<StaffTaskCategory, Record<string, string>> = {
  housekeeping: { in_progress: "Task started.", completed: "Housekeeping completed." },
  maintenance: { in_progress: "Task started.", resolved: "Ticket resolved." },
  guest_request: { in_progress: "Task started.", resolved: "Request completed." },
};

function statusLabel(status: string): string {
  if (status === "in_progress") return "In Progress";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function statusTone(status: string): "slate" | "green" | "amber" | "red" {
  if (DONE_STATUSES.has(status) || status === "checked_out") return "green";
  if (status === "in_progress" || status === "checked_in") return "amber";
  if (status === "cancelled") return "red";
  return "slate";
}

function priorityTone(priority: string): "slate" | "green" | "amber" | "red" {
  if (priority === "urgent" || priority === "high") return "red";
  if (priority === "medium") return "amber";
  return "slate";
}

function relativeTimeShort(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export default function StaffOperationsPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const myUserId = decodeUserId();

  const [tab, setTab] = useState<Tab>("mine");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [dashboard, setDashboard] = useState<StaffDashboardOut | null>(null);
  const [checkIns, setCheckIns] = useState<CheckInLine[]>([]);
  const [checkOuts, setCheckOuts] = useState<CheckOutLine[]>([]);
  const [allOpenTasks, setAllOpenTasks] = useState<StaffTaskOut[]>([]); // used to derive the "Urgent" Today stat, client-side, from data already fetched elsewhere — no new endpoint
  const [tasks, setTasks] = useState<StaffTaskOut[]>([]);
  const [inHouseBookings, setInHouseBookings] = useState<BookingOut[]>([]);
  const [guestsById, setGuestsById] = useState<Record<string, GuestOut>>({});
  const [unitsById, setUnitsById] = useState<Record<string, UnitOut>>({});
  const [usersById, setUsersById] = useState<Record<string, UserOut>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [requestForm, setRequestForm] = useState({ booking_id: "", request_type: "towels", description: "", assigned_to: "" });
  const [showRequestForm, setShowRequestForm] = useState(false);
  const [assigningId, setAssigningId] = useState<string | null>(null); // task id currently mid-PATCH — disables the control to prevent a duplicate submit

  async function loadDashboard() {
    try {
      const [{ data: dash }, { data: openTasks }] = await Promise.all([
        api.get("/staff/dashboard"),
        api.get("/staff/tasks"), // same unified endpoint the task tabs already use — reused, not duplicated, just to compute an "urgent" count client-side (the dashboard endpoint has no priority breakdown)
      ]);
      setDashboard(dash);
      setAllOpenTasks(openTasks.filter((t: StaffTaskOut) => !DONE_STATUSES.has(t.status)));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function loadGuestsAndUnits() {
    // Unlike /users, both /guests and /units are admin+staff readable — same
    // RBAC tier the rest of this page already relies on for unit_name — so
    // this is safe to fetch unconditionally for every role.
    try {
      const [{ data: guests }, { data: units }] = await Promise.all([api.get("/guests"), api.get("/units")]);
      const gMap: Record<string, GuestOut> = {};
      for (const g of guests as GuestOut[]) gMap[g.id] = g;
      setGuestsById(gMap);
      const uMap: Record<string, UnitOut> = {};
      for (const u of units as UnitOut[]) uMap[u.id] = u;
      setUnitsById(uMap);
    } catch {
      // non-critical — the guest picker falls back to raw ids if this fails
    }
  }

  async function loadUsers() {
    if (!isAdmin) return; // /users is admin-only — staff can't resolve arbitrary names, by existing RBAC design
    try {
      const { data } = await api.get("/users");
      const map: Record<string, UserOut> = {};
      for (const u of data as UserOut[]) map[u.id] = u;
      setUsersById(map);
    } catch {
      // non-critical — assignment still shows a generic indicator if this fails
    }
  }

  async function loadTabData(current: Tab) {
    setLoading(true);
    setError(null);
    try {
      if (current === "today" || current === "check-ins") {
        const { data } = await api.get("/staff/check-ins");
        setCheckIns(data);
      }
      if (current === "today" || current === "check-outs") {
        const { data } = await api.get("/staff/check-outs");
        setCheckOuts(data);
      }
      if (current === "housekeeping") {
        const { data } = await api.get("/staff/tasks", { params: { category: "housekeeping" } });
        setTasks(data);
      }
      if (current === "maintenance") {
        const { data } = await api.get("/staff/tasks", { params: { category: "maintenance" } });
        setTasks(data);
      }
      if (current === "guest_request") {
        const [{ data: reqData }, { data: bookingsData }] = await Promise.all([
          api.get("/staff/tasks", { params: { category: "guest_request" } }),
          api.get("/bookings", { params: { status: "checked_in" } }),
        ]);
        setTasks(reqData);
        setInHouseBookings(bookingsData);
      }
      if (current === "mine") {
        const { data } = await api.get("/staff/tasks", { params: { assigned_to: myUserId ?? undefined } });
        setTasks(data);
      }
      if (current === "all") {
        const { data } = await api.get("/staff/tasks");
        setTasks(data);
      }
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadDashboard();
    loadUsers();
    loadGuestsAndUnits();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setExpandedId(null);
    loadTabData(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  function refreshAll() {
    loadDashboard();
    loadTabData(tab);
  }

  function retry() {
    setError(null);
    refreshAll();
  }

  async function handleCheckIn(bookingId: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.patch(`/bookings/${bookingId}/status`, null, { params: { new_status: "checked_in" } });
      setSuccess("Guest checked in.");
      refreshAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleCheckOut(bookingId: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.patch(`/bookings/${bookingId}/status`, null, { params: { new_status: "checked_out" } });
      setSuccess("Guest checked out.");
      refreshAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleTaskStatus(task: StaffTaskOut, nextStatus: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.patch(`/staff/tasks/${task.id}`, { status: nextStatus }, { params: { category: task.category } });
      setSuccess(SUCCESS_MESSAGE[task.category][nextStatus] ?? "Task updated.");
      refreshAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleAssign(task: StaffTaskOut, userId: string | null) {
    setError(null);
    setSuccess(null);
    setAssigningId(task.id);
    try {
      await api.patch(`/staff/tasks/${task.id}`, { assigned_to: userId }, { params: { category: task.category } });
      setSuccess(userId === null ? "Task unassigned." : userId === myUserId ? "Task claimed." : "Task assigned.");
      refreshAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setAssigningId(null);
    }
  }

  async function handleCreateRequest(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    if (!requestForm.booking_id) {
      setError("Select a guest (from currently checked-in bookings) first.");
      return;
    }
    try {
      await api.post("/guest-requests", {
        booking_id: requestForm.booking_id,
        request_type: requestForm.request_type,
        description: requestForm.description,
        assigned_to: requestForm.assigned_to || undefined,
      });
      setSuccess("Guest request logged.");
      setRequestForm({ booking_id: "", request_type: "towels", description: "", assigned_to: "" });
      setShowRequestForm(false);
      loadTabData("guest_request");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function assignedLabel(task: StaffTaskOut): string {
    if (!task.assigned_to) return "Unassigned";
    if (task.assigned_to === myUserId) return "Assigned to you";
    const u = usersById[task.assigned_to];
    return u ? `Assigned: ${u.name}` : "Assigned";
  }

  const urgentCount = useMemo(() => allOpenTasks.filter((t) => t.priority === "urgent").length, [allOpenTasks]);

  const todayStats = dashboard
    ? [
        { label: "Check-ins", value: dashboard.check_ins_today },
        { label: "Check-outs", value: dashboard.check_outs_today },
        { label: "Open Tasks", value: dashboard.pending_staff_tasks },
        { label: "Urgent", value: urgentCount, emphasize: urgentCount > 0 },
        { label: "Guest Requests", value: dashboard.open_guest_requests },
      ]
    : [];
  const secondaryStats = dashboard
    ? [
        { label: "Occupied", value: dashboard.occupied_units },
        { label: "Vacant", value: dashboard.vacant_units },
        { label: "Needs Cleaning", value: dashboard.units_needing_cleaning },
      ]
    : [];

  const visibleTasks = tasks.filter((t) => {
    if (statusFilter === "all") return true;
    if (statusFilter === "pending") return OPEN_STATUSES.has(t.status);
    if (statusFilter === "in_progress") return t.status === "in_progress";
    if (statusFilter === "completed") return DONE_STATUSES.has(t.status);
    return true;
  });

  const showTaskFilters = ["mine", "housekeeping", "maintenance", "guest_request", "all"].includes(tab);
  const showTaskList = showTaskFilters;

  return (
    <div>
      <PageHeader title="Staff Operations" />
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-2 mb-4 flex items-center justify-between gap-3">
          <span>{error}</span>
          <button onClick={retry} className="shrink-0 text-red-700 underline font-medium hover:text-red-900">
            Retry
          </button>
        </div>
      )}
      <SuccessBanner message={success} />

      {/* TODAY — the numbers staff need at a glance */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-2">
        {dashboard
          ? todayStats.map((s) => (
              <Card key={s.label} className={`p-3 ${s.emphasize ? "border-red-300 bg-red-50" : ""}`}>
                <p className={`text-2xl font-bold ${s.emphasize ? "text-red-700" : "text-slate-900"}`}>{s.value}</p>
                <p className={`text-xs mt-0.5 ${s.emphasize ? "text-red-600 font-medium" : "text-slate-500"}`}>{s.label}</p>
              </Card>
            ))
          : Array.from({ length: 5 }).map((_, i) => (
              <Card key={i} className="p-3">
                <SkeletonLine width="40%" />
                <div className="mt-2">
                  <SkeletonLine width="70%" />
                </div>
              </Card>
            ))}
      </div>
      <div className="grid grid-cols-3 gap-2 mb-6">
        {dashboard &&
          secondaryStats.map((s) => (
            <Card key={s.label} className="p-2.5">
              <p className="text-base font-semibold text-slate-700">{s.value}</p>
              <p className="text-[11px] text-slate-400">{s.label}</p>
            </Card>
          ))}
      </div>

      {/* Tabs — My Tasks leads, matching what staff need first */}
      <div className="flex gap-2 mb-3 overflow-x-auto pb-1" role="tablist" aria-label="Staff Operations sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 rounded-lg text-sm font-medium whitespace-nowrap transition focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 ${
              tab === t.key ? "bg-slate-900 text-white" : "bg-white border border-slate-300 text-slate-700 hover:bg-slate-50"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {showTaskFilters && (
        <div className="flex gap-2 mb-4 overflow-x-auto pb-1" role="tablist" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              role="tab"
              aria-selected={statusFilter === f.key}
              onClick={() => setStatusFilter(f.key)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 ${
                statusFilter === f.key ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="p-4">
              <SkeletonLine width="30%" />
              <div className="mt-2">
                <SkeletonLine width="60%" />
              </div>
            </Card>
          ))}
        </div>
      )}

      {!loading && (tab === "today" || tab === "check-ins") && (
        <Card className="mb-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Check-ins {tab === "today" ? "Today" : ""}</h2>
          {checkIns.length === 0 ? (
            <EmptyState message="No pending check-ins. All caught up." />
          ) : (
            <div className="space-y-3">
              {checkIns.map((c) => (
                <div key={c.booking_id} className="border border-slate-200 rounded-lg p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-lg font-bold text-slate-900">{c.unit_name}</p>
                      <p className="text-sm font-medium text-slate-700 mt-0.5">{c.guest_name}</p>
                      <p className="text-xs text-slate-500 mt-1">
                        {c.adults} adult{c.adults !== 1 ? "s" : ""}{c.children ? `, ${c.children} child${c.children !== 1 ? "ren" : ""}` : ""}
                        {c.arrival_time ? ` · Arriving ${c.arrival_time}` : ""}
                      </p>
                      <p className="text-xs text-slate-400 mt-0.5">Paid so far: ₹{c.net_paid.toLocaleString()}</p>
                    </div>
                    <Badge tone={statusTone(c.status)}>{statusLabel(c.status)}</Badge>
                  </div>
                  <Button variant="primary" className="w-full mt-3" onClick={() => handleCheckIn(c.booking_id)}>
                    Check In
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {!loading && (tab === "today" || tab === "check-outs") && (
        <Card className="mb-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Check-outs {tab === "today" ? "Today" : ""}</h2>
          {checkOuts.length === 0 ? (
            <EmptyState message="No pending check-outs. All caught up." />
          ) : (
            <div className="space-y-3">
              {checkOuts.map((c) => (
                <div key={c.booking_id} className="border border-slate-200 rounded-lg p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-lg font-bold text-slate-900">{c.unit_name}</p>
                      <p className="text-sm font-medium text-slate-700 mt-0.5">{c.guest_name}</p>
                      <p className="text-xs text-slate-500 mt-1">
                        {c.departure_time ? `Departing ${c.departure_time} · ` : ""}Room {c.housekeeping_status}
                      </p>
                    </div>
                    <Badge tone={statusTone(c.status)}>{statusLabel(c.status)}</Badge>
                  </div>
                  <Button variant="primary" className="w-full mt-3" onClick={() => handleCheckOut(c.booking_id)}>
                    Mark Checked Out
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {!loading && tab === "guest_request" && (
        <Card className="mb-4">
          {!showRequestForm ? (
            <button
              onClick={() => setShowRequestForm(true)}
              className="w-full text-center py-2 text-sm font-medium text-slate-700 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 rounded-lg"
            >
              + Log a Guest Request
            </button>
          ) : (
            <form onSubmit={handleCreateRequest} className="space-y-2">
              <h2 className="text-sm font-semibold text-slate-900 mb-1">Log a Guest Request</h2>
              <label className="block text-xs text-slate-500" htmlFor="req-booking">Guest</label>
              <select
                id="req-booking"
                required
                value={requestForm.booking_id}
                onChange={(e) => setRequestForm({ ...requestForm, booking_id: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">Select checked-in guest...</option>
                {inHouseBookings.map((b) => {
                  const guestName = guestsById[b.guest_id]?.name ?? "Guest";
                  const unitName = unitsById[b.unit_id]?.name ?? "Unit";
                  return (
                    <option key={b.id} value={b.id}>
                      {guestName} — {unitName} ({b.check_in} → {b.check_out})
                    </option>
                  );
                })}
              </select>
              <label className="block text-xs text-slate-500" htmlFor="req-type">Request type</label>
              <select
                id="req-type"
                value={requestForm.request_type}
                onChange={(e) => setRequestForm({ ...requestForm, request_type: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                {["towels", "linens", "cleaning", "water", "maintenance", "check_in_help", "check_out_help", "other"].map((t) => (
                  <option key={t} value={t}>{t.replace("_", " ")}</option>
                ))}
              </select>
              <label className="block text-xs text-slate-500" htmlFor="req-desc">What's needed?</label>
              <input
                id="req-desc"
                required
                placeholder="e.g. 2 extra towels"
                value={requestForm.description}
                onChange={(e) => setRequestForm({ ...requestForm, description: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <label className="block text-xs text-slate-500" htmlFor="req-assign">Assign to staff (optional)</label>
              <select
                id="req-assign"
                value={requestForm.assigned_to}
                onChange={(e) => setRequestForm({ ...requestForm, assigned_to: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">Unassigned</option>
                {Object.values(usersById)
                  .filter((u) => u.is_active)
                  .map((u) => (
                    <option key={u.id} value={u.id}>{u.name}</option>
                  ))}
              </select>
              <div className="flex gap-2 pt-1">
                <Button type="submit">Log Request</Button>
                <button type="button" onClick={() => setShowRequestForm(false)} className="text-sm text-slate-500 hover:underline">
                  Cancel
                </button>
              </div>
            </form>
          )}
        </Card>
      )}

      {!loading && showTaskList && (
        visibleTasks.length === 0 ? (
          <Card>
            <EmptyState
              message={
                tab === "mine"
                  ? "No tasks assigned to you. All caught up."
                  : "Nothing here right now."
              }
            />
          </Card>
        ) : (
          <div className="space-y-3">
            {visibleTasks.map((t) => {
              const action = NEXT_ACTION[t.category][t.status];
              const expanded = expandedId === t.id;
              return (
                <Card key={`${t.category}-${t.id}`} className="p-0 overflow-hidden">
                  <button
                    onClick={() => setExpandedId(expanded ? null : t.id)}
                    aria-expanded={expanded}
                    className="w-full text-left p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-slate-900"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-xl font-bold text-slate-900 truncate">{t.unit_name ?? "—"}</p>
                        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                          <Badge tone="slate">{t.category === "guest_request" ? "Guest Request" : t.category === "housekeeping" ? "Housekeeping" : "Maintenance"}</Badge>
                          {t.priority && (
                            <Badge tone={priorityTone(t.priority)}>
                              {t.priority === "urgent" ? "⚠ URGENT" : t.priority.toUpperCase()}
                            </Badge>
                          )}
                          <Badge tone={statusTone(t.status)}>{statusLabel(t.status)}</Badge>
                        </div>
                        <p className="text-sm font-medium text-slate-800 mt-1.5">{t.label}</p>
                        {t.notes && <p className="text-xs text-slate-500 mt-0.5 truncate">{t.notes}</p>}
                        <p className="text-xs text-slate-400 mt-1">{assignedLabel(t)} · {relativeTimeShort(t.created_at)}</p>
                      </div>
                    </div>
                  </button>

                  {expanded && (
                    <div className="px-4 pb-3 border-t border-slate-100 pt-3 text-xs text-slate-600 space-y-1">
                      <p><span className="text-slate-400">Apartment:</span> {t.unit_name ?? "—"}</p>
                      <p><span className="text-slate-400">{t.category === "maintenance" ? "Category" : "Type"}:</span> {t.label}</p>
                      <p><span className="text-slate-400">Status:</span> {statusLabel(t.status)}</p>
                      {t.priority && <p><span className="text-slate-400">Priority:</span> {t.priority}</p>}
                      {t.notes && <p><span className="text-slate-400">{t.category === "guest_request" ? "Staff notes" : t.category === "maintenance" ? "Description" : "Notes"}:</span> {t.notes}</p>}
                      {t.booking_id && <p><span className="text-slate-400">Booking:</span> {t.booking_id.slice(0, 8)}</p>}
                      <p><span className="text-slate-400">Assigned:</span> {assignedLabel(t)}</p>
                      <div className="flex items-center gap-2 flex-wrap pt-1 pb-1">
                        {/* Admin sees the full active-staff roster (usersById is only
                            populated for admin — /users is admin-only, same RBAC
                            boundary as every other admin-gated list in this app).
                            Staff sees just "Unassigned" here — same trade-off
                            GuestRequestsPage.tsx already makes — so they can release
                            an assignment but not pick a specific named colleague
                            without the roster. The "Claim this task" button below is
                            the separate, RBAC-safe way for staff to take ownership. */}
                        <select
                          value={t.assigned_to ?? ""}
                          disabled={assigningId === t.id}
                          onChange={(e) => handleAssign(t, e.target.value || null)}
                          aria-label={`Reassign ${t.label}`}
                          className="rounded-lg border border-slate-300 px-2 py-1 text-xs disabled:opacity-50"
                        >
                          <option value="">Unassigned</option>
                          {Object.values(usersById)
                            .filter((u) => u.is_active)
                            .map((u) => (
                              <option key={u.id} value={u.id}>{u.name}</option>
                            ))}
                        </select>
                        {!isAdmin && t.assigned_to !== myUserId && (
                          <Button
                            variant="secondary"
                            disabled={assigningId === t.id}
                            onClick={() => handleAssign(t, myUserId)}
                          >
                            {assigningId === t.id ? "Saving..." : "Claim this task"}
                          </Button>
                        )}
                        {assigningId === t.id && <span className="text-xs text-slate-400">Saving…</span>}
                      </div>
                      <p><span className="text-slate-400">Created:</span> {new Date(t.created_at).toLocaleString()}</p>
                      {t.started_at && <p><span className="text-slate-400">Started:</span> {new Date(t.started_at).toLocaleString()}</p>}
                      {t.completed_at && <p><span className="text-slate-400">Completed:</span> {new Date(t.completed_at).toLocaleString()}</p>}
                    </div>
                  )}

                  {action && (
                    <div className="px-4 pb-4">
                      <Button variant="primary" className="w-full" onClick={() => handleTaskStatus(t, action.next)}>
                        {action.label}
                      </Button>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )
      )}
    </div>
  );
}
