import { useCallback, useEffect, useState } from "react";
import { api, extractErrorMessage } from "../lib/api";
import { Badge, Button, Card, Drawer, EmptyState, ErrorBanner, PageHeader, Spinner, type BadgeTone } from "../components/ui";

interface DisplayStatus {
  label: string;
  tone: BadgeTone;
}

interface ConversationSummary {
  id: string;
  guest_name: string | null;
  guest_phone: string | null;
  guest_email: string | null;
  unit_name: string | null;
  check_in: string | null;
  check_out: string | null;
  nights: number | null;
  guests: number | null;
  booking_reference: string | null;
  display_status: DisplayStatus;
  last_message_preview: string | null;
  last_message_at: string | null;
  unread_count: number;
  created_at: string;
}

interface Message {
  id: string;
  sender_type: "guest" | "staff" | "system";
  sender_name: string | null;
  body: string;
  message_type: string;
  created_at: string;
  read_at: string | null;
}

interface ConversationDetail extends ConversationSummary {
  booking_id: string | null;
  hold_id: string | null;
  inquiry_id: string | null;
  guest_id: string | null;
  messages: Message[];
}

interface BookingDetails {
  booking_reference: string;
  booking_id: string;
  guest_name: string;
  guest_phone: string | null;
  guest_email: string | null;
  unit_name: string | null;
  check_in: string;
  check_out: string;
  checkin_time: string;
  checkout_time: string;
  nights: number;
  guests: number;
  booking_status: string;
  display_status: DisplayStatus;
  payment_status: string;
  total_amount: number;
  paid_amount: number;
  balance_due: number;
}

interface ConfirmationPreview {
  subject: string;
  body: string;
  html_body: string;
}

const FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All Messages" },
  { key: "unread", label: "Unread" },
  { key: "enquiry", label: "Enquiries" },
  { key: "booking_request", label: "Booking Requests" },
  { key: "payment_pending", label: "Payment Pending" },
  { key: "upcoming_checkin", label: "Upcoming Check-ins" },
  { key: "confirmed", label: "Confirmed" },
  { key: "cancelled", label: "Cancelled" },
];

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function whatsappHref(phone: string | null, text: string): string {
  const digits = (phone ?? "").replace(/\D/g, "").slice(-10);
  const withCountryCode = digits ? `91${digits}` : "";
  return `https://wa.me/${withCountryCode}?text=${encodeURIComponent(text)}`;
}

