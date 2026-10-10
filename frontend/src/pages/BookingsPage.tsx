import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type BookingAttachmentOut, type BookingGuestsOut, type BookingOut, type GuestOut, type UnitOut, type PaymentOut, type PropertyOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, SkeletonRows, EmptyState, type BadgeTone } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import GuestPicker from "../components/GuestPicker";
import { useRealtimeRefresh } from "../lib/realtime";

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

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  return `${d.getDate()} ${monthShortFormatter.format(d)} ${d.getFullYear()}, ${time}`;
}

function nightsBetween(checkIn: string, checkOut: string): number {
  return Math.max(0, Math.round((new Date(checkOut).getTime() - new Date(checkIn).getTime()) / (1000 * 60 * 60 * 24)));
}

// Identity Verification display — separate from Account Verification,
// which this page never touches. Only ever changed via the dedicated
// /guests/{id}/identity/* actions on the Guests page, never here.
function identityStatusMeta(status: GuestOut["identity_status"]): { label: string; tone: BadgeTone } {
  if (status === "verified") return { label: "Identity Verified", tone: "green" };
  if (status === "submitted" || status === "under_review") return { label: "Identity Pending", tone: "amber" };
  if (status === "pending") return { label: "Identity Pending", tone: "amber" };
  if (status === "rejected") return { label: "Identity Rejected", tone: "red" };
  if (status === "expired") return { label: "Identity Expired", tone: "slate" };
  return { label: "Identity Not Verified", tone: "slate" };
}

// Same PAID / PARTIALLY PAID / PENDING vocabulary already used elsewhere in
// OS10 (Messages/Guests booking-details panels) — derived here from the
// same total/paid figures already shown on this page, not a new business
// rule or an invented status value.
function paymentStatusOf(total: number, paid: number): { label: string; tone: "green" | "amber" | "slate" } {
  if (total > 0 && paid >= total) return { label: "Paid", tone: "green" };
  if (paid > 0) return { label: "Partially Paid", tone: "amber" };
  return { label: "Pending", tone: "amber" };
}

function SectionLabel({ children }: { children: string }) {
  return <h3 className="mb-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">{children}</h3>;
}

const INPUT_CLASSES = "w-full rounded-[10px] border border-slate-300 px-3 py-2.5 text-sm placeholder:text-slate-400";

