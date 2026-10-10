import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type HousekeepingTaskOut, type HousekeepingPhotoOut, type UnitOut, type UserOut, type TimelineEvent, type InventoryItemOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState, Spinner, type BadgeTone } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useRealtimeRefresh } from "../lib/realtime";

const TASK_TYPES = ["routine", "checkout_clean", "deep_clean", "inspection"];

// Same standard OS10 apartment order already used on Properties &
// Availability Calendar — this page's own copy, matching how each of those
// pages independently keeps its own local ordering helper rather than
// sharing one, to keep every per-page polish task narrowly scoped.
const UNIT_ORDER = ["A1", "A2", "B3", "B4", "C5", "C6"];

function orderUnits(list: UnitOut[]): UnitOut[] {
  return [...list].sort((a, b) => {
    const aCode = a.unit_code || a.name;
    const bCode = b.unit_code || b.name;
    const aIndex = UNIT_ORDER.indexOf(aCode);
    const bIndex = UNIT_ORDER.indexOf(bCode);
    if (aIndex >= 0 || bIndex >= 0) return (aIndex < 0 ? UNIT_ORDER.length : aIndex) - (bIndex < 0 ? UNIT_ORDER.length : bIndex);
    return aCode.localeCompare(bCode);
  });
}

// unit.housekeeping_status is only ever set to "clean" or "dirty" anywhere
// in this codebase (see app/modules/properties/models.py's own comment and
// every write site) — never invented values, just a soft-color mapping for
// the two real ones plus a neutral, non-alarming fallback for anything
// unexpected.
function unitHkStatusMeta(status: string): { label: string; tone: BadgeTone } {
  if (status === "clean") return { label: "Clean", tone: "green" };
  if (status === "dirty") return { label: "Dirty", tone: "red" };
  return { label: status, tone: "slate" };
}

// HousekeepingTask.status is DB-constrained to exactly these three values
// (see app/modules/housekeeping/models.py TASK_STATUSES) — Pending/In
// Progress/Completed, soft amber/blue/green per the approved palette.
function taskStatusMeta(status: string): { label: string; tone: BadgeTone } {
  if (status === "completed") return { label: "Completed", tone: "green" };
  if (status === "in_progress") return { label: "In progress", tone: "blue" };
  if (status === "pending") return { label: "Pending", tone: "amber" };
  return { label: status.replace("_", " "), tone: "slate" };
}

function SectionLabel({ children }: { children: string }) {
  return <h3 className="mb-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">{children}</h3>;
}

const INPUT_CLASSES = "w-full rounded-[10px] border border-slate-300 px-3 py-2.5 text-sm placeholder:text-slate-400";

