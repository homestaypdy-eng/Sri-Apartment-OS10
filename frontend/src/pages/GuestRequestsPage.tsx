import { useEffect, useState } from "react";
import { api, extractErrorMessage, type GuestRequestOut, type GuestOut, type UserOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Badge, EmptyState } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useRealtimeRefresh } from "../lib/realtime";

const PRIORITIES = ["low", "medium", "high", "urgent"];

function priorityTone(p: string): "red" | "amber" | "slate" {
  if (p === "urgent") return "red";
  if (p === "high") return "amber";
  return "slate";
}

export default function GuestRequestsPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [requests, setRequests] = useState<GuestRequestOut[]>([]);
  const [guests, setGuests] = useState<GuestOut[]>([]);
  const [staff, setStaff] = useState<UserOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [search, setSearch] = useState("");
  const [notesDraft, setNotesDraft] = useState<Record<string, string>>({});
  const [feedbackDraft, setFeedbackDraft] = useState<Record<string, { rating: string; text: string }>>({});

  async function load() {
    try {
      const params: Record<string, string> = {};
      if (statusFilter) params.status = statusFilter;
      // /users is admin-only server-side — staff are fully authorized for
      // every Guest Requests endpoint but would 403 the whole Promise.all
      // (and the page with it) if this call weren't guarded. Same pattern
      // as HousekeepingPage.tsx's assignee-list fetch: staff sees the page
      // and can act on requests, just without a full staff-name dropdown
      // for assignment (an already-assigned request shows "—" instead of
      // a resolved name for a non-admin viewer, same accepted trade-off).
      const [r, s] = await Promise.all([
        api.get("/guest-requests", { params }),
        isAdmin ? api.get("/users") : Promise.resolve({ data: [] }),
      ]);
      setRequests(r.data);
      setStaff(s.data);

      // Fetch exactly the guests referenced by these requests, by ID —
      // not a generic paginated /guests list. A generic list defaults to
      // the first 200 guests alphabetically; once a business has more
      // guests than that, older/alphabetically-later requesters would
      // silently show as "—" and drop out of name search. This guarantees
      // every request's guest name resolves correctly regardless of how
      // many guests exist in total.
      const guestIds = Array.from(new Set((r.data as GuestRequestOut[]).map((req) => req.guest_id)));
      if (guestIds.length > 0) {
        const g = await api.get("/guests", { params: { ids: guestIds.join(","), include_merged: true } });
        setGuests(g.data);
      } else {
        setGuests([]);
      }
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useRealtimeRefresh(["guest_requests.changed", "guests.changed", "staff_operations.changed"], load);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  function guestName(id: string) {
    return guests.find((g) => g.id === id)?.name ?? "—";
  }
  function staffName(id: string | null) {
    if (!id) return "Unassigned";
    return staff.find((u) => u.id === id)?.name ?? "—";
  }

  async function updateStatus(id: string, status: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.patch(`/guest-requests/${id}`, { status, staff_notes: notesDraft[id] || undefined });
      setSuccess("Request updated.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function updatePriority(id: string, priority: string) {
    setError(null);
    try {
      await api.patch(`/guest-requests/${id}`, { priority });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function updateAssignment(id: string, assigned_to: string) {
    setError(null);
    try {
      await api.patch(`/guest-requests/${id}`, { assigned_to: assigned_to || null });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function submitFeedback(id: string) {
    const draft = feedbackDraft[id];
    if (!draft?.rating) return;
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/portal/guest-requests/${id}/feedback`, { rating: Number(draft.rating), text: draft.text || undefined });
      setSuccess("Feedback recorded.");
      setFeedbackDraft({ ...feedbackDraft, [id]: { rating: "", text: "" } });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function approveExtension(id: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/guest-requests/${id}/approve-extension`);
      setSuccess("Extension approved — the booking's check-out date has been updated.");
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const filtered = requests
    .filter((r) => !priorityFilter || r.priority === priorityFilter)
    .filter((r) => !search || guestName(r.guest_id).toLowerCase().includes(search.toLowerCase()) || r.description.toLowerCase().includes(search.toLowerCase()));

  return (
    <div>
      <PageHeader title="Guest Requests" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Requests &amp; Complaints</h2>
        <div className="flex flex-wrap gap-2 mb-4">
          <input
            placeholder="Search guest or description..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 min-w-[180px] rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
          />
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1 text-xs">
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
          </select>
          <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1 text-xs">
            <option value="">All priorities</option>
            {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>

        {filtered.length === 0 ? (
          <EmptyState message="No requests match these filters." />
        ) : (
          <div className="space-y-3">
            {filtered.map((r) => (
              <div key={r.id} className="border border-slate-100 rounded-lg p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge tone={r.request_type === "complaint" ? "red" : "slate"}>{r.request_type.replace("_", " ")}</Badge>
                    <Badge tone={priorityTone(r.priority)}>{r.priority}</Badge>
                    <span className="text-xs text-slate-500 font-medium">{guestName(r.guest_id)}</span>
                    <span className="text-xs text-slate-400">{new Date(r.created_at).toLocaleString()}</span>
                  </div>
                  <Badge tone={r.status === "resolved" ? "green" : r.status === "in_progress" ? "amber" : "slate"}>{r.status}</Badge>
                </div>
                <p className="text-sm text-slate-700 mt-2">{r.description}</p>
                {r.requested_check_out_date && (
                  <div className="flex items-center gap-2 mt-1">
                    <p className="text-xs text-slate-500">Requested new check-out: {r.requested_check_out_date}</p>
                    {r.status !== "resolved" && (
                      <button onClick={() => approveExtension(r.id)} className="text-xs text-emerald-600 hover:underline font-medium">
                        Approve Extension (updates the real booking)
                      </button>
                    )}
                  </div>
                )}

                <div className="flex flex-wrap gap-2 mt-3">
                  <select value={r.priority} onChange={(e) => updatePriority(r.id, e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1 text-xs">
                    {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                  <select value={r.assigned_to ?? ""} onChange={(e) => updateAssignment(r.id, e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1 text-xs">
                    <option value="">Unassigned</option>
                    {staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                  <span className="text-xs text-slate-400 self-center">Assigned: {staffName(r.assigned_to)}</span>
                </div>

                {r.status !== "resolved" && (
                  <div className="mt-3 flex flex-col sm:flex-row gap-2">
                    <input
                      placeholder="Staff notes (optional)"
                      value={notesDraft[r.id] || ""}
                      onChange={(e) => setNotesDraft({ ...notesDraft, [r.id]: e.target.value })}
                      className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                    <div className="flex gap-2">
                      {r.status === "open" && (
                        <Button variant="secondary" onClick={() => updateStatus(r.id, "in_progress")}>
                          Start
                        </Button>
                      )}
                      <Button onClick={() => updateStatus(r.id, "resolved")}>Resolve</Button>
                    </div>
                  </div>
                )}
                {r.staff_notes && <p className="text-xs text-slate-400 mt-2">Note: {r.staff_notes}</p>}
                {r.guest_feedback_rating != null && (
                  <div className="mt-2 bg-amber-50 rounded-lg px-3 py-2 text-xs">
                    <span className="font-medium text-amber-700">Guest Feedback: {"★".repeat(r.guest_feedback_rating)}{"☆".repeat(5 - r.guest_feedback_rating)}</span>
                    {r.guest_feedback_text && <p className="text-amber-600 mt-1">{r.guest_feedback_text}</p>}
                  </div>
                )}
                {r.status === "resolved" && r.guest_feedback_rating == null && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 bg-slate-50 rounded-lg px-3 py-2">
                    <span className="text-xs text-slate-500">Record guest feedback:</span>
                    <select
                      value={feedbackDraft[r.id]?.rating ?? ""}
                      onChange={(e) => setFeedbackDraft({ ...feedbackDraft, [r.id]: { rating: e.target.value, text: feedbackDraft[r.id]?.text ?? "" } })}
                      className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
                    >
                      <option value="">Rating...</option>
                      {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{"★".repeat(n)}</option>)}
                    </select>
                    <input
                      placeholder="Comments (optional)"
                      value={feedbackDraft[r.id]?.text ?? ""}
                      onChange={(e) => setFeedbackDraft({ ...feedbackDraft, [r.id]: { rating: feedbackDraft[r.id]?.rating ?? "", text: e.target.value } })}
                      className="flex-1 min-w-[120px] rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                    <Button variant="secondary" onClick={() => submitFeedback(r.id)}>Save</Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
