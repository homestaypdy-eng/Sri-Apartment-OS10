import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { guestApi, extractErrorMessage, type GuestBookingOut, type GuestIdentityStatusOut, type GuestProfileOut, type GuestRequestOut } from "../lib/api";
import { useGuestAuth } from "../context/GuestAuthContext";

const IDENTITY_DOCUMENT_TYPES = [
  { value: "aadhaar", label: "Aadhaar" },
  { value: "pan", label: "PAN" },
  { value: "passport", label: "Passport" },
  { value: "driving_license", label: "Driving Licence" },
  { value: "other", label: "Other" },
];

// Simple, professional labels (section 30) — never "Fully Verified"/
// "Government Verified Account"-style wording. Identity Verification is
// entirely separate from this session's own login, which this page's own
// existing "unlinked" screen already documents as proving channel control
// only (see its own comment above).
function identityBadgeClasses(status: GuestIdentityStatusOut["status"]): string {
  if (status === "verified") return "bg-emerald-100 text-emerald-700";
  if (status === "pending" || status === "submitted" || status === "under_review") return "bg-amber-100 text-amber-700";
  if (status === "rejected") return "bg-red-100 text-red-700";
  return "bg-slate-100 text-slate-600";
}

function identityBadgeLabel(status: GuestIdentityStatusOut["status"]): string {
  if (status === "verified") return "✓ Identity Verified";
  if (status === "pending") return "◷ Verification Pending";
  if (status === "submitted" || status === "under_review") return "◷ Verification Pending";
  if (status === "rejected") return "✕ Verification Rejected";
  return "⚠ Identity Verification Required";
}

