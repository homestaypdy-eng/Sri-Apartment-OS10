import { useCallback, useEffect, useState } from "react";
import { api, extractErrorMessage } from "../lib/api";
import { Badge, Button, Card, Drawer, EmptyState, ErrorBanner, Spinner, type BadgeTone } from "../components/ui";

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

// Grouped only for visual scanning (a thin divider between groups below) —
// the filter keys, labels, and the /messages/conversations?filter= contract
// are unchanged from before.
const FILTER_GROUPS: { key: string; label: string }[][] = [
  [
    { key: "all", label: "All Messages" },
    { key: "unread", label: "Unread" },
  ],
  [
    { key: "enquiry", label: "Enquiries" },
    { key: "booking_request", label: "Booking Requests" },
    { key: "payment_pending", label: "Payment Pending" },
    { key: "upcoming_checkin", label: "Upcoming Check-ins" },
  ],
  [
    { key: "confirmed", label: "Confirmed" },
    { key: "cancelled", label: "Cancelled" },
  ],
];

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (sameDay(date, today)) return `Today · ${formatTime(iso)}`;
  if (sameDay(date, yesterday)) return `Yesterday · ${formatTime(iso)}`;
  return date.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

// Day-group headings inside the message timeline ("Today" / "Yesterday" /
// "25 Sep 2026") — purely a presentation grouping over the existing
// messages array, no new data.
function dayLabel(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (sameDay(date, today)) return "Today";
  if (sameDay(date, yesterday)) return "Yesterday";
  return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function whatsappHref(phone: string | null, text: string): string {
  const digits = (phone ?? "").replace(/\D/g, "").slice(-10);
  const withCountryCode = digits ? `91${digits}` : "";
  return `https://wa.me/${withCountryCode}?text=${encodeURIComponent(text)}`;
}

// Presentation-only fix: the backend confirmation-message generator
// (app/modules/notifications/service.py) deliberately reuses the exact
// text of the real guest email, which includes the internal UUID as
// "Booking ID: <uuid>" — correct for that shared template, but not
// something staff should read, copy, email, or WhatsApp to a guest from
// this page when a human-readable booking reference already exists.
// Swaps that one line for "Booking Reference: SA-2026-..." wherever this
// page uses the confirmation text (preview, copy, email body, WhatsApp
// text) — never touches the backend template or what it actually emails
// automatically elsewhere in the booking flow.
function humanizeConfirmationBody(body: string | undefined | null, bookingReference: string | null): string {
  if (!body || !bookingReference) return body ?? "";
  return body.replace(/^Booking ID: .*/m, `Booking Reference: ${bookingReference}`);
}

type PaymentTone = "green" | "amber" | "red" | "slate";

function paymentStatusTone(status: string): PaymentTone {
  const normalized = status.toLowerCase();
  if (normalized.includes("partially")) return "amber";
  if (normalized === "paid") return "green";
  if (normalized.includes("pending")) return "amber";
  if (normalized.includes("cancel") || normalized.includes("refund")) return "red";
  return "slate";
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
    const textToCopy = humanizeConfirmationBody(confirmation.body, bookingDetails?.booking_reference ?? null);
    navigator.clipboard.writeText(textToCopy).then(() => {
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
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Messages</h1>
          <p className="text-sm text-slate-400">Guest communication, enquiries and booking conversations.</p>
        </div>
      </div>
      <ErrorBanner message={error} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[340px_1fr_340px]">
        {/* LEFT: conversation list */}
        <Card className={`!rounded-[14px] p-0 shadow-sm ${selectedId ? "hidden lg:block" : ""}`}>
          <div className="border-b border-slate-200 p-4">
            <div className="relative">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-slate-400"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="M21 21l-4.3-4.3" />
              </svg>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search guest, booking, reference or apartment…"
                aria-label="Search conversations"
                className="w-full rounded-lg border border-slate-300 py-2 pr-3 pl-8 text-sm"
              />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              {FILTER_GROUPS.map((group, groupIndex) => (
                <div
                  key={groupIndex}
                  className={`flex flex-wrap gap-1.5 ${groupIndex > 0 ? "border-l border-slate-200 pl-2" : ""}`}
                >
                  {group.map((f) => (
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
        <Card className={`!rounded-[14px] flex flex-col p-0 shadow-sm ${selectedId ? "" : "hidden lg:flex"}`}>
          {!selectedId ? (
            <div className="flex flex-1 flex-col items-center justify-center py-16 text-center">
              <p className="text-sm font-semibold text-slate-600">Select a conversation</p>
              <p className="mt-1 max-w-xs text-sm text-slate-400">
                Choose a guest conversation to view messages and booking details.
              </p>
            </div>
          ) : detailLoading || !detail ? (
            <div className="p-6">
              <Spinner label="Loading conversation" />
            </div>
          ) : (
            <>
              <div className="border-b border-slate-200 p-4">
                <button onClick={() => setSelectedId(null)} className="mb-2 text-xs font-medium text-slate-400 hover:text-slate-600 lg:hidden">
                  ← Messages
                </button>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-slate-900">{detail.guest_name ?? "Unknown guest"}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-slate-400">
                      <Badge tone={detail.display_status.tone}>{detail.display_status.label}</Badge>
                      <span>· {detail.unit_name ? `Sri Apartment · ${detail.unit_name}` : "Sri Apartment"}</span>
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
                      {detail.check_in && detail.check_out && `${formatDate(detail.check_in)} → ${formatDate(detail.check_out)}`}
                      {detail.guests != null && ` · ${detail.guests} Guest${detail.guests === 1 ? "" : "s"}`}
                      {detail.booking_reference && ` · Ref ${detail.booking_reference}`}
                    </p>
                  </div>
                  <Button variant="secondary" className="shrink-0 lg:hidden" onClick={() => setShowDetailsDrawer(true)}>
                    Booking Details
                  </Button>
                </div>
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto p-4">
                {detail.messages.length === 0 ? (
                  <div className="flex flex-1 flex-col items-center justify-center py-10 text-center">
                    <p className="text-sm font-medium text-slate-500">No messages yet.</p>
                    <p className="mt-1 text-xs text-slate-400">Send a message to start the conversation.</p>
                  </div>
                ) : (
                  groupMessagesByDay(detail.messages).map((group) => (
                    <div key={group.label + group.items[0].id} className="space-y-3">
                      <DayDivider label={group.label} />
                      {group.items.map((m) => <MessageBubble key={m.id} message={m} />)}
                    </div>
                  ))
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
      className={`block w-full border-b border-slate-100 border-l-[3px] px-4 py-3 text-left transition hover:bg-slate-50 ${
        active ? "bg-brand-green/5" : ""
      } ${unread ? "border-l-brand-green" : "border-l-transparent"}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className={`truncate text-sm ${unread ? "font-semibold text-slate-900" : "font-medium text-slate-700"}`}>
          {unread && <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-brand-green align-middle" aria-hidden="true" />}
          {conversation.guest_name ?? "Unknown guest"}
        </p>
        {conversation.last_message_at && (
          <span className={`shrink-0 text-[11px] ${unread ? "font-semibold text-brand-green" : "text-slate-400"}`}>
            {formatDateTime(conversation.last_message_at)}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-xs text-slate-400">
        {conversation.unit_name ? `Sri Apartment · ${conversation.unit_name}` : "Sri Apartment"}
        {conversation.guests != null && ` · ${conversation.guests} Guest${conversation.guests === 1 ? "" : "s"}`}
      </p>
      {conversation.check_in && conversation.check_out && (
        <p className="mt-0.5 text-xs text-slate-400">
          {formatDate(conversation.check_in)} → {formatDate(conversation.check_out)}
        </p>
      )}
      <div className="mt-1.5">
        <Badge tone={conversation.display_status.tone}>{conversation.display_status.label}</Badge>
      </div>
      {conversation.last_message_preview && <p className="mt-1.5 truncate text-xs text-slate-500">"{conversation.last_message_preview}"</p>}
    </button>
  );
}

// Groups messages under a "Today" / "Yesterday" / "25 Sep 2026" heading so a
// long conversation stays scannable — pure presentation over the same
// messages array, no change to what's fetched or stored.
function groupMessagesByDay(messages: Message[]): { label: string; items: Message[] }[] {
  const groups: { label: string; items: Message[] }[] = [];
  for (const message of messages) {
    const label = dayLabel(message.created_at);
    const current = groups[groups.length - 1];
    if (current && current.label === label) {
      current.items.push(message);
    } else {
      groups.push({ label, items: [message] });
    }
  }
  return groups;
}

function DayDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-1" role="separator" aria-label={label}>
      <div className="h-px flex-1 bg-slate-200" />
      <span className="text-[11px] font-semibold tracking-wide text-slate-400 uppercase">{label}</span>
      <div className="h-px flex-1 bg-slate-200" />
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  if (message.message_type === "system_event" || message.sender_type === "system") {
    return <p className="text-center text-xs text-slate-400">{message.body} · {formatTime(message.created_at)}</p>;
  }
  const isStaff = message.sender_type === "staff";
  return (
    <div className={`flex ${isStaff ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${isStaff ? "bg-brand-green text-white" : "border border-slate-200 bg-slate-50 text-slate-800"}`}>
        {message.message_type === "note" && <p className="mb-1 text-[10px] font-bold tracking-wide uppercase opacity-70">Internal note</p>}
        <p className="whitespace-pre-wrap">{message.body}</p>
        <p className={`mt-1 text-[10px] ${isStaff ? "text-white/60" : "text-slate-400"}`}>
          {isStaff ? message.sender_name ?? "Staff" : "Guest"} · {formatTime(message.created_at)}
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
    // No confirmed booking yet (an enquiry or booking request) — still show
    // whatever the conversation itself already carries (status, unit,
    // requested dates, guest count) instead of a bare empty state, without
    // inventing anything the conversation doesn't already have.
    const hasRequestDates = Boolean(detail.check_in && detail.check_out);
    return (
      <Card className="!rounded-[14px] shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs font-bold tracking-wide text-slate-400 uppercase">Booking / Request Details</h2>
          <Badge tone={detail.display_status.tone}>{detail.display_status.label}</Badge>
        </div>
        <dl className="space-y-2 text-sm">
          {detail.unit_name && <Row label="Apartment" value={detail.unit_name} />}
          {hasRequestDates && <Row label="Requested stay" value={`${formatDate(detail.check_in)} → ${formatDate(detail.check_out)}`} />}
          {detail.guests != null && <Row label="Guests" value={String(detail.guests)} />}
        </dl>
        <p className="mt-3 text-xs text-slate-400">No confirmed booking linked yet.</p>
      </Card>
    );
  }

  const humanizedBody = confirmation ? humanizeConfirmationBody(confirmation.body, bookingDetails.booking_reference) : null;
  const whatsappText = humanizedBody
    ?? `Hi ${bookingDetails.guest_name}, your booking ${bookingDetails.booking_reference} at ${bookingDetails.unit_name ?? "Sri Apartment"} is confirmed.`;
  const balanceDue = bookingDetails.balance_due > 0;

  return (
    <div className="space-y-4">
      <Card className="!rounded-[14px] shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs font-bold tracking-wide text-slate-400 uppercase">Booking Details</h2>
          <Badge tone={bookingDetails.display_status.tone}>{bookingDetails.display_status.label}</Badge>
        </div>

        <SectionLabel>Booking</SectionLabel>
        <dl className="space-y-2 text-sm">
          <Row label="Booking Reference" value={bookingDetails.booking_reference} />
          <Row label="Guest" value={bookingDetails.guest_name} />
          {bookingDetails.guest_phone && <Row label="Phone" value={bookingDetails.guest_phone} />}
          {bookingDetails.guest_email && <Row label="Email" value={bookingDetails.guest_email} />}
          <Row label="Apartment" value={bookingDetails.unit_name ?? "—"} />
        </dl>

        <SectionLabel>Stay</SectionLabel>
        <dl className="space-y-2 text-sm">
          <Row label="Check-in" value={`${formatDate(bookingDetails.check_in)} · ${bookingDetails.checkin_time}`} />
          <Row label="Check-out" value={`${formatDate(bookingDetails.check_out)} · ${bookingDetails.checkout_time}`} />
          <Row label="Nights" value={String(bookingDetails.nights)} />
          <Row label="Guests" value={String(bookingDetails.guests)} />
        </dl>

        <SectionLabel>Payment</SectionLabel>
        <dl className="space-y-2 text-sm">
          <div className="flex items-start justify-between gap-3">
            <dt className="text-slate-400">Payment Status</dt>
            <dd><Badge tone={paymentStatusTone(bookingDetails.payment_status)}>{bookingDetails.payment_status}</Badge></dd>
          </div>
          <Row label="Total" value={`₹${bookingDetails.total_amount.toLocaleString("en-IN")}`} />
          <Row label="Paid" value={`₹${bookingDetails.paid_amount.toLocaleString("en-IN")}`} />
          <div className="flex items-start justify-between gap-3">
            <dt className="text-slate-400">Balance</dt>
            <dd className={balanceDue ? "font-semibold text-amber-700" : "font-medium text-slate-800"}>
              ₹{bookingDetails.balance_due.toLocaleString("en-IN")}
            </dd>
          </div>
        </dl>
      </Card>

      <Card className="!rounded-[14px] shadow-sm">
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
              {humanizedBody}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="secondary" onClick={onCopy}>
                {copyState === "copied" ? "Copied ✓" : "Copy Message"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  const subject = encodeURIComponent(confirmation.subject);
                  const body = encodeURIComponent(humanizedBody ?? confirmation.body);
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

function SectionLabel({ children }: { children: string }) {
  return <h3 className="mt-4 mb-2 text-[10px] font-bold tracking-wider text-slate-400/80 uppercase">{children}</h3>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-slate-400">{label}</dt>
      <dd className="text-right font-medium text-slate-800">{value}</dd>
    </div>
  );
}