export default function MessagesPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const [bookingDetails, setBookingDetails] = useState<BookingDetails | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmationPreview | null>(null);
  const [confirmationLoading, setConfirmationLoading] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  const [showDetailsDrawer, setShowDetailsDrawer] = useState(false);

  const loadConversations = useCallback(async () => {
    setListLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/messages/conversations", { params: { filter, q: search, limit: 100 } });
      setConversations(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setListLoading(false);
    }
  }, [filter, search]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  async function openConversation(id: string) {
    setSelectedId(id);
    setDetail(null);
    setDetailLoading(true);
    setBookingDetails(null);
    setConfirmation(null);
    setError(null);
    try {
      const { data } = await api.get(`/messages/conversations/${id}`);
      setDetail(data);
      if (data.unread_count > 0) {
        await api.post(`/messages/conversations/${id}/read`);
        setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, unread_count: 0 } : c)));
      }
      if (data.booking_id) {
        const bookingResp = await api.get(`/messages/conversations/${id}/booking-details`);
        setBookingDetails(bookingResp.data);
        // Auto-load the Confirmation Message as soon as a booking is
        // selected — no extra click needed. Same endpoint, same wording
        // as a manual "Load" click would have produced; failures here are
        // surfaced the same way (setError) and onLoadConfirmation below
        // still works as a manual retry if this attempt fails.
        setConfirmationLoading(true);
        try {
          const confirmationResp = await api.post(`/messages/conversations/${id}/confirmation-preview`);
          setConfirmation(confirmationResp.data);
        } catch (confirmationErr) {
          setError(extractErrorMessage(confirmationErr));
        } finally {
          setConfirmationLoading(false);
        }
      }
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDetailLoading(false);
    }
  }

  async function refreshDetail() {
    if (!selectedId) return;
    const { data } = await api.get(`/messages/conversations/${selectedId}`);
    setDetail(data);
  }

  async function handleSend() {
    if (!selectedId || !draft.trim()) return;
    setSending(true);
    try {
      await api.post(`/messages/conversations/${selectedId}/messages`, { body: draft.trim(), sender_type: "staff" });
      setDraft("");
      await refreshDetail();
      loadConversations();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSending(false);
    }
  }

  async function loadConfirmationPreview() {
    if (!selectedId || !detail?.booking_id) return;
    setConfirmationLoading(true);
    try {
      const { data } = await api.post(`/messages/conversations/${selectedId}/confirmation-preview`);
      setConfirmation(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setConfirmationLoading(false);
    }
  }

  function copyMessage() {
    if (!confirmation) return;
    navigator.clipboard.writeText(confirmation.body).then(() => {
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 2000);
    });
  }

  const detailsPanel = (
    <BookingDetailsPanel
      detail={detail}
      bookingDetails={bookingDetails}
      confirmation={confirmation}
      confirmationLoading={confirmationLoading}
      onLoadConfirmation={loadConfirmationPreview}
      onCopy={copyMessage}
      copyState={copyState}
    />
  );

  return (
    <div>
      <PageHeader title="Messages" />
      <p className="-mt-4 mb-6 text-sm text-slate-400">Manage guest conversations, enquiries and booking communication.</p>
      <ErrorBanner message={error} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_1fr_340px]">
        {/* LEFT: conversation list */}
        <Card className={`p-0 ${selectedId ? "hidden lg:block" : ""}`}>
          <div className="border-b border-slate-200 p-4">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search guest, ref, apartment…"
              aria-label="Search conversations"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <div className="mt-3 flex flex-wrap gap-1.5">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  onClick={() => setFilter(f.key)}
                  className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
                    filter === f.key ? "bg-brand-green text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
          <div className="max-h-[70vh] overflow-y-auto">
            {listLoading ? (
              <div className="p-6">
                <Spinner label="Loading conversations" />
              </div>
            ) : conversations.length === 0 ? (
              <EmptyState message="No conversations found." />
            ) : (
              conversations.map((c) => (
                <ConversationListItem key={c.id} conversation={c} active={c.id === selectedId} onClick={() => openConversation(c.id)} />
              ))
            )}
          </div>
        </Card>

        {/* CENTER: conversation panel */}
        <Card className={`flex flex-col p-0 ${selectedId ? "" : "hidden lg:flex"}`}>
          {!selectedId ? (
            <EmptyState message="Select a conversation to view messages and booking details." />
          ) : detailLoading || !detail ? (
            <div className="p-6">
              <Spinner label="Loading conversation" />
            </div>
          ) : (
            <>
              <div className="border-b border-slate-200 p-4">
                <button onClick={() => setSelectedId(null)} className="mb-2 text-xs font-medium text-slate-400 hover:text-slate-600 lg:hidden">
                  ← All conversations
                </button>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-semibold text-slate-900">{detail.guest_name ?? "Unknown guest"}</p>
                    <p className="text-xs text-slate-400">
                      {detail.unit_name ? `Sri Apartment · ${detail.unit_name}` : "Sri Apartment"}
                      {detail.check_in && detail.check_out && ` · ${formatDate(detail.check_in)} → ${formatDate(detail.check_out)}`}
                      {detail.guests != null && ` · ${detail.guests} Guest${detail.guests === 1 ? "" : "s"}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={detail.display_status.tone}>{detail.display_status.label}</Badge>
                    <Button variant="secondary" className="lg:hidden" onClick={() => setShowDetailsDrawer(true)}>
                      Booking Details
                    </Button>
                  </div>
                </div>
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto p-4">
                {detail.messages.length === 0 ? (
                  <EmptyState message="No messages yet." />
                ) : (
                  detail.messages.map((m) => <MessageBubble key={m.id} message={m} />)
                )}
              </div>

              <div className="border-t border-slate-200 p-4">
                <div className="flex gap-2">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                      }
                    }}
                    rows={2}
                    placeholder="Type your message…"
                    aria-label="Message to guest"
                    className="flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                  <Button onClick={handleSend} disabled={sending || !draft.trim()}>
                    {sending ? "Sending…" : "Send"}
                  </Button>
                </div>
              </div>
            </>
          )}
        </Card>

        {/* RIGHT: booking details — persistent on desktop, hidden elsewhere (opens as a Drawer instead) */}
        <div className="hidden lg:block">{selectedId && detailsPanel}</div>
      </div>

      {showDetailsDrawer && (
        <Drawer title="Booking Details" onClose={() => setShowDetailsDrawer(false)}>
          {detailsPanel}
        </Drawer>
      )}
    </div>
  );
}

function ConversationListItem({ conversation, active, onClick }: { conversation: ConversationSummary; active: boolean; onClick: () => void }) {
  const unread = conversation.unread_count > 0;
  return (
    <button
      onClick={onClick}
      className={`block w-full border-b border-slate-100 px-4 py-3 text-left transition hover:bg-slate-50 ${active ? "bg-brand-green/5" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className={`truncate text-sm ${unread ? "font-semibold text-slate-900" : "font-medium text-slate-700"}`}>
          {unread && <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-brand-green align-middle" aria-hidden="true" />}
          {conversation.guest_name ?? "Unknown guest"}
        </p>
        {conversation.last_message_at && <span className="shrink-0 text-[11px] text-slate-400">{formatDateTime(conversation.last_message_at)}</span>}
      </div>
      <p className="mt-0.5 text-xs text-slate-400">
        {conversation.unit_name ? `Sri Apartment · ${conversation.unit_name}` : "Sri Apartment"}
      </p>
      {conversation.check_in && conversation.check_out && (
        <p className="mt-0.5 text-xs text-slate-400">
          {formatDate(conversation.check_in)} → {formatDate(conversation.check_out)}
          {conversation.guests != null && ` · ${conversation.guests} Guest${conversation.guests === 1 ? "" : "s"}`}
        </p>
      )}
      {conversation.last_message_preview && <p className="mt-1 truncate text-xs text-slate-500">"{conversation.last_message_preview}"</p>}
      <div className="mt-2">
        <Badge tone={conversation.display_status.tone}>{conversation.display_status.label}</Badge>
      </div>
    </button>
  );
}

function MessageBubble({ message }: { message: Message }) {
  if (message.message_type === "system_event" || message.sender_type === "system") {
    return <p className="text-center text-xs text-slate-400">{message.body} · {formatDateTime(message.created_at)}</p>;
  }
  const isStaff = message.sender_type === "staff";
  return (
    <div className={`flex ${isStaff ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${isStaff ? "bg-brand-green text-white" : "border border-slate-200 bg-slate-50 text-slate-800"}`}>
        {message.message_type === "note" && <p className="mb-1 text-[10px] font-bold tracking-wide uppercase opacity-70">Internal note</p>}
        <p className="whitespace-pre-wrap">{message.body}</p>
        <p className={`mt-1 text-[10px] ${isStaff ? "text-white/60" : "text-slate-400"}`}>
          {isStaff ? message.sender_name ?? "Staff" : "Guest"} · {formatDateTime(message.created_at)}
        </p>
      </div>
    </div>
  );
}

function BookingDetailsPanel({
  detail,
  bookingDetails,
  confirmation,
  confirmationLoading,
  onLoadConfirmation,
  onCopy,
  copyState,
}: {
  detail: ConversationDetail | null;
  bookingDetails: BookingDetails | null;
  confirmation: ConfirmationPreview | null;
  confirmationLoading: boolean;
  onLoadConfirmation: () => void;
  onCopy: () => void;
  copyState: "idle" | "copied";
}) {
  if (!detail) return null;

  if (!detail.booking_id || !bookingDetails) {
    return (
      <Card>
        <h2 className="mb-1 text-xs font-bold tracking-wide text-slate-400 uppercase">Booking Details</h2>
        <EmptyState message="No booking linked to this conversation." />
      </Card>
    );
  }

  const whatsappText = confirmation
    ? confirmation.body
    : `Hi ${bookingDetails.guest_name}, your booking ${bookingDetails.booking_reference} at ${bookingDetails.unit_name ?? "Sri Apartment"} is confirmed.`;

  return (
    <div className="space-y-4">
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs font-bold tracking-wide text-slate-400 uppercase">Booking Details</h2>
          <Badge tone={bookingDetails.display_status.tone}>{bookingDetails.display_status.label}</Badge>
        </div>
        <dl className="space-y-2 text-sm">
          <Row label="Booking Reference" value={bookingDetails.booking_reference} />
          <Row label="Guest" value={bookingDetails.guest_name} />
          {bookingDetails.guest_phone && <Row label="Phone" value={bookingDetails.guest_phone} />}
          {bookingDetails.guest_email && <Row label="Email" value={bookingDetails.guest_email} />}
          <Row label="Apartment" value={bookingDetails.unit_name ?? "—"} />
          <Row label="Check-in" value={`${formatDate(bookingDetails.check_in)} · ${bookingDetails.checkin_time}`} />
          <Row label="Check-out" value={`${formatDate(bookingDetails.check_out)} · ${bookingDetails.checkout_time}`} />
          <Row label="Nights" value={String(bookingDetails.nights)} />
          <Row label="Guests" value={String(bookingDetails.guests)} />
          <Row label="Payment Status" value={bookingDetails.payment_status} />
          <Row label="Total" value={`₹${bookingDetails.total_amount.toLocaleString("en-IN")}`} />
          <Row label="Paid" value={`₹${bookingDetails.paid_amount.toLocaleString("en-IN")}`} />
          <Row label="Balance" value={`₹${bookingDetails.balance_due.toLocaleString("en-IN")}`} />
        </dl>
      </Card>

      <Card>
        <h2 className="mb-3 text-xs font-bold tracking-wide text-slate-400 uppercase">Confirmation Message</h2>
        {!confirmation ? (
          confirmationLoading ? (
            <Spinner label="Loading confirmation message" />
          ) : (
            // Auto-loads on selection (see openConversation) — this button
            // only appears as a manual retry if that automatic attempt failed.
            <Button variant="secondary" onClick={onLoadConfirmation}>
              Retry Loading Confirmation Message
            </Button>
          )
        ) : (
          <>
            <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3.5 text-sm leading-relaxed whitespace-pre-wrap text-slate-700">
              {confirmation.body}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="secondary" onClick={onCopy}>
                {copyState === "copied" ? "Copied ✓" : "Copy Message"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  const subject = encodeURIComponent(confirmation.subject);
                  const body = encodeURIComponent(confirmation.body);
                  window.open(`mailto:${bookingDetails.guest_email ?? ""}?subject=${subject}&body=${body}`, "_blank");
                }}
                disabled={!bookingDetails.guest_email}
              >
                Send Email
              </Button>
              <Button
                variant="secondary"
                onClick={() => window.open(whatsappHref(bookingDetails.guest_phone, whatsappText), "_blank")}
                disabled={!bookingDetails.guest_phone}
              >
                WhatsApp
              </Button>
            </div>
            <p className="mt-2 text-[11px] text-slate-400">
              Send Email opens your own email client; WhatsApp opens wa.me with this message prefilled — neither is sent automatically by OS10.
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-slate-400">{label}</dt>
      <dd className="text-right font-medium text-slate-800">{value}</dd>
    </div>
  );
}
