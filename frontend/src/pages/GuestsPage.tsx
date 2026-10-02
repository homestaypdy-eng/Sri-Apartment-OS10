import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type GuestOut, type GuestHistory, type GuestDocumentOut, type GuestSegment } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState, type BadgeTone } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { INDIAN_STATES } from "../lib/indianStates";

// Identity Verification display — entirely separate from Account
// Verification (which this app has no Guest-level field for at all).
// Only ever changed via the dedicated /guests/{id}/identity/* actions
// below; nothing here can set a guest's own status.
function identityStatusMeta(status: GuestOut["identity_status"]): { label: string; tone: BadgeTone } {
  if (status === "verified") return { label: "Identity Verified", tone: "green" };
  if (status === "submitted") return { label: "Identity Submitted", tone: "blue" };
  if (status === "under_review") return { label: "Identity Under Review", tone: "blue" };
  if (status === "pending") return { label: "Identity Pending", tone: "amber" };
  if (status === "rejected") return { label: "Identity Rejected", tone: "red" };
  if (status === "expired") return { label: "Identity Expired", tone: "slate" };
  return { label: "Identity Not Verified", tone: "slate" };
}

type SortBy = "name" | "total_bookings" | "total_spent" | "last_stay" | "segment" | "source";

const DOCUMENT_TYPES = ["aadhaar", "pan", "passport", "driving_license", "other"];

// Indian comma grouping ("₹11,340", not "₹11340" or a locale-dependent
// "₹11 340") — explicit locale rather than a bare toLocaleString(), which
// would otherwise fall back to the browser/OS locale's own grouping.
function formatCurrency(value: number): string {
  return `₹${value.toLocaleString("en-IN")}`;
}

// Fixed "en-US" Intl formatter for the month name specifically (always
// 3-letter, always English, regardless of the browser/OS locale), with
// day/month/year assembled explicitly in OS10's own order ("27 Sep 2026")
// rather than trusting any single locale's own part ordering.
const monthShortFormatter = new Intl.DateTimeFormat("en-US", { month: "short" });

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.split("-").map(Number);
  if (!year || !month || !day) return iso;
  const d = new Date(year, month - 1, day);
  return `${day} ${monthShortFormatter.format(d)} ${year}`;
}

// Shared input styling for the Add Guest form — 10px radius (OS10's
// "10–12px input radius" target) and comfortable padding; the teal
// focus ring itself comes for free from the app-wide :focus-visible rule
// in index.css, so it isn't repeated per-input here.
const INPUT_CLASSES = "rounded-[10px] border border-slate-300 px-3 py-2.5 text-sm placeholder:text-slate-400";

function FormSectionLabel({ children }: { children: string }) {
  return <h3 className="mb-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">{children}</h3>;
}