export default function HousekeepingPage() {
  const { role } = useAuth();
  const [tasks, setTasks] = useState<HousekeepingTaskOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [staff, setStaff] = useState<UserOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [success, setSuccess] = useState<string | null>(null);
  const [form, setForm] = useState({ unit_id: "", task_type: "routine", assigned_to: "", notes: "" });
  const [statusFilter, setStatusFilter] = useState("");

  const [detailsTaskId, setDetailsTaskId] = useState<string | null>(null);
  const [photos, setPhotos] = useState<HousekeepingPhotoOut[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [inventoryItems, setInventoryItems] = useState<InventoryItemOut[]>([]);
  const [consumptionForm, setConsumptionForm] = useState({ item_id: "", quantity: "" });
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [caption, setCaption] = useState("");

  async function loadAll() {
    setLoading(true);
    try {
      const [t, u, s] = await Promise.all([
        api.get("/housekeeping-tasks", { params: statusFilter ? { status: statusFilter } : {} }),
        api.get("/units"),
        role === "admin" ? api.get("/users") : Promise.resolve({ data: [] }),
      ]);
      setTasks(t.data);
      setUnits(u.data);
      setStaff(s.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useRealtimeRefresh(["housekeeping.changed", "availability.changed", "inventory.changed", "staff_operations.changed"], loadAll);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/housekeeping-tasks", {
        unit_id: form.unit_id,
        task_type: form.task_type,
        assigned_to: form.assigned_to || undefined,
        notes: form.notes || undefined,
      });
      setSuccess("Task created.");
      setForm({ unit_id: "", task_type: "routine", assigned_to: "", notes: "" });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function advanceStatus(task: HousekeepingTaskOut) {
    setError(null);
    const next = task.status === "pending" ? "in_progress" : "completed";
    try {
      await api.patch(`/housekeeping-tasks/${task.id}`, { status: next });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function unitName(id: string) {
    return units.find((u) => u.id === id)?.name ?? "—";
  }
  function staffName(id: string | null) {
    if (!id) return "Unassigned";
    return staff.find((u) => u.id === id)?.name ?? "—";
  }

  // Active-only + standard apartment order, same presentational convention
  // already used on Properties & Availability Calendar — no new data, no
  // change to what /units returns.
  const orderedActiveUnits = orderUnits(units.filter((u) => u.is_active));

  // Section 4 — unit-card click reuses the EXISTING task-details interaction
  // (openDetails, already wired to the Details button below) rather than
  // any new endpoint or business logic: a unit's card is only ever
  // clickable when at least one already-loaded task exists for it, and
  // clicking it opens that unit's most recently created task's existing
  // details view. No safe interaction exists for a unit with zero tasks,
  // so those cards stay non-clickable, per the brief.
  function mostRecentTaskForUnit(unitId: string): HousekeepingTaskOut | undefined {
    return tasks
      .filter((t) => t.unit_id === unitId)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
  }

  async function openDetails(taskId: string) {
    setDetailsTaskId(taskId);
    setUploadFile(null);
    setCaption("");
    setConsumptionForm({ item_id: "", quantity: "" });
    try {
      const [p, tl, inv] = await Promise.all([
        api.get(`/housekeeping-tasks/${taskId}/photos`),
        api.get(`/housekeeping-tasks/${taskId}/timeline`),
        api.get("/inventory/items"),
      ]);
      setPhotos(p.data);
      setTimeline(tl.data.events);
      setInventoryItems(inv.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function closeDetails() {
    setDetailsTaskId(null);
    setPhotos([]);
    setTimeline([]);
  }

  async function handleUploadPhoto() {
    if (!detailsTaskId || !uploadFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      await api.post(`/housekeeping-tasks/${detailsTaskId}/photos`, formData, {
        params: caption ? { caption } : {},
        headers: { "Content-Type": "multipart/form-data" },
      });
      setSuccess("Photo uploaded.");
      setUploadFile(null);
      setCaption("");
      const r = await api.get(`/housekeeping-tasks/${detailsTaskId}/photos`);
      setPhotos(r.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadPhoto(photoId: string, filename: string) {
    if (!detailsTaskId) return;
    try {
      const res = await api.get(`/housekeeping-tasks/${detailsTaskId}/photos/${photoId}/download`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeletePhoto(photoId: string) {
    if (!detailsTaskId) return;
    try {
      await api.delete(`/housekeeping-tasks/${detailsTaskId}/photos/${photoId}`);
      setPhotos((prev) => prev.filter((p) => p.id !== photoId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function recordConsumption() {
    if (!detailsTaskId || !consumptionForm.item_id || !consumptionForm.quantity) return;
    const task = tasks.find((t) => t.id === detailsTaskId);
    setError(null);
    setSuccess(null);
    try {
      await api.post("/inventory/stock-out", {
        item_id: consumptionForm.item_id,
        quantity: Number(consumptionForm.quantity),
        housekeeping_task_id: detailsTaskId,
        unit_id: task?.unit_id,
        reason: "Housekeeping task consumption",
      });
      setSuccess("Supplies used recorded — stock updated.");
      setConsumptionForm({ item_id: "", quantity: "" });
      const inv = await api.get("/inventory/items");
      setInventoryItems(inv.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader title="Housekeeping" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-8">
        {orderedActiveUnits.map((u) => {
          const meta = unitHkStatusMeta(u.housekeeping_status);
          // Existing data only — a count of already-loaded, still-open
          // tasks for this unit. Never a new API call, never an invented
          // field (checkout status/guest info/deadlines/priority stay out).
          const openTaskCount = tasks.filter((t) => t.unit_id === u.id && t.status !== "completed").length;
          const linkedTask = mostRecentTaskForUnit(u.id);
          const cardBody = (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-slate-800">
                  {u.name}
                  {u.unit_code && <span className="ml-1 text-xs font-normal text-slate-400">({u.unit_code})</span>}
                </span>
                <Badge tone={meta.tone}>{meta.label}</Badge>
              </div>
              {openTaskCount > 0 && (
                <p className="mt-2 text-xs text-slate-400">
                  {openTaskCount} open task{openTaskCount === 1 ? "" : "s"}
                </p>
              )}
            </>
          );
          const cardClasses = "w-full text-left bg-white rounded-[14px] border border-slate-200 shadow-sm p-4";
          return linkedTask ? (
            <button
              key={u.id}
              type="button"
              onClick={() => openDetails(linkedTask.id)}
              aria-label={`View housekeeping details for ${u.name}`}
              className={`${cardClasses} transition hover:border-slate-300 hover:shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-slate-900`}
            >
              {cardBody}
            </button>
          ) : (
            <div key={u.id} className={cardClasses}>
              {cardBody}
            </div>
          );
        })}
      </div>

      <Card className="!rounded-[14px] mb-8 max-w-lg shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Assign Cleaning Task</h2>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <SectionLabel>Task Details</SectionLabel>
            <div className="space-y-3">
              <div>
                <label htmlFor="hk-unit" className="mb-1 block text-xs text-slate-500">Unit</label>
                <select id="hk-unit" required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className={INPUT_CLASSES}>
                  <option value="">Select unit...</option>
                  {orderedActiveUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="hk-task-type" className="mb-1 block text-xs text-slate-500">Task Type</label>
                <select id="hk-task-type" value={form.task_type} onChange={(e) => setForm({ ...form, task_type: e.target.value })} className={INPUT_CLASSES}>
                  {TASK_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="hk-assignee" className="mb-1 block text-xs text-slate-500">Assign to staff (optional)</label>
                <select id="hk-assignee" value={form.assigned_to} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })} className={INPUT_CLASSES}>
                  <option value="">Unassigned</option>
                  {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="hk-notes" className="mb-1 block text-xs text-slate-500">Notes (optional)</label>
                <input
                  id="hk-notes"
                  placeholder="Notes (optional)"
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  className={INPUT_CLASSES}
                />
              </div>
            </div>
          </div>
          <Button type="submit">Create Task</Button>
        </form>
      </Card>

      <Card className="!rounded-[14px] shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Tasks</h2>
          <div>
            <label htmlFor="hk-status-filter" className="sr-only">Filter by status</label>
            <select
              id="hk-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-[10px] border border-slate-300 px-2.5 py-1.5 text-xs text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900"
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="in_progress">In progress</option>
              <option value="completed">Completed</option>
            </select>
          </div>
        </div>
        {loading ? (
          <Spinner label="Loading tasks..." />
        ) : tasks.length === 0 ? (
          <EmptyState message="No housekeeping tasks yet — create one above to get started." />
        ) : (
          <Table headers={["Unit", "Type", "Assigned To", "Status", "Actions"]}>
            {tasks.map((t) => {
              const meta = taskStatusMeta(t.status);
              return (
                <tr key={t.id}>
                  <td className="px-4 py-2.5 font-semibold text-slate-800">{unitName(t.unit_id)}</td>
                  <td className="px-4 py-2.5 text-slate-500">{t.task_type.replace("_", " ")}</td>
                  <td className="px-4 py-2.5 text-slate-500">{staffName(t.assigned_to)}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                  </td>
                  <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                    {t.status !== "completed" && (
                      <Button onClick={() => advanceStatus(t)} aria-label={`${t.status === "pending" ? "Start" : "Mark complete"} — ${unitName(t.unit_id)}`}>
                        {t.status === "pending" ? "Start" : "Mark Complete"}
                      </Button>
                    )}
                    <Button variant="secondary" onClick={() => openDetails(t.id)} aria-label={`Details — ${unitName(t.unit_id)} ${t.task_type.replace("_", " ")}`}>
                      Details
                    </Button>
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      {detailsTaskId && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={closeDetails}>
          <div className="bg-white rounded-xl max-w-2xl w-full max-h-[85vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Task Details</h2>
              <button onClick={closeDetails} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
            </div>

            <h3 className="text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2">Supplies Used</h3>
            <div className="flex flex-wrap gap-2 mb-4">
              <select
                value={consumptionForm.item_id}
                onChange={(e) => setConsumptionForm({ ...consumptionForm, item_id: e.target.value })}
                className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs min-w-[160px]"
              >
                <option value="">Select item...</option>
                {inventoryItems.map((item) => (
                  <option key={item.id} value={item.id}>{item.name} ({item.current_stock} {item.unit_of_measure} left)</option>
                ))}
              </select>
              <input
                type="number"
                placeholder="Qty used"
                value={consumptionForm.quantity}
                onChange={(e) => setConsumptionForm({ ...consumptionForm, quantity: e.target.value })}
                className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
              />
              <Button variant="secondary" onClick={recordConsumption}>Record</Button>
            </div>

            <h3 className="text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2">Photos</h3>
            <div className="space-y-2 mb-4">
              <input type="file" accept="image/*" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} className="text-xs" />
              <input placeholder="Caption (optional)" value={caption} onChange={(e) => setCaption(e.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs" />
              <Button variant="secondary" onClick={handleUploadPhoto}>Upload Photo</Button>
            </div>
            {photos.length === 0 ? (
              <p className="text-xs text-slate-400 mb-6">No photos yet.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2 mb-6">
                {photos.map((p) => (
                  <div key={p.id} className="border border-slate-100 rounded-lg p-2 text-xs">
                    <p className="font-medium text-slate-700">{p.filename}</p>
                    {p.caption && <p className="text-slate-500">{p.caption}</p>}
                    <div className="flex gap-2 mt-1">
                      <button onClick={() => handleDownloadPhoto(p.id, p.filename)} className="text-slate-600 hover:underline">Download</button>
                      <button onClick={() => handleDeletePhoto(p.id)} className="text-red-500 hover:underline">Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <h3 className="text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2">Timeline</h3>
            {timeline.length === 0 ? (
              <p className="text-xs text-slate-400">No events recorded yet.</p>
            ) : (
              <div className="space-y-2">
                {timeline.map((event, i) => (
                  <div key={i} className="text-xs border-l-2 border-slate-200 pl-3 py-1">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-slate-800">{event.action}</span>
                      <span className="text-slate-400">{new Date(event.created_at).toLocaleString()}</span>
                    </div>
                    <p className="text-slate-500">By {event.user_name}</p>
                    {event.details && <p className="text-slate-500 mt-0.5">{event.details}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
