import { useEffect, useState } from "react";
import { api, extractErrorMessage, type AuditLogOut, type LoginEventOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, Table, Badge } from "../components/ui";

export default function AuditPage() {
  const [logs, setLogs] = useState<AuditLogOut[]>([]);
  const [logins, setLogins] = useState<LoginEventOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"logs" | "logins">("logs");

  useEffect(() => {
    Promise.all([api.get("/audit/logs"), api.get("/audit/logins")])
      .then(([l, li]) => {
        setLogs(l.data);
        setLogins(li.data);
      })
      .catch((err) => setError(extractErrorMessage(err)));
  }, []);

  return (
    <div>
      <PageHeader title="Audit & Security" />
      <ErrorBanner message={error} />

      <div className="flex gap-1 mb-6 border-b border-slate-200">
        <button
          onClick={() => setTab("logs")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${tab === "logs" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-400"}`}
        >
          Audit Logs
        </button>
        <button
          onClick={() => setTab("logins")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${tab === "logins" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-400"}`}
        >
          Login History
        </button>
      </div>

      {tab === "logs" ? (
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Audit Logs</h2>
          <p className="text-xs text-slate-400 mb-4">
            Covers settings changes and user role/activation changes — a targeted set of sensitive actions, not
            every endpoint in the system.
          </p>
          <div className="overflow-x-auto">
            <Table headers={["Action", "Entity", "Details", "When"]}>
              {logs.map((l) => (
                <tr key={l.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{l.action}</td>
                  <td className="px-4 py-2.5 text-slate-500">{l.entity_type ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500 max-w-md truncate">{l.details ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{new Date(l.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </Table>
          </div>
        </Card>
      ) : (
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Login History</h2>
          <div className="overflow-x-auto">
            <Table headers={["Email", "Result", "IP Address", "When"]}>
              {logins.map((l) => (
                <tr key={l.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{l.email_attempted}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={l.success ? "green" : "red"}>{l.success ? "success" : "failed"}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">{l.ip_address ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{new Date(l.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </Table>
          </div>
        </Card>
      )}
    </div>
  );
}