export default function GuestsPage() {
  // RBAC Phase B security fix (finding I-7): POST /guests/merge is, and
  // always was, admin-only on the backend (see bookings/router.py) — this
  // page just never had any role awareness at all, so Manager/Staff could
  // see and attempt "Merge Duplicate" only to get a 403 at submit time.
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [guests, setGuests] = useState<GuestOut[]>([]);
  const [segments, setSegments] = useState<GuestSegment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [segmentFilter, setSegmentFilter] = useState<"" | "new" | "repeat">("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [form, setForm] = useState({
    name: "", phone: "", email: "", address: "", state_code: "", date_of_birth: "", gender: "", nationality: "", occupation: "",
    whatsapp_number: "", emergency_contact_name: "", emergency_contact_phone: "", notes: "",
  });

  const [detailGuestId, setDetailGuestId] = useState<string | null>(null);
  const [history, setHistory] = useState<GuestHistory | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [editForm, setEditForm] = useState<Record<string, string>>({});
  const [documents, setDocuments] = useState<GuestDocumentOut[]>([]);
  const [docForm, setDocForm] = useState({ document_type: "aadhaar", document_number: "", side: "front" });
  const [docFile, setDocFile] = useState<File | null>(null);

  const [mergeMode, setMergeMode] = useState(false);
  const [mergeDuplicateId, setMergeDuplicateId] = useState("");

  const [verifyDocId, setVerifyDocId] = useState("");
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showResetForm, setShowResetForm] = useState(false);
  const [resetReason, setResetReason] = useState("");

  async function loadGuests() {
    try {
      const { data } = await api.get("/guests", { params: search ? { search } : {} });
      setGuests(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function loadSegments() {
    try {
      const params: Record<string, string> = { sort_by: sortBy, sort_dir: sortDir };
      if (search) params.search = search;
      if (segmentFilter) params.segment = segmentFilter;
      if (sourceFilter) params.source = sourceFilter;
      const { data } = await api.get("/guests/segments", { params });
      setSegments(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    loadGuests();
    loadSegments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, segmentFilter, sourceFilter, sortBy, sortDir]);

  const guestsById = new Map(guests.map((g) => [g.id, g]));

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post("/guests", {
        name: form.name, phone: form.phone, email: form.email || undefined, address: form.address || undefined,
        state_code: form.state_code || undefined,
        date_of_birth: form.date_of_birth || undefined, gender: form.gender || undefined, nationality: form.nationality || undefined,
        occupation: form.occupation || undefined, whatsapp_number: form.whatsapp_number || undefined,
        emergency_contact_name: form.emergency_contact_name || undefined, emergency_contact_phone: form.emergency_contact_phone || undefined,
        notes: form.notes || undefined,
      });
      setGuests((prev) => [...prev, data]);
      setSuccess(`Guest "${data.name}" created.`);
      setForm({ name: "", phone: "", email: "", address: "", state_code: "", date_of_birth: "", gender: "", nationality: "", occupation: "", whatsapp_number: "", emergency_contact_name: "", emergency_contact_phone: "", notes: "" });
      loadSegments();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function openDetail(guest: GuestOut) {
    setDetailGuestId(guest.id);
    setHistory(null);
    setDocuments([]);
    setMergeMode(false);
    setVerifyDocId("");
    setShowRejectForm(false);
    setRejectReason("");
    setShowResetForm(false);
    setResetReason("");
    setEditForm({
      name: guest.name, phone: guest.phone, email: guest.email ?? "", address: guest.address ?? "",
      state_code: guest.state_code ?? "",
      date_of_birth: guest.date_of_birth ?? "", gender: guest.gender ?? "", nationality: guest.nationality ?? "",
      occupation: guest.occupation ?? "", whatsapp_number: guest.whatsapp_number ?? "",
      emergency_contact_name: guest.emergency_contact_name ?? "", emergency_contact_phone: guest.emergency_contact_phone ?? "",
      notes: guest.notes ?? "",
    });
    setLoadingHistory(true);
    setError(null);
    try {
      const [h, d] = await Promise.all([
        api.get(`/guests/${guest.id}/history`),
        api.get(`/guests/${guest.id}/documents`),
      ]);
      setHistory(h.data);
      setDocuments(d.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoadingHistory(false);
    }
  }

  function closeDetail() {
    setDetailGuestId(null);
    setHistory(null);
    setDocuments([]);
  }

  async function handleSaveEdit() {
    if (!detailGuestId) return;
    setError(null);
    setSuccess(null);
    try {
      const payload: Record<string, string | undefined> = {};
      Object.entries(editForm).forEach(([k, v]) => { payload[k] = v || undefined; });
      const { data } = await api.patch(`/guests/${detailGuestId}`, payload);
      setSuccess("Guest updated.");
      setGuests((prev) => prev.map((g) => (g.id === detailGuestId ? data : g)));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function toggleVip() {
    if (!detailGuestId) return;
    const guest = guests.find((g) => g.id === detailGuestId);
    if (!guest) return;
    try {
      const { data } = await api.patch(`/guests/${detailGuestId}`, { is_vip: !guest.is_vip });
      setGuests((prev) => prev.map((g) => (g.id === detailGuestId ? data : g)));
      setSuccess(data.is_vip ? "Marked as VIP." : "VIP status removed.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function toggleBlacklist() {
    if (!detailGuestId) return;
    const guest = guests.find((g) => g.id === detailGuestId);
    if (!guest) return;
    try {
      const { data } = await api.patch(`/guests/${detailGuestId}`, { is_blacklisted: !guest.is_blacklisted });
      setGuests((prev) => prev.map((g) => (g.id === detailGuestId ? data : g)));
      setSuccess(data.is_blacklisted ? "Guest blacklisted." : "Guest removed from blacklist.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleUploadDoc() {
    if (!detailGuestId || !docFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", docFile);
      await api.post(`/guests/${detailGuestId}/documents`, formData, {
        params: { document_type: docForm.document_type, document_number: docForm.document_number || undefined, side: docForm.side },
        headers: { "Content-Type": "multipart/form-data" },
      });
      setSuccess("Document uploaded.");
      setDocFile(null);
      const { data } = await api.get(`/guests/${detailGuestId}/documents`);
      setDocuments(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadDoc(docId: string, filename: string) {
    if (!detailGuestId) return;
    try {
      const res = await api.get(`/guests/${detailGuestId}/documents/${docId}/download`, { responseType: "blob" });
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

  async function handleDeleteDoc(docId: string) {
    if (!detailGuestId) return;
    try {
      await api.delete(`/guests/${detailGuestId}/documents/${docId}`);
      setDocuments((prev) => prev.filter((d) => d.id !== docId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleVerifyIdentity() {
    if (!detailGuestId || !verifyDocId) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post(`/guests/${detailGuestId}/identity/verify`, { document_id: verifyDocId });
      setGuests((prev) => prev.map((g) => (g.id === detailGuestId ? data : g)));
      setVerifyDocId("");
      setSuccess("Identity verified.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleRejectIdentity() {
    if (!detailGuestId || !rejectReason.trim()) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post(`/guests/${detailGuestId}/identity/reject`, { reason: rejectReason.trim() });
      setGuests((prev) => prev.map((g) => (g.id === detailGuestId ? data : g)));
      setShowRejectForm(false);
      setRejectReason("");
      setSuccess("Identity verification rejected.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleResetIdentity() {
    if (!detailGuestId || !resetReason.trim()) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post(`/guests/${detailGuestId}/identity/reset`, { reason: resetReason.trim() });
      setGuests((prev) => prev.map((g) => (g.id === detailGuestId ? data : g)));
      setShowResetForm(false);
      setResetReason("");
      setSuccess("Re-verification requested — the guest will need to submit a new document.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleMerge() {
    if (!detailGuestId || !mergeDuplicateId) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post("/guests/merge", { primary_guest_id: detailGuestId, duplicate_guest_id: mergeDuplicateId });
      setSuccess(`Merged — ${data.bookings_reassigned} booking(s) reassigned.`);
      setMergeMode(false);
      setMergeDuplicateId("");
      loadGuests();
      loadSegments();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const activeGuest = guests.find((g) => g.id === detailGuestId);

  return (
    <div>
      <PageHeader title="Guests" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <Card className="!rounded-[14px] mb-8 max-w-2xl shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Guest</h2>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <FormSectionLabel>Guest Information</FormSectionLabel>
            <div className="grid grid-cols-2 gap-3">
              <input required placeholder="Full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT_CLASSES} />
              <input required placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className={INPUT_CLASSES} />
              <input type="email" placeholder="Email (optional)" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={INPUT_CLASSES} />
              <input placeholder="WhatsApp (optional)" value={form.whatsapp_number} onChange={(e) => setForm({ ...form, whatsapp_number: e.target.value })} className={INPUT_CLASSES} />
            </div>
          </div>

          <div>
            <FormSectionLabel>Personal Details</FormSectionLabel>
            <div className="grid grid-cols-3 gap-3">
              <input type="date" lang="en" placeholder="Date of birth (optional)" value={form.date_of_birth} onChange={(e) => setForm({ ...form, date_of_birth: e.target.value })} className={INPUT_CLASSES} />
              <select value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })} className={INPUT_CLASSES}>
                <option value="">Gender (optional)...</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
                <option value="other">Other</option>
              </select>
              <input placeholder="Nationality (optional)" value={form.nationality} onChange={(e) => setForm({ ...form, nationality: e.target.value })} className={INPUT_CLASSES} />
            </div>
          </div>

          <div>
            <FormSectionLabel>Address</FormSectionLabel>
            <div className="space-y-3">
              <input placeholder="Address (optional)" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} className={`w-full ${INPUT_CLASSES}`} />
              <select value={form.state_code} onChange={(e) => setForm({ ...form, state_code: e.target.value })} className={`w-full ${INPUT_CLASSES}`}>
                <option value="">State (optional, for GST)...</option>
                {INDIAN_STATES.map((s) => (
                  <option key={s.code} value={s.code}>{s.name}</option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <FormSectionLabel>Emergency Contact</FormSectionLabel>
            <div className="grid grid-cols-2 gap-3">
              <input placeholder="Emergency contact name (optional)" value={form.emergency_contact_name} onChange={(e) => setForm({ ...form, emergency_contact_name: e.target.value })} className={INPUT_CLASSES} />
              <input placeholder="Emergency contact phone (optional)" value={form.emergency_contact_phone} onChange={(e) => setForm({ ...form, emergency_contact_phone: e.target.value })} className={INPUT_CLASSES} />
            </div>
          </div>

          <Button type="submit">Add Guest</Button>
        </form>
      </Card>

      <Card className="!rounded-[14px] shadow-sm">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-slate-900">Guests</h2>
          <input placeholder="Search name, phone, or email..." value={search} onChange={(e) => setSearch(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs min-w-[220px]" />
        </div>
        <div className="flex flex-wrap items-center gap-2 mb-4 pb-3 border-b border-slate-100">
          <span className="text-xs text-slate-400">Filter:</span>
          <select value={segmentFilter} onChange={(e) => setSegmentFilter(e.target.value as "" | "new" | "repeat")} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
            <option value="">All guests</option>
            <option value="new">New</option>
            <option value="repeat">Repeat</option>
          </select>
          <input
            placeholder="Latest source (e.g. direct, whatsapp)..."
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs min-w-[180px]"
          />
          <span className="text-xs text-slate-400 ml-2">Sort by:</span>
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value as SortBy)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
            <option value="name">Name</option>
            <option value="total_bookings">Bookings</option>
            <option value="total_spent">Total Spent</option>
            <option value="last_stay">Last Stay</option>
            <option value="segment">New / Repeat</option>
            <option value="source">Latest Source</option>
          </select>
          <button
            onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
            className="text-xs rounded-lg border border-slate-300 px-2 py-1.5 text-slate-600 hover:bg-slate-50"
            title="Toggle sort direction"
          >
            {sortDir === "asc" ? "↑ Asc" : "↓ Desc"}
          </button>
        </div>
        {segments.length === 0 ? (
          <EmptyState
            message={
              !search && !segmentFilter && !sourceFilter
                ? "No guests found. Add your first guest above to start managing guest records."
                : "No guests match your current filters."
            }
          />
        ) : (
          <Table headers={["Name", "Phone", "Email", "Segment", "Stays", "Total Spent", "Latest Source", "Last Stay", "Status", "Actions"]}>
            {segments.map((seg) => {
              const g = guestsById.get(seg.guest_id);
              return (
                <tr key={seg.guest_id}>
                  <td className="px-4 py-2.5 font-semibold text-slate-900">{seg.name}</td>
                  <td className="px-4 py-2.5 text-slate-500">{seg.phone}</td>
                  <td className="px-4 py-2.5 text-slate-500">{seg.email ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    {seg.is_repeat_guest ? <Badge tone="green">Repeat</Badge> : <Badge tone="blue">New</Badge>}
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">{seg.total_bookings}</td>
                  <td className="px-4 py-2.5 text-slate-500">{formatCurrency(seg.total_spent)}</td>
                  <td className="px-4 py-2.5 text-slate-500">{seg.latest_source ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{formatDate(seg.last_stay)}</td>
                  <td className="px-4 py-2.5 space-x-1">
                    {(g?.is_vip ?? seg.is_vip) && <Badge tone="amber">VIP</Badge>}
                    {(g?.is_blacklisted ?? seg.is_blacklisted) && <Badge tone="red">Blacklisted</Badge>}
                    {g && g.identity_status === "verified" && <Badge tone="green">Identity ✓</Badge>}
                    {g && (g.identity_status === "pending" || g.identity_status === "submitted" || g.identity_status === "under_review") && (
                      <Badge tone="amber">Identity Pending</Badge>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    {g ? (
                      <Button variant="secondary" onClick={() => openDetail(g)}>
                        View / Edit →
                      </Button>
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      {detailGuestId && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={closeDetail}>
          <div className="bg-white rounded-[14px] shadow-sm max-w-3xl w-full max-h-[88vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            {loadingHistory && <p className="text-sm text-slate-400">Loading...</p>}
            {history && activeGuest && (
              <>
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                      {history.guest.name}
                      {history.is_repeat_guest && <Badge tone="green">Repeat Guest</Badge>}
                      {activeGuest.is_vip && <Badge tone="amber">VIP</Badge>}
                      {activeGuest.is_blacklisted && <Badge tone="red">Blacklisted</Badge>}
                      <Badge tone={identityStatusMeta(activeGuest.identity_status).tone}>{identityStatusMeta(activeGuest.identity_status).label}</Badge>
                    </h2>
                    <p className="text-sm text-slate-500">{history.guest.phone}{history.guest.email ? ` · ${history.guest.email}` : ""}</p>
                  </div>
                  <button onClick={closeDetail} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
                </div>

                <div className="flex gap-2 mb-4">
                  <Button variant="secondary" onClick={toggleVip}>{activeGuest.is_vip ? "Remove VIP" : "Mark VIP"}</Button>
                  <Button variant="secondary" onClick={toggleBlacklist}>{activeGuest.is_blacklisted ? "Remove from Blacklist" : "Blacklist Guest"}</Button>
                  {isAdmin && <Button variant="secondary" onClick={() => setMergeMode(!mergeMode)}>Merge Duplicate</Button>}
                </div>

                {isAdmin && mergeMode && (
                  <div className="mb-4 bg-amber-50 rounded-lg p-3">
                    <p className="text-xs text-amber-700 mb-2">Select the duplicate record to merge into this guest. All of its bookings will be reassigned here.</p>
                    <div className="flex gap-2">
                      <select value={mergeDuplicateId} onChange={(e) => setMergeDuplicateId(e.target.value)} className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs">
                        <option value="">Select duplicate guest...</option>
                        {guests.filter((g) => g.id !== detailGuestId).map((g) => <option key={g.id} value={g.id}>{g.name} ({g.phone})</option>)}
                      </select>
                      <Button onClick={handleMerge}>Merge</Button>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-4 mb-6">
                  <div className="bg-slate-50 rounded-lg p-3">
                    <p className="text-xs text-slate-500">Total Bookings</p>
                    <p className="text-xl font-semibold text-slate-900">{history.total_bookings}</p>
                  </div>
                  <div className="bg-slate-50 rounded-lg p-3">
                    <p className="text-xs text-slate-500">Total Spent</p>
                    <p className="text-xl font-semibold text-slate-900">{formatCurrency(history.total_spent)}</p>
                  </div>
                </div>

                <h3 className="text-sm font-semibold text-slate-900 mb-2">Edit Profile</h3>
                <div className="grid grid-cols-2 gap-2 mb-6 text-xs">
                  <input placeholder="Name" value={editForm.name ?? ""} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Phone" value={editForm.phone ?? ""} onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Email" value={editForm.email ?? ""} onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="WhatsApp" value={editForm.whatsapp_number ?? ""} onChange={(e) => setEditForm({ ...editForm, whatsapp_number: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Address" value={editForm.address ?? ""} onChange={(e) => setEditForm({ ...editForm, address: e.target.value })} className="col-span-2 rounded-lg border border-slate-300 px-2 py-1.5" />
                  <select value={editForm.state_code ?? ""} onChange={(e) => setEditForm({ ...editForm, state_code: e.target.value })} className="col-span-2 rounded-lg border border-slate-300 px-2 py-1.5">
                    <option value="">State (for GST)...</option>
                    {INDIAN_STATES.map((s) => (
                      <option key={s.code} value={s.code}>{s.name}</option>
                    ))}
                  </select>
                  <input type="date" lang="en" value={editForm.date_of_birth ?? ""} onChange={(e) => setEditForm({ ...editForm, date_of_birth: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Occupation" value={editForm.occupation ?? ""} onChange={(e) => setEditForm({ ...editForm, occupation: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Emergency contact name" value={editForm.emergency_contact_name ?? ""} onChange={(e) => setEditForm({ ...editForm, emergency_contact_name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Emergency contact phone" value={editForm.emergency_contact_phone ?? ""} onChange={(e) => setEditForm({ ...editForm, emergency_contact_phone: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <textarea placeholder="Notes" value={editForm.notes ?? ""} onChange={(e) => setEditForm({ ...editForm, notes: e.target.value })} className="col-span-2 rounded-lg border border-slate-300 px-2 py-1.5" rows={2} />
                </div>
                <Button variant="secondary" onClick={handleSaveEdit}>Save Profile</Button>

                <h3 className="text-sm font-semibold text-slate-900 mt-6 mb-2">Documents</h3>
                <div className="flex flex-wrap gap-2 mb-3">
                  <select value={docForm.document_type} onChange={(e) => setDocForm({ ...docForm, document_type: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                    {DOCUMENT_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
                  </select>
                  <select value={docForm.side} onChange={(e) => setDocForm({ ...docForm, side: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                    <option value="front">Front</option>
                    <option value="back">Back</option>
                    <option value="document">Document (PDF)</option>
                  </select>
                  <input placeholder="Document number (optional)" value={docForm.document_number} onChange={(e) => setDocForm({ ...docForm, document_number: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
                  <input type="file" onChange={(e) => setDocFile(e.target.files?.[0] ?? null)} className="text-xs" />
                  <Button variant="secondary" onClick={handleUploadDoc}>Upload</Button>
                </div>
                {documents.length === 0 ? (
                  <p className="text-xs text-slate-400 mb-6">No documents uploaded yet.</p>
                ) : (
                  <div className="grid grid-cols-2 gap-2 mb-6">
                    {documents.map((d) => (
                      <div key={d.id} className="border border-slate-100 rounded-lg p-2 text-xs">
                        <p className="font-medium text-slate-700">{d.document_type.replace("_", " ")} ({d.side})</p>
                        {d.document_number && <p className="text-slate-500">{d.document_number}</p>}
                        <div className="flex gap-2 mt-1">
                          <button onClick={() => handleDownloadDoc(d.id, d.filename)} className="text-slate-600 hover:underline">Download</button>
                          <button onClick={() => handleDeleteDoc(d.id)} className="text-red-500 hover:underline">Delete</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/*
                  Identity Verification — separate from Account
                  Verification (this page has no such field to show; see
                  the feature's own architecture notes). Status is visible
                  to everyone who can open this modal (admin/staff/manager,
                  the same as document access, unchanged); the actions
                  that actually change status (verify/reject/request
                  re-verification) are admin-only, matching the backend's
                  own require_role("admin") on those three endpoints —
                  this UI gate is presentation only, not the real security
                  boundary.
                */}
                <h3 className="text-sm font-semibold text-slate-900 mb-2">Identity Verification</h3>
                <div className="mb-6 bg-slate-50 rounded-lg p-3 text-xs space-y-2">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <Badge tone={identityStatusMeta(activeGuest.identity_status).tone}>{identityStatusMeta(activeGuest.identity_status).label}</Badge>
                    {activeGuest.identity_verified_at && (
                      <span className="text-slate-400">Verified {formatDate(activeGuest.identity_verified_at.slice(0, 10))}</span>
                    )}
                  </div>
                  {activeGuest.identity_status === "rejected" && activeGuest.identity_rejection_reason && (
                    <p className="text-red-600">Reason: {activeGuest.identity_rejection_reason}</p>
                  )}

                  {isAdmin && (
                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      <select
                        value={verifyDocId}
                        onChange={(e) => setVerifyDocId(e.target.value)}
                        aria-label="Document to verify"
                        className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
                        disabled={documents.length === 0}
                      >
                        <option value="">Select document to verify...</option>
                        {documents.map((d) => (
                          <option key={d.id} value={d.id}>{d.document_type.replace("_", " ")} ({d.side})</option>
                        ))}
                      </select>
                      <Button onClick={handleVerifyIdentity} disabled={!verifyDocId}>Verify</Button>
                      <Button variant="warning" onClick={() => setShowRejectForm(!showRejectForm)}>Reject</Button>
                      {(activeGuest.identity_status === "verified" || activeGuest.identity_status === "rejected" || activeGuest.identity_status === "expired") && (
                        <Button variant="secondary" onClick={() => setShowResetForm(!showResetForm)}>Request Re-verification</Button>
                      )}
                    </div>
                  )}

                  {isAdmin && showRejectForm && (
                    <div className="flex gap-2 pt-2">
                      <label htmlFor="reject-reason" className="sr-only">Rejection reason</label>
                      <input
                        id="reject-reason"
                        required
                        placeholder="Reason (shown to staff, not the guest)"
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                        className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5"
                      />
                      <Button variant="danger" onClick={handleRejectIdentity} disabled={!rejectReason.trim()}>Confirm Reject</Button>
                    </div>
                  )}

                  {isAdmin && showResetForm && (
                    <div className="flex gap-2 pt-2">
                      <label htmlFor="reset-reason" className="sr-only">Re-verification reason</label>
                      <input
                        id="reset-reason"
                        required
                        placeholder="Reason (e.g. document expired)"
                        value={resetReason}
                        onChange={(e) => setResetReason(e.target.value)}
                        className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5"
                      />
                      <Button variant="secondary" onClick={handleResetIdentity} disabled={!resetReason.trim()}>Confirm</Button>
                    </div>
                  )}
                </div>

                <h3 className="text-sm font-semibold text-slate-900 mb-2">Booking History</h3>
                {history.bookings.length === 0 ? (
                  <p className="text-xs text-slate-400 mb-6">No bookings yet.</p>
                ) : (
                  <div className="overflow-x-auto mb-6">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-left text-slate-400 border-b border-slate-100">
                          <th className="py-1.5 pr-3">Unit</th>
                          <th className="py-1.5 pr-3">Stay</th>
                          <th className="py-1.5 pr-3">Status</th>
                          <th className="py-1.5 pr-3">Paid</th>
                          <th className="py-1.5">Invoice</th>
                        </tr>
                      </thead>
                      <tbody>
                        {history.bookings.map((b) => (
                          <tr key={b.booking_id} className="border-b border-slate-50">
                            <td className="py-1.5 pr-3 font-medium text-slate-700">{b.unit_name}</td>
                            <td className="py-1.5 pr-3 text-slate-500">{formatDate(b.check_in)} → {formatDate(b.check_out)}</td>
                            <td className="py-1.5 pr-3"><Badge tone={b.status === "checked_out" ? "green" : "slate"}>{b.status}</Badge></td>
                            <td className="py-1.5 pr-3 text-slate-500">{formatCurrency(b.total_paid)}</td>
                            <td className="py-1.5 text-slate-500">{b.invoice_no ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <h3 className="text-sm font-semibold text-slate-900 mb-2">Requests / Complaints</h3>
                {history.requests.length === 0 ? (
                  <p className="text-xs text-slate-400">None raised.</p>
                ) : (
                  <div className="space-y-2">
                    {history.requests.map((r, i) => (
                      <div key={i} className="border border-slate-100 rounded-lg px-3 py-2 text-xs">
                        <span className="font-medium text-slate-700">{r.request_type}</span>
                        <span className="text-slate-500"> · {r.description} · </span>
                        <Badge tone={r.status === "resolved" ? "green" : "amber"}>{r.status}</Badge>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
