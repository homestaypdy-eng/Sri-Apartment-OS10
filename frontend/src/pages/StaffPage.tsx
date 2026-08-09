import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type AttendanceOut, type LeaveRequestOut, type PayrollOut, type ShiftOut, type HolidayOut, type UserOut, type UnitOut, type StaffApartmentAssignmentOut, type StaffPerformanceNoteOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge } from "../components/ui";
import { useAuth } from "../context/AuthContext";

type Tab = "attendance" | "leave" | "payroll" | "shifts" | "holidays" | "assignments" | "performance";

export default function StaffPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [tab, setTab] = useState<Tab>("attendance");
  const [users, setUsers] = useState<UserOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (isAdmin) {
      api.get("/users").then((r) => setUsers(r.data)).catch(() => {});
    }
  }, [isAdmin]);

  const tabs: { key: Tab; label: string }[] = [
    { key: "attendance", label: "Attendance" },
    { key: "leave", label: "Leave" },
    { key: "payroll", label: "Payroll" },
    { key: "shifts", label: "Shifts" },
    { key: "holidays", label: "Holidays" },
    { key: "assignments", label: "Apartment Assignment" },
    { key: "performance", label: "Performance Notes" },
  ];

  return (
    <div>
      <PageHeader title="Staff Management" />
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

      {tab === "attendance" && <AttendanceTab users={users} setError={setError} setSuccess={setSuccess} />}
      {tab === "leave" && <LeaveTab users={users} isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "payroll" && <PayrollTab users={users} isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "shifts" && <ShiftsTab users={users} isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "holidays" && <HolidaysTab isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "assignments" && <AssignmentsTab isAdmin={isAdmin} users={users} setError={setError} setSuccess={setSuccess} />}
      {tab === "performance" && <PerformanceTab isAdmin={isAdmin} users={users} setError={setError} setSuccess={setSuccess} />}
    </div>
  );
}

function userName(users: UserOut[], id: string) {
  return users.find((u) => u.id === id)?.name ?? id.slice(0, 8);
}

