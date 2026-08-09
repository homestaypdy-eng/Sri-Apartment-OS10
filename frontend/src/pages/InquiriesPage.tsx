import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type InquiryOut, type UnitOut, type PriceQuoteOut, type InquiryAttachmentOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge } from "../components/ui";
import QuotationPopup from "../components/QuotationPopup";

const emptyForm = {
  client_name: "",
  client_phone: "",
  client_email: "",
  client_address: "",
  unit_id: "",
  flat_room_label: "",
  num_guests: "2",
  check_in_date: "",
  check_out_date: "",
  rate_per_night: "",
  extra_charges: "0",
  discount: "0",
  advance_amount: "0",
  source: "phone",
  priority: "medium",
};

const INQUIRY_SOURCES = ["walk_in", "phone", "whatsapp", "ota", "website"];
const INQUIRY_PRIORITIES = ["low", "medium", "high", "urgent"];

const LEAD_TONES: Record<string, "slate" | "green" | "red" | "amber"> = {
  new: "slate",
  contacted: "amber",
  follow_up: "amber",
  converted: "green",
  lost: "red",
};

export default function InquiriesPage() {
  const [inquiries, setInquiries] = useState<InquiryOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [followUpsDue, setFollowUpsDue] = useState<InquiryOut[]>([]);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [activeQuotation, setActiveQuotation] = useState<InquiryOut | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [converting, setConverting] = useState<string | null>(null);
  const [sendingWhatsapp, setSendingWhatsapp] = useState<string | null>(null);
  const [sendingEmail, setSendingEmail] = useState<string | null>(null);
  const [priceQuote, setPriceQuote] = useState<PriceQuoteOut | null>(null);
  const [loadingQuote, setLoadingQuote] = useState(false);
  const [manualOverride, setManualOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [inquirySearch, setInquirySearch] = useState("");
  const [detailInquiry, setDetailInquiry] = useState<InquiryOut | null>(null);
  const [timeline, setTimeline] = useState<{ action: string; user_name: string; details: string | null; created_at: string }[]>([]);
  const [attachments, setAttachments] = useState<InquiryAttachmentOut[]>([]);
  const [attachFile, setAttachFile] = useState<File | null>(null);

  async function loadAll() {
    try {
      const [i, u, f] = await Promise.all([
        api.get("/inquiries"),
        api.get("/units"),
        api.get("/inquiries/follow-ups/due"),
      ]);
      setInquiries(i.data);
      setUnits(u.data);
      setFollowUpsDue(f.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  function handleUnitSelect(unitId: string) {
    const unit = units.find((u) => u.id === unitId);
    setForm({
      ...form,
      unit_id: unitId,
      flat_room_label: unit ? unit.name : form.flat_room_label,
    });
    setManualOverride(false);
    setOverrideReason("");
  }

  // Live-fetch the auto-calculated price whenever a linked unit + both dates
  // are set and the user hasn't switched to manual override — this is what
  // powers the price preview shown below the date fields.
  useEffect(() => {
    if (!form.unit_id || !form.check_in_date || !form.check_out_date || manualOverride) {
      setPriceQuote(null);
      return;
    }
    if (form.check_out_date <= form.check_in_date) {
      setPriceQuote(null);
      return;
    }
    setLoadingQuote(true);
    const timeout = setTimeout(async () => {
      try {
        const { data } = await api.get(`/units/${form.unit_id}/price-quote`, {
          params: { check_in: form.check_in_date, check_out: form.check_out_date },
        });
        setPriceQuote(data);
      } catch {
        setPriceQuote(null);
      } finally {
        setLoadingQuote(false);
      }
    }, 300);
    return () => clearTimeout(timeout);
  }, [form.unit_id, form.check_in_date, form.check_out_date, manualOverride]);

  function handleStartEdit(inq: InquiryOut) {
    setForm({
      client_name: inq.client_name,
      client_phone: inq.client_phone,
      client_email: inq.client_email || "",
      client_address: inq.client_address || "",
      unit_id: inq.unit_id || "",
      flat_room_label: inq.flat_room_label,
      num_guests: String(inq.num_guests),
      check_in_date: inq.check_in_date,
      check_out_date: inq.check_out_date,
      rate_per_night: String(inq.rate_per_night),
      extra_charges: String(inq.extra_charges),
      discount: String(inq.discount),
      advance_amount: String(inq.advance_amount),
      source: inq.source,
      priority: inq.priority,
    });
    setEditingId(inq.id);
    setManualOverride(inq.pricing_source === "manual");
    setOverrideReason("");
    setPriceQuote(null);
    setError(null);
    setSuccess(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function handleCancelEdit() {
    setEditingId(null);
    setForm(emptyForm);
    setManualOverride(false);
    setOverrideReason("");
    setPriceQuote(null);
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setSubmitting(true);
    try {
      const useManualRate = manualOverride || !form.unit_id;
      const sharedPayload = {
        client_name: form.client_name,
        client_phone: form.client_phone,
        client_email: form.client_email || null,
        client_address: form.client_address || null,
        unit_id: form.unit_id || null,
        flat_room_label: form.flat_room_label,
        num_guests: Number(form.num_guests),
        check_in_date: form.check_in_date,
        check_out_date: form.check_out_date,
        rate_per_night: useManualRate ? Number(form.rate_per_night) : undefined,
        override_reason: useManualRate && form.unit_id ? overrideReason : undefined,
        extra_charges: Number(form.extra_charges),
        discount: Number(form.discount),
      };

      if (editingId) {
        const { data } = await api.patch(`/inquiries/${editingId}`, {
          ...sharedPayload,
          advance_amount: Number(form.advance_amount),
          priority: form.priority,
        });
        setSuccess(`Quotation ${data.offer_no} updated.`);
        setActiveQuotation((prev) => (prev?.id === data.id ? data : prev));
        handleCancelEdit();
      } else {
        const { data } = await api.post("/inquiries", { ...sharedPayload, advance_amount: Number(form.advance_amount), source: form.source, priority: form.priority });
        setSuccess(`Quotation ${data.offer_no} created.`);
        setForm(emptyForm);
        setManualOverride(false);
        setOverrideReason("");
        setPriceQuote(null);
        setActiveQuotation(data);
      }
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDownloadPdf() {
    if (!activeQuotation) return;
    setDownloading(true);
    setError(null);
    try {
      const res = await api.get(`/inquiries/${activeQuotation.id}/pdf`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${activeQuotation.offer_no.replace(/\//g, "_")}_quotation.pdf`;
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  async function handleConvertToBooking(inquiry: InquiryOut) {
    setError(null);
    setSuccess(null);
    setConverting(inquiry.id);
    try {
      const { data } = await api.post(`/inquiries/${inquiry.id}/convert-to-booking`, {});
      setSuccess(
        `Converted to booking successfully${data.guest_reused ? " (matched an existing guest by phone)" : ""}. ` +
          `Booking is now confirmed — view it in the Bookings tab.`
      );
      if (activeQuotation?.id === inquiry.id) {
        setActiveQuotation(data.inquiry);
      }
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setConverting(null);
    }
  }

  async function handleSendWhatsApp(inquiry: InquiryOut) {
    setError(null);
    setSuccess(null);
    setSendingWhatsapp(inquiry.id);
    try {
      const { data } = await api.post(`/inquiries/${inquiry.id}/send/whatsapp`, {});
      window.open(data.whatsapp_url, "_blank");
      setSuccess(
        "WhatsApp opened with a prefilled message. Note: WhatsApp doesn't support auto-attaching files via this " +
          "link — download the PDF and attach it manually in the chat that just opened."
      );
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSendingWhatsapp(null);
    }
  }

  async function handleSendEmail(inquiry: InquiryOut) {
    setError(null);
    setSuccess(null);
    setSendingEmail(inquiry.id);
    try {
      const { data } = await api.post(`/inquiries/${inquiry.id}/send/email`, {});
      setSuccess(`Quotation emailed to ${data.to_email}.`);
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSendingEmail(null);
    }
  }

  async function handleQuickFollowUp(inquiry: InquiryOut, days: number) {
    setError(null);
    const nextDate = new Date();
    nextDate.setDate(nextDate.getDate() + days);
    try {
      await api.patch(`/inquiries/${inquiry.id}`, {
        lead_status: "follow_up",
        next_follow_up_date: nextDate.toISOString().slice(0, 10),
      });
      setSuccess(`Follow-up scheduled for ${nextDate.toISOString().slice(0, 10)}.`);
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function openDetail(inq: InquiryOut) {
    setDetailInquiry(inq);
    setError(null);
    try {
      const [tl, at] = await Promise.all([
        api.get(`/inquiries/${inq.id}/timeline`),
        api.get(`/inquiries/${inq.id}/attachments`),
      ]);
      setTimeline(tl.data.events);
      setAttachments(at.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function closeDetail() {
    setDetailInquiry(null);
    setTimeline([]);
    setAttachments([]);
  }

  async function handleUploadAttachment() {
    if (!detailInquiry || !attachFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", attachFile);
      await api.post(`/inquiries/${detailInquiry.id}/attachments`, formData, { headers: { "Content-Type": "multipart/form-data" } });
      setSuccess("Attachment uploaded.");
      setAttachFile(null);
      const { data } = await api.get(`/inquiries/${detailInquiry.id}/attachments`);
      setAttachments(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadAttachment(attachmentId: string, filename: string) {
    if (!detailInquiry) return;
    try {
      const res = await api.get(`/inquiries/${detailInquiry.id}/attachments/${attachmentId}/download`, { responseType: "blob" });
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

  async function handleDeleteAttachment(attachmentId: string) {
    if (!detailInquiry) return;
    try {
      await api.delete(`/inquiries/${detailInquiry.id}/attachments/${attachmentId}`);
      setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader title="Inquiries & Pre-Booking Quotations" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      {followUpsDue.length > 0 && (
        <Card className="mb-6 bg-amber-50 border-amber-200">
          <h2 className="text-sm font-semibold text-amber-900 mb-2">
            Follow-ups Due ({followUpsDue.length})
          </h2>
          <div className="space-y-1">
            {followUpsDue.map((inq) => (
              <div key={inq.id} className="flex items-center justify-between text-sm">
                <span className="text-amber-800">
                  <button className="font-medium hover:underline" onClick={() => setActiveQuotation(inq)}>
                    {inq.client_name}
                  </button>{" "}
                  ({inq.offer_no}) — due {inq.next_follow_up_date}
                  {inq.follow_up_notes && <span className="text-amber-600"> · {inq.follow_up_notes}</span>}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="mb-8 max-w-2xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">{editingId ? "Edit Inquiry" : "New Inquiry"}</h2>
          {editingId && (
            <button type="button" onClick={handleCancelEdit} className="text-xs text-slate-400 hover:text-slate-600 underline">
              Cancel edit
            </button>
          )}
        </div>
        <form onSubmit={handleCreate} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <input
              required
              placeholder="Client name"
              value={form.client_name}
              onChange={(e) => setForm({ ...form, client_name: e.target.value })}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <input
              required
              placeholder="Phone"
              value={form.client_phone}
              onChange={(e) => setForm({ ...form, client_phone: e.target.value })}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <input
              type="email"
              placeholder="Email (optional)"
              value={form.client_email}
              onChange={(e) => setForm({ ...form, client_email: e.target.value })}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <input
              placeholder="Address (optional)"
              value={form.client_address}
              onChange={(e) => setForm({ ...form, client_address: e.target.value })}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>

          <select
            value={form.unit_id}
            onChange={(e) => handleUnitSelect(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          >
            <option value="">Link to an existing unit (optional)...</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} (₹{u.base_rate}/night)
              </option>
            ))}
          </select>

          <input
            required
            placeholder="Flat / Room label (e.g. 2BHK - First Floor)"
            value={form.flat_room_label}
            onChange={(e) => setForm({ ...form, flat_room_label: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Check-in</label>
              <input
                required
                type="date"
                value={form.check_in_date}
                onChange={(e) => setForm({ ...form, check_in_date: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Check-out</label>
              <input
                required
                type="date"
                value={form.check_out_date}
                onChange={(e) => setForm({ ...form, check_out_date: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Guests</label>
              <input
                type="number"
                min={1}
                value={form.num_guests}
                onChange={(e) => setForm({ ...form, num_guests: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
          </div>

          {form.unit_id && !manualOverride ? (
            <div className="border border-slate-200 rounded-lg p-3 bg-slate-50">
              {loadingQuote && <p className="text-xs text-slate-400">Calculating price...</p>}
              {!loadingQuote && priceQuote && (
                <div className="space-y-1 text-xs">
                  <p className="font-medium text-slate-700 mb-2">Auto-calculated price ({priceQuote.nights} nights)</p>
                  {priceQuote.daily_breakdown.map((d) => (
                    <div key={d.the_date} className="flex justify-between text-slate-500">
                      <span>
                        {d.the_date} {d.source === "seasonal" ? `(${d.seasonal_name})` : d.source === "weekend" ? "(weekend)" : ""}
                      </span>
                      <span>₹{d.rate}</span>
                    </div>
                  ))}
                  <div className="flex justify-between text-slate-500 pt-1 border-t border-slate-200 mt-1">
                    <span>Subtotal</span>
                    <span>₹{priceQuote.subtotal_before_discount}</span>
                  </div>
                  {priceQuote.long_stay_discount_percent > 0 && (
                    <div className="flex justify-between text-emerald-600">
                      <span>Long-stay discount ({priceQuote.long_stay_discount_percent}%)</span>
                      <span>−₹{priceQuote.long_stay_discount_amount}</span>
                    </div>
                  )}
                  <div className="flex justify-between font-semibold text-slate-800 pt-1 border-t border-slate-200 mt-1">
                    <span>Accommodation total</span>
                    <span>₹{priceQuote.subtotal_after_discount}</span>
                  </div>
                </div>
              )}
              {!loadingQuote && !priceQuote && <p className="text-xs text-slate-400">Select check-in and check-out dates to see the price.</p>}
              <button
                type="button"
                onClick={() => setManualOverride(true)}
                className="text-xs text-slate-500 hover:text-slate-700 underline mt-2"
              >
                Override this rate manually
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Rate / night (₹) — manual</label>
                <input
                  required
                  type="number"
                  value={form.rate_per_night}
                  onChange={(e) => setForm({ ...form, rate_per_night: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>
              {form.unit_id && (
                <>
                  <input
                    placeholder="Reason for overriding the calculated rate (logged for audit)"
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setManualOverride(false);
                      setOverrideReason("");
                    }}
                    className="text-xs text-slate-500 hover:text-slate-700 underline"
                  >
                    Use auto-calculated price instead
                  </button>
                </>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Extra charges (₹)</label>
              <input
                type="number"
                value={form.extra_charges}
                onChange={(e) => setForm({ ...form, extra_charges: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Discount (₹)</label>
              <input
                type="number"
                value={form.discount}
                onChange={(e) => setForm({ ...form, discount: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            {!editingId && (
              <div>
                <label className="block text-xs text-slate-500 mb-1">Source</label>
                <select
                  value={form.source}
                  onChange={(e) => setForm({ ...form, source: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  {INQUIRY_SOURCES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                </select>
              </div>
            )}
            <div>
              <label className="block text-xs text-slate-500 mb-1">Priority</label>
              <select
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                {INQUIRY_PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>

          <div className="w-1/3">
            <label className="block text-xs text-slate-500 mb-1">Advance amount (₹)</label>
            <input
              type="number"
              value={form.advance_amount}
              onChange={(e) => setForm({ ...form, advance_amount: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>

          <Button type="submit" disabled={submitting}>
            {submitting ? "Saving..." : editingId ? "Save Changes" : "Generate Quotation"}
          </Button>
        </form>
      </Card>

      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">All Inquiries</h2>
          <input
            placeholder="Search client name or phone..."
            value={inquirySearch}
            onChange={(e) => setInquirySearch(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs min-w-[220px]"
          />
        </div>
        <Table headers={["Offer No.", "Client", "Stay", "Grand Total", "Source / Priority", "Lead Status", "Doc Status", "Actions"]}>
          {inquiries
            .filter((inq) => !inquirySearch || inq.client_name.toLowerCase().includes(inquirySearch.toLowerCase()) || inq.client_phone.includes(inquirySearch))
            .map((inq) => (
            <tr key={inq.id}>
              <td className="px-4 py-2.5 font-medium text-slate-800">
                {inq.offer_no}
                {inq.is_expired && (
                  <span className="ml-1">
                    <Badge tone="red">expired</Badge>
                  </span>
                )}
              </td>
              <td className="px-4 py-2.5 text-slate-500">{inq.client_name}</td>
              <td className="px-4 py-2.5 text-slate-500">
                {inq.check_in_date} → {inq.check_out_date}
              </td>
              <td className="px-4 py-2.5 text-slate-500">
                ₹{inq.grand_total.toLocaleString()}{" "}
                <span className="text-xs text-slate-400">({inq.pricing_source === "auto" ? "auto" : "manual"})</span>
              </td>
              <td className="px-4 py-2.5 text-slate-500">
                {inq.source.replace("_", " ")}{" "}
                <Badge tone={inq.priority === "urgent" || inq.priority === "high" ? "red" : "slate"}>{inq.priority}</Badge>
              </td>
              <td className="px-4 py-2.5">
                <Badge tone={LEAD_TONES[inq.lead_status] ?? "slate"}>{inq.lead_status.replace("_", " ")}</Badge>
              </td>
              <td className="px-4 py-2.5">
                <Badge tone={inq.status === "confirmed" ? "green" : inq.status === "cancelled" ? "red" : "slate"}>
                  {inq.status}
                </Badge>
              </td>
              <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                <Button variant="secondary" onClick={() => openDetail(inq)}>
                  Details
                </Button>
                <Button variant="secondary" onClick={() => setActiveQuotation(inq)}>
                  View
                </Button>
                {!inq.booking_id && (
                  <Button variant="secondary" onClick={() => handleStartEdit(inq)}>
                    Edit
                  </Button>
                )}
                <Button
                  variant="secondary"
                  onClick={() => handleSendWhatsApp(inq)}
                  disabled={sendingWhatsapp === inq.id}
                >
                  {sendingWhatsapp === inq.id ? "..." : "WhatsApp"}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => handleSendEmail(inq)}
                  disabled={sendingEmail === inq.id || !inq.client_email}
                >
                  {sendingEmail === inq.id ? "..." : "Email"}
                </Button>
                {!inq.booking_id && (
                  <Button variant="secondary" onClick={() => handleQuickFollowUp(inq, 3)}>
                    Follow up in 3d
                  </Button>
                )}
                {inq.booking_id ? (
                  <Badge tone="green">Booked</Badge>
                ) : (
                  <Button
                    onClick={() => handleConvertToBooking(inq)}
                    disabled={converting === inq.id || inq.advance_amount <= 0}
                  >
                    {converting === inq.id ? "Converting..." : "Convert to Booking"}
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </Card>

      {activeQuotation && (
        <QuotationPopup
          inquiry={activeQuotation}
          onClose={() => setActiveQuotation(null)}
          onDownloadPdf={handleDownloadPdf}
          downloading={downloading}
          onConvertToBooking={() => handleConvertToBooking(activeQuotation)}
          converting={converting === activeQuotation.id}
          onSendWhatsApp={() => handleSendWhatsApp(activeQuotation)}
          sendingWhatsapp={sendingWhatsapp === activeQuotation.id}
          onSendEmail={() => handleSendEmail(activeQuotation)}
          sendingEmail={sendingEmail === activeQuotation.id}
        />
      )}

      {detailInquiry && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={closeDetail}>
          <div className="bg-white rounded-xl max-w-2xl w-full max-h-[85vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Details — {detailInquiry.client_name} ({detailInquiry.offer_no})</h2>
              <button onClick={closeDetail} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
            </div>

            <h3 className="text-sm font-semibold text-slate-900 mb-2">Attachments</h3>
            <div className="flex gap-2 mb-3">
              <input type="file" onChange={(e) => setAttachFile(e.target.files?.[0] ?? null)} className="text-xs" />
              <Button variant="secondary" onClick={handleUploadAttachment}>Upload</Button>
            </div>
            {attachments.length === 0 ? (
              <p className="text-xs text-slate-400 mb-6">No attachments uploaded yet.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2 mb-6">
                {attachments.map((a) => (
                  <div key={a.id} className="border border-slate-100 rounded-lg p-2 text-xs">
                    <p className="font-medium text-slate-700 truncate">{a.filename}</p>
                    <div className="flex gap-2 mt-1">
                      <button onClick={() => handleDownloadAttachment(a.id, a.filename)} className="text-slate-600 hover:underline">Download</button>
                      <button onClick={() => handleDeleteAttachment(a.id)} className="text-red-500 hover:underline">Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <h3 className="text-sm font-semibold text-slate-900 mb-2">Timeline</h3>
            {timeline.length === 0 ? (
              <p className="text-xs text-slate-400">No history yet.</p>
            ) : (
              <div className="space-y-1">
                {timeline.map((e, i) => (
                  <div key={i} className="border border-slate-100 rounded-lg px-3 py-2 text-xs">
                    <span className="font-medium text-slate-700">{e.action}</span>
                    <span className="text-slate-500"> — {e.user_name} — {new Date(e.created_at).toLocaleString()}</span>
                    {e.details && <p className="text-slate-400">{e.details}</p>}
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
