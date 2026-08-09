import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type HousekeepingTaskOut, type HousekeepingPhotoOut, type UnitOut, type UserOut, type TimelineEvent, type InventoryItemOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState, Spinner } from "../components/ui";
import { useAuth } from "../context/AuthContext";

const TASK_TYPES = ["routine", "checkout_clean", "deep_clean", "inspection"];

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

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-8">
        {units.map((u) => (
          <Card key={u.id} className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-800">{u.name}</span>
            <Badge tone={u.housekeeping_status === "clean" ? "green" : u.housekeeping_status === "dirty" ? "red" : "amber"}>
              {u.housekeeping_status}
            </Badge>
          </Card>
        ))}
      </div>

      <Card className="mb-8 max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Assign Cleaning Task</h2>
        <form onSubmit={handleCreate} className="space-y-3">
          <select required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Select unit...</option>
            {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <select value={form.task_type} onChange={(e) => setForm({ ...form, task_type: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            {TASK_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
          </select>
          <select value={form.assigned_to} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Assign to staff (optional)...</option>
            {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <input
            placeholder="Notes (optional)"
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <Button type="submit">Create Task</Button>
        </form>
      </Card>

      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Tasks</h2>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1 text-xs">
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="in_progress">In progress</option>
            <option value="completed">Completed</option>
          </select>
        </div>
        {loading ? (
          <Spinner label="Loading tasks..." />
        ) : tasks.length === 0 ? (
          <EmptyState message="No housekeeping tasks yet — create one above to get started." />
        ) : (
          <Table headers={["Unit", "Type", "Assigned To", "Status", "Actions"]}>
            {tasks.map((t) => (
              <tr key={t.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{unitName(t.unit_id)}</td>
                <td className="px-4 py-2.5 text-slate-500">{t.task_type.replace("_", " ")}</td>
                <td className="px-4 py-2.5 text-slate-500">{staffName(t.assigned_to)}</td>
                <td className="px-4 py-2.5">
                  <Badge tone={t.status === "completed" ? "green" : t.status === "in_progress" ? "amber" : "slate"}>
                    {t.status.replace("_", " ")}
                  </Badge>
                </td>
                <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                  {t.status !== "completed" && (
                    <Button variant="secondary" onClick={() => advanceStatus(t)}>
                      {t.status === "pending" ? "Start" : "Mark Complete"}
                    </Button>
                  )}
                  <Button variant="secondary" onClick={() => openDetails(t.id)}>Details</Button>
                </td>
              </tr>
            ))}
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