export default function BookingsPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";

  const [bookings, setBookings] = useState<BookingOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [properties, setProperties] = useState<PropertyOut[]>([]);
  const [guests, setGuests] = useState<GuestOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [form, setForm] = useState({ unit_id: "", guest_id: "", check_in: "", check_out: "", adults: "1", children: "0", extra_bed: false, arrival_time: "", departure_time: "" });
  const [selectedNewGuest, setSelectedNewGuest] = useState<GuestOut | null>(null);
  const [availability, setAvailability] = useState<{ checked: boolean; available: boolean }>({
    checked: false,
    available: false,
  });
  const [checkingAvailability, setCheckingAvailability] = useState(false);
  const [loadingBookings, setLoadingBookings] = useState(true);
  const [refreshingBookings, setRefreshingBookings] = useState(false);
  const lastBookingsRefreshRef = useRef(0);

  const [detailsBookingId, setDetailsBookingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ guest_id: "", unit_id: "", check_in: "", check_out: "", rate_applied: "", reason: "", adults: "1", children: "0", extra_bed: false, arrival_time: "", departure_time: "", internal_notes: "", special_requests: "", expected_updated_at: "" });
  const [selectedEditGuest, setSelectedEditGuest] = useState<GuestOut | null>(null);
  const [attachments, setAttachments] = useState<BookingAttachmentOut[]>([]);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [timeline, setTimeline] = useState<{ action: string; user_name: string; details: string | null; previous_value: string | null; new_value: string | null; created_at: string }[]>([]);
  const [showTimeline, setShowTimeline] = useState(false);
  const [payments, setPayments] = useState<PaymentOut[]>([]);
  const [paymentForm, setPaymentForm] = useState({ amount: "", mode: "upi", reference_no: "" });
  const [refundingPaymentId, setRefundingPaymentId] = useState<string | null>(null);
  const [refundForm, setRefundForm] = useState({ refund_amount: "", reason: "" });
  const [bookingSearch, setBookingSearch] = useState("");
  const [bookingStatusFilter, setBookingStatusFilter] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [archivingBooking, setArchivingBooking] = useState(false);
  const [archiveReason, setArchiveReason] = useState("");
  const detailsRef = useRef<HTMLDivElement>(null);

  // Additional Guests (section 14/17) — Principal Guest stays exactly
  // editForm.guest_id/selectedEditGuest above, unchanged; this is only the
  // list of Additional Guests attached via the new booking_guests
  // assignment, fetched alongside the booking's other sub-resources.
  const [bookingGuests, setBookingGuests] = useState<BookingGuestsOut | null>(null);
  const [showAddGuest, setShowAddGuest] = useState(false);

  // Clicking Edit opens the Reservation Details panel above the booking
  // table (it's a single shared panel, not one per row), which can be far
  // below the row that was clicked on a long list — this brings the panel
  // into view instead of leaving the user to scroll and hunt for it.
  useEffect(() => {
    if (detailsBookingId && detailsRef.current) {
      detailsRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
      detailsRef.current.focus();
    }
  }, [detailsBookingId]);

  async function loadAll() {
    try {
      const [b, u, g, p] = await Promise.all([
        api.get("/bookings", { params: showArchived ? { include_archived: true } : undefined }),
        api.get("/units"),
        api.get("/guests"),
        api.get("/properties"),
      ]);
      setBookings(b.data);
      setUnits(u.data);
      setGuests(g.data);
      setProperties(p.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoadingBookings(false);
    }
  }

  // Lighter-weight than loadAll(): re-fetches bookings and guests (not
  // units/properties, which don't go stale nearly as often — the six real
  // apartments essentially never change) and never shows the full-table
  // skeleton — existing rows stay visible while this resolves, since it's
  // a background refresh, not the initial load. Guests are included
  // alongside bookings because guestName() below resolves a booking's
  // guest_id against this same list: a booking for a brand-new guest
  // (e.g. a website guest who has never booked before) would otherwise
  // render "—" after a background refresh until the next full page load,
  // even though the booking itself was already showing correctly.
  const refreshBookings = useCallback(async () => {
    setRefreshingBookings(true);
    try {
      const [b, g] = await Promise.all([
        api.get("/bookings", { params: showArchived ? { include_archived: true } : undefined }),
        api.get("/guests"),
      ]);
      setBookings(b.data);
      setGuests(g.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRefreshingBookings(false);
    }
  }, [showArchived]);

  useRealtimeRefresh(["bookings.changed", "availability.changed", "guests.changed", "payments.changed", "properties.changed"], refreshBookings);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showArchived]);

  // Booking/OS10 freshness fix: a booking confirmed on the public website
  // while this page was already open (e.g. a guest paying while staff has
  // Bookings open in another tab) never appeared until a manual page
  // reload, since the bookings list was only ever fetched once on mount.
  // Refetching when the tab regains focus/visibility closes that gap
  // without resorting to continuous polling.
  useEffect(() => {
    function handleVisibilityOrFocus() {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      // Browsers commonly fire both "visibilitychange" and "focus" for the
      // same tab-switch — without this guard, returning to the tab would
      // trigger two near-simultaneous /bookings requests instead of one.
      if (now - lastBookingsRefreshRef.current < 1000) return;
      lastBookingsRefreshRef.current = now;
      refreshBookings();
    }
    window.addEventListener("focus", handleVisibilityOrFocus);
    document.addEventListener("visibilitychange", handleVisibilityOrFocus);
    return () => {
      window.removeEventListener("focus", handleVisibilityOrFocus);
      document.removeEventListener("visibilitychange", handleVisibilityOrFocus);
    };
  }, [refreshBookings]);

  function handleManualBookingsRefresh() {
    lastBookingsRefreshRef.current = Date.now();
    refreshBookings();
  }

  useEffect(() => {
    setAvailability({ checked: false, available: false });
    if (!form.unit_id || !form.check_in || !form.check_out) return;
    if (form.check_out <= form.check_in) return;

    setCheckingAvailability(true);
    const timeout = setTimeout(async () => {
      try {
        const { data } = await api.get(`/units/${form.unit_id}/availability`, {
          params: { check_in: form.check_in, check_out: form.check_out },
        });
        setAvailability({ checked: true, available: data.available });
      } catch {
        setAvailability({ checked: false, available: false });
      } finally {
        setCheckingAvailability(false);
      }
    }, 300);

    return () => clearTimeout(timeout);
  }, [form.unit_id, form.check_in, form.check_out]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    if (!form.guest_id) {
      setError("Select or create a guest before confirming the booking.");
      return;
    }
    try {
      const { data } = await api.post("/bookings", {
        ...form,
        adults: Number(form.adults),
        children: Number(form.children),
        arrival_time: form.arrival_time || undefined,
        departure_time: form.departure_time || undefined,
      });
      setSuccess(`Booking confirmed: ${data.check_in} → ${data.check_out}`);
      setForm({ unit_id: "", guest_id: "", check_in: "", check_out: "", adults: "1", children: "0", extra_bed: false, arrival_time: "", departure_time: "" });
      setSelectedNewGuest(null);
      setAvailability({ checked: false, available: false });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function transitionStatus(bookingId: string, newStatus: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.patch(`/bookings/${bookingId}/status`, null, { params: { new_status: newStatus } });
      setSuccess(`Booking updated to ${newStatus.replace("_", " ")}.`);
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleCancelBooking(bookingId: string) {
    if (!window.confirm("Cancel this booking? OS10 determines refund eligibility and any policy refund amount. Cancellation may start the applicable refund workflow; some payment sources or cancellations at/after check-in require manual review or reconciliation. Staff cannot set the policy refund amount. Booking, payment, and invoice records are preserved.")) return;
    await transitionStatus(bookingId, "cancelled");
  }

  const activePropertyIds = new Set(properties.filter((p) => p.is_active).map((p) => p.id));
  const bookableUnits = units.filter((u) => u.is_active && activePropertyIds.has(u.property_id));

  function unitName(id: string) {
    return units.find((u) => u.id === id)?.name ?? "—";
  }
  function guestName(id: string) {
    return guests.find((g) => g.id === id)?.name ?? "—";
  }

  const filteredBookings = bookings
    .filter((b) => !bookingStatusFilter || b.status === bookingStatusFilter)
    .filter((b) => {
      if (!bookingSearch) return true;
      const q = bookingSearch.toLowerCase();
      return unitName(b.unit_id).toLowerCase().includes(q) || guestName(b.guest_id).toLowerCase().includes(q);
    });

  function exportBookingsCsv() {
    const rows = [
      ["Unit", "Guest", "Check-in", "Check-out", "Adults", "Children", "Rate/night", "Status"],
      ...filteredBookings.map((b) => [unitName(b.unit_id), guestName(b.guest_id), b.check_in, b.check_out, String(b.adults), String(b.children), String(b.rate_applied), b.status]),
    ];
    const csv = rows.map((r) => r.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `bookings_${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    window.URL.revokeObjectURL(url);
  }

  function openDetails(b: BookingOut) {
    setDetailsBookingId(b.id);
    setEditForm({
      guest_id: b.guest_id, unit_id: b.unit_id, check_in: b.check_in, check_out: b.check_out,
      rate_applied: String(b.rate_applied), reason: "",
      adults: String(b.adults), children: String(b.children), extra_bed: b.extra_bed,
      arrival_time: b.arrival_time ?? "", departure_time: b.departure_time ?? "",
      internal_notes: b.internal_notes ?? "", special_requests: b.special_requests ?? "",
      expected_updated_at: b.updated_at,
    });
    setSelectedEditGuest(guests.find((g) => g.id === b.guest_id) ?? null);
    api.get(`/bookings/${b.id}/attachments`).then((r) => setAttachments(r.data)).catch(() => setAttachments([]));
    api.get(`/bookings/${b.id}/timeline`).then((r) => setTimeline(r.data.events)).catch(() => setTimeline([]));
    api.get(`/payments/${b.id}`).then((r) => setPayments(r.data)).catch(() => setPayments([]));
    api.get(`/bookings/${b.id}/guests`).then((r) => setBookingGuests(r.data)).catch(() => setBookingGuests(null));
    setShowTimeline(false);
    setRefundingPaymentId(null);
    setArchivingBooking(false);
    setArchiveReason("");
    setShowAddGuest(false);
  }

  function closeDetails() {
    setDetailsBookingId(null);
    setAttachments([]);
    setTimeline([]);
    setUploadFile(null);
    setPayments([]);
    setRefundingPaymentId(null);
    setSelectedEditGuest(null);
    setArchivingBooking(false);
    setArchiveReason("");
    setBookingGuests(null);
    setShowAddGuest(false);
  }

  async function handleAddAdditionalGuest(guest: GuestOut) {
    if (!detailsBookingId) return;
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/bookings/${detailsBookingId}/guests`, { guest_id: guest.id });
      const r = await api.get(`/bookings/${detailsBookingId}/guests`);
      setBookingGuests(r.data);
      setShowAddGuest(false);
      setSuccess(`${guest.name} added as an additional guest.`);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleRemoveAdditionalGuest(bookingGuestId: string) {
    if (!detailsBookingId) return;
    setError(null);
    setSuccess(null);
    try {
      await api.delete(`/bookings/${detailsBookingId}/guests/${bookingGuestId}`);
      const r = await api.get(`/bookings/${detailsBookingId}/guests`);
      setBookingGuests(r.data);
      setSuccess("Additional guest removed from this booking.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleAddPayment(e: FormEvent) {
    e.preventDefault();
    if (!detailsBookingId) return;
    setError(null);
    setSuccess(null);
    try {
      await api.post("/payments", {
        booking_id: detailsBookingId, amount: Number(paymentForm.amount), mode: paymentForm.mode,
        reference_no: paymentForm.reference_no || undefined,
      });
      setSuccess("Payment recorded.");
      setPaymentForm({ amount: "", mode: "upi", reference_no: "" });
      const r = await api.get(`/payments/${detailsBookingId}`);
      setPayments(r.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleRefund(e: FormEvent) {
    e.preventDefault();
    if (!detailsBookingId || !refundingPaymentId) return;
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/payments/${refundingPaymentId}/refund`, {
        refund_amount: Number(refundForm.refund_amount), reason: refundForm.reason,
      });
      setSuccess("Refund recorded.");
      setRefundingPaymentId(null);
      setRefundForm({ refund_amount: "", reason: "" });
      const r = await api.get(`/payments/${detailsBookingId}`);
      setPayments(r.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function netPaid(list: PaymentOut[]): number {
    return list.reduce((sum, p) => sum + p.amount, 0);
  }

  function estimatedTotal(b: BookingOut): number {
    const nights = Math.max(0, (new Date(b.check_out).getTime() - new Date(b.check_in).getTime()) / (1000 * 60 * 60 * 24));
    return Math.round(nights * b.rate_applied);
  }

  function refundedSoFar(list: PaymentOut[], paymentId: string): number {
    return list.filter((p) => p.refund_of_payment_id === paymentId).reduce((sum, p) => sum + Math.abs(p.amount), 0);
  }

  async function handleSaveEdit(e: FormEvent) {
    e.preventDefault();
    if (!detailsBookingId) return;
    setError(null);
    setSuccess(null);
    if (!editForm.guest_id) {
      setError("Select a guest before saving changes.");
      return;
    }
    try {
      await api.patch(`/bookings/${detailsBookingId}`, {
        guest_id: editForm.guest_id, unit_id: editForm.unit_id,
        check_in: editForm.check_in, check_out: editForm.check_out,
        rate_applied: Number(editForm.rate_applied), reason: editForm.reason || undefined,
        adults: Number(editForm.adults), children: Number(editForm.children), extra_bed: editForm.extra_bed,
        arrival_time: editForm.arrival_time || undefined, departure_time: editForm.departure_time || undefined,
        internal_notes: editForm.internal_notes || undefined, special_requests: editForm.special_requests || undefined,
        expected_updated_at: editForm.expected_updated_at || undefined,
      });
      setSuccess("Booking updated.");
      closeDetails();
      loadAll();
    } catch (err) {
      // A 409 here means someone else changed this booking since it was
      // opened — reload its current state into the form instead of just
      // showing an error, so the user isn't stuck re-entering everything
      // from scratch to see what changed.
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 409 && detailsBookingId) {
        try {
          const fresh = await api.get(`/bookings`, { params: { include_archived: true } });
          const freshBooking = (fresh.data as BookingOut[]).find((b) => b.id === detailsBookingId);
          if (freshBooking) openDetails(freshBooking);
        } catch {
          // fall through to showing the original error below
        }
      }
      setError(extractErrorMessage(err));
    }
  }

  async function handleDelete(bookingId: string) {
    if (!window.confirm("Delete this booking? This cannot be undone. If a payment, invoice, or deposit is linked, this will be rejected — cancel the booking instead in that case.")) return;
    setError(null);
    setSuccess(null);
    try {
      await api.delete(`/bookings/${bookingId}`);
      setSuccess("Booking deleted.");
      closeDetails();
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleArchiveBooking(e: FormEvent) {
    e.preventDefault();
    if (!detailsBookingId) return;
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/bookings/${detailsBookingId}/archive`, { reason: archiveReason || undefined });
      setSuccess("Booking archived. It's been removed from the active list but its history, payments, and invoices are preserved — restore it any time.");
      closeDetails();
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleRestoreBooking(bookingId: string) {
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/bookings/${bookingId}/restore`);
      setSuccess("Booking restored to the active list.");
      closeDetails();
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleUpload() {
    if (!detailsBookingId || !uploadFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      await api.post(`/bookings/${detailsBookingId}/attachments`, formData, { headers: { "Content-Type": "multipart/form-data" } });
      setSuccess("Attachment uploaded.");
      setUploadFile(null);
      const r = await api.get(`/bookings/${detailsBookingId}/attachments`);
      setAttachments(r.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadAttachment(attachmentId: string, filename: string) {
    if (!detailsBookingId) return;
    try {
      const res = await api.get(`/bookings/${detailsBookingId}/attachments/${attachmentId}/download`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeleteAttachment(attachmentId: string) {
    if (!detailsBookingId) return;
    try {
      await api.delete(`/bookings/${detailsBookingId}/attachments/${attachmentId}`);
      setAttachments(attachments.filter((a) => a.id !== attachmentId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const detailsBooking = bookings.find((b) => b.id === detailsBookingId);

  return (
    <div>
      <PageHeader title="Bookings" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <Card className="!rounded-[14px] mb-8 max-w-2xl shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">New Booking</h2>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <SectionLabel>Booking Setup</SectionLabel>
            <div className="space-y-3">
              <select required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className={INPUT_CLASSES}>
                <option value="">Select unit...</option>
                {bookableUnits.map((u) => (
                  <option key={u.id} value={u.id}>{u.name} ({formatCurrency(u.base_rate)}/night)</option>
                ))}
              </select>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Guest</label>
                <GuestPicker
                  selectedGuest={selectedNewGuest}
                  onSelect={(g) => { setSelectedNewGuest(g); setForm({ ...form, guest_id: g.id }); }}
                  onClear={() => { setSelectedNewGuest(null); setForm({ ...form, guest_id: "" }); }}
                  setError={setError}
                />
              </div>
            </div>
          </div>

          <div>
            <SectionLabel>Stay</SectionLabel>
            <div className="flex gap-3">
              <div className="w-full">
                <label className="block text-xs text-slate-500 mb-1">Check-in</label>
                <input required type="date" lang="en" value={form.check_in} onChange={(e) => setForm({ ...form, check_in: e.target.value })} className={INPUT_CLASSES} />
              </div>
              <div className="w-full">
                <label className="block text-xs text-slate-500 mb-1">Check-out</label>
                <input required type="date" lang="en" value={form.check_out} onChange={(e) => setForm({ ...form, check_out: e.target.value })} className={INPUT_CLASSES} />
              </div>
            </div>
          </div>

          <div>
            <SectionLabel>Guests</SectionLabel>
            <div className="flex gap-3">
              <div className="w-full">
                <label className="block text-xs text-slate-500 mb-1">Adults</label>
                <input required type="number" min={1} value={form.adults} onChange={(e) => setForm({ ...form, adults: e.target.value })} className={INPUT_CLASSES} />
              </div>
              <div className="w-full">
                <label className="block text-xs text-slate-500 mb-1">Children</label>
                <input required type="number" min={0} value={form.children} onChange={(e) => setForm({ ...form, children: e.target.value })} className={INPUT_CLASSES} />
              </div>
              <div className="w-full flex items-end pb-2.5">
                <label className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="checkbox" checked={form.extra_bed} onChange={(e) => setForm({ ...form, extra_bed: e.target.checked })} />
                  Extra Bed
                </label>
              </div>
            </div>
          </div>

          <div>
            <SectionLabel>Timing</SectionLabel>
            <div className="flex gap-3">
              <div className="w-full">
                <label className="block text-xs text-slate-500 mb-1">Arrival Time (optional)</label>
                <input placeholder="e.g. 14:00" value={form.arrival_time} onChange={(e) => setForm({ ...form, arrival_time: e.target.value })} className={INPUT_CLASSES} />
              </div>
              <div className="w-full">
                <label className="block text-xs text-slate-500 mb-1">Departure Time (optional)</label>
                <input placeholder="e.g. 11:00" value={form.departure_time} onChange={(e) => setForm({ ...form, departure_time: e.target.value })} className={INPUT_CLASSES} />
              </div>
            </div>
          </div>

          {checkingAvailability && <p className="text-xs text-slate-400">Checking availability...</p>}
          {!checkingAvailability && availability.checked && (
            <p className={`text-xs font-medium ${availability.available ? "text-emerald-600" : "text-red-600"}`}>
              {availability.available ? "✓ Unit is available for these dates" : "✗ Unit is already booked for these dates"}
            </p>
          )}

          <Button type="submit" disabled={availability.checked && !availability.available}>
            Confirm Booking
          </Button>
        </form>
      </Card>

      {detailsBooking && (
        <div ref={detailsRef} tabIndex={-1} className="outline-none">
        <Card className="!rounded-[14px] mb-8 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
              Reservation Details
              {detailsBooking.is_archived && <Badge tone="amber">Archived</Badge>}
            </h2>
            <button onClick={closeDetails} className="text-xs text-slate-400 hover:text-slate-600 underline">Close</button>
          </div>

          <form onSubmit={handleSaveEdit} className="mb-4 space-y-5">
            <div>
              <SectionLabel>Guest &amp; Apartment</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="sm:col-span-2">
                  <label className="block text-xs text-slate-500 mb-1 flex items-center gap-1.5">
                    Guest (Principal Guest)
                    {selectedEditGuest && (
                      <Badge tone={identityStatusMeta(selectedEditGuest.identity_status).tone}>
                        {identityStatusMeta(selectedEditGuest.identity_status).label}
                      </Badge>
                    )}
                  </label>
                  <GuestPicker
                    selectedGuest={selectedEditGuest}
                    onSelect={(g) => { setSelectedEditGuest(g); setEditForm({ ...editForm, guest_id: g.id }); }}
                    onClear={() => { setSelectedEditGuest(null); setEditForm({ ...editForm, guest_id: "" }); }}
                    setError={setError}
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Apartment</label>
                  <select value={editForm.unit_id} onChange={(e) => setEditForm({ ...editForm, unit_id: e.target.value })} className={INPUT_CLASSES}>
                    {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Rate / night</label>
                  <input type="number" placeholder="Rate/night" value={editForm.rate_applied} onChange={(e) => setEditForm({ ...editForm, rate_applied: e.target.value })} className={INPUT_CLASSES} />
                </div>
              </div>
            </div>

            <div>
              <SectionLabel>Stay Details</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Check-in</label>
                  <input type="date" lang="en" value={editForm.check_in} onChange={(e) => setEditForm({ ...editForm, check_in: e.target.value })} className={INPUT_CLASSES} />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Check-out</label>
                  <input type="date" lang="en" value={editForm.check_out} onChange={(e) => setEditForm({ ...editForm, check_out: e.target.value })} className={INPUT_CLASSES} />
                </div>
                {editForm.check_in && editForm.check_out && (
                  <p className="text-xs text-slate-500 pb-2.5">
                    <span className="font-semibold text-slate-800">{nightsBetween(editForm.check_in, editForm.check_out)}</span> night(s) ·{" "}
                    {formatDate(editForm.check_in)} → {formatDate(editForm.check_out)}
                  </p>
                )}
              </div>
            </div>

            <div>
              <SectionLabel>Guests &amp; Timing</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="flex gap-3">
                  <div className="w-full">
                    <label className="block text-xs text-slate-500 mb-1">Adults</label>
                    <input type="number" min={1} placeholder="Adults" value={editForm.adults} onChange={(e) => setEditForm({ ...editForm, adults: e.target.value })} className={INPUT_CLASSES} />
                  </div>
                  <div className="w-full">
                    <label className="block text-xs text-slate-500 mb-1">Children</label>
                    <input type="number" min={0} placeholder="Children" value={editForm.children} onChange={(e) => setEditForm({ ...editForm, children: e.target.value })} className={INPUT_CLASSES} />
                  </div>
                </div>
                <label className="flex items-center gap-2 text-sm text-slate-600 sm:self-center">
                  <input type="checkbox" checked={editForm.extra_bed} onChange={(e) => setEditForm({ ...editForm, extra_bed: e.target.checked })} />
                  Extra Bed
                </label>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Arrival Time</label>
                  <input placeholder="e.g. 14:00" value={editForm.arrival_time} onChange={(e) => setEditForm({ ...editForm, arrival_time: e.target.value })} className={INPUT_CLASSES} />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Departure Time</label>
                  <input placeholder="e.g. 11:00" value={editForm.departure_time} onChange={(e) => setEditForm({ ...editForm, departure_time: e.target.value })} className={INPUT_CLASSES} />
                </div>
              </div>
            </div>

            <div>
              <SectionLabel>Additional Guests</SectionLabel>
              <div className="space-y-2">
                {bookingGuests && bookingGuests.additional_guests.length === 0 && !showAddGuest && (
                  <p className="text-xs text-slate-400">No additional guests on this booking yet.</p>
                )}
                {bookingGuests?.additional_guests.map((ag) => (
                  <div key={ag.id} className="flex items-center justify-between gap-2 rounded-[10px] border border-slate-200 px-3 py-2 text-sm">
                    <div className="flex items-center gap-2 flex-wrap min-w-0">
                      <span className="font-medium text-slate-800 truncate">{ag.name}</span>
                      <span className="text-xs text-slate-400">{ag.phone}</span>
                      <Badge tone={identityStatusMeta(ag.identity_status).tone}>{identityStatusMeta(ag.identity_status).label}</Badge>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRemoveAdditionalGuest(ag.id)}
                      aria-label={`Remove ${ag.name} from this booking`}
                      className="shrink-0 text-xs text-red-500 hover:underline"
                    >
                      Remove
                    </button>
                  </div>
                ))}

                {showAddGuest ? (
                  <div className="rounded-[10px] border border-slate-200 p-3">
                    <GuestPicker
                      selectedGuest={null}
                      onSelect={handleAddAdditionalGuest}
                      onClear={() => setShowAddGuest(false)}
                      setError={setError}
                    />
                    <button type="button" onClick={() => setShowAddGuest(false)} className="mt-2 text-xs text-slate-500 hover:underline">
                      Cancel
                    </button>
                  </div>
                ) : (
                  <Button type="button" variant="secondary" onClick={() => setShowAddGuest(true)}>
                    + Add Guest
                  </Button>
                )}
              </div>
            </div>

            <div>
              <SectionLabel>Internal Notes</SectionLabel>
              <div className="space-y-3">
                <textarea
                  placeholder="Internal notes (staff-only, never shown to the guest)"
                  value={editForm.internal_notes}
                  onChange={(e) => setEditForm({ ...editForm, internal_notes: e.target.value })}
                  className={INPUT_CLASSES}
                  rows={2}
                />
                <textarea
                  placeholder="Special requests (what the guest asked for)"
                  value={editForm.special_requests}
                  onChange={(e) => setEditForm({ ...editForm, special_requests: e.target.value })}
                  className={INPUT_CLASSES}
                  rows={2}
                />
                <input placeholder="Reason for change (for audit log)" value={editForm.reason} onChange={(e) => setEditForm({ ...editForm, reason: e.target.value })} className={INPUT_CLASSES} />
              </div>
            </div>

            <div>
              <SectionLabel>Actions</SectionLabel>
              <div className="flex gap-3 flex-wrap">
                <Button type="submit">Save Changes</Button>
                {(detailsBooking.status === "confirmed" || detailsBooking.status === "inquiry") && (
                  <Button type="button" variant="warning" onClick={() => handleCancelBooking(detailsBooking.id)}>
                    Cancel Booking
                  </Button>
                )}
                {isAdmin && !detailsBooking.is_archived && (
                  <Button type="button" variant="secondary" onClick={() => { setArchivingBooking(true); setArchiveReason(""); }}>
                    Archive Booking
                  </Button>
                )}
                {isAdmin && detailsBooking.is_archived && (
                  <Button type="button" variant="secondary" onClick={() => handleRestoreBooking(detailsBooking.id)}>
                    Restore Booking
                  </Button>
                )}
                {isAdmin && (
                  <Button type="button" variant="danger" onClick={() => handleDelete(detailsBooking.id)}>
                    Delete Booking
                  </Button>
                )}
              </div>
            </div>
          </form>

          {archivingBooking && (
            <form onSubmit={handleArchiveBooking} className="mb-4 bg-amber-50 border border-amber-200 rounded-[10px] p-4 text-sm space-y-2">
              <h3 className="text-xs font-semibold text-amber-800 uppercase tracking-wide">Archive Booking</h3>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-700">
                <span>Guest: <span className="font-medium">{guestName(detailsBooking.guest_id)}</span></span>
                <span>Apartment: <span className="font-medium">{unitName(detailsBooking.unit_id)}</span></span>
                <span>Check-in: <span className="font-medium">{formatDate(detailsBooking.check_in)}</span></span>
                <span>Check-out: <span className="font-medium">{formatDate(detailsBooking.check_out)}</span></span>
                <span>Status: <span className="font-medium">{detailsBooking.status}</span></span>
                <span>Estimated total: <span className="font-medium">{formatCurrency(estimatedTotal(detailsBooking))}</span></span>
                <span>Advance received: <span className="font-medium">{formatCurrency(netPaid(payments))}</span></span>
              </div>
              <p className="text-xs text-amber-700">
                This removes the booking from the active list. Payments, invoices, GST records, and audit history are all preserved, and it can be restored at any time.
              </p>
              <input
                placeholder="Reason (optional)"
                value={archiveReason}
                onChange={(e) => setArchiveReason(e.target.value)}
                className="w-full rounded-[10px] border border-amber-300 px-3 py-2 text-xs"
              />
              <div className="flex gap-2">
                <Button type="submit" variant="danger">Archive Booking</Button>
                <button type="button" onClick={() => setArchivingBooking(false)} className="text-slate-500 hover:underline text-xs">Cancel</button>
              </div>
            </form>
          )}

          <div className="border-t border-slate-100 pt-4 mb-4">
            <SectionLabel>Payment Summary</SectionLabel>
            {(() => {
              const total = estimatedTotal(detailsBooking);
              const paid = netPaid(payments);
              const balance = total - paid;
              const status = paymentStatusOf(total, paid);
              return (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                  <div className="bg-slate-50 rounded-[10px] px-3 py-2.5">
                    <p className="text-[11px] text-slate-500">Total</p>
                    <p className="text-sm font-semibold text-slate-900">{formatCurrency(total)}</p>
                  </div>
                  <div className="bg-slate-50 rounded-[10px] px-3 py-2.5">
                    <p className="text-[11px] text-slate-500">Paid</p>
                    <p className="text-sm font-semibold text-slate-900">{formatCurrency(paid)}</p>
                  </div>
                  <div className="bg-slate-50 rounded-[10px] px-3 py-2.5">
                    <p className="text-[11px] text-slate-500">Balance</p>
                    <p className={`text-sm font-semibold ${balance > 0 ? "text-amber-700" : "text-slate-900"}`}>{formatCurrency(Math.max(0, balance))}</p>
                  </div>
                  <div className="bg-slate-50 rounded-[10px] px-3 py-2.5">
                    <p className="text-[11px] text-slate-500">Payment Status</p>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                </div>
              );
            })()}
            <h3 className="text-xs font-semibold text-slate-700 mb-3 uppercase tracking-wide">Payment History</h3>

            {isAdmin && (
              <form onSubmit={handleAddPayment} className="flex flex-wrap gap-2 mb-3">
                <input required type="number" placeholder="Amount (₹)" value={paymentForm.amount} onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })} className="w-28 rounded-[10px] border border-slate-300 px-2 py-1.5 text-xs" />
                <select value={paymentForm.mode} onChange={(e) => setPaymentForm({ ...paymentForm, mode: e.target.value })} className="rounded-[10px] border border-slate-300 px-2 py-1.5 text-xs">
                  <option value="upi">UPI</option>
                  <option value="card">Card</option>
                  <option value="bank_transfer">Bank Transfer</option>
                  <option value="cash">Cash</option>
                </select>
                <input placeholder="Reference no (optional)" value={paymentForm.reference_no} onChange={(e) => setPaymentForm({ ...paymentForm, reference_no: e.target.value })} className="flex-1 min-w-[120px] rounded-[10px] border border-slate-300 px-2 py-1.5 text-xs" />
                <Button type="submit">Record Payment</Button>
              </form>
            )}

            {payments.length === 0 ? (
              <p className="text-xs text-slate-400">No payments recorded yet.</p>
            ) : (
              <div className="space-y-2">
                {payments.map((p) => {
                  const isRefund = p.amount < 0;
                  const alreadyRefunded = refundedSoFar(payments, p.id);
                  const refundable = !isRefund && p.amount - alreadyRefunded > 0;
                  return (
                    <div key={p.id} className="border border-slate-100 rounded-lg px-3 py-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className={`font-medium ${isRefund ? "text-red-600" : "text-slate-800"}`}>
                          {isRefund ? "Refund" : "Payment"}: {formatCurrency(Math.abs(p.amount))} ({p.mode})
                        </span>
                        <span className="text-slate-400">{formatDate(p.paid_at.slice(0, 10))}</span>
                      </div>
                      {p.reference_no && <p className="text-slate-500 mt-0.5">Ref: {p.reference_no}</p>}
                      {p.refund_reason && <p className="text-slate-500 mt-0.5">Reason: {p.refund_reason}</p>}
                      {isAdmin && refundable && refundingPaymentId !== p.id && (
                        <button onClick={() => { setRefundingPaymentId(p.id); setRefundForm({ refund_amount: "", reason: "" }); }} className="text-red-500 hover:underline mt-1">
                          Refund this payment
                        </button>
                      )}
                      {refundingPaymentId === p.id && (
                        <form onSubmit={handleRefund} className="mt-2 flex flex-wrap gap-2 items-center bg-red-50 rounded-lg p-2">
                          <input
                            required type="number" placeholder="Refund amount"
                            value={refundForm.refund_amount}
                            onChange={(e) => setRefundForm({ ...refundForm, refund_amount: e.target.value })}
                            className="w-28 rounded-md border border-slate-300 px-2 py-1 text-xs"
                          />
                          <input
                            required placeholder="Reason" value={refundForm.reason}
                            onChange={(e) => setRefundForm({ ...refundForm, reason: e.target.value })}
                            className="flex-1 min-w-[100px] rounded-md border border-slate-300 px-2 py-1 text-xs"
                          />
                          <Button type="submit" variant="danger">Confirm Refund</Button>
                          <button type="button" onClick={() => setRefundingPaymentId(null)} className="text-slate-500 hover:underline">Cancel</button>
                        </form>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="border-t border-slate-100 pt-4">
            <h3 className="text-xs font-semibold text-slate-700 mb-3 uppercase tracking-wide">Attachments (ID proof, agreements, etc.)</h3>
            <div className="flex gap-2 mb-3">
              <input type="file" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} className="text-xs flex-1" />
              <Button onClick={handleUpload} disabled={!uploadFile}>Upload</Button>
            </div>
            {attachments.length === 0 ? (
              <p className="text-xs text-slate-400">No attachments yet.</p>
            ) : (
              <div className="space-y-1.5">
                {attachments.map((a) => (
                  <div key={a.id} className="flex items-center justify-between text-xs border border-slate-100 rounded-lg px-3 py-1.5">
                    <span className="text-slate-700">{a.filename}</span>
                    <div className="flex gap-3">
                      <button onClick={() => handleDownloadAttachment(a.id, a.filename)} className="text-slate-500 hover:text-slate-800">Download</button>
                      <button onClick={() => handleDeleteAttachment(a.id)} className="text-red-500 hover:text-red-700">Remove</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="mt-6 pt-4 border-t border-slate-100">
            <button
              onClick={() => setShowTimeline(!showTimeline)}
              className="text-xs font-semibold text-slate-700 uppercase tracking-wide hover:text-slate-900"
            >
              {showTimeline ? "Hide" : "Show"} Timeline ({timeline.length} events)
            </button>
            {showTimeline && (
              <div className="mt-3 space-y-2">
                {timeline.length === 0 ? (
                  <p className="text-xs text-slate-400">No events recorded yet.</p>
                ) : (
                  timeline.map((event, i) => (
                    <div key={i} className="text-xs border-l-2 border-slate-200 pl-3 py-1">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-slate-800">{event.action}</span>
                        <span className="text-slate-400">{formatDateTime(event.created_at)}</span>
                      </div>
                      <p className="text-slate-500">By {event.user_name}</p>
                      {event.details && <p className="text-slate-500 mt-0.5">{event.details}</p>}
                      {event.previous_value && <p className="text-slate-400 mt-0.5">Before: {event.previous_value}</p>}
                      {event.new_value && <p className="text-slate-400">After: {event.new_value}</p>}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </Card>
        </div>
      )}

      <Card className="!rounded-[14px] shadow-sm">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-slate-900">All Bookings</h2>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={handleManualBookingsRefresh} disabled={refreshingBookings}>
              {refreshingBookings ? "Refreshing..." : "Refresh"}
            </Button>
            <Button variant="secondary" onClick={exportBookingsCsv}>Export CSV</Button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 mb-4">
          <input
            placeholder="Search by guest or unit..."
            value={bookingSearch}
            onChange={(e) => setBookingSearch(e.target.value)}
            className="flex-1 min-w-[180px] rounded-[10px] border border-slate-300 px-3 py-1.5 text-xs"
          />
          <select value={bookingStatusFilter} onChange={(e) => setBookingStatusFilter(e.target.value)} className="rounded-[10px] border border-slate-300 px-2 py-1.5 text-xs">
            <option value="">All statuses</option>
            <option value="confirmed">Confirmed</option>
            <option value="checked_in">Checked In</option>
            <option value="checked_out">Checked Out</option>
            <option value="cancelled">Cancelled</option>
            <option value="no_show">No Show</option>
          </select>
          {isAdmin && (
            <label className="flex items-center gap-1.5 text-xs text-slate-600">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              Show archived
            </label>
          )}
        </div>
        {!loadingBookings && filteredBookings.length === 0 ? (
          <EmptyState message={bookings.length === 0 ? "No bookings yet. Create one using the form above." : "No bookings match these filters."} />
        ) : (
          <div className="overflow-x-auto">
            <Table headers={["Unit", "Guest", "Check-in", "Check-out", "Rate/night", "Status", "Action"]}>
              {loadingBookings ? (
                <SkeletonRows columns={7} />
              ) : (
                filteredBookings.map((b) => (
                  <tr key={b.id}>
                    <td className="px-4 py-2.5 font-medium text-slate-800">{unitName(b.unit_id)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{guestName(b.guest_id)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{formatDate(b.check_in)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{formatDate(b.check_out)}</td>
                    <td className="px-4 py-2.5 text-slate-500">{formatCurrency(b.rate_applied)}</td>
                    <td className="px-4 py-2.5 space-x-1">
                      <Badge tone={b.status === "confirmed" ? "green" : b.status === "checked_in" ? "amber" : "slate"}>{b.status}</Badge>
                      {b.is_archived && <Badge tone="amber">Archived</Badge>}
                    </td>
                    <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                      {!b.is_archived && b.status === "confirmed" && (
                        <Button variant="secondary" onClick={() => transitionStatus(b.id, "checked_in")}>Check In</Button>
                      )}
                      {!b.is_archived && b.status === "checked_in" && (
                        <Button variant="secondary" onClick={() => transitionStatus(b.id, "checked_out")}>Check Out</Button>
                      )}
                      {!b.is_archived && (b.status === "confirmed" || b.status === "inquiry") && (
                        <Button variant="danger" onClick={() => handleCancelBooking(b.id)}>Cancel</Button>
                      )}
                      {b.is_archived && isAdmin && (
                        <Button variant="secondary" onClick={() => handleRestoreBooking(b.id)}>Restore</Button>
                      )}
                      <Button variant="secondary" onClick={() => openDetails(b)}>Edit</Button>
                      <Button variant="secondary" onClick={() => openDetails(b)}>Details</Button>
                    </td>
                  </tr>
                ))
              )}
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}
