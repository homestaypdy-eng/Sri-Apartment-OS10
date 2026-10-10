import { useEffect, useState } from "react";
import { api, extractErrorMessage, type NotificationLogOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useRealtimeRefresh } from "../lib/realtime";

export default function NotificationsPage() {
  const { role } = useAuth();
  const [logs, setLogs] = useState<NotificationLogOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  async function load() {
    try {
      const { data } = await api.get("/notifications/logs");
      setLogs(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useRealtimeRefresh(["notifications.changed", "bookings.changed", "inquiries.changed", "guest_requests.changed", "maintenance.changed"], load);

  useEffect(() => {
    load();
  }, []);

  async function handleScan() {
    setError(null);
    setSuccess(null);
    setScanning(true);
    try {
      const { data } = await api.post("/notifications/scan-reminders");
      setSuccess(
        `Scan complete: ${data.checkin_reminder} check-in, ${data.checkout_reminder} check-out, ` +
          `${data.review_request} review, ${data.payment_reminder} payment reminder(s) sent.`
      );
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setScanning(false);
    }
  }

  return (
    <div>
      <PageHeader title="Notifications" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <Card className="mb-6 bg-amber-50 border-amber-200">
        <p className="text-xs text-amber-800">
          Automatic email reminders (check-in, check-out, review request, payment due) only run if the background
          scheduler is enabled on the server. Email sending itself requires SMTP credentials to be configured —
          without them, sends are logged as failed with a clear "not configured" message rather than silently
          disappearing. WhatsApp sending (see Inquiries) always requires a manual click — WhatsApp's own
          click-to-chat links can't be triggered automatically.
        </p>
      </Card>

      {role === "admin" && (
        <Card className="mb-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">Manual Reminder Scan</h2>
              <p className="text-xs text-slate-400 mt-1">
                Runs the same check the automatic scheduler does — useful to trigger on demand or verify it's working.
              </p>
            </div>
            <Button onClick={handleScan} disabled={scanning}>
              {scanning ? "Scanning..." : "Scan Now"}
            </Button>
          </div>
        </Card>
      )}

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Notification Log</h2>
        {logs.length === 0 ? (
          <EmptyState message="No notifications yet." />
        ) : (
          <div className="overflow-x-auto">
            <Table headers={["Event", "Channel", "Recipient", "Status", "When"]}>
              {logs.map((l) => (
                <tr key={l.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{l.event_type.replace("_", " ")}</td>
                  <td className="px-4 py-2.5 text-slate-500">{l.channel}</td>
                  <td className="px-4 py-2.5 text-slate-500">{l.recipient}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={l.success ? "green" : "red"}>{l.success ? "sent" : "failed"}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">{new Date(l.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}
