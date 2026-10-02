import { useEffect, useState } from "react";
import { api, extractErrorMessage, type PromoCodeOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState, Spinner } from "../components/ui";
import { useAuth } from "../context/AuthContext";

/**
 * Admin CRUD for the promo codes the public website's checkout already
 * validates against (POST /api/v1/public/promo/validate). Discount values
 * live only in the database (PromoCode.value) — never hardcoded on the
 * website, which only ever sends a code string and receives back a
 * server-computed discount amount.
 */

const EMPTY_FORM = { code: "", discount_type: "percent" as "percent" | "flat", value: "", valid_from: "", valid_to: "", max_uses: "" };

export default function PromoCodesPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [codes, setCodes] = useState<PromoCodeOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ value: "", valid_to: "" });
  const [savingEdit, setSavingEdit] = useState(false);

  async function load() {
    try {
      const { data } = await api.get("/promo-codes");
      setCodes(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate() {
    setError(null);
    setSuccess(null);
    if (!form.code || !form.value || !form.valid_from || !form.valid_to) {
      setError("Code, value, valid-from, and valid-to are required.");
      return;
    }
    setCreating(true);
    try {
      await api.post("/promo-codes", {
        code: form.code.toUpperCase(),
        discount_type: form.discount_type,
        value: Number(form.value),
        valid_from: form.valid_from,
        valid_to: form.valid_to,
        max_uses: form.max_uses ? Number(form.max_uses) : undefined,
      });
      setSuccess(`Promo code ${form.code.toUpperCase()} created.`);
      setForm(EMPTY_FORM);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(promo: PromoCodeOut) {
    setError(null);
    try {
      await api.patch(`/promo-codes/${promo.id}`, { active: !promo.active });
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function startEditing(promo: PromoCodeOut) {
    setEditingId(promo.id);
    setEditForm({ value: String(promo.value), valid_to: promo.valid_to });
    setError(null);
    setSuccess(null);
  }

  function cancelEditing() {
    setEditingId(null);
  }

  async function handleSaveEdit(promo: PromoCodeOut) {
    setError(null);
    setSuccess(null);
    if (!editForm.value || !editForm.valid_to) {
      setError("Value and expiry date are required.");
      return;
    }
    setSavingEdit(true);
    try {
      await api.patch(`/promo-codes/${promo.id}`, { value: Number(editForm.value), valid_to: editForm.valid_to });
      setSuccess(`Promo code ${promo.code} updated.`);
      setEditingId(null);
      load();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSavingEdit(false);
    }
  }

  return (
    <div>
      <PageHeader title="Promo Codes" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      {isAdmin && (
        <Card className="mb-6">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">Create Promo Code</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <input
              aria-label="Promo code"
              placeholder="CODE"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm uppercase"
            />
            <select
              aria-label="Discount type"
              value={form.discount_type}
              onChange={(e) => setForm({ ...form, discount_type: e.target.value as "percent" | "flat" })}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="percent">Percent off</option>
              <option value="flat">Flat amount off</option>
            </select>
            <input
              type="number"
              min={0}
              max={form.discount_type === "percent" ? 100 : undefined}
              step="0.01"
              aria-label="Discount value"
              placeholder={form.discount_type === "percent" ? "e.g. 10 (%)" : "e.g. 500 (Rs.)"}
              value={form.value}
              onChange={(e) => setForm({ ...form, value: e.target.value })}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Valid from</label>
              <input type="date" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Valid to (expiry)</label>
              <input type="date" value={form.valid_to} onChange={(e) => setForm({ ...form, valid_to: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Max uses (optional)</label>
              <input
                type="number"
                min={1}
                step="1"
                placeholder="Unlimited"
                value={form.max_uses}
                onChange={(e) => setForm({ ...form, max_uses: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
          </div>
          <div className="mt-3">
            <Button onClick={handleCreate} disabled={creating}>
              {creating ? "Creating..." : "Create Promo Code"}
            </Button>
          </div>
        </Card>
      )}

      <Card>
        <h2 className="mb-4 text-sm font-semibold text-slate-900">All Promo Codes</h2>
        {loading ? (
          <Spinner label="Loading promo codes..." />
        ) : codes.length === 0 ? (
          <EmptyState message="No promo codes yet." />
        ) : (
          <div className="overflow-x-auto">
            <Table headers={["Code", "Discount", "Valid", "Uses", "Status", ...(isAdmin ? ["Actions"] : [])]}>
              {codes.map((c) => {
                const today = new Date().toISOString().slice(0, 10);
                const expired = c.valid_to < today;
                const isEditing = editingId === c.id;
                return (
                  <tr key={c.id}>
                    <td className="px-4 py-2.5 font-mono font-medium text-slate-800">{c.code}</td>
                    <td className="px-4 py-2.5 text-slate-500">
                      {isEditing ? (
                        <div className="flex items-center gap-1">
                          <input
                            type="number"
                            min={0}
                            max={c.discount_type === "percent" ? 100 : undefined}
                            step="0.01"
                            aria-label={`Discount value for ${c.code}`}
                            value={editForm.value}
                            onChange={(e) => setEditForm({ ...editForm, value: e.target.value })}
                            className="w-20 rounded border border-slate-300 px-2 py-1 text-xs"
                          />
                          <span>{c.discount_type === "percent" ? "%" : "Rs."}</span>
                        </div>
                      ) : c.discount_type === "percent" ? (
                        `${c.value}%`
                      ) : (
                        `Rs. ${c.value}`
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500">
                      {isEditing ? (
                        <div className="flex items-center gap-1 text-xs">
                          {c.valid_from} →{" "}
                          <input
                            type="date"
                            value={editForm.valid_to}
                            onChange={(e) => setEditForm({ ...editForm, valid_to: e.target.value })}
                            className="rounded border border-slate-300 px-2 py-1 text-xs"
                          />
                        </div>
                      ) : (
                        <>
                          {c.valid_from} → {c.valid_to}
                        </>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500">
                      {c.used_count}
                      {c.max_uses != null ? ` / ${c.max_uses}` : ""}
                    </td>
                    <td className="px-4 py-2.5">
                      {!c.active ? (
                        <Badge tone="slate">deactivated</Badge>
                      ) : expired ? (
                        <Badge tone="red">expired</Badge>
                      ) : (
                        <Badge tone="green">active</Badge>
                      )}
                    </td>
                    {isAdmin && (
                      <td className="px-4 py-2.5">
                        {isEditing ? (
                          <div className="flex gap-2">
                            <button onClick={() => handleSaveEdit(c)} disabled={savingEdit} className="text-xs font-semibold text-emerald-700 underline hover:text-emerald-900">
                              {savingEdit ? "Saving..." : "Save"}
                            </button>
                            <button onClick={cancelEditing} className="text-xs font-semibold text-slate-600 underline hover:text-slate-900">
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="flex gap-2">
                            <button onClick={() => handleToggleActive(c)} className="text-xs font-semibold text-slate-600 underline hover:text-slate-900">
                              {c.active ? "Deactivate" : "Activate"}
                            </button>
                            <button onClick={() => startEditing(c)} className="text-xs font-semibold text-slate-600 underline hover:text-slate-900">
                              Edit
                            </button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}
