import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type AttendanceOut, type LeaveRequestOut, type PayrollOut, type ShiftOut, type HolidayOut, type UserOut, type UnitOut, type StaffApartmentAssignmentOut, type StaffPerformanceNoteOut, type StaffProfileOut, type StaffAdvanceOut, type StaffDesignationOut, type ResolvedPolicyOut, type AttendanceMonthlySummaryOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge } from "../components/ui";
import { useAuth } from "../context/AuthContext";

type Tab = "directory" | "attendance" | "leave" | "payroll" | "shifts" | "holidays" | "assignments" | "performance" | "designations";

function minutesToHm(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "—";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h}h ${m}m`;
}

function sourceBadge(source: "override" | "designation" | "unconfigured" | undefined) {
  if (source === "override") return <Badge tone="blue">custom override</Badge>;
  if (source === "designation") return <Badge tone="slate">post default</Badge>;
  return <Badge tone="amber">unconfigured</Badge>;
}

const DESIGNATION_SUGGESTIONS = [
  "Property Manager", "Housekeeping Supervisor", "Housekeeping Attendant", "Maintenance Technician",
  "Receptionist", "Accountant", "Security", "Driver", "Cleaner",
];

export default function StaffPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [tab, setTab] = useState<Tab>("directory");
  const [users, setUsers] = useState<UserOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (isAdmin) {
      api.get("/users").then((r) => setUsers(r.data)).catch(() => {});
    }
  }, [isAdmin]);

  const tabs: { key: Tab; label: string }[] = [
    { key: "directory", label: "Staff Directory" },
    ...(isAdmin ? [{ key: "designations" as Tab, label: "Designations" }] : []),
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
              tab === t.key ? "border-brand-green text-brand-green" : "border-transparent text-slate-400 hover:text-slate-600"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "directory" && <DirectoryTab users={users} isAdmin={isAdmin} setError={setError} setSuccess={setSuccess} />}
      {tab === "designations" && isAdmin && <DesignationsTab setError={setError} setSuccess={setSuccess} />}
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

// --- Designations (post/designation master — admin-managed defaults that
// an individual employee's profile can override; see CompensationPanel) ---

const EMPTY_DESIGNATION_FORM = {
  id: "", name: "", default_basic_salary: "", default_shift_start: "09:00", default_shift_end: "18:00",
  default_lunch_break_minutes: "60", standard_working_hours: "8", leave_entitlement_per_month: "4",
  overtime_mode: "tracked_no_pay", overtime_rate_multiplier: "",
};

function DesignationsTab({ setError, setSuccess }: { setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [designations, setDesignations] = useState<StaffDesignationOut[]>([]);
  const [showInactive, setShowInactive] = useState(false);
  const [form, setForm] = useState(EMPTY_DESIGNATION_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);

  function load() {
    api.get("/staff/designations", { params: { include_inactive: showInactive } }).then((r) => setDesignations(r.data)).catch(() => {});
  }
  useEffect(load, [showInactive]);

  function startEdit(d: StaffDesignationOut) {
    setEditingId(d.id);
    setForm({
      id: d.id, name: d.name, default_basic_salary: String(d.default_basic_salary),
      default_shift_start: d.default_shift_start.slice(0, 5), default_shift_end: d.default_shift_end.slice(0, 5),
      default_lunch_break_minutes: String(d.default_lunch_break_minutes), standard_working_hours: String(d.standard_working_hours),
      leave_entitlement_per_month: String(d.leave_entitlement_per_month), overtime_mode: d.overtime_mode,
      overtime_rate_multiplier: d.overtime_rate_multiplier === null ? "" : String(d.overtime_rate_multiplier),
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    const payload = {
      name: form.name,
      default_basic_salary: Number(form.default_basic_salary),
      default_shift_start: `${form.default_shift_start}:00`,
      default_shift_end: `${form.default_shift_end}:00`,
      default_lunch_break_minutes: Number(form.default_lunch_break_minutes),
      standard_working_hours: Number(form.standard_working_hours),
      leave_entitlement_per_month: Number(form.leave_entitlement_per_month),
      overtime_mode: form.overtime_mode,
      overtime_rate_multiplier: form.overtime_rate_multiplier ? Number(form.overtime_rate_multiplier) : null,
    };
    try {
      if (editingId) {
        await api.patch(`/staff/designations/${editingId}`, payload);
        setSuccess("Designation updated.");
      } else {
        await api.post("/staff/designations", payload);
        setSuccess("Designation created.");
      }
      setForm(EMPTY_DESIGNATION_FORM);
      setEditingId(null);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function toggleActive(d: StaffDesignationOut) {
    setError(null);
    try {
      await api.patch(`/staff/designations/${d.id}/${d.is_active ? "deactivate" : "reactivate"}`);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <h3 className="text-sm font-semibold text-slate-900 mb-1">{editingId ? "Edit Designation" : "New Designation"}</h3>
        <p className="text-xs text-slate-400 mb-3">
          These are the defaults every employee assigned to this post gets — salary, shift timing, lunch break, standard hours,
          overtime handling, and leave entitlement. An individual employee's profile can override any of these; see the
          "Compensation & Schedule" tab on their staff profile.
        </p>
        <form onSubmit={handleSubmit} className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <input placeholder="Post name (e.g. Manager)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required className="rounded-lg border border-slate-300 px-2 py-1.5 col-span-2" />
          <input type="number" min="0" step="0.01" placeholder="Default basic salary (₹/month)" value={form.default_basic_salary} onChange={(e) => setForm({ ...form, default_basic_salary: e.target.value })} required className="rounded-lg border border-slate-300 px-2 py-1.5 col-span-2" />
          <div>
            <label className="block text-slate-500 mb-1">Shift start</label>
            <input type="time" value={form.default_shift_start} onChange={(e) => setForm({ ...form, default_shift_start: e.target.value })} required className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
          <div>
            <label className="block text-slate-500 mb-1">Shift end</label>
            <input type="time" value={form.default_shift_end} onChange={(e) => setForm({ ...form, default_shift_end: e.target.value })} required className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
          <div>
            <label className="block text-slate-500 mb-1">Lunch break (minutes)</label>
            <input type="number" min="0" value={form.default_lunch_break_minutes} onChange={(e) => setForm({ ...form, default_lunch_break_minutes: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
          <div>
            <label className="block text-slate-500 mb-1">Standard working hours</label>
            <input type="number" min="0" step="0.5" value={form.standard_working_hours} onChange={(e) => setForm({ ...form, standard_working_hours: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
          <div>
            <label className="block text-slate-500 mb-1">Leave entitlement / month</label>
            <input type="number" min="0" value={form.leave_entitlement_per_month} onChange={(e) => setForm({ ...form, leave_entitlement_per_month: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
          <div>
            <label className="block text-slate-500 mb-1">Overtime handling</label>
            <select value={form.overtime_mode} onChange={(e) => setForm({ ...form, overtime_mode: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5">
              <option value="untracked">Not tracked</option>
              <option value="tracked_no_pay">Tracked, no auto-pay</option>
              <option value="tracked_with_rate">Tracked, with rate</option>
            </select>
          </div>
          {form.overtime_mode === "tracked_with_rate" && (
            <div>
              <label className="block text-slate-500 mb-1">Overtime rate multiplier</label>
              <input type="number" min="0" step="0.1" placeholder="e.g. 1.5" value={form.overtime_rate_multiplier} onChange={(e) => setForm({ ...form, overtime_rate_multiplier: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
            </div>
          )}
          <div className="col-span-2 md:col-span-4 flex gap-2">
            <Button type="submit">{editingId ? "Save Changes" : "Create Designation"}</Button>
            {editingId && <Button type="button" variant="secondary" onClick={() => { setEditingId(null); setForm(EMPTY_DESIGNATION_FORM); }}>Cancel</Button>}
          </div>
        </form>
      </Card>

      <Card>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-900">Posts / Designations</h3>
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            Show deactivated
          </label>
        </div>
        <Table headers={["Post", "Default Salary", "Shift", "Lunch", "Std. Hours", "Leave/mo", "Overtime", "Status", "Actions"]}>
          {designations.map((d) => (
            <tr key={d.id}>
              <td className="px-4 py-2.5 font-medium text-slate-800">{d.name}</td>
              <td className="px-4 py-2.5 text-slate-500">₹{d.default_basic_salary.toLocaleString()}</td>
              <td className="px-4 py-2.5 text-slate-500">{d.default_shift_start.slice(0, 5)}–{d.default_shift_end.slice(0, 5)}</td>
              <td className="px-4 py-2.5 text-slate-500">{d.default_lunch_break_minutes}m</td>
              <td className="px-4 py-2.5 text-slate-500">{d.standard_working_hours}h</td>
              <td className="px-4 py-2.5 text-slate-500">{d.leave_entitlement_per_month}</td>
              <td className="px-4 py-2.5 text-slate-500">
                {d.overtime_mode === "untracked" ? "Not tracked" : d.overtime_mode === "tracked_no_pay" ? "Tracked only" : `Tracked ×${d.overtime_rate_multiplier ?? "—"}`}
              </td>
              <td className="px-4 py-2.5"><Badge tone={d.is_active ? "green" : "slate"}>{d.is_active ? "Active" : "Deactivated"}</Badge></td>
              <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                <button onClick={() => startEdit(d)} className="text-xs text-slate-500 hover:text-slate-900">Edit</button>
                <button onClick={() => toggleActive(d)} className="text-xs text-slate-500 hover:text-slate-900">{d.is_active ? "Deactivate" : "Reactivate"}</button>
              </td>
            </tr>
          ))}
        </Table>
        {designations.length === 0 && <p className="text-xs text-slate-400 mt-2">No designations created yet.</p>}
      </Card>
    </div>
  );
}

// --- Staff Directory ---

function DirectoryTab({ users, isAdmin, setError, setSuccess }: { users: UserOut[]; isAdmin: boolean; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [profiles, setProfiles] = useState<StaffProfileOut[]>([]);
  const [payroll, setPayroll] = useState<PayrollOut[]>([]);
  const [modalTarget, setModalTarget] = useState<{ user: UserOut; profile: StaffProfileOut | null } | null>(null);

  async function load() {
    try {
      const { data } = await api.get("/staff/profiles");
      setProfiles(data);
      if (isAdmin) {
        const p = await api.get("/staff/payroll");
        setPayroll(p.data);
      }
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  function profileFor(userId: string) {
    return profiles.find((p) => p.user_id === userId) ?? null;
  }
  function latestSalary(userId: string) {
    const list = payroll.filter((p) => p.user_id === userId).sort((a, b) => b.period_year - a.period_year || b.period_month - a.period_month);
    return list.length > 0 ? list[0].basic_salary : null;
  }

  function handleSaved(profile: StaffProfileOut) {
    setProfiles((prev) => (prev.some((p) => p.id === profile.id) ? prev.map((p) => (p.id === profile.id ? profile : p)) : [...prev, profile]));
    setModalTarget((prev) => (prev ? { ...prev, profile } : prev));
  }

  if (!isAdmin) {
    const own = profiles[0] ?? null;
    return (
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">My Staff Profile</h2>
        {own === null ? (
          <p className="text-sm text-slate-400">No staff profile has been created for you yet — contact an admin.</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 text-sm max-w-xl">
            <p><span className="text-slate-400">Designation:</span> {own.designation ?? "—"}</p>
            <p><span className="text-slate-400">Department:</span> {own.department ?? "—"}</p>
            <p><span className="text-slate-400">Employee ID:</span> {own.employee_id ?? "—"}</p>
            <p><span className="text-slate-400">Employment Type:</span> {own.employment_type ?? "—"}</p>
            <p><span className="text-slate-400">Phone:</span> {own.phone ?? "—"}</p>
            <p><span className="text-slate-400">Join Date:</span> {own.join_date ?? "—"}</p>
            <p><span className="text-slate-400">Weekly Off:</span> {own.weekly_off_day ?? "—"}</p>
            <p><span className="text-slate-400">Status:</span> <Badge tone={own.is_active ? "green" : "slate"}>{own.is_active ? "Active" : "Inactive"}</Badge></p>
          </div>
        )}
      </Card>
    );
  }

  return (
    <div>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Staff Directory</h2>
        <div className="overflow-x-auto">
          <Table headers={["Employee", "Designation", "Role", "Phone", "Salary", "Join Date", "Status", "Actions"]}>
            {users.map((u) => {
              const profile = profileFor(u.id);
              const salary = latestSalary(u.id);
              return (
                <tr key={u.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{u.name}</td>
                  <td className="px-4 py-2.5 text-slate-500">{profile?.designation ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500 capitalize">{u.role}</td>
                  <td className="px-4 py-2.5 text-slate-500">{profile?.phone ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{salary !== null ? `₹${salary.toLocaleString()}` : "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{profile?.join_date ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    {profile === null ? <Badge tone="slate">No Profile</Badge> : <Badge tone={profile.is_active ? "green" : "slate"}>{profile.is_active ? "Active" : "Inactive"}</Badge>}
                  </td>
                  <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                    {profile === null ? (
                      <Button variant="secondary" onClick={() => setModalTarget({ user: u, profile: null })}>Create Profile</Button>
                    ) : (
                      <>
                        <Button variant="secondary" onClick={() => setModalTarget({ user: u, profile })}>View</Button>
                        <Button variant="secondary" onClick={() => setModalTarget({ user: u, profile })}>Edit</Button>
                        <ToggleActiveProfileButton profile={profile} setError={setError} setSuccess={setSuccess} onDone={handleSaved} />
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </Table>
        </div>
      </Card>

      {modalTarget && (
        <StaffProfileModal
          user={modalTarget.user}
          profile={modalTarget.profile}
          users={users}
          onClose={() => setModalTarget(null)}
          onSaved={handleSaved}
          setError={setError}
          setSuccess={setSuccess}
        />
      )}
    </div>
  );
}

function ToggleActiveProfileButton({ profile, setError, setSuccess, onDone }: { profile: StaffProfileOut; setError: (e: string | null) => void; setSuccess: (s: string | null) => void; onDone: (p: StaffProfileOut) => void }) {
  async function toggle() {
    const action = profile.is_active ? "deactivate" : "reactivate";
    if (!window.confirm(`${profile.is_active ? "Deactivate" : "Reactivate"} this staff profile? Attendance, payroll, leave, shift, performance, and advance history are all preserved either way.`)) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.patch(`/staff/profiles/${profile.id}/${action}`);
      setSuccess(`Staff profile ${action}d.`);
      onDone(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  return (
    <Button variant={profile.is_active ? "danger" : "secondary"} onClick={toggle}>
      {profile.is_active ? "Deactivate" : "Reactivate"}
    </Button>
  );
}

type ProfileModalTab =
  | "overview" | "employment" | "compensation" | "personal" | "emergency" | "documents" | "bank" | "work"
  | "attendance" | "leave" | "payroll" | "advances" | "shifts" | "performance" | "audit";

const PROFILE_EDIT_TABS: ProfileModalTab[] = ["employment", "personal", "emergency", "documents", "bank", "work"];
const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

function StaffProfileModal({
  user, profile, users, onClose, onSaved, setError, setSuccess,
}: {
  user: UserOut; profile: StaffProfileOut | null; users: UserOut[];
  onClose: () => void; onSaved: (p: StaffProfileOut) => void;
  setError: (e: string | null) => void; setSuccess: (s: string | null) => void;
}) {
  const [modalTab, setModalTab] = useState<ProfileModalTab>("overview");
  const [currentProfile, setCurrentProfile] = useState<StaffProfileOut | null>(profile);
  const [form, setForm] = useState<Record<string, string>>({
    employee_id: profile?.employee_id ?? "", designation: profile?.designation ?? "", department: profile?.department ?? "",
    employment_type: profile?.employment_type ?? "", join_date: profile?.join_date ?? "", exit_date: profile?.exit_date ?? "",
    phone: profile?.phone ?? "", alternate_phone: profile?.alternate_phone ?? "",
    date_of_birth: profile?.date_of_birth ?? "", address: profile?.address ?? "",
    emergency_contact_name: profile?.emergency_contact_name ?? "", emergency_contact_phone: profile?.emergency_contact_phone ?? "",
    emergency_contact_relationship: profile?.emergency_contact_relationship ?? "",
    id_type: profile?.id_type ?? "", id_number: profile?.id_number ?? "",
    bank_name: profile?.bank_name ?? "", account_holder_name: profile?.account_holder_name ?? "",
    account_number: profile?.account_number ?? "", ifsc: profile?.ifsc ?? "", payment_method: profile?.payment_method ?? "",
    weekly_off_day: profile?.weekly_off_day ?? "", supervisor_user_id: profile?.supervisor_user_id ?? "", notes: profile?.notes ?? "",
  });

  async function handleSave() {
    setError(null);
    setSuccess(null);
    try {
      const payload: Record<string, string | undefined> = {};
      Object.entries(form).forEach(([k, v]) => { payload[k] = v || undefined; });
      if (currentProfile === null) {
        const { data } = await api.post("/staff/profiles", { user_id: user.id, ...payload });
        setCurrentProfile(data);
        setSuccess("Staff profile created.");
        onSaved(data);
      } else {
        const { data } = await api.patch(`/staff/profiles/${currentProfile.id}`, payload);
        setCurrentProfile(data);
        setSuccess("Staff profile updated.");
        onSaved(data);
      }
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const modalTabs: { key: ProfileModalTab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "employment", label: "Employment" },
    { key: "compensation", label: "Compensation & Schedule" },
    { key: "personal", label: "Personal" },
    { key: "emergency", label: "Emergency Contact" },
    { key: "documents", label: "Documents / ID" },
    { key: "bank", label: "Bank & Payroll" },
    { key: "work", label: "Work Assignment" },
    { key: "attendance", label: "Attendance" },
    { key: "leave", label: "Leave" },
    { key: "payroll", label: "Payroll" },
    { key: "advances", label: "Advances" },
    { key: "shifts", label: "Shifts" },
    { key: "performance", label: "Performance" },
    { key: "audit", label: "Audit History" },
  ];

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-900">{user.name}{currentProfile?.designation ? ` — ${currentProfile.designation}` : ""}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
        </div>

        <div className="flex gap-1 mb-4 border-b border-slate-200 overflow-x-auto">
          {modalTabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setModalTab(t.key)}
              className={`px-3 py-1.5 text-xs font-medium whitespace-nowrap border-b-2 -mb-px ${
                modalTab === t.key ? "border-brand-green text-brand-green" : "border-transparent text-slate-400 hover:text-slate-600"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {modalTab === "overview" && (
          <div className="grid grid-cols-2 gap-3 text-sm">
            <p><span className="text-slate-400">Name:</span> {user.name}</p>
            <p><span className="text-slate-400">Email:</span> {user.email}</p>
            <p><span className="text-slate-400">System Role:</span> <span className="capitalize">{user.role}</span></p>
            <p><span className="text-slate-400">Designation:</span> {currentProfile?.designation ?? "—"}</p>
            <p><span className="text-slate-400">Employee ID:</span> {currentProfile?.employee_id ?? "—"}</p>
            <p><span className="text-slate-400">Department:</span> {currentProfile?.department ?? "—"}</p>
            <p><span className="text-slate-400">Phone:</span> {currentProfile?.phone ?? "—"}</p>
            <p><span className="text-slate-400">Join Date:</span> {currentProfile?.join_date ?? "—"}</p>
            <p><span className="text-slate-400">Status:</span> {currentProfile ? <Badge tone={currentProfile.is_active ? "green" : "slate"}>{currentProfile.is_active ? "Active" : "Inactive"}</Badge> : <Badge tone="slate">No Profile</Badge>}</p>
            <p><span className="text-slate-400">Supervisor:</span> {currentProfile?.supervisor_user_id ? (users.find((u) => u.id === currentProfile.supervisor_user_id)?.name ?? "—") : "—"}</p>
          </div>
        )}

        {modalTab === "employment" && (
          <div className="grid grid-cols-2 gap-2 text-xs">
            <input placeholder="Employee ID" value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Designation" list="designation-suggestions" value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <datalist id="designation-suggestions">
              {DESIGNATION_SUGGESTIONS.map((d) => <option key={d} value={d} />)}
            </datalist>
            <input placeholder="Department" value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Employment type (e.g. full_time)" value={form.employment_type} onChange={(e) => setForm({ ...form, employment_type: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <div>
              <label className="block text-slate-500 mb-1">Join Date</label>
              <input type="date" value={form.join_date} onChange={(e) => setForm({ ...form, join_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
            </div>
            <div>
              <label className="block text-slate-500 mb-1">Exit Date</label>
              <input type="date" value={form.exit_date} onChange={(e) => setForm({ ...form, exit_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
            </div>
            <select value={form.supervisor_user_id} onChange={(e) => setForm({ ...form, supervisor_user_id: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 col-span-2">
              <option value="">No supervisor</option>
              {users.filter((u) => u.id !== user.id).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <textarea placeholder="Notes" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="col-span-2 rounded-lg border border-slate-300 px-2 py-1.5" rows={2} />
          </div>
        )}

        {modalTab === "compensation" && (
          currentProfile ? (
            <CompensationPanel profile={currentProfile} onSaved={(p) => { setCurrentProfile(p); onSaved(p); }} setError={setError} setSuccess={setSuccess} />
          ) : (
            <p className="text-xs text-slate-400">Create the staff profile first (Employment tab) before setting compensation and schedule.</p>
          )
        )}

        {modalTab === "personal" && (
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <label className="block text-slate-500 mb-1">Date of Birth</label>
              <input type="date" value={form.date_of_birth} onChange={(e) => setForm({ ...form, date_of_birth: e.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5" />
            </div>
            <input placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Alternate Phone" value={form.alternate_phone} onChange={(e) => setForm({ ...form, alternate_phone: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Address" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} className="col-span-2 rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
        )}

        {modalTab === "emergency" && (
          <div className="grid grid-cols-2 gap-2 text-xs">
            <input placeholder="Emergency Contact Name" value={form.emergency_contact_name} onChange={(e) => setForm({ ...form, emergency_contact_name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Emergency Contact Phone" value={form.emergency_contact_phone} onChange={(e) => setForm({ ...form, emergency_contact_phone: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Relationship" value={form.emergency_contact_relationship} onChange={(e) => setForm({ ...form, emergency_contact_relationship: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
        )}

        {modalTab === "documents" && (
          <div className="grid grid-cols-2 gap-2 text-xs">
            <input placeholder="ID Type (e.g. aadhaar)" value={form.id_type} onChange={(e) => setForm({ ...form, id_type: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="ID Number" value={form.id_number} onChange={(e) => setForm({ ...form, id_number: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
          </div>
        )}

        {modalTab === "bank" && (
          <div className="grid grid-cols-2 gap-2 text-xs">
            <input placeholder="Bank Name" value={form.bank_name} onChange={(e) => setForm({ ...form, bank_name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Account Holder Name" value={form.account_holder_name} onChange={(e) => setForm({ ...form, account_holder_name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="Account Number" value={form.account_number} onChange={(e) => setForm({ ...form, account_number: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <input placeholder="IFSC" value={form.ifsc} onChange={(e) => setForm({ ...form, ifsc: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            <select value={form.payment_method} onChange={(e) => setForm({ ...form, payment_method: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5">
              <option value="">Payment method...</option>
              <option value="bank_transfer">Bank Transfer</option>
              <option value="cash">Cash</option>
              <option value="upi">UPI</option>
            </select>
            {currentProfile && <p className="col-span-2 text-slate-400">Current salary is read from the latest Payroll record — see the Payroll tab. No separate salary field is stored here to avoid it drifting out of sync.</p>}
          </div>
        )}

        {modalTab === "work" && (
          <div className="space-y-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Weekly Off Day</label>
              <select value={form.weekly_off_day} onChange={(e) => setForm({ ...form, weekly_off_day: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                <option value="">Not set</option>
                {WEEKDAYS.map((d) => <option key={d} value={d} className="capitalize">{d}</option>)}
              </select>
            </div>
            {currentProfile ? (
              <WorkAssignmentPanel targetUserId={user.id} setError={setError} setSuccess={setSuccess} />
            ) : (
              <p className="text-xs text-slate-400">Create the staff profile first (any tab, then Create Profile) before assigning apartments.</p>
            )}
          </div>
        )}

        {PROFILE_EDIT_TABS.includes(modalTab) && (
          <Button variant="secondary" onClick={handleSave} type="button">
            {currentProfile === null ? "Create Profile" : "Save Changes"}
          </Button>
        )}

        {modalTab === "attendance" && <ScopedAttendancePanel userId={user.id} setError={setError} setSuccess={setSuccess} />}
        {modalTab === "leave" && <ScopedLeavePanel userId={user.id} setError={setError} setSuccess={setSuccess} />}
        {modalTab === "payroll" && <ScopedPayrollPanel userId={user.id} setError={setError} setSuccess={setSuccess} />}
        {modalTab === "advances" && <ScopedAdvancesPanel userId={user.id} setError={setError} setSuccess={setSuccess} />}
        {modalTab === "shifts" && <ScopedShiftsPanel userId={user.id} setError={setError} setSuccess={setSuccess} />}
        {modalTab === "performance" && <ScopedPerformancePanel userId={user.id} setError={setError} setSuccess={setSuccess} />}
        {modalTab === "audit" && (currentProfile ? <ScopedAuditPanel entityId={currentProfile.id} setError={setError} /> : <p className="text-xs text-slate-400">Create the staff profile first to see its audit history.</p>)}
      </div>
    </div>
  );
}

// Post default -> employee-specific override, resolved. Shows what
// actually applies right now (via GET /staff/policy/{user_id}) and lets
// an admin set/clear each override individually — clearing an override
// (empty input, Save) reverts that field to the linked post's default.
function CompensationPanel({
  profile, onSaved, setError, setSuccess,
}: {
  profile: StaffProfileOut; onSaved: (p: StaffProfileOut) => void;
  setError: (e: string | null) => void; setSuccess: (s: string | null) => void;
}) {
  const [designations, setDesignations] = useState<StaffDesignationOut[]>([]);
  const [policy, setPolicy] = useState<ResolvedPolicyOut | null>(null);
  const [designationId, setDesignationId] = useState(profile.designation_id ?? "");
  const [overrides, setOverrides] = useState({
    salary_override: profile.salary_override === null ? "" : String(profile.salary_override),
    shift_start_override: profile.shift_start_override?.slice(0, 5) ?? "",
    shift_end_override: profile.shift_end_override?.slice(0, 5) ?? "",
    lunch_break_override_minutes: profile.lunch_break_override_minutes === null ? "" : String(profile.lunch_break_override_minutes),
    standard_working_hours_override: profile.standard_working_hours_override === null ? "" : String(profile.standard_working_hours_override),
    leave_entitlement_override: profile.leave_entitlement_override === null ? "" : String(profile.leave_entitlement_override),
  });

  function loadPolicy() {
    api.get(`/staff/policy/${profile.user_id}`).then((r) => setPolicy(r.data)).catch(() => {});
  }
  useEffect(() => {
    api.get("/staff/designations").then((r) => setDesignations(r.data)).catch(() => {});
    loadPolicy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.user_id]);

  async function handleSave() {
    setError(null);
    setSuccess(null);
    // Empty input explicitly clears the override (reverts to post
    // default) — sent as null, not omitted, so the backend's
    // exclude_unset PATCH actually changes it rather than leaving it alone.
    const payload = {
      designation_id: designationId || null,
      salary_override: overrides.salary_override === "" ? null : Number(overrides.salary_override),
      shift_start_override: overrides.shift_start_override === "" ? null : `${overrides.shift_start_override}:00`,
      shift_end_override: overrides.shift_end_override === "" ? null : `${overrides.shift_end_override}:00`,
      lunch_break_override_minutes: overrides.lunch_break_override_minutes === "" ? null : Number(overrides.lunch_break_override_minutes),
      standard_working_hours_override: overrides.standard_working_hours_override === "" ? null : Number(overrides.standard_working_hours_override),
      leave_entitlement_override: overrides.leave_entitlement_override === "" ? null : Number(overrides.leave_entitlement_override),
    };
    try {
      const { data } = await api.patch(`/staff/profiles/${profile.id}`, payload);
      onSaved(data);
      setSuccess("Compensation & schedule updated.");
      loadPolicy();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const rows: { label: string; overrideKey: keyof typeof overrides; type: string; resolved: string; source?: "override" | "designation" | "unconfigured" }[] = [
    { label: "Basic salary (₹/month)", overrideKey: "salary_override", type: "number",
      resolved: policy?.basic_salary != null ? `₹${policy.basic_salary.toLocaleString()}` : "—", source: policy?.source.basic_salary },
    { label: "Shift start", overrideKey: "shift_start_override", type: "time",
      resolved: policy?.shift_start ?? "—", source: policy?.source.shift_start },
    { label: "Shift end", overrideKey: "shift_end_override", type: "time",
      resolved: policy?.shift_end ?? "—", source: policy?.source.shift_end },
    { label: "Lunch break (minutes)", overrideKey: "lunch_break_override_minutes", type: "number",
      resolved: policy?.lunch_break_minutes != null ? `${policy.lunch_break_minutes}m` : "—", source: policy?.source.lunch_break_minutes },
    { label: "Standard working hours", overrideKey: "standard_working_hours_override", type: "number",
      resolved: policy?.standard_working_hours != null ? `${policy.standard_working_hours}h` : "—", source: policy?.source.standard_working_hours },
    { label: "Leave entitlement / month", overrideKey: "leave_entitlement_override", type: "number",
      resolved: policy?.leave_entitlement_per_month != null ? String(policy.leave_entitlement_per_month) : "—", source: policy?.source.leave_entitlement_per_month },
  ];

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs text-slate-500 mb-1">Post / Designation</label>
        <select value={designationId} onChange={(e) => setDesignationId(e.target.value)} className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
          <option value="">No post linked (defaults unconfigured)</option>
          {designations.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </div>

      {policy?.overtime_mode && (
        <p className="text-xs text-slate-400">
          Overtime: {policy.overtime_mode === "untracked" ? "not tracked for this post" : policy.overtime_mode === "tracked_no_pay" ? "hours tracked, not auto-paid" : `tracked at ×${policy.overtime_rate_multiplier}`}
        </p>
      )}

      <div className="border border-slate-200 rounded-xl overflow-hidden">
        <table className="min-w-full text-xs">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-slate-500">Field</th>
              <th className="text-left px-3 py-2 font-medium text-slate-500">Currently Applies</th>
              <th className="text-left px-3 py-2 font-medium text-slate-500">Employee Override</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.overrideKey}>
                <td className="px-3 py-2 text-slate-700">{r.label}</td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-600">{r.resolved}</span>
                    {sourceBadge(r.source)}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <input
                    type={r.type}
                    step={r.overrideKey === "salary_override" ? "0.01" : r.overrideKey === "standard_working_hours_override" ? "0.5" : undefined}
                    placeholder="use post default"
                    value={overrides[r.overrideKey]}
                    onChange={(e) => setOverrides({ ...overrides, [r.overrideKey]: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-2 py-1"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-400">Leave any override field blank and save to revert that field to the post's default.</p>
      <Button onClick={handleSave} type="button">Save Compensation & Schedule</Button>
    </div>
  );
}

function WorkAssignmentPanel({ targetUserId, setError, setSuccess }: { targetUserId: string; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [assignments, setAssignments] = useState<StaffApartmentAssignmentOut[]>([]);
  const [unitId, setUnitId] = useState("");

  async function load() {
    try {
      const [u, a] = await Promise.all([api.get("/units"), api.get("/staff/apartment-assignments", { params: { user_id: targetUserId } })]);
      setUnits(u.data);
      setAssignments(a.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetUserId]);

  function unitName(id: string) {
    return units.find((u) => u.id === id)?.name ?? "—";
  }

  async function assign() {
    if (!unitId) return;
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/apartment-assignments", { user_id: targetUserId, unit_id: unitId });
      setSuccess("Apartment assigned.");
      setUnitId("");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function remove(id: string) {
    setError(null);
    try {
      await api.delete(`/staff/apartment-assignments/${id}`);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <h3 className="text-xs font-semibold text-slate-700 mb-2 uppercase tracking-wide">Assigned Apartments</h3>
      <div className="flex gap-2 mb-3">
        <select value={unitId} onChange={(e) => setUnitId(e.target.value)} className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
          <option value="">Select apartment...</option>
          {units.filter((u) => u.is_active).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
        </select>
        <Button variant="secondary" onClick={assign}>Assign</Button>
      </div>
      {assignments.length === 0 ? (
        <p className="text-xs text-slate-400">No apartments assigned yet.</p>
      ) : (
        <div className="space-y-1">
          {assignments.map((a) => (
            <div key={a.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-1.5 text-xs">
              <span>{unitName(a.unit_id)}</span>
              <button onClick={() => remove(a.id)} className="text-red-500 hover:underline">Remove</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScopedAttendancePanel({ userId, setError, setSuccess }: { userId: string; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<AttendanceOut[]>([]);
  const [form, setForm] = useState({ date: "", status: "present", check_in_time: "", check_out_time: "" });
  const [checkoutTimes, setCheckoutTimes] = useState<Record<string, string>>({});
  const [summary, setSummary] = useState<AttendanceMonthlySummaryOut | null>(null);
  const today = new Date();

  async function load() {
    try {
      const { data } = await api.get("/staff/attendance", { params: { user_id: userId } });
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  function loadSummary() {
    api.get("/staff/attendance/monthly-summary", { params: { user_id: userId, year: today.getFullYear(), month: today.getMonth() + 1 } })
      .then((r) => setSummary(r.data)).catch(() => {});
  }
  useEffect(() => {
    load();
    loadSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/attendance", {
        user_id: userId, date: form.date, status: form.status,
        check_in_time: form.check_in_time ? `${form.check_in_time}:00` : undefined,
        check_out_time: form.check_out_time ? `${form.check_out_time}:00` : undefined,
      });
      setSuccess("Attendance recorded.");
      setForm({ date: "", status: "present", check_in_time: "", check_out_time: "" });
      load();
      loadSummary();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleCheckOut(recordId: string) {
    const time = checkoutTimes[recordId];
    if (!time) return;
    setError(null);
    setSuccess(null);
    try {
      await api.patch(`/staff/attendance/${recordId}`, { check_out_time: `${time}:00` });
      setSuccess("Check-out recorded — hours computed.");
      load();
      loadSummary();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-xs">
          <div className="border border-slate-100 rounded-lg px-3 py-2"><p className="text-slate-400">This month, working hours</p><p className="font-medium text-slate-800">{minutesToHm(summary.total_working_minutes)}</p></div>
          <div className="border border-slate-100 rounded-lg px-3 py-2"><p className="text-slate-400">This month, overtime</p><p className="font-medium text-slate-800">{minutesToHm(summary.total_overtime_minutes)}</p></div>
          <div className="border border-slate-100 rounded-lg px-3 py-2"><p className="text-slate-400">Approved leave (free / excess)</p><p className="font-medium text-slate-800">{summary.free_leave_days} / {summary.excess_leave_days}</p></div>
          <div className="border border-slate-100 rounded-lg px-3 py-2"><p className="text-slate-400">Leave entitlement</p><p className="font-medium text-slate-800">{summary.leave_entitlement_per_month ?? "—"}/mo</p></div>
        </div>
      )}
      <form onSubmit={handleSubmit} className="flex flex-wrap gap-2 mb-3">
        <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input type="time" title="Check-in time" value={form.check_in_time} onChange={(e) => setForm({ ...form, check_in_time: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input type="time" title="Check-out time (leave blank to check out later)" value={form.check_out_time} onChange={(e) => setForm({ ...form, check_out_time: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
          <option value="present">Present</option>
          <option value="absent">Absent</option>
          <option value="half_day">Half Day</option>
          <option value="on_leave">On Leave</option>
        </select>
        <Button type="submit">Record</Button>
      </form>
      {records.length === 0 ? (
        <p className="text-xs text-slate-400">No attendance recorded yet.</p>
      ) : (
        <div className="space-y-1">
          {records.map((r) => (
            <div key={r.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-1.5 text-xs gap-2">
              <span className="w-24 shrink-0">{r.date}</span>
              <Badge tone={r.status === "present" ? "green" : r.status === "absent" ? "red" : "amber"}>{r.status.replace("_", " ")}</Badge>
              <span className="text-slate-500">{r.check_in_time?.slice(0, 5) ?? "—"} → {r.check_out_time?.slice(0, 5) ?? "—"}</span>
              {r.working_minutes !== null ? (
                <span className="text-slate-500 ml-auto">
                  {minutesToHm(r.working_minutes)} worked{r.overtime_minutes ? `, ${minutesToHm(r.overtime_minutes)} OT` : ""}
                </span>
              ) : r.check_in_time ? (
                <div className="flex items-center gap-1 ml-auto">
                  <input type="time" value={checkoutTimes[r.id] ?? ""} onChange={(e) => setCheckoutTimes({ ...checkoutTimes, [r.id]: e.target.value })} className="rounded border border-slate-300 px-1.5 py-0.5" />
                  <button onClick={() => handleCheckOut(r.id)} className="text-slate-500 hover:text-slate-900">Check out</button>
                </div>
              ) : (
                <span className="text-slate-300 ml-auto">no check-in</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScopedLeavePanel({ userId, setError, setSuccess }: { userId: string; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<LeaveRequestOut[]>([]);
  const [form, setForm] = useState({ from_date: "", to_date: "", reason: "" });

  async function load() {
    try {
      const { data } = await api.get("/staff/leave", { params: { user_id: userId } });
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/leave", { user_id: userId, ...form });
      setSuccess("Leave request submitted.");
      setForm({ from_date: "", to_date: "", reason: "" });
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
    <div>
      <form onSubmit={handleSubmit} className="flex flex-wrap gap-2 mb-3">
        <input required type="date" value={form.from_date} onChange={(e) => setForm({ ...form, from_date: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input required type="date" value={form.to_date} onChange={(e) => setForm({ ...form, to_date: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input placeholder="Reason" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="flex-1 min-w-[100px] rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <Button type="submit">Apply</Button>
      </form>
      {records.length === 0 ? (
        <p className="text-xs text-slate-400">No leave requests yet.</p>
      ) : (
        <div className="space-y-1">
          {records.map((r) => (
            <div key={r.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-1.5 text-xs">
              <span>{r.from_date} → {r.to_date}{r.reason ? ` — ${r.reason}` : ""}</span>
              <div className="flex items-center gap-2">
                <Badge tone={r.status === "approved" ? "green" : r.status === "rejected" ? "red" : "slate"}>{r.status}</Badge>
                {r.status === "pending" && (
                  <>
                    <button onClick={() => review(r.id, "approved")} className="text-emerald-600 hover:underline">Approve</button>
                    <button onClick={() => review(r.id, "rejected")} className="text-red-500 hover:underline">Reject</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScopedPayrollPanel({ userId, setError, setSuccess }: { userId: string; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<PayrollOut[]>([]);
  const [form, setForm] = useState({ period_year: String(new Date().getFullYear()), period_month: String(new Date().getMonth() + 1), basic_salary: "", deductions: "0", bonus: "0" });

  async function load() {
    try {
      const { data } = await api.get("/staff/payroll", { params: { user_id: userId } });
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/payroll", {
        user_id: userId, period_year: Number(form.period_year), period_month: Number(form.period_month),
        basic_salary: Number(form.basic_salary), deductions: Number(form.deductions), bonus: Number(form.bonus),
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

  return (
    <div>
      <form onSubmit={handleSubmit} className="flex flex-wrap gap-2 mb-3">
        <input required type="number" placeholder="Year" value={form.period_year} onChange={(e) => setForm({ ...form, period_year: e.target.value })} className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input required type="number" placeholder="Month" value={form.period_month} onChange={(e) => setForm({ ...form, period_month: e.target.value })} className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input required type="number" placeholder="Basic salary" value={form.basic_salary} onChange={(e) => setForm({ ...form, basic_salary: e.target.value })} className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input type="number" placeholder="Deductions" value={form.deductions} onChange={(e) => setForm({ ...form, deductions: e.target.value })} className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input type="number" placeholder="Bonus" value={form.bonus} onChange={(e) => setForm({ ...form, bonus: e.target.value })} className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <Button type="submit">Generate</Button>
      </form>
      {records.length === 0 ? (
        <p className="text-xs text-slate-400">No payroll records yet.</p>
      ) : (
        <div className="space-y-1">
          {records.map((r) => (
            <div key={r.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-1.5 text-xs">
              <span>{r.period_month}/{r.period_year} — Net ₹{r.net_pay.toLocaleString()}</span>
              <div className="flex items-center gap-2">
                <Badge tone={r.status === "paid" ? "green" : "slate"}>{r.status}</Badge>
                {r.status === "draft" && <button onClick={() => markPaid(r.id)} className="text-emerald-600 hover:underline">Mark Paid</button>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScopedAdvancesPanel({ userId, setError, setSuccess }: { userId: string; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<StaffAdvanceOut[]>([]);
  const [form, setForm] = useState({ date: "", amount: "", category: "advance", reason: "" });
  const [deductForm, setDeductForm] = useState<{ id: string; amount: string } | null>(null);

  async function load() {
    try {
      const { data } = await api.get("/staff/advances", { params: { user_id: userId } });
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/advances", { user_id: userId, date: form.date, amount: Number(form.amount), category: form.category, reason: form.reason || undefined });
      setSuccess("Advance recorded.");
      setForm({ date: "", amount: "", category: "advance", reason: "" });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeduct(e: FormEvent) {
    e.preventDefault();
    if (!deductForm) return;
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/staff/advances/${deductForm.id}/deduct`, { amount: Number(deductForm.amount) });
      setSuccess("Deduction recorded.");
      setDeductForm(null);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <form onSubmit={handleSubmit} className="flex flex-wrap gap-2 mb-3">
        <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input required type="number" placeholder="Amount" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
          <option value="advance">Advance</option>
          <option value="bonus">Bonus</option>
          <option value="reimbursement">Reimbursement</option>
          <option value="loan">Loan</option>
        </select>
        <input placeholder="Reason" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="flex-1 min-w-[100px] rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <Button type="submit">Record</Button>
      </form>
      {records.length === 0 ? (
        <p className="text-xs text-slate-400">No advances recorded yet.</p>
      ) : (
        <div className="space-y-2">
          {records.map((r) => (
            <div key={r.id} className="border border-slate-100 rounded-lg px-3 py-2 text-xs">
              <div className="flex items-center justify-between">
                <span>{r.date} — ₹{r.amount.toLocaleString()} ({r.category}){r.reason ? `: ${r.reason}` : ""}</span>
                <Badge tone={r.status === "fully_deducted" ? "green" : r.status === "partially_deducted" ? "amber" : "slate"}>{r.status.replace("_", " ")}</Badge>
              </div>
              <p className="text-slate-400 mt-1">Outstanding: ₹{r.outstanding_amount.toLocaleString()}</p>
              {r.status !== "fully_deducted" && deductForm?.id !== r.id && (
                <button onClick={() => setDeductForm({ id: r.id, amount: "" })} className="text-emerald-600 hover:underline mt-1">Record Deduction</button>
              )}
              {deductForm?.id === r.id && (
                <form onSubmit={handleDeduct} className="mt-2 flex gap-2 items-center bg-emerald-50 rounded-lg p-2">
                  <input required type="number" placeholder="Deduct amount" value={deductForm.amount} onChange={(e) => setDeductForm({ ...deductForm, amount: e.target.value })} className="w-28 rounded-md border border-slate-300 px-2 py-1 text-xs" />
                  <Button type="submit">Confirm</Button>
                  <button type="button" onClick={() => setDeductForm(null)} className="text-slate-500 hover:underline">Cancel</button>
                </form>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScopedShiftsPanel({ userId, setError, setSuccess }: { userId: string; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<ShiftOut[]>([]);
  const [form, setForm] = useState({ date: "", shift_start: "09:00", shift_end: "17:00" });

  async function load() {
    try {
      const { data } = await api.get("/staff/shifts", { params: { user_id: userId } });
      setRecords(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/shifts", { user_id: userId, date: form.date, shift_start: `${form.shift_start}:00`, shift_end: `${form.shift_end}:00` });
      setSuccess("Shift created.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <form onSubmit={handleSubmit} className="flex flex-wrap gap-2 mb-3">
        <input required type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input type="time" value={form.shift_start} onChange={(e) => setForm({ ...form, shift_start: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <input type="time" value={form.shift_end} onChange={(e) => setForm({ ...form, shift_end: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <Button type="submit">Create</Button>
      </form>
      {records.length === 0 ? (
        <p className="text-xs text-slate-400">No shifts scheduled yet.</p>
      ) : (
        <div className="space-y-1">
          {records.map((r) => (
            <div key={r.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-1.5 text-xs">
              <span>{r.date}</span>
              <span>{r.shift_start} – {r.shift_end}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScopedPerformancePanel({ userId, setError, setSuccess }: { userId: string; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [notes, setNotes] = useState<StaffPerformanceNoteOut[]>([]);
  const [note, setNote] = useState("");

  async function load() {
    try {
      const { data } = await api.get("/staff/performance-notes", { params: { user_id: userId } });
      setNotes(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/staff/performance-notes", { user_id: userId, note });
      setSuccess("Note added.");
      setNote("");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <form onSubmit={handleSubmit} className="flex gap-2 mb-3">
        <textarea required rows={2} placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
        <Button type="submit">Add</Button>
      </form>
      {notes.length === 0 ? (
        <p className="text-xs text-slate-400">No performance notes yet.</p>
      ) : (
        <div className="space-y-2">
          {notes.map((n) => (
            <div key={n.id} className="border border-slate-100 rounded-lg p-2 text-xs">
              <p className="text-slate-400 mb-1">{new Date(n.created_at).toLocaleDateString()}</p>
              <p className="text-slate-700">{n.note}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScopedAuditPanel({ entityId, setError }: { entityId: string; setError: (e: string | null) => void }) {
  const [logs, setLogs] = useState<{ id: string; action: string; details: string | null; created_at: string }[]>([]);

  useEffect(() => {
    api
      .get("/audit/logs", { params: { entity_type: "staff_profile", entity_id: entityId } })
      .then((r) => setLogs(r.data))
      .catch((err) => setError(extractErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId]);

  if (logs.length === 0) return <p className="text-xs text-slate-400">No audit history yet.</p>;
  return (
    <div className="space-y-2">
      {logs.map((l) => (
        <div key={l.id} className="text-xs border-l-2 border-slate-200 pl-3 py-1">
          <div className="flex items-center justify-between">
            <span className="font-medium text-slate-800">{l.action}</span>
            <span className="text-slate-400">{new Date(l.created_at).toLocaleString()}</span>
          </div>
          {l.details && <p className="text-slate-500 mt-0.5">{l.details}</p>}
        </div>
      ))}
    </div>
  );
}

function AttendanceTab({ users, setError, setSuccess }: { users: UserOut[]; setError: (e: string | null) => void; setSuccess: (s: string | null) => void }) {
  const [records, setRecords] = useState<AttendanceOut[]>([]);
  const [form, setForm] = useState({ user_id: "", date: "", status: "present", check_in_time: "", check_out_time: "" });

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
      await api.post("/staff/attendance", {
        user_id: form.user_id, date: form.date, status: form.status,
        check_in_time: form.check_in_time ? `${form.check_in_time}:00` : undefined,
        check_out_time: form.check_out_time ? `${form.check_out_time}:00` : undefined,
      });
      setSuccess("Attendance recorded.");
      setForm({ user_id: "", date: "", status: "present", check_in_time: "", check_out_time: "" });
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
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Check-in (optional)</label>
              <input type="time" value={form.check_in_time} onChange={(e) => setForm({ ...form, check_in_time: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Check-out (optional — can add later)</label>
              <input type="time" value={form.check_out_time} onChange={(e) => setForm({ ...form, check_out_time: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
          </div>
          <Button type="submit">Record</Button>
        </form>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Records</h2>
        <div className="overflow-x-auto">
          <Table headers={["Staff", "Date", "Status", "In → Out", "Worked / Overtime"]}>
            {records.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{userName(users, r.user_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{r.date}</td>
                <td className="px-4 py-2.5"><Badge tone={r.status === "present" ? "green" : r.status === "absent" ? "red" : "amber"}>{r.status.replace("_", " ")}</Badge></td>
                <td className="px-4 py-2.5 text-slate-500">{r.check_in_time?.slice(0, 5) ?? "—"} → {r.check_out_time?.slice(0, 5) ?? "—"}</td>
                <td className="px-4 py-2.5 text-slate-500">
                  {r.working_minutes !== null ? `${minutesToHm(r.working_minutes)}${r.overtime_minutes ? ` (+${minutesToHm(r.overtime_minutes)} OT)` : ""}` : "—"}
                </td>
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
  const [summary, setSummary] = useState<AttendanceMonthlySummaryOut | null>(null);

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

  // Selecting a staff member + period pre-fills basic salary from their
  // resolved policy (post default, or their own override) and pulls the
  // month's attendance/leave numbers in as reference — the admin still
  // reviews and can adjust everything before submitting; see plan §F on
  // why excess-leave/overtime deductions are never auto-filled.
  useEffect(() => {
    if (!form.user_id) { setSummary(null); return; }
    api.get(`/staff/policy/${form.user_id}`).then((r) => {
      if (r.data.basic_salary != null) setForm((f) => ({ ...f, basic_salary: String(r.data.basic_salary) }));
    }).catch(() => {});
    api.get("/staff/attendance/monthly-summary", { params: { user_id: form.user_id, year: Number(form.period_year), month: Number(form.period_month) } })
      .then((r) => setSummary(r.data)).catch(() => setSummary(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.user_id, form.period_year, form.period_month]);

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
          {summary && (
            <div className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2 space-y-0.5">
              <p>Basic salary pre-filled from their post default / override — review before generating.</p>
              <p>This period: {minutesToHm(summary.total_working_minutes)} worked, {minutesToHm(summary.total_overtime_minutes)} overtime.</p>
              <p>
                Approved leave: {summary.approved_leave_days_in_month} day(s) — {summary.free_leave_days} free
                {summary.excess_leave_days > 0 ? `, ${summary.excess_leave_days} beyond entitlement (no automatic deduction — add one above manually if applicable)` : ", within entitlement"}.
              </p>
            </div>
          )}
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