function AttendanceTab({ users, setError, setSuccess }: { users: UserOut[]; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<AttendanceOut[]>([]);
  const [form, setForm] = useState({ user_id: "", date: "", status: "present" });

  async function load() {
    try {
      const { data } = await api.get("/staff/attendance");
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/attendance", form);
      setSuccess("Attendance recorded.");
      setForm({ user_id: "", date: "", status: "present" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="space-y-6">
      <Card className="max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Mark Attendance</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <select required value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Select staff member...</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.name}</option>
            ))}
          </select>
          <div className="grid grid-cols-2 gap-3">
            <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="present">Present</option>
              <option value="absent">Absent</option>
              <option value="half_day">Half Day</option>
              <option value="on_leave">On Leave</option>
            </select>
          </div>
          <Button type="submit">Record</Button>
        </form>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Records</h2>
        <div className="overflow-x-auto">
          <Table headers={["Staff", "Date", "Status"]}>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{userName(users, r.user_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.date}</td>
                <td className="px-4 py-2.5"><Badge tone={r.status === "present" ? "green" : r.status === "absent" ? "red" : "amber"}>{r.status.replace("_", " ")}</Badge></td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

function LeaveTab({ users, isAdmin, setError, setSuccess }: { users: UserOut[]; isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<LeaveRequestOut[]>([]);
  const [form, setForm] = useState({ user_id: "", from_date: "", to_date: "", reason: "" });

  async function load() {
    try {
      const { data } = await api.get("/staff/leave");
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/leave", form);
      setSuccess("Leave request submitted.");
      setForm({ user_id: "", from_date: "", to_date: "", reason: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function review(id: string, status: "approved" | "rejected") {
    setError(null);
    try {
      await api.patch(`/staff/leave/${id}`, { status });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="space-y-6">
      <Card className="max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Apply for Leave</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <select required value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Select staff member...</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.name}</option>
            ))}
          </select>
          <div className="grid grid-cols-2 gap-3">
            <input required type="date" value={form.from_date} onChange={(e) => setForm({ ...form, from_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required type="date" value={form.to_date} onChange={(e) => setForm({ ...form, to_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <input placeholder="Reason" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <Button type="submit">Submit Request</Button>
        </form>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Leave Requests</h2>
        <div className="overflow-x-auto">
          <Table headers={["Staff", "From", "To", "Reason", "Status", isAdmin ? "Action" : ""]}>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{userName(users, r.user_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.from_date}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.to_date}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.reason ?? "—"}</td>
                <td className="px-4 py-2.5"><Badge tone={r.status === "approved" ? "green" : r.status === "rejected" ? "red" : "slate"}>{r.status}</Badge></td>
                <td className="px-4 py-2.5 space-x-2">
                  {isAdmin && r.status === "pending" && (
                    <>
                      <Button variant="secondary" onClick={() => review(r.id, "approved")}>Approve</Button>
                      <Button variant="danger" onClick={() => review(r.id, "rejected")}>Reject</Button>
                    </>
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

function PayrollTab({ users, isAdmin, setError, setSuccess }: { users: UserOut[]; isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<PayrollOut[]>([]);
  const [form, setForm] = useState({ user_id: "", period_year: String(new Date().getFullYear()), period_month: String(new Date().getMonth() + 1), basic_salary: "", deductions: "0", bonus: "0" });

  async function load() {
    try {
      const { data } = await api.get("/staff/payroll");
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/payroll", {
        user_id: form.user_id,
        period_year: Number(form.period_year),
        period_month: Number(form.period_month),
        basic_salary: Number(form.basic_salary),
        deductions: Number(form.deductions),
        bonus: Number(form.bonus),
      });
      setSuccess("Payroll generated.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function markPaid(id: string) {
    setError(null);
    try {
      await api.patch(`/staff/payroll/${id}/mark-paid`);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  if (!isAdmin) {
    return (
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Payroll</h2>
        <div className="overflow-x-auto">
          <Table headers={["Staff", "Period", "Net Pay", "Status"]}>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{userName(users, r.user_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.period_month}/{r.period_year}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{r.net_pay.toLocaleString()}</td>
                <td className="px-4 py-2.5"><Badge tone={r.status === "paid" ? "green" : "slate"}>{r.status}</Badge></td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card className="max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Generate Payroll</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <select required value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Select staff member...</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.name}</option>
            ))}
          </select>
          <div className="grid grid-cols-2 gap-3">
            <input required type="number" placeholder="Year" value={form.period_year} onChange={(e) => setForm({ ...form, period_year: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required type="number" placeholder="Month (1-12)" value={form.period_month} onChange={(e) => setForm({ ...form, period_month: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <input required type="number" placeholder="Basic salary" value={form.basic_salary} onChange={(e) => setForm({ ...form, basic_salary: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input type="number" placeholder="Deductions" value={form.deductions} onChange={(e) => setForm({ ...form, deductions: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input type="number" placeholder="Bonus" value={form.bonus} onChange={(e) => setForm({ ...form, bonus: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <Button type="submit">Generate</Button>
        </form>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Payroll Records</h2>
        <div className="overflow-x-auto">
          <Table headers={["Staff", "Period", "Basic", "Net Pay", "Status", "Action"]}>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{userName(users, r.user_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.period_month}/{r.period_year}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{r.basic_salary.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">₹{r.net_pay.toLocaleString()}</td>
                <td className="px-4 py-2.5"><Badge tone={r.status === "paid" ? "green" : "slate"}>{r.status}</Badge></td>
                <td className="px-4 py-2.5">
                  {r.status === "draft" && <Button variant="secondary" onClick={() => markPaid(r.id)}>Mark Paid</Button>}
                </td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

function ShiftsTab({ users, isAdmin, setError, setSuccess }: { users: UserOut[]; isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<ShiftOut[]>([]);
  const [form, setForm] = useState({ user_id: "", date: "", shift_start: "09:00", shift_end: "17:00" });

  async function load() {
    try {
      const { data } = await api.get("/staff/shifts");
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/shifts", { ...form, shift_start: `${form.shift_start}:00`, shift_end: `${form.shift_end}:00` });
      setSuccess("Shift created.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="space-y-6">
      {isAdmin && (
        <Card className="max-w-lg">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Create Shift</h2>
          <form onSubmit={handleSubmit} className="space-y-3">
            <select required value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">Select staff member...</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
            <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <div className="grid grid-cols-2 gap-3">
              <input type="time" value={form.shift_start} onChange={(e) => setForm({ ...form, shift_start: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              <input type="time" value={form.shift_end} onChange={(e) => setForm({ ...form, shift_end: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <Button type="submit">Create Shift</Button>
          </form>
        </Card>
      )}
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Shifts</h2>
        <div className="overflow-x-auto">
          <Table headers={["Staff", "Date", "Start", "End"]}>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{userName(users, r.user_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.date}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.shift_start}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.shift_end}</td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

function HolidaysTab({ isAdmin, setError, setSuccess }: { isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<HolidayOut[]>([]);
  const [form, setForm] = useState({ date: "", name: "" });

  async function load() {
    try {
      const { data } = await api.get("/staff/holidays");
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/holidays", form);
      setSuccess("Holiday added.");
      setForm({ date: "", name: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="space-y-6">
      {isAdmin && (
        <Card className="max-w-lg">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Holiday</h2>
          <form onSubmit={handleSubmit} className="space-y-3">
            <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required placeholder="Holiday name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <Button type="submit">Add Holiday</Button>
          </form>
        </Card>
      )}
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Holiday Calendar</h2>
        <div className="overflow-x-auto">
          <Table headers={["Date", "Name"]}>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{r.date}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.name}</td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
    </div>
  );
}

// --- Apartment Assignment ---
function AssignmentsTab({ isAdmin, users, setError, setSuccess }: { isAdmin: boolean; users: UserOut[]; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [assignments, setAssignments] = useState<StaffApartmentAssignmentOut[]>([]);
  const [form, setForm] = useState({ user_id: "", unit_id: "" });

  async function load() {
    try {
      const [u, a] = await Promise.all([api.get("/units"), api.get("/staff/apartment-assignments")]);
      setUnits(u.data);
      setAssignments(a.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function userName(id: string) {
    return users.find((u) => u.id === id)?.name ?? "—";
  }
  function unitName(id: string) {
    return units.find((u) => u.id === id)?.name ?? "—";
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/apartment-assignments", { user_id: form.user_id, unit_id: form.unit_id });
      setSuccess("Assignment created.");
      setForm({ user_id: "", unit_id: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.delete(`/staff/apartment-assignments/${id}`);
      setSuccess("Assignment removed.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      {isAdmin && (
        <Card className="mb-6 max-w-lg">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Assign Staff to an Apartment</h2>
          <form onSubmit={handleCreate} className="space-y-3">
            <select required value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">Select staff...</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <select required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">Select apartment...</option>
              {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <Button type="submit">Assign</Button>
          </form>
        </Card>
      )}
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Current Assignments</h2>
        {assignments.length === 0 ? (
          <p className="text-sm text-slate-400">No assignments yet.</p>
        ) : (
          <Table headers={["Staff", "Apartment", "Assigned On", isAdmin ? "Action" : ""]}>
            {assignments.map((a) => (
              <tr key={a.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{userName(a.user_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{unitName(a.unit_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{new Date(a.assigned_at).toLocaleDateString()}</td>
                <td className="px-4 py-2.5">
                  {isAdmin && (
                    <button onClick={() => handleDelete(a.id)} className="text-xs text-red-500 hover:text-red-700">
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

// --- Performance Notes ---
function PerformanceTab({ isAdmin, users, setError, setSuccess }: { isAdmin: boolean; users: UserOut[]; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [selectedUser, setSelectedUser] = useState("");
  const [notes, setNotes] = useState<StaffPerformanceNoteOut[]>([]);
  const [form, setForm] = useState({ user_id: "", note: "" });

  async function load(userId?: string) {
    try {
      const { data } = await api.get("/staff/performance-notes", { params: userId ? { user_id: userId } : {} });
      setNotes(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load(selectedUser || undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedUser]);

  function userName(id: string) {
    return users.find((u) => u.id === id)?.name ?? "—";
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/performance-notes", { user_id: form.user_id, note: form.note });
      setSuccess("Note added.");
      setForm({ user_id: form.user_id, note: "" });
      load(selectedUser || undefined);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      {isAdmin && (
        <Card className="mb-6 max-w-lg">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Performance Note</h2>
          <form onSubmit={handleCreate} className="space-y-3">
            <select required value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">Select staff...</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <textarea required rows={3} placeholder="Note" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <Button type="submit">Add Note</Button>
          </form>
        </Card>
      )}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Performance Notes</h2>
          <select value={selectedUser} onChange={(e) => setSelectedUser(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs">
            <option value="">All staff</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </div>
        {notes.length === 0 ? (
          <p className="text-sm text-slate-400">No notes yet.</p>
        ) : (
          <div className="space-y-3">
            {notes.map((n) => (
              <div key={n.id} className="border border-slate-100 rounded-lg p-3 text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-slate-800">{userName(n.user_id)}</span>
                  <span className="text-xs text-slate-400">{new Date(n.created_at).toLocaleDateString()}</span>
                </div>
                <p className="text-slate-600">{n.note}</p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
