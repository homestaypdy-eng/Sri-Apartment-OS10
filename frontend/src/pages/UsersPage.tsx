import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type UserOut, type SessionOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge } from "../components/ui";
import { useRealtimeRefresh } from "../lib/realtime";

// Decoded locally (not via AuthContext, which exposes only `role`) so this
// self-identification stays entirely within this file's approved scope.
// Mirrors the JWT-payload decode AuthContext.tsx already does for itself.
function getCurrentUserId(): string | null {
  const token = localStorage.getItem("access_token");
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload.sub ?? null;
  } catch {
    return null;
  }
}

export default function UsersPage() {
  const [users, setUsers] = useState<UserOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", email: "", password: "", role: "staff" });
  const [sessions, setSessions] = useState<SessionOut[]>([]);
  const currentUserId = getCurrentUserId();
  const activeAdminCount = users.filter((u) => u.role === "admin" && u.is_active).length;

  async function loadSessions() {
    try {
      const { data } = await api.get("/auth/sessions");
      setSessions(data);
    } catch {
      // best-effort
    }
  }

  async function handleRevokeSession(id: string) {
    try {
      await api.post(`/auth/sessions/${id}/revoke`);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      setSuccess("Session revoked.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleRevokeAllSessions() {
    try {
      await api.post("/auth/sessions/revoke-all");
      setSessions([]);
      setSuccess("All sessions revoked — you'll need to sign in again on other devices.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function loadUsers() {
    try {
      const { data } = await api.get("/users");
      setUsers(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useRealtimeRefresh(["users.changed"], loadUsers);

  useEffect(() => {
    loadUsers();
    loadSessions();
  }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/auth/register", form);
      setSuccess(`User "${form.name}" created.`);
      setForm({ name: "", email: "", password: "", role: "staff" });
      loadUsers();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function toggleActive(user: UserOut) {
    setError(null);
    try {
      await api.patch(`/users/${user.id}`, { is_active: !user.is_active });
      loadUsers();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function changeRole(user: UserOut, role: string) {
    setError(null);
    try {
      await api.patch(`/users/${user.id}`, { role });
      loadUsers();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader title="Users" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <Card className="mb-8 max-w-lg">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Add User</h2>
        <form onSubmit={handleCreate} className="space-y-3">
          <input
            required
            placeholder="Full name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <input
            required
            type="email"
            placeholder="Email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <input
            required
            type="password"
            placeholder="Temporary password (min 8 chars)"
            minLength={8}
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          >
            <option value="staff">Staff</option>
            <option value="manager">Manager</option>
            <option value="admin">Admin</option>
          </select>
          <Button type="submit">Create User</Button>
        </form>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-4">All Users</h2>
        <Table headers={["Name", "Email", "Role", "Status", "Actions"]}>
          {users.map((u) => {
            // UX-only guardrails mirroring the backend's authoritative
            // self-lockout / last-admin checks in users/router.py — these
            // just disable the controls with a reason; the server still
            // rejects the request either way if bypassed.
            const isSelf = u.id === currentUserId;
            const isLastActiveAdmin = u.role === "admin" && u.is_active && activeAdminCount === 1;
            const lockReason = isSelf
              ? "You cannot change your own role or deactivate your own account."
              : "At least one active admin must remain — promote another admin first.";
            const roleLocked = (isSelf || isLastActiveAdmin) && u.role === "admin";
            const deactivateLocked = u.is_active && (isSelf || isLastActiveAdmin);
            return (
              <tr key={u.id}>
                <td className="px-4 py-2.5 font-medium text-slate-800">
                  {u.name}
                  {isSelf && <span className="ml-1.5 text-xs font-normal text-slate-400">(You)</span>}
                </td>
                <td className="px-4 py-2.5 text-slate-500">{u.email}</td>
                <td className="px-4 py-2.5">
                  <select
                    value={u.role}
                    onChange={(e) => changeRole(u, e.target.value)}
                    disabled={roleLocked}
                    title={roleLocked ? lockReason : undefined}
                    className="rounded-md border border-slate-200 text-xs px-2 py-1 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <option value="staff">staff</option>
                    <option value="manager">manager</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td className="px-4 py-2.5">
                  <Badge tone={u.is_active ? "green" : "red"}>{u.is_active ? "active" : "deactivated"}</Badge>
                  {isLastActiveAdmin && (
                    <span className="ml-1.5">
                      <Badge tone="amber">last admin</Badge>
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <span title={deactivateLocked ? lockReason : undefined}>
                    <Button variant="secondary" onClick={() => toggleActive(u)} disabled={deactivateLocked}>
                      {u.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                  </span>
                </td>
              </tr>
            );
          })}
        </Table>
      </Card>

      <Card className="mt-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">My Sessions</h2>
          {sessions.length > 0 && (
            <Button variant="secondary" onClick={handleRevokeAllSessions}>Log Out All Devices</Button>
          )}
        </div>
        {sessions.length === 0 ? (
          <p className="text-xs text-slate-400">No active sessions.</p>
        ) : (
          <Table headers={["Device / Browser", "IP Address", "Started", "Last Active", "Actions"]}>
            {sessions.map((s) => (
              <tr key={s.id}>
                <td className="px-4 py-2.5 text-slate-500 max-w-[220px] truncate">{s.user_agent ?? "Unknown device"}</td>
                <td className="px-4 py-2.5 text-slate-500">{s.ip_address ?? "—"}</td>
                <td className="px-4 py-2.5 text-slate-500">{new Date(s.created_at).toLocaleString()}</td>
                <td className="px-4 py-2.5 text-slate-500">{new Date(s.last_used_at).toLocaleString()}</td>
                <td className="px-4 py-2.5">
                  <Button variant="secondary" onClick={() => handleRevokeSession(s.id)}>Revoke</Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
