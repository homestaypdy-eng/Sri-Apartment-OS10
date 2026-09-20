import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  extractErrorMessage,
  type UnitOut,
  type ZotelConfigStatus,
  type ZotelConnectivityReport,
  type ZotelReconciliationReport,
  type ZotelRoomMappingOut,
  type ZotelSyncLogDetail,
  type ZotelSyncLogOut,
  type ZotelUnmappedRoom,
} from "../lib/api";
import { Badge, Button, Card, Drawer, EmptyState, ErrorBanner, PageHeader, Spinner, SuccessBanner, Table, type BadgeTone } from "../components/ui";

function syncStatusTone(status: string): BadgeTone {
  if (status === "success") return "green";
  if (status === "partial") return "amber";
  return "red";
}

function outcomeTone(outcome: string): BadgeTone {
  if (outcome === "imported" || outcome === "updated") return "green";
  if (outcome === "skipped") return "slate";
  if (outcome === "unmapped") return "amber";
  return "red"; // conflicted / failed
}

function reconciliationTone(category: string): BadgeTone {
  if (category === "matched") return "green";
  if (category === "missing_in_os10" || category === "missing_in_zotel") return "amber";
  return "red"; // duplicate / conflicting / cancelled_mismatch / error
}

export default function ZotelPage() {
  const [status, setStatus] = useState<ZotelConfigStatus | null>(null);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [mappings, setMappings] = useState<ZotelRoomMappingOut[]>([]);
  const [unmapped, setUnmapped] = useState<ZotelUnmappedRoom[]>([]);
  const [syncLogs, setSyncLogs] = useState<ZotelSyncLogOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [connectivity, setConnectivity] = useState<ZotelConnectivityReport | null>(null);
  const [testingConnectivity, setTestingConnectivity] = useState(false);

  const [syncing, setSyncing] = useState(false);

  const [reconciliation, setReconciliation] = useState<ZotelReconciliationReport | null>(null);
  const [reconciling, setReconciling] = useState(false);

  const [showMappingForm, setShowMappingForm] = useState(false);
  const [mappingForm, setMappingForm] = useState({ unit_id: "", zotel_room_type_id: "", zotel_room_type_name: "" });

  const [selectedLog, setSelectedLog] = useState<ZotelSyncLogDetail | null>(null);
  const [loadingLogDetail, setLoadingLogDetail] = useState(false);

  async function loadAll() {
    setError(null);
    try {
      const [statusRes, unitsRes, mappingsRes, unmappedRes, logsRes] = await Promise.all([
        api.get("/zotel/status"),
        api.get("/units"),
        api.get("/zotel/mappings"),
        api.get("/zotel/unmapped"),
        api.get("/zotel/sync/logs"),
      ]);
      setStatus(statusRes.data);
      setUnits(unitsRes.data);
      setMappings(mappingsRes.data);
      setUnmapped(unmappedRes.data);
      setSyncLogs(logsRes.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  function unitLabel(unitId: string): string {
    const unit = units.find((u) => u.id === unitId);
    return unit ? `${unit.name}${unit.unit_code ? ` (${unit.unit_code})` : ""}` : unitId;
  }

  const mappedUnitIds = new Set(mappings.map((m) => m.unit_id));
  const unmappedUnits = units.filter((u) => u.is_active && !mappedUnitIds.has(u.id));

  async function handleRunConnectivityTest() {
    setError(null);
    setSuccess(null);
    setTestingConnectivity(true);
    setConnectivity(null);
    try {
      const { data } = await api.post("/zotel/connectivity-test");
      setConnectivity(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setTestingConnectivity(false);
    }
  }

  async function handleTriggerSync() {
    setError(null);
    setSuccess(null);
    setSyncing(true);
    try {
      const { data } = await api.post("/zotel/sync/bookings");
      setSuccess(`Sync complete: ${data.records_imported} imported, ${data.records_updated} updated, ${data.records_skipped} skipped, ${data.records_unmapped} unmapped, ${data.records_conflicted} conflicted, ${data.records_failed} failed.`);
      await loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSyncing(false);
    }
  }

  async function handleRunReconciliation() {
    setError(null);
    setSuccess(null);
    setReconciling(true);
    setReconciliation(null);
    try {
      const { data } = await api.get("/zotel/reconciliation");
      setReconciliation(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setReconciling(false);
    }
  }

  async function handleCreateMapping(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/zotel/mappings", {
        unit_id: mappingForm.unit_id,
        zotel_room_type_id: mappingForm.zotel_room_type_id,
        zotel_room_type_name: mappingForm.zotel_room_type_name || null,
      });
      setSuccess("Room mapping created.");
      setMappingForm({ unit_id: "", zotel_room_type_id: "", zotel_room_type_name: "" });
      setShowMappingForm(false);
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeleteMapping(mapping: ZotelRoomMappingOut) {
    if (!window.confirm(`Remove the mapping between ${unitLabel(mapping.unit_id)} and Zotel room type "${mapping.zotel_room_type_id}"? Existing imported bookings are not affected — only future syncs stop matching this room.`)) return;
    setError(null);
    try {
      await api.delete(`/zotel/mappings/${mapping.id}`);
      setSuccess("Mapping removed.");
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleViewLog(log: ZotelSyncLogOut) {
    setLoadingLogDetail(true);
    setError(null);
    try {
      const { data } = await api.get(`/zotel/sync/logs/${log.id}`);
      setSelectedLog(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoadingLogDetail(false);
    }
  }

  if (loading) {
    return (
      <div>
        <PageHeader title="Zotel Integration" />
        <Spinner label="Loading Zotel integration status..." />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Zotel Integration" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <Card>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-slate-900">Connection Status</h2>
            {status?.configured ? <Badge tone="green">Configured</Badge> : <Badge tone="slate">Not Configured</Badge>}
          </div>
          {status && (
            <ul className="text-sm text-slate-600 space-y-1 mb-4">
              <li>Base URL: {status.base_url_set ? <Badge tone="green">Set</Badge> : <Badge tone="slate">Not set</Badge>}</li>
              <li>Hotel ID: {status.hotel_id_set ? <Badge tone="green">Set</Badge> : <Badge tone="slate">Not set</Badge>}</li>
              <li>API Key: {status.api_key_set ? <Badge tone="green">Set</Badge> : <Badge tone="slate">Not set</Badge>}</li>
              <li>API Secret: {status.api_secret_set ? <Badge tone="green">Set</Badge> : <Badge tone="slate">Not set</Badge>}</li>
            </ul>
          )}
          <p className="text-xs text-slate-400 mb-3">
            Credential values are never shown here or anywhere in OS10 — only whether each is configured. Set ZOTEL_BASE_URL /
            ZOTEL_HOTEL_ID / ZOTEL_API_KEY / ZOTEL_API_SECRET as environment variables to configure Zotel.
          </p>
          <Button onClick={handleRunConnectivityTest} disabled={testingConnectivity} variant="secondary">
            {testingConnectivity ? "Testing..." : "Run Connectivity Test"}
          </Button>

          {connectivity && (
            <div className="mt-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-slate-700">Overall:</span>
                {connectivity.overall_passed ? <Badge tone="green">PASS</Badge> : <Badge tone="red">FAIL</Badge>}
              </div>
              {connectivity.checks.map((c) => (
                <div key={c.category} className="flex items-start gap-2 text-sm border-t border-slate-100 pt-2">
                  {c.passed ? <Badge tone="green">PASS</Badge> : <Badge tone="red">FAIL</Badge>}
                  <div>
                    <span className="font-medium text-slate-800 capitalize">{c.category}</span>
                    <p className="text-slate-500">{c.message}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-3">Bookings Sync</h2>
          <p className="text-sm text-slate-500 mb-4">
            Fetches reservations from Zotel and imports them into OS10 as bookings, keyed by Zotel's reservation ID so repeated
            syncs never create duplicates. Read-only against Zotel — no reservation, availability, or rate is ever pushed out.
          </p>
          <Button onClick={handleTriggerSync} disabled={syncing || !status?.configured}>
            {syncing ? "Syncing..." : "Trigger Bookings Sync"}
          </Button>
          {!status?.configured && <p className="text-xs text-slate-400 mt-2">Configure Zotel credentials before syncing.</p>}
        </Card>
      </div>

      <Card className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-900">Room Mappings</h2>
          <Button onClick={() => setShowMappingForm(true)} variant="secondary">Add Mapping</Button>
        </div>
        {mappings.length === 0 ? (
          <EmptyState message="No Zotel rooms mapped yet. Add a mapping to enable bookings sync for that unit." />
        ) : (
          <Table headers={["OS10 Unit", "Zotel Room Type ID", "Zotel Room Name", "Mapped On", ""]}>
            {mappings.map((m) => (
              <tr key={m.id}>
                <td className="px-4 py-2.5">{unitLabel(m.unit_id)}</td>
                <td className="px-4 py-2.5 font-mono text-xs text-slate-500">{m.zotel_room_type_id}</td>
                <td className="px-4 py-2.5">{m.zotel_room_type_name || "—"}</td>
                <td className="px-4 py-2.5 text-slate-500">{new Date(m.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-2.5">
                  <Button variant="danger" onClick={() => handleDeleteMapping(m)}>Delete</Button>
                </td>
              </tr>
            ))}
          </Table>
        )}

        {unmappedUnits.length > 0 && (
          <p className="text-xs text-slate-400 mt-3">
            {unmappedUnits.length} OS10 unit{unmappedUnits.length === 1 ? "" : "s"} not yet mapped to a Zotel room:{" "}
            {unmappedUnits.map((u) => u.name).join(", ")}.
          </p>
        )}

        {unmapped.length > 0 && (
          <div className="mt-4 border-t border-slate-100 pt-4">
            <h3 className="text-xs font-semibold text-amber-700 mb-2">Unmapped Zotel Rooms Seen In Sync History</h3>
            <p className="text-xs text-slate-500 mb-2">
              These Zotel room type IDs appeared in fetched reservations but have no OS10 unit mapping — those reservations were
              not imported. Add a mapping above using the exact ID shown here to import them on the next sync.
            </p>
            <ul className="text-sm space-y-1">
              {unmapped.map((u) => (
                <li key={u.zotel_room_type_id} className="flex items-center gap-2">
                  <Badge tone="amber">Unmapped</Badge>
                  <span className="font-mono text-xs text-slate-600">{u.zotel_room_type_id}</span>
                  <span className="text-xs text-slate-400">first seen in reservation {u.first_seen_in_reservation}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      <Card className="mb-6">
        <h2 className="text-sm font-semibold text-slate-900 mb-3">Sync Logs</h2>
        {syncLogs.length === 0 ? (
          <EmptyState message="No sync runs yet." />
        ) : (
          <Table headers={["When", "Type", "Status", "Fetched", "Imported", "Updated", "Skipped", "Unmapped", "Conflicted", "Failed", ""]}>
            {syncLogs.map((log) => (
              <tr key={log.id}>
                <td className="px-4 py-2.5 text-slate-500">{new Date(log.created_at).toLocaleString()}</td>
                <td className="px-4 py-2.5">{log.sync_type.replace(/_/g, " ")}</td>
                <td className="px-4 py-2.5"><Badge tone={syncStatusTone(log.status)}>{log.status}</Badge></td>
                <td className="px-4 py-2.5">{log.records_fetched}</td>
                <td className="px-4 py-2.5">{log.records_imported}</td>
                <td className="px-4 py-2.5">{log.records_updated}</td>
                <td className="px-4 py-2.5">{log.records_skipped}</td>
                <td className="px-4 py-2.5">{log.records_unmapped > 0 ? <Badge tone="amber">{log.records_unmapped}</Badge> : 0}</td>
                <td className="px-4 py-2.5">{log.records_conflicted > 0 ? <Badge tone="red">{log.records_conflicted}</Badge> : 0}</td>
                <td className="px-4 py-2.5">{log.records_failed > 0 ? <Badge tone="red">{log.records_failed}</Badge> : 0}</td>
                <td className="px-4 py-2.5">
                  <Button variant="secondary" onClick={() => handleViewLog(log)}>View</Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-900">Reconciliation</h2>
          <Button onClick={handleRunReconciliation} disabled={reconciling || !status?.configured} variant="secondary">
            {reconciling ? "Checking..." : "Run Reconciliation"}
          </Button>
        </div>
        <p className="text-sm text-slate-500 mb-4">
          Compares Zotel's current reservations against OS10 bookings — read-only, makes no changes to either system. Anything
          flagged here needs a person to look at it; nothing is auto-fixed.
        </p>
        {reconciliation && (
          <div>
            <div className="flex flex-wrap gap-2 mb-4">
              {Object.entries(reconciliation.summary).map(([category, count]) => (
                <Badge key={category} tone={reconciliationTone(category)}>{category.replace(/_/g, " ")}: {count}</Badge>
              ))}
            </div>
            {reconciliation.entries.length === 0 ? (
              <EmptyState message="No reservations returned by Zotel to reconcile." />
            ) : (
              <Table headers={["Zotel Reservation", "Category", "Detail"]}>
                {reconciliation.entries.map((entry, i) => (
                  <tr key={`${entry.external_reservation_id}-${i}`}>
                    <td className="px-4 py-2.5 font-mono text-xs">{entry.external_reservation_id || "—"}</td>
                    <td className="px-4 py-2.5"><Badge tone={reconciliationTone(entry.category)}>{entry.category.replace(/_/g, " ")}</Badge></td>
                    <td className="px-4 py-2.5 text-slate-500">{entry.detail}</td>
                  </tr>
                ))}
              </Table>
            )}
          </div>
        )}
      </Card>

      {showMappingForm && (
        <Drawer title="Add Room Mapping" onClose={() => setShowMappingForm(false)}>
          <form onSubmit={handleCreateMapping} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">OS10 Unit</label>
              <select
                required
                value={mappingForm.unit_id}
                onChange={(e) => setMappingForm({ ...mappingForm, unit_id: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">Select a unit...</option>
                {units.filter((u) => u.is_active).map((u) => (
                  <option key={u.id} value={u.id} disabled={mappedUnitIds.has(u.id)}>
                    {u.name}{u.unit_code ? ` (${u.unit_code})` : ""}{mappedUnitIds.has(u.id) ? " — already mapped" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Zotel Room Type ID</label>
              <input
                required
                placeholder="e.g. the room_type_id from Zotel's availability/prices response"
                value={mappingForm.zotel_room_type_id}
                onChange={(e) => setMappingForm({ ...mappingForm, zotel_room_type_id: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Zotel Room Name (optional, for display only)</label>
              <input
                placeholder="e.g. Deluxe Room"
                value={mappingForm.zotel_room_type_name}
                onChange={(e) => setMappingForm({ ...mappingForm, zotel_room_type_name: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <Button type="submit">Save Mapping</Button>
          </form>
        </Drawer>
      )}

      {selectedLog && (
        <Drawer title={`Sync Log — ${new Date(selectedLog.created_at).toLocaleString()}`} onClose={() => setSelectedLog(null)}>
          {loadingLogDetail ? (
            <Spinner />
          ) : (
            <div>
              <div className="flex items-center gap-2 mb-4">
                <Badge tone={syncStatusTone(selectedLog.status)}>{selectedLog.status}</Badge>
                <span className="text-sm text-slate-500">{selectedLog.message}</span>
              </div>
              {selectedLog.records.length === 0 ? (
                <EmptyState message="No per-reservation records for this run." />
              ) : (
                <div className="space-y-2">
                  {selectedLog.records.map((r) => (
                    <div key={r.id} className="border border-slate-200 rounded-lg p-3 text-sm">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-mono text-xs text-slate-500">{r.external_reservation_id}</span>
                        <Badge tone={outcomeTone(r.outcome)}>{r.outcome}</Badge>
                      </div>
                      {r.detail && <p className="text-xs text-slate-500">{r.detail}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </Drawer>
      )}
    </div>
  );
}
