import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type BookingOut, type InvoiceOut, type CreditNoteOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table } from "../components/ui";
import { useAuth } from "../context/AuthContext";

const PAYMENT_MODES = ["upi", "card", "bank_transfer", "cash"];

export default function BillingPage() {
  // RBAC Phase B: credit-note issuance is Admin-only on the backend
  // (POST /invoices/{id}/credit-notes) — this page previously had no role
  // check at all, so Staff already saw a form that would 403 for them;
  // gating it here fixes that for Staff too, not just Manager, since the
  // check matches the real backend boundary rather than singling out one
  // role. Viewing/downloading existing credit notes stays visible to
  // everyone who can reach this page (that's a GET, already granted).
  const { role } = useAuth();
  const isAdmin = role === "admin";

  const [bookings, setBookings] = useState<BookingOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [paymentForm, setPaymentForm] = useState({ booking_id: "", amount: "", mode: "upi", reference_no: "" });
  const [invoiceBookingId, setInvoiceBookingId] = useState("");
  const [generatedInvoice, setGeneratedInvoice] = useState<InvoiceOut | null>(null);
  const [sendingEmail, setSendingEmail] = useState(false);
  const [sendingWhatsapp, setSendingWhatsapp] = useState(false);
  const [creditNotes, setCreditNotes] = useState<CreditNoteOut[]>([]);
  const [creditNoteForm, setCreditNoteForm] = useState({ amount: "", reason: "" });

  async function loadBookings() {
    try {
      const { data } = await api.get("/bookings");
      setBookings(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    loadBookings();
  }, []);

  async function handleRecordPayment(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/payments", {
        booking_id: paymentForm.booking_id,
        amount: Number(paymentForm.amount),
        mode: paymentForm.mode,
        reference_no: paymentForm.reference_no || null,
      });
      setSuccess("Payment recorded.");
      setPaymentForm({ booking_id: "", amount: "", mode: "upi", reference_no: "" });
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleGenerateInvoice(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setGeneratedInvoice(null);
    setCreditNotes([]);
    try {
      const { data } = await api.post(`/invoices/generate/${invoiceBookingId}`);
      setGeneratedInvoice(data);
      setSuccess(`Invoice ${data.invoice_no} generated.`);
      const cn = await api.get(`/invoices/${data.id}/credit-notes`);
      setCreditNotes(cn.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadPdf(invoiceId: string, invoiceNo: string) {
    try {
      const res = await api.get(`/invoices/${invoiceId}/pdf`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${invoiceNo.replace(/\//g, "_")}.pdf`;
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleSendInvoiceEmail() {
    if (!generatedInvoice) return;
    setSendingEmail(true);
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post(`/invoices/${generatedInvoice.id}/send/email`, {});
      setSuccess(data.sent ? `Invoice emailed to ${data.to_email}.` : `Attempted to email ${data.to_email} — check Notifications log for delivery status.`);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSendingEmail(false);
    }
  }

  async function handleSendInvoiceWhatsApp() {
    if (!generatedInvoice) return;
    setSendingWhatsapp(true);
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post(`/invoices/${generatedInvoice.id}/send/whatsapp`);
      window.open(data.whatsapp_url, "_blank");
      setSuccess("WhatsApp opened with the invoice message ready to send.");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSendingWhatsapp(false);
    }
  }

  async function handleCreateCreditNote(e: FormEvent) {
    e.preventDefault();
    if (!generatedInvoice) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.post(`/invoices/${generatedInvoice.id}/credit-notes`, {
        amount: Number(creditNoteForm.amount),
        reason: creditNoteForm.reason,
      });
      setCreditNotes((prev) => [...prev, data]);
      setCreditNoteForm({ amount: "", reason: "" });
      setSuccess(`Credit note ${data.credit_note_no} issued.`);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadCreditNotePdf(creditNoteId: string, creditNoteNo: string) {
    try {
      const res = await api.get(`/credit-notes/${creditNoteId}/pdf`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${creditNoteNo.replace(/\//g, "_")}.pdf`;
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader title="Billing" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <div className="grid grid-cols-2 gap-6 mb-8">
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Record Payment</h2>
          <form onSubmit={handleRecordPayment} className="space-y-3">
            <select
              required
              value={paymentForm.booking_id}
              onChange={(e) => setPaymentForm({ ...paymentForm, booking_id: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Select booking...</option>
              {bookings.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.check_in} → {b.check_out} (₹{b.rate_applied}/night)
                </option>
              ))}
            </select>
            <input
              required
              type="number"
              placeholder="Amount (₹)"
              value={paymentForm.amount}
              onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <select
              value={paymentForm.mode}
              onChange={(e) => setPaymentForm({ ...paymentForm, mode: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              {PAYMENT_MODES.map((m) => (
                <option key={m} value={m}>
                  {m.replace("_", " ").toUpperCase()}
                </option>
              ))}
            </select>
            <input
              placeholder="Reference number (optional)"
              value={paymentForm.reference_no}
              onChange={(e) => setPaymentForm({ ...paymentForm, reference_no: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <Button type="submit">Record Payment</Button>
          </form>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Generate GST Invoice</h2>
          <form onSubmit={handleGenerateInvoice} className="space-y-3">
            <select
              required
              value={invoiceBookingId}
              onChange={(e) => setInvoiceBookingId(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Select booking...</option>
              {bookings.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.check_in} → {b.check_out}
                </option>
              ))}
            </select>
            <Button type="submit">Generate Invoice</Button>
          </form>

          {generatedInvoice && (
            <div className="mt-4 p-4 bg-slate-50 rounded-lg text-sm space-y-1">
              <p className="font-medium text-slate-800">{generatedInvoice.invoice_no}</p>
              <p className="text-slate-500">Subtotal: ₹{generatedInvoice.subtotal}</p>
              <p className="text-slate-500">
                GST rate applied: {generatedInvoice.gst_rate_percent}% (tariff slab
                {generatedInvoice.itc_available ? ", ITC available" : ", ITC not available"})
              </p>
              {generatedInvoice.cgst > 0 && <p className="text-slate-500">CGST: ₹{generatedInvoice.cgst}</p>}
              {generatedInvoice.sgst > 0 && <p className="text-slate-500">SGST: ₹{generatedInvoice.sgst}</p>}
              {generatedInvoice.igst > 0 && <p className="text-slate-500">IGST: ₹{generatedInvoice.igst}</p>}
              <p className="font-semibold text-slate-900">Total: ₹{generatedInvoice.total}</p>
              <div className="flex flex-wrap gap-2 pt-1">
                <Button
                  variant="secondary"
                  onClick={() => handleDownloadPdf(generatedInvoice.id, generatedInvoice.invoice_no)}
                >
                  Download PDF
                </Button>
                <Button variant="secondary" onClick={handleSendInvoiceEmail} disabled={sendingEmail}>
                  {sendingEmail ? "Sending..." : "Email Invoice"}
                </Button>
                <Button variant="secondary" onClick={handleSendInvoiceWhatsApp} disabled={sendingWhatsapp}>
                  {sendingWhatsapp ? "Preparing..." : "Send via WhatsApp"}
                </Button>
              </div>

              <div className="pt-3 mt-3 border-t border-slate-200">
                <h3 className="text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2">Credit Notes</h3>
                {isAdmin && (
                  <form onSubmit={handleCreateCreditNote} className="flex flex-wrap gap-2 mb-2">
                    <input
                      required
                      type="number"
                      placeholder="Amount (excl. GST)"
                      value={creditNoteForm.amount}
                      onChange={(e) => setCreditNoteForm({ ...creditNoteForm, amount: e.target.value })}
                      className="w-40 rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
                    />
                    <input
                      required
                      placeholder="Reason"
                      value={creditNoteForm.reason}
                      onChange={(e) => setCreditNoteForm({ ...creditNoteForm, reason: e.target.value })}
                      className="flex-1 min-w-[160px] rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
                    />
                    <Button type="submit" variant="secondary">Issue Credit Note</Button>
                  </form>
                )}
                {creditNotes.length === 0 ? (
                  <p className="text-xs text-slate-400">No credit notes issued against this invoice.</p>
                ) : (
                  <div className="space-y-1">
                    {creditNotes.map((cn) => (
                      <div key={cn.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-2 text-xs">
                        <span>
                          <span className="font-medium text-slate-800">{cn.credit_note_no}</span>
                          <span className="text-slate-500"> — ₹{cn.total} — {cn.reason}</span>
                        </span>
                        <button onClick={() => handleDownloadCreditNotePdf(cn.id, cn.credit_note_no)} className="text-slate-600 hover:underline">
                          PDF
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">All Bookings (for reference)</h2>
        <Table headers={["Check-in", "Check-out", "Rate/night", "Status"]}>
          {bookings.map((b) => (
            <tr key={b.id}>
              <td className="px-4 py-2.5 text-slate-700">{b.check_in}</td>
              <td className="px-4 py-2.5 text-slate-700">{b.check_out}</td>
              <td className="px-4 py-2.5 text-slate-500">₹{b.rate_applied}</td>
              <td className="px-4 py-2.5 text-slate-500">{b.status}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