export default function GuestDashboardPage() {
  const { guestLogout } = useGuestAuth();
  const navigate = useNavigate();
  const [profile, setProfile] = useState<GuestProfileOut | null>(null);
  const [bookings, setBookings] = useState<GuestBookingOut[]>([]);
  const [requests, setRequests] = useState<GuestRequestOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  // A guest OTP/magic-link session can be genuinely valid while the
  // identity behind it isn't linked to a Guest record yet (see the
  // customer-auth/guest-auth Phase 2A architecture: a brand-new phone/
  // email that hasn't been staff-linked to a booking yet). That is NOT a
  // login failure — never bounce the guest back to /guest/login for it,
  // and never show the raw backend wording ("Guest not found").
  const [unlinked, setUnlinked] = useState(false);

  const [requestForm, setRequestForm] = useState({ booking_id: "", description: "", type: "service" as "service" | "complaint" | "extend" });
  const [extendDate, setExtendDate] = useState("");

  // Identity Verification — separate from Account Verification, which this
  // page has no field for at all (the guest's login already succeeded by
  // the time this page renders; that proves channel control only).
  const [identity, setIdentity] = useState<GuestIdentityStatusOut | null>(null);
  const [identityDocType, setIdentityDocType] = useState("aadhaar");
  const [identityFile, setIdentityFile] = useState<File | null>(null);
  const [submittingIdentity, setSubmittingIdentity] = useState(false);

  async function loadAll() {
    try {
      const [p, b, r, id] = await Promise.all([
        guestApi.get("/portal/me"),
        guestApi.get("/portal/bookings"),
        guestApi.get("/portal/requests"),
        guestApi.get("/portal/me/identity"),
      ]);
      setProfile(p.data);
      setBookings(b.data);
      setRequests(r.data);
      setIdentity(id.data);
    } catch (err) {
      const status = (err as any)?.response?.status;
      const detail = (err as any)?.response?.data?.detail;
      if (status === 401 && detail === "Guest not found") {
        setUnlinked(true);
        return;
      }
      setError(extractErrorMessage(err));
      if (status === 401) {
        guestLogout();
        navigate("/guest/login");
      }
    }
  }

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleLogout() {
    guestLogout();
    navigate("/guest/login");
  }

  async function handleSubmitRequest(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      const endpoint = requestForm.type === "complaint" ? "/portal/complaints" : "/portal/requests";
      await guestApi.post(endpoint, {
        booking_id: requestForm.booking_id || null,
        description: requestForm.description,
        requested_check_out_date: requestForm.type === "extend" ? extendDate : null,
      });
      setSuccess("Request submitted. Our team will follow up.");
      setRequestForm({ booking_id: "", description: "", type: "service" });
      setExtendDate("");
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleSubmitIdentity(e: FormEvent) {
    e.preventDefault();
    if (!identityFile) return;
    setError(null);
    setSuccess(null);
    setSubmittingIdentity(true);
    try {
      const formData = new FormData();
      formData.append("file", identityFile);
      const upload = await guestApi.post("/portal/me/identity/documents", formData, {
        params: { document_type: identityDocType },
        headers: { "Content-Type": "multipart/form-data" },
      });
      await guestApi.post("/portal/me/identity/submit", { document_id: upload.data.id });
      setSuccess("Identity document submitted. We'll review it shortly.");
      setIdentityFile(null);
      const { data } = await guestApi.get("/portal/me/identity");
      setIdentity(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSubmittingIdentity(false);
    }
  }

  async function downloadInvoice(bookingId: string) {
    setError(null);
    try {
      const res = await guestApi.get(`/portal/invoices/${bookingId}/pdf`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "invoice.pdf";
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  if (unlinked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
          <h1 className="font-semibold text-slate-900">You're verified — almost there</h1>
          <p className="mt-2 text-sm text-slate-500">
            We couldn't find a booking linked to this login yet. If you've just made a booking, please check back
            shortly, or contact our front desk staff for assistance.
          </p>
          <button
            onClick={handleLogout}
            className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Log out
          </button>
        </div>
      </div>
    );
  }

  if (!profile) {
    return <div className="min-h-screen flex items-center justify-center text-sm text-slate-400">Loading...</div>;
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200 px-4 sm:px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="font-semibold text-slate-900">Welcome, {profile.name}</h1>
          <p className="text-xs text-slate-400">Sri Apartment Homestay Guest Portal</p>
        </div>
        <button onClick={handleLogout} className="text-sm text-slate-500 hover:text-slate-800">
          Log out
        </button>
      </header>

      <main className="max-w-3xl mx-auto p-4 sm:p-6 space-y-6">
        {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-2">{error}</div>}
        {success && <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm rounded-lg px-4 py-2">{success}</div>}

        <section className="bg-white rounded-xl border border-slate-200 p-4 sm:p-6">
          <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
            <h2 className="text-sm font-semibold text-slate-900">Identity Verification</h2>
            {identity && (
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${identityBadgeClasses(identity.status)}`}>
                {identityBadgeLabel(identity.status)}
              </span>
            )}
          </div>

          {identity?.status === "verified" && (
            <p className="text-sm text-slate-500">
              {identity.document_type && (
                <>
                  Document: <span className="text-slate-700">{IDENTITY_DOCUMENT_TYPES.find((d) => d.value === identity.document_type)?.label ?? identity.document_type}</span>
                  <br />
                </>
              )}
              {identity.verified_at && <>Verified on {identity.verified_at.slice(0, 10)}</>}
            </p>
          )}

          {(identity?.status === "submitted" || identity?.status === "under_review") && (
            <p className="text-sm text-slate-500">Your identity verification is being reviewed. We'll let you know once it's complete.</p>
          )}

          {identity && (identity.status === "not_required" || identity.status === "pending" || identity.status === "rejected") && (
            <>
              {identity.status === "rejected" && identity.message && (
                <p className="text-sm text-red-600 mb-3">{identity.message}</p>
              )}
              {identity.status === "not_required" && (
                <p className="text-sm text-slate-500 mb-3">Please verify your identity before completing your stay registration.</p>
              )}
              <form onSubmit={handleSubmitIdentity} className="space-y-3">
                <label htmlFor="identity-doc-type" className="sr-only">Document type</label>
                <select
                  id="identity-doc-type"
                  value={identityDocType}
                  onChange={(e) => setIdentityDocType(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  {IDENTITY_DOCUMENT_TYPES.map((d) => (
                    <option key={d.value} value={d.value}>{d.label}</option>
                  ))}
                </select>
                <label htmlFor="identity-file" className="sr-only">Identity document file</label>
                <input
                  id="identity-file"
                  type="file"
                  required
                  accept="image/jpeg,image/png,image/webp,application/pdf"
                  onChange={(e) => setIdentityFile(e.target.files?.[0] ?? null)}
                  className="w-full text-sm"
                />
                <button
                  type="submit"
                  disabled={submittingIdentity || !identityFile}
                  className="bg-slate-900 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
                >
                  {submittingIdentity ? "Submitting..." : "Verify Identity"}
                </button>
              </form>
            </>
          )}
        </section>

        <section className="bg-white rounded-xl border border-slate-200 p-4 sm:p-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Your Bookings</h2>
          {bookings.length === 0 ? (
            <p className="text-sm text-slate-400">No bookings found.</p>
          ) : (
            <div className="space-y-3">
              {bookings.map((b) => (
                <div key={b.id} className="border border-slate-100 rounded-lg p-3 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-slate-800">
                      {b.check_in} → {b.check_out}
                    </p>
                    <p className="text-xs text-slate-400">
                      Status: {b.status} · ₹{b.rate_applied}/night
                    </p>
                  </div>
                  <button
                    onClick={() => downloadInvoice(b.id)}
                    className="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg px-3 py-1.5 self-start sm:self-auto"
                  >
                    Download Invoice
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="bg-white rounded-xl border border-slate-200 p-4 sm:p-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Need Something?</h2>
          <form onSubmit={handleSubmitRequest} className="space-y-3">
            <select
              value={requestForm.type}
              onChange={(e) => setRequestForm({ ...requestForm, type: e.target.value as any })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="service">Service Request</option>
              <option value="complaint">Complaint</option>
              <option value="extend">Extend My Stay</option>
            </select>
            {bookings.length > 0 && (
              <select
                value={requestForm.booking_id}
                onChange={(e) => setRequestForm({ ...requestForm, booking_id: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">Related booking (optional)</option>
                {bookings.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.check_in} → {b.check_out}
                  </option>
                ))}
              </select>
            )}
            {requestForm.type === "extend" && (
              <input
                type="date"
                required
                value={extendDate}
                onChange={(e) => setExtendDate(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            )}
            <textarea
              required
              placeholder="Tell us more..."
              value={requestForm.description}
              onChange={(e) => setRequestForm({ ...requestForm, description: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              rows={3}
            />
            <button type="submit" className="bg-slate-900 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-slate-800">
              Submit
            </button>
          </form>
        </section>

        <section className="bg-white rounded-xl border border-slate-200 p-4 sm:p-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Your Requests</h2>
          {requests.length === 0 ? (
            <p className="text-sm text-slate-400">No requests yet.</p>
          ) : (
            <div className="space-y-2">
              {requests.map((r) => (
                <div key={r.id} className="border border-slate-100 rounded-lg p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-700 uppercase">{r.request_type.replace("_", " ")}</span>
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full ${
                        r.status === "resolved" ? "bg-emerald-100 text-emerald-700" : r.status === "in_progress" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {r.status}
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 mt-1">{r.description}</p>
                  {r.staff_notes && <p className="text-xs text-slate-400 mt-1">Staff note: {r.staff_notes}</p>}
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
