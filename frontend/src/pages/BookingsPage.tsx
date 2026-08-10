import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type BookingAttachmentOut, type BookingOut, type GuestOut, type UnitOut, type PaymentOut, type PropertyOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, SkeletonRows, EmptyState } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import GuestPicker from "../components/GuestPicker";

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

  const [detailsBookingId, setDetailsBookingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ guest_id: "", unit_id: "", check_in: "", check_out: "", rate_applied: "", reason: "", adults: "1", children: "0", extra_bed: false, arrival_time: "", departure_time: "" });
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

  async function loadAll() {
    try {
      const [b, u, g, p] = await Promise.all([api.get("/bookings"), api.get("/units"), api.get("/guests"), api.get("/properties")]);
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

  useEffect(() => {
    loadAll();
  }, []);

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
    if (!window.confirm("Cancel this booking? The booking record, payments, and any invoice are preserved — this only changes its status. Refunds (if needed) are handled separately from the payment's own Refund action.")) return;
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
    });
    setSelectedEditGuest(guests.find((g) => g.id === b.guest_id) ?? null);
    api.get(`/bookings/${b.id}/attachments`).then((r) => setAttachments(r.data)).catch(() => setAttachments([]));
    api.get(`/bookings/${b.id}/timeline`).then((r) => setTimeline(r.data.events)).catch(() => setTimeline([]));
    api.get(`/payments/${b.id}`).then((r) => setPayments(r.data)).catch(() => setPayments([]));
    setShowTimeline(false);
    setRefundingPaymentId(null);
  }

  function closeDetails() {
    setDetailsBookingId(null);
    setAttachments([]);
    setTimeline([]);
    setUploadFile(null);
    setPayments([]);
    setRefundingPaymentId(null);
    setSelectedEditGuest(null);
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
      });
      setSuccess("Booking updated.");
      closeDetails();
      loadAll();
    } catch (err) {
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

      <Card className="mb-8 max-w-xl">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">New Booking</h2>
        <form onSubmit={handleCreate} className="space-y-3">
          <select required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">Select unit...</option>
            {bookableUnits.map((u) => (
              <option key={u.id} value={u.id}>{u.name} (₹{u.base_rate}/night)</option>
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

          <div className="flex gap-3">
            <div className="w-full">
              <label className="block text-xs text-slate-500 mb-1">Check-in</label>
              <input required type="date" value={form.check_in} onChange={(e) => setForm({ ...form, check_in: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div className="w-full">
              <label className="block text-xs text-slate-500 mb-1">Check-out</label>
              <input required type="date" value={form.check_out} onChange={(e) => setForm({ ...form, check_out: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
          </div>

          <div className="flex gap-3">
            <div className="w-full">
              <label className="block text-xs text-slate-500 mb-1">Adults</label>
              <input required type="number" min={1} value={form.adults} onChange={(e) => setForm({ ...form, adults: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div className="w-full">
              <label className="block text-xs text-slate-500 mb-1">Children</label>
              <input required type="number" min={0} value={form.children} onChange={(e) => setForm({ ...form, children: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div className="w-full flex items-end pb-2">
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input type="checkbox" checked={form.extra_bed} onChange={(e) => setForm({ ...form, extra_bed: e.target.checked })} />
                Extra Bed
              </label>
            </div>
          </div>

          <div className="flex gap-3">
            <div className="w-full">
              <label className="block text-xs text-slate-500 mb-1">Arrival Time (optional)</label>
              <input placeholder="e.g. 14:00" value={form.arrival_time} onChange={(e) => setForm({ ...form, arrival_time: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div className="w-full">
              <label className="block text-xs text-slate-500 mb-1">Departure Time (optional)</label>
              <input placeholder="e.g. 11:00" value={form.departure_time} onChange={(e) => setForm({ ...form, departure_time: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
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
        <Card className="mb-8">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-slate-900">Reservation Details</h2>
            <button onClick={closeDetails} className="text-xs text-slate-400 hover:text-slate-600 underline">Close</button>
          </div>

          <form onSubmit={handleSaveEdit} className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
            <div className="sm:col-span-2">
              <label className="block text-xs text-slate-500 mb-1">Guest</label>
              <GuestPicker
                selectedGuest={selectedEditGuest}
                onSelect={(g) => { setSelectedEditGuest(g); setEditForm({ ...editForm, guest_id: g.id }); }}
                onClear={() => { setSelectedEditGuest(null); setEditForm({ ...editForm, guest_id: "" }); }}
                setError={setError}
              />
            </div>
            <select value={editForm.unit_id} onChange={(e) => setEditForm({ ...editForm, unit_id: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
            <input type="date" value={editForm.check_in} onChange={(e) => setEditForm({ ...editForm, check_in: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input type="date" value={editForm.check_out} onChange={(e) => setEditForm({ ...editForm, check_out: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input type="number" placeholder="Rate/night" value={editForm.rate_applied} onChange={(e) => setEditForm({ ...editForm, rate_applied: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input type="number" min={1} placeholder="Adults" value={editForm.adults} onChange={(e) => setEditForm({ ...editForm, adults: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input type="number" min={0} placeholder="Children" value={editForm.children} onChange={(e) => setEditForm({ ...editForm, children: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={editForm.extra_bed} onChange={(e) => setEditForm({ ...editForm, extra_bed: e.target.checked })} />
              Extra Bed
            </label>
            <input placeholder="Arrival time (e.g. 14:00)" value={editForm.arrival_time} onChange={(e) => setEditForm({ ...editForm, arrival_time: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input placeholder="Departure time (e.g. 11:00)" value={editForm.departure_time} onChange={(e) => setEditForm({ ...editForm, departure_time: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input placeholder="Reason for change (for audit log)" value={editForm.reason} onChange={(e) => setEditForm({ ...editForm, reason: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <div className="sm:col-span-2 flex gap-3">
              <Button type="submit">Save Changes</Button>
              {(detailsBooking.status === "confirmed" || detailsBooking.status === "inquiry") && (
                <Button type="button" variant="danger" onClick={() => handleCancelBooking(detailsBooking.id)}>
                  Cancel Booking
                </Button>
              )}
              {isAdmin && (
                <Button type="button" variant="danger" onClick={() => handleDelete(detailsBooking.id)}>
                  Delete Booking
                </Button>
              )}
            </div>
          </form>

          <div className="border-t border-slate-100 pt-4 mb-4">
            <h3 className="text-xs font-semibold text-slate-700 mb-3 uppercase tracking-wide">Payments</h3>
            <div className="flex items-center justify-between mb-3 bg-slate-50 rounded-lg px-3 py-2">
              <span className="text-xs text-slate-500">Net Paid</span>
              <span className="text-sm font-semibold text-slate-900">₹{netPaid(payments).toLocaleString()}</span>
            </div>

            {isAdmin && (
              <form onSubmit={handleAddPayment} className="flex flex-wrap gap-2 mb-3">
                <input required type="number" placeholder="Amount (₹)" value={paymentForm.amount} onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })} className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
                <select value={paymentForm.mode} onChange={(e) => setPaymentForm({ ...paymentForm, mode: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                  <option value="upi">UPI</option>
                  <option value="card">Card</option>
                  <option value="bank_transfer">Bank Transfer</option>
                  <option value="cash">Cash</option>
                </select>
                <input placeholder="Reference no (optional)" value={paymentForm.reference_no} onChange={(e) => setPaymentForm({ ...paymentForm, reference_no: e.target.value })} className="flex-1 min-w-[120px] rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
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
                          {isRefund ? "Refund" : "Payment"}: ₹{Math.abs(p.amount).toLocaleString()} ({p.mode})
                        </span>
                        <span className="text-slate-400">{new Date(p.paid_at).toLocaleDateString()}</span>
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
                        <span className="text-slate-400">{new Date(event.created_at).toLocaleString()}</span>
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
      )}

      <Card>
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-slate-900">All Bookings</h2>
          <Button variant="secondary" onClick={exportBookingsCsv}>Export CSV</Button>
        </div>
        <div className="flex flex-wrap gap-2 mb-4">
          <input
            placeholder="Search by guest or unit..."
            value={bookingSearch}
            onChange={(e) => setBookingSearch(e.target.value)}
            className="flex-1 min-w-[180px] rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
          />
          <select value={bookingStatusFilter} onChange={(e) => setBookingStatusFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
            <option value="">All statuses</option>
            <option value="confirmed">Confirmed</option>
            <option value="checked_in">Checked In</option>
            <option value="checked_out">Checked Out</option>
            <option value="cancelled">Cancelled</option>
            <option value="no_show">No Show</option>
          </select>
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
                    <td className="px-4 py-2.5 text-slate-500">{b.check_in}</td>
                    <td className="px-4 py-2.5 text-slate-500">{b.check_out}</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{b.rate_applied}</td>
                    <td className="px-4 py-2.5">
                      <Badge tone={b.status === "confirmed" ? "green" : b.status === "checked_in" ? "amber" : "slate"}>{b.status}</Badge>
                    </td>
                    <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                      {b.status === "confirmed" && (
                        <Button variant="secondary" onClick={() => transitionStatus(b.id, "checked_in")}>Check In</Button>
                      )}
                      {b.status === "checked_in" && (
                        <Button variant="secondary" onClick={() => transitionStatus(b.id, "checked_out")}>Check Out</Button>
                      )}
                      {(b.status === "confirmed" || b.status === "inquiry") && (
                        <Button variant="danger" onClick={() => handleCancelBooking(b.id)}>Cancel</Button>
                      )}
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
