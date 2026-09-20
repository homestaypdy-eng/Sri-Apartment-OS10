import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, MAINTENANCE_TICKET_CATEGORIES, type MaintenanceTicketOut, type MaintenancePhotoOut, type UnitOut, type UserOut, type TimelineEvent } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState, Spinner } from "../components/ui";
import { useAuth } from "../context/AuthContext";

const PRIORITIES = ["low", "medium", "high", "urgent"];
const CATEGORY_LABELS: Record<string, string> = {
  ac: "AC", electrical: "Electrical", plumbing: "Plumbing", water: "Water",
  appliance: "Appliance", furniture: "Furniture", internet: "Internet", other: "Other",
};

export default function MaintenancePage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [tickets, setTickets] = useState<MaintenanceTicketOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [staff, setStaff] = useState<UserOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [success, setSuccess] = useState<string | null>(null);
  const [form, setForm] = useState({ unit_id: "", description: "", priority: "medium", category: "", assigned_to: "" });
  const [costInputs, setCostInputs] = useState<Record<string, string>>({});

  const [detailsTicketId, setDetailsTicketId] = useState<string | null>(null);
  const [photos, setPhotos] = useState<MaintenancePhotoOut[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [photoType, setPhotoType] = useState<"before" | "after">("before");

  async function loadAll() {
    setLoading(true);
    try {
      // /users is admin-only server-side — same guarded-fetch pattern
      // HousekeepingPage.tsx and GuestRequestsPage.tsx already use, so
      // staff still get the full ticket list, just without a named-staff
      // assignment dropdown at creation time.
      const [t, u, s] = await Promise.all([
        api.get("/maintenance-tickets"),
        api.get("/units"),
        isAdmin ? api.get("/users") : Promise.resolve({ data: [] }),
      ]);
      setTickets(t.data);
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
  }, [isAdmin]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/maintenance-tickets", { ...form, category: form.category || undefined, assigned_to: form.assigned_to || undefined });
      setSuccess("Ticket raised.");
      setForm({ unit_id: "", description: "", priority: "medium", category: "", assigned_to: "" });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function updateStatus(ticket: MaintenanceTicketOut, status: string) {
    setError(null);
    try {
      const payload: { status: string; cost?: number } = { status };
      if (status === "resolved") {
        const cost = costInputs[ticket.id];
        if (cost) payload.cost = Number(cost);
      }
      await api.patch(`/maintenance-tickets/${ticket.id}`, payload);
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

  function priorityTone(p: string): "red" | "amber" | "slate" {
    if (p === "urgent") return "red";
    if (p === "high") return "amber";
    return "slate";
  }

  async function openDetails(ticketId: string) {
    setDetailsTicketId(ticketId);
    setUploadFile(null);
    setPhotoType("before");
    try {
      const [p, tl] = await Promise.all([
        api.get(`/maintenance-tickets/${ticketId}/photos`),
        api.get(`/maintenance-tickets/${ticketId}/timeline`),
      ]);
      setPhotos(p.data);
      setTimeline(tl.data.events);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function closeDetails() {
    setDetailsTicketId(null);
    setPhotos([]);
    setTimeline([]);
  }

  async function handleUploadPhoto() {
    if (!detailsTicketId || !uploadFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      await api.post(`/maintenance-tickets/${detailsTicketId}/photos`, formData, {
        params: { photo_type: photoType },
        headers: { "Content-Type": "multipart/form-data" },
      });
      setSuccess(`${photoType === "before" ? "Before" : "After"} photo uploaded.`);
      setUploadFile(null);
      const r = await api.get(`/maintenance-tickets/${detailsTicketId}/photos`);
      setPhotos(r.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadPhoto(photoId: string, filename: string) {
    if (!detailsTicketId) return;
    try {
      const res = await api.get(`/maintenance-tickets/${detailsTicketId}/photos/${photoId}/download`, { responseType: "blob" });
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
    if (!detailsTicketId) return;
    try {
      await api.delete(`/maintenance-tickets/${detailsTicketId}/photos/${photoId}`);
      setPhotos((prev) => prev.filter((p) => p.id !== photoId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const beforePhotos = photos.filter((p) => p.photo_type === "before");
  const afterPhotos = photos.filter((p) => p.photo_type === "after");

  return (
    <div>
      <PageHeader title="Maintenance" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <Card className="mb-8 max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Raise Ticket</h2>
        <form onSubmit={handleCreate} className="space-y-3">
          <select required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Select unit...</option>
            {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <textarea required placeholder="Describe the issue" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" rows={3} />
          <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Category (optional)...</option>
            {MAINTENANCE_TICKET_CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
          </select>
          <select value={form.assigned_to} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Assign to staff (optional)...</option>
            {staff.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <Button type="submit">Raise Ticket</Button>
        </form>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Tickets</h2>
        {loading ? (
          <Spinner label="Loading tickets..." />
        ) : tickets.length === 0 ? (
          <EmptyState message="No maintenance tickets yet — raise one above to get started." />
        ) : (
          <Table headers={["Unit", "Issue", "Category", "Priority", "Status", "Assigned To", "Cost", "Actions"]}>
            {tickets.map((t) => (
              <tr key={t.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">{unitName(t.unit_id)}</td>
                <td className="px-4 py-2.5 text-slate-500 max-w-xs">{t.description}</td>
                <td className="px-4 py-2.5 text-slate-500">{t.category ? CATEGORY_LABELS[t.category] ?? t.category : "—"}</td>
                <td className="px-4 py-2.5">
                  <Badge tone={priorityTone(t.priority)}>{t.priority}</Badge>
                </td>
                <td className="px-4 py-2.5">
                  <Badge tone={t.status === "closed" || t.status === "resolved" ? "green" : t.status === "in_progress" ? "amber" : "slate"}>
                    {t.status.replace("_", " ")}
                  </Badge>
                </td>
                <td className="px-4 py-2.5 text-slate-500">{staffName(t.assigned_to)}</td>
                <td className="px-4 py-2.5 text-slate-500">{t.cost != null ? `₹${t.cost}` : "—"}</td>
                <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                  {t.status === "open" && (
                    <Button variant="secondary" onClick={() => updateStatus(t, "in_progress")}>Start</Button>
                  )}
                  {t.status === "in_progress" && (
                    <span className="inline-flex items-center gap-2">
                      <input
                        type="number"
                        placeholder="Cost"
                        title="Resolving with a cost automatically records it as a Finance expense for this apartment"
                        value={costInputs[t.id] ?? ""}
                        onChange={(e) => setCostInputs({ ...costInputs, [t.id]: e.target.value })}
                        className="w-20 rounded-md border border-slate-300 px-2 py-1 text-xs"
                      />
                      <Button variant="secondary" onClick={() => updateStatus(t, "resolved")}>Resolve</Button>
                    </span>
                  )}
                  {t.status === "resolved" && (
                    <Button variant="secondary" onClick={() => updateStatus(t, "closed")}>Close</Button>
                  )}
                  <Button variant="secondary" onClick={() => openDetails(t.id)}>Details</Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {detailsTicketId && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={closeDetails}>
          <div className="bg-white rounded-xl max-w-2xl w-full max-h-[85vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Ticket Details</h2>
              <button onClick={closeDetails} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
            </div>

            <h3 className="text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2">Before / After Photos</h3>
            <div className="flex items-center gap-2 mb-3">
              <select value={photoType} onChange={(e) => setPhotoType(e.target.value as "before" | "after")} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                <option value="before">Before</option>
                <option value="after">After</option>
              </select>
              <input type="file" accept="image/*" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} className="text-xs" />
              <Button variant="secondary" onClick={handleUploadPhoto}>Upload</Button>
            </div>

            <div className="grid grid-cols-2 gap-4 mb-6">
              <div>
                <p className="text-xs font-medium text-slate-500 mb-2">Before ({beforePhotos.length})</p>
                {beforePhotos.length === 0 ? (
                  <p className="text-xs text-slate-400">None yet.</p>
                ) : (
                  <div className="space-y-2">
                    {beforePhotos.map((p) => (
                      <div key={p.id} className="border border-slate-100 rounded-lg p-2 text-xs">
                        <p className="font-medium text-slate-700 truncate">{p.filename}</p>
                        <div className="flex gap-2 mt-1">
                          <button onClick={() => handleDownloadPhoto(p.id, p.filename)} className="text-slate-600 hover:underline">Download</button>
                          <button onClick={() => handleDeletePhoto(p.id)} className="text-red-500 hover:underline">Delete</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <p className="text-xs font-medium text-slate-500 mb-2">After ({afterPhotos.length})</p>
                {afterPhotos.length === 0 ? (
                  <p className="text-xs text-slate-400">None yet.</p>
                ) : (
                  <div className="space-y-2">
                    {afterPhotos.map((p) => (
                      <div key={p.id} className="border border-slate-100 rounded-lg p-2 text-xs">
                        <p className="font-medium text-slate-700 truncate">{p.filename}</p>
                        <div className="flex gap-2 mt-1">
                          <button onClick={() => handleDownloadPhoto(p.id, p.filename)} className="text-slate-600 hover:underline">Download</button>
                          <button onClick={() => handleDeletePhoto(p.id)} className="text-red-500 hover:underline">Delete</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

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
