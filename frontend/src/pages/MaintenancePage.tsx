import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, MAINTENANCE_TICKET_CATEGORIES, type MaintenanceTicketOut, type MaintenancePhotoOut, type UnitOut, type UserOut, type TimelineEvent } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState, Spinner, type BadgeTone } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useRealtimeRefresh } from "../lib/realtime";

const PRIORITIES = ["low", "medium", "high", "urgent"];
const CATEGORY_LABELS: Record<string, string> = {
  ac: "AC", electrical: "Electrical", plumbing: "Plumbing", water: "Water",
  appliance: "Appliance", furniture: "Furniture", internet: "Internet", other: "Other",
};

// Same standard OS10 apartment order already used on Properties,
// Availability Calendar & Housekeeping — this page's own local copy, per
// the established convention of not sharing it across pages.
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

// MaintenanceTicket.priority is DB-constrained to exactly these four values
// (see app/modules/maintenance/models.py TICKET_PRIORITIES) — soft
// slate/blue/amber/red per the approved palette, never a new value.
function priorityMeta(priority: string): { label: string; tone: BadgeTone } {
  if (priority === "urgent") return { label: "Urgent", tone: "red" };
  if (priority === "high") return { label: "High", tone: "amber" };
  if (priority === "medium") return { label: "Medium", tone: "blue" };
  if (priority === "low") return { label: "Low", tone: "slate" };
  return { label: priority, tone: "slate" };
}

// MaintenanceTicket.status is DB-constrained to exactly these four values
// (see app/modules/maintenance/models.py TICKET_STATUSES) — Open/In
// Progress/Resolved/Closed, soft amber/blue/green/slate. Previously
// resolved AND closed both rendered "green", and open had no distinct
// color at all — this is a pure display-mapping fix, not a status change.
function statusMeta(status: string): { label: string; tone: BadgeTone } {
  if (status === "resolved") return { label: "Resolved", tone: "green" };
  if (status === "closed") return { label: "Closed", tone: "slate" };
  if (status === "in_progress") return { label: "In progress", tone: "blue" };
  if (status === "open") return { label: "Open", tone: "amber" };
  return { label: status.replace("_", " "), tone: "slate" };
}

function SectionLabel({ children }: { children: string }) {
  return <h3 className="mb-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">{children}</h3>;
}

// No explicit focus classes needed: index.css already has a global
// `input/select/textarea:focus-visible` teal outline rule every OS10 page
// relies on instead of a per-page override.
const INPUT_CLASSES = "w-full rounded-[10px] border border-slate-300 px-3 py-2.5 text-sm placeholder:text-slate-400";

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

  useRealtimeRefresh(["maintenance.changed", "finance.changed", "properties.changed", "staff_operations.changed"], loadAll);

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

  // Active-only + standard apartment order — same presentational
  // convention already applied on Properties, Availability Calendar &
  // Housekeeping; /units itself is unchanged.
  const orderedActiveUnits = orderUnits(units.filter((u) => u.is_active));

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

      <Card className="!rounded-[14px] mb-8 max-w-lg shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Raise Ticket</h2>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <SectionLabel>Ticket Details</SectionLabel>
            <div className="space-y-3">
              <div>
                <label htmlFor="mt-unit" className="mb-1 block text-xs text-slate-500">Unit</label>
                <select id="mt-unit" required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className={INPUT_CLASSES}>
                  <option value="">Select unit...</option>
                  {orderedActiveUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="mt-description" className="mb-1 block text-xs text-slate-500">Issue Description</label>
                <textarea
                  id="mt-description"
                  required
                  placeholder="Describe the issue"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  className={INPUT_CLASSES}
                  rows={3}
                />
              </div>
              <div>
                <label htmlFor="mt-priority" className="mb-1 block text-xs text-slate-500">Priority</label>
                <select id="mt-priority" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className={INPUT_CLASSES}>
                  {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="mt-category" className="mb-1 block text-xs text-slate-500">Category (optional)</label>
                <select id="mt-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className={INPUT_CLASSES}>
                  <option value="">Category (optional)...</option>
                  {MAINTENANCE_TICKET_CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="mt-assignee" className="mb-1 block text-xs text-slate-500">Assign to Staff (optional)</label>
                <select id="mt-assignee" value={form.assigned_to} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })} className={INPUT_CLASSES}>
                  <option value="">Unassigned</option>
                  {staff.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </div>
            </div>
          </div>
          <Button type="submit">Raise Ticket</Button>
        </form>
      </Card>

      <Card className="!rounded-[14px] shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Tickets</h2>
        {loading ? (
          <Spinner label="Loading tickets..." />
        ) : tickets.length === 0 ? (
          <EmptyState message="No maintenance tickets yet — raise one above to get started." />
        ) : (
          <Table headers={["Unit", "Issue", "Priority", "Category", "Assigned To", "Status", "Cost", "Actions"]}>
            {tickets.map((t) => {
              const priority = priorityMeta(t.priority);
              const status = statusMeta(t.status);
              return (
                <tr key={t.id}>
                  <td className="px-4 py-2.5 font-semibold text-slate-800">{unitName(t.unit_id)}</td>
                  <td className="px-4 py-2.5 text-slate-500 max-w-xs truncate" title={t.description}>{t.description}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={priority.tone}>{priority.label}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">{t.category ? CATEGORY_LABELS[t.category] ?? t.category : "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{staffName(t.assigned_to)}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">{t.cost != null ? `₹${t.cost}` : "—"}</td>
                  <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                    {t.status === "open" && (
                      <Button onClick={() => updateStatus(t, "in_progress")} aria-label={`Start — ${unitName(t.unit_id)}`}>
                        Start
                      </Button>
                    )}
                    {t.status === "in_progress" && (
                      <span className="inline-flex items-center gap-2">
                        <label htmlFor={`mt-cost-${t.id}`} className="sr-only">Cost for {unitName(t.unit_id)} ticket</label>
                        <input
                          id={`mt-cost-${t.id}`}
                          type="number"
                          placeholder="Cost"
                          title="Resolving with a cost automatically records it as a Finance expense for this apartment"
                          value={costInputs[t.id] ?? ""}
                          onChange={(e) => setCostInputs({ ...costInputs, [t.id]: e.target.value })}
                          className="w-20 rounded-[10px] border border-slate-300 px-2 py-1.5 text-xs"
                        />
                        <Button onClick={() => updateStatus(t, "resolved")} aria-label={`Resolve — ${unitName(t.unit_id)}`}>
                          Resolve
                        </Button>
                      </span>
                    )}
                    {t.status === "resolved" && (
                      <Button onClick={() => updateStatus(t, "closed")} aria-label={`Close — ${unitName(t.unit_id)}`}>
                        Close
                      </Button>
                    )}
                    <Button variant="secondary" onClick={() => openDetails(t.id)} aria-label={`Details — ${unitName(t.unit_id)} ticket`}>
                      Details
                    </Button>
                  </td>
                </tr>
              );
            })}
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
