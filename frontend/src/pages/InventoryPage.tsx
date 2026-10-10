import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  extractErrorMessage,
  INVENTORY_CATEGORIES,
  type InventoryItemOut,
  type InventoryReport,
  type SupplierOut,
  type StockMovementOut,
} from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState } from "../components/ui";
import { useRealtimeRefresh } from "../lib/realtime";

export default function InventoryPage() {
  const [items, setItems] = useState<InventoryItemOut[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierOut[]>([]);
  const [report, setReport] = useState<InventoryReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [lowStockOnly, setLowStockOnly] = useState(false);

  const [itemForm, setItemForm] = useState({ name: "", category: "other", minimum_stock: "5", unit_of_measure: "pcs", brand: "", expiry_date: "", sku: "" });
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [editingItem, setEditingItem] = useState<InventoryItemOut | null>(null);
  const [editForm, setEditForm] = useState({ minimum_stock: "", brand: "", expiry_date: "", unit_cost: "", sku: "" });
  const [movementItemId, setMovementItemId] = useState("");
  const [movementQty, setMovementQty] = useState("");
  const [movementReason, setMovementReason] = useState("");
  const [movementCost, setMovementCost] = useState("");
  const [movementSupplierId, setMovementSupplierId] = useState("");
  const [supplierForm, setSupplierForm] = useState({ name: "", phone: "", email: "" });
  const [historyItem, setHistoryItem] = useState<InventoryItemOut | null>(null);
  const [movements, setMovements] = useState<StockMovementOut[]>([]);

  async function loadAll() {
    try {
      const [i, s, r] = await Promise.all([
        api.get("/inventory/items", { params: lowStockOnly ? { low_stock_only: true } : {} }),
        api.get("/inventory/suppliers"),
        api.get("/inventory/reports/summary"),
      ]);
      setItems(i.data);
      setSuppliers(s.data);
      setReport(r.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useRealtimeRefresh(["inventory.changed", "finance.changed", "housekeeping.changed"], loadAll);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lowStockOnly]);

  async function handleCreateItem(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/inventory/items", {
        name: itemForm.name,
        category: itemForm.category,
        minimum_stock: Number(itemForm.minimum_stock),
        unit_of_measure: itemForm.unit_of_measure,
        brand: itemForm.brand || undefined,
        expiry_date: itemForm.expiry_date || undefined,
        sku: itemForm.sku || undefined,
      });
      setSuccess("Item created.");
      setItemForm({ name: "", category: "other", minimum_stock: "5", unit_of_measure: "pcs", brand: "", expiry_date: "", sku: "" });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function openEdit(item: InventoryItemOut) {
    setEditingItem(item);
    setEditForm({
      minimum_stock: String(item.minimum_stock),
      brand: item.brand ?? "",
      expiry_date: item.expiry_date ?? "",
      unit_cost: item.unit_cost != null ? String(item.unit_cost) : "",
      sku: item.sku ?? "",
    });
  }

  async function handleSaveEdit() {
    if (!editingItem) return;
    setError(null);
    setSuccess(null);
    try {
      await api.patch(`/inventory/items/${editingItem.id}`, {
        minimum_stock: Number(editForm.minimum_stock),
        brand: editForm.brand || null,
        expiry_date: editForm.expiry_date || null,
        unit_cost: editForm.unit_cost ? Number(editForm.unit_cost) : null,
        sku: editForm.sku || null,
      });
      setSuccess("Item updated.");
      setEditingItem(null);
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleStockMovement(type: "stock-in" | "stock-out") {
    setError(null);
    setSuccess(null);
    if (!movementItemId || !movementQty) {
      setError("Select an item and enter a quantity.");
      return;
    }
    try {
      await api.post(`/inventory/${type}`, {
        item_id: movementItemId,
        quantity: Number(movementQty),
        reason: movementReason || undefined,
        unit_cost: movementCost ? Number(movementCost) : undefined,
        supplier_id: type === "stock-in" && movementSupplierId ? movementSupplierId : undefined,
      });
      setSuccess(`${type === "stock-in" ? "Stock added" : "Stock removed"} successfully.`);
      setMovementQty("");
      setMovementReason("");
      setMovementCost("");
      setMovementSupplierId("");
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function openHistory(item: InventoryItemOut) {
    setHistoryItem(item);
    setError(null);
    try {
      const { data } = await api.get(`/inventory/items/${item.id}/movements`);
      setMovements(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function supplierName(id: string | null) {
    if (!id) return "—";
    return suppliers.find((s) => s.id === id)?.name ?? "—";
  }

  async function handleCreateSupplier(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/inventory/suppliers", supplierForm);
      setSuccess("Supplier added.");
      setSupplierForm({ name: "", phone: "", email: "" });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  const filteredItems = items
    .filter((item) => !categoryFilter || item.category === categoryFilter)
    .filter((item) => !search || item.name.toLowerCase().includes(search.toLowerCase()) || (item.brand ?? "").toLowerCase().includes(search.toLowerCase()) || (item.sku ?? "").toLowerCase().includes(search.toLowerCase()));

  return (
    <div>
      <PageHeader title="Inventory" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      {report && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-6">
          <Card>
            <p className="text-xs text-slate-500 mb-1">Total Items</p>
            <p className="text-xl font-semibold text-slate-900">{report.total_items}</p>
          </Card>
          <Card>
            <p className="text-xs text-slate-500 mb-1">Low Stock</p>
            <p className="text-xl font-semibold text-red-600">{report.low_stock_count}</p>
          </Card>
          <Card className="col-span-2 sm:col-span-1">
            <p className="text-xs text-slate-500 mb-1">Stock Value</p>
            <p className="text-xl font-semibold text-slate-900">₹{report.total_stock_value.toLocaleString()}</p>
          </Card>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Item</h2>
          <form onSubmit={handleCreateItem} className="space-y-3">
            <input
              required
              placeholder="Item name"
              value={itemForm.name}
              onChange={(e) => setItemForm({ ...itemForm, name: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <div className="grid grid-cols-2 gap-3">
              <select
                value={itemForm.category}
                onChange={(e) => setItemForm({ ...itemForm, category: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                {INVENTORY_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c.replace("_", " ")}
                  </option>
                ))}
              </select>
              <input
                placeholder="Unit (pcs/kg/box)"
                value={itemForm.unit_of_measure}
                onChange={(e) => setItemForm({ ...itemForm, unit_of_measure: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <input
              type="number"
              placeholder="Minimum stock threshold"
              value={itemForm.minimum_stock}
              onChange={(e) => setItemForm({ ...itemForm, minimum_stock: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <div className="grid grid-cols-2 gap-3">
              <input
                placeholder="Brand (optional)"
                value={itemForm.brand}
                onChange={(e) => setItemForm({ ...itemForm, brand: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                placeholder="SKU (optional)"
                value={itemForm.sku}
                onChange={(e) => setItemForm({ ...itemForm, sku: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <input
              type="date"
              placeholder="Expiry date (optional)"
              value={itemForm.expiry_date}
              onChange={(e) => setItemForm({ ...itemForm, expiry_date: e.target.value })}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <Button type="submit">Create Item</Button>
          </form>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Stock In / Stock Out</h2>
          <div className="space-y-3">
            <select
              value={movementItemId}
              onChange={(e) => setMovementItemId(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Select item...</option>
              {items.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name} (current: {i.current_stock})
                </option>
              ))}
            </select>
            <div className="grid grid-cols-2 gap-3">
              <input
                type="number"
                placeholder="Quantity"
                value={movementQty}
                onChange={(e) => setMovementQty(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                type="number"
                placeholder="Unit cost (for stock-in)"
                value={movementCost}
                onChange={(e) => setMovementCost(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <select
              value={movementSupplierId}
              onChange={(e) => setMovementSupplierId(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Supplier (for stock-in purchases)...</option>
              {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <p className="text-xs text-slate-400">Giving both a supplier and unit cost on Stock In records it as a real purchase in the Finance Purchase Register.</p>
            <input
              placeholder="Reason (optional)"
              value={movementReason}
              onChange={(e) => setMovementReason(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <div className="flex gap-3">
              <Button onClick={() => handleStockMovement("stock-in")}>Stock In</Button>
              <Button variant="secondary" onClick={() => handleStockMovement("stock-out")}>
                Stock Out
              </Button>
            </div>
          </div>
        </Card>
      </div>

      <Card className="mb-8">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Supplier</h2>
        <form onSubmit={handleCreateSupplier} className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <input
            required
            placeholder="Supplier name"
            value={supplierForm.name}
            onChange={(e) => setSupplierForm({ ...supplierForm, name: e.target.value })}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <input
            placeholder="Phone"
            value={supplierForm.phone}
            onChange={(e) => setSupplierForm({ ...supplierForm, phone: e.target.value })}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <div className="flex gap-2">
            <input
              placeholder="Email"
              value={supplierForm.email}
              onChange={(e) => setSupplierForm({ ...supplierForm, email: e.target.value })}
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <Button type="submit">Add</Button>
          </div>
        </form>
        {suppliers.length > 0 && (
          <p className="text-xs text-slate-400 mt-3">{suppliers.map((s) => s.name).join(", ")}</p>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-slate-900">Items</h2>
          <div className="flex items-center gap-3 flex-wrap">
            <input
              placeholder="Search items..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
            />
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
            >
              <option value="">All categories</option>
              {INVENTORY_CATEGORIES.map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}
            </select>
            <label className="flex items-center gap-2 text-xs text-slate-500">
              <input type="checkbox" checked={lowStockOnly} onChange={(e) => setLowStockOnly(e.target.checked)} />
              Low stock only
            </label>
          </div>
        </div>
        {filteredItems.length === 0 ? (
          <EmptyState message={items.length === 0 ? "No inventory items yet." : "No items match these filters."} />
        ) : (
          <div className="overflow-x-auto">
            <Table headers={["Name", "SKU", "Brand", "Category", "Stock", "Min", "Unit Cost", "Expiry", "Status", "Actions"]}>
              {filteredItems.map((item) => (
                <tr key={item.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{item.name}</td>
                  <td className="px-4 py-2.5 text-slate-500">{item.sku ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{item.brand ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{item.category.replace("_", " ")}</td>
                  <td className="px-4 py-2.5 text-slate-500">
                    {item.current_stock} {item.unit_of_measure}
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">{item.minimum_stock}</td>
                  <td className="px-4 py-2.5 text-slate-500">{item.unit_cost ? `₹${item.unit_cost}` : "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{item.expiry_date ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={item.is_low_stock ? "red" : "green"}>{item.is_low_stock ? "low stock" : "ok"}</Badge>
                  </td>
                  <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                    <button onClick={() => openEdit(item)} className="text-xs text-slate-600 hover:underline">Edit</button>
                    <button onClick={() => openHistory(item)} className="text-xs text-slate-600 hover:underline">History</button>
                  </td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>

      {historyItem && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setHistoryItem(null)}>
          <div className="bg-white rounded-xl max-w-2xl w-full max-h-[80vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Stock Movement History — {historyItem.name}</h2>
              <button onClick={() => setHistoryItem(null)} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
            </div>
            {movements.length === 0 ? (
              <EmptyState message="No stock movements recorded yet for this item." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-400 border-b border-slate-100">
                      <th className="py-2 pr-3">Date</th>
                      <th className="py-2 pr-3">Type</th>
                      <th className="py-2 pr-3">Qty</th>
                      <th className="py-2 pr-3">Supplier</th>
                      <th className="py-2 pr-3">Unit Cost</th>
                      <th className="py-2 pr-3">Reason</th>
                      <th className="py-2">Linked To</th>
                    </tr>
                  </thead>
                  <tbody>
                    {movements.map((m) => (
                      <tr key={m.id} className="border-b border-slate-50">
                        <td className="py-2 pr-3 text-slate-500">{new Date(m.created_at).toLocaleDateString()}</td>
                        <td className="py-2 pr-3">
                          <Badge tone={m.movement_type === "stock_in" ? "green" : "amber"}>{m.movement_type === "stock_in" ? "Stock In" : "Stock Out"}</Badge>
                        </td>
                        <td className="py-2 pr-3 font-medium text-slate-800">{m.movement_type === "stock_in" ? "+" : "-"}{m.quantity}</td>
                        <td className="py-2 pr-3 text-slate-500">{supplierName(m.supplier_id)}</td>
                        <td className="py-2 pr-3 text-slate-500">{m.unit_cost ? `₹${m.unit_cost}` : "—"}</td>
                        <td className="py-2 pr-3 text-slate-500">{m.reason ?? "—"}</td>
                        <td className="py-2 text-slate-500">
                          {m.housekeeping_task_id ? "Housekeeping" : m.maintenance_ticket_id ? "Maintenance" : m.linked_purchase_record_id ? "Purchase Register" : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {editingItem && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setEditingItem(null)}>
          <div className="bg-white rounded-xl max-w-md w-full p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Edit {editingItem.name}</h2>
              <button onClick={() => setEditingItem(null)} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Minimum Stock</label>
                <input type="number" value={editForm.minimum_stock} onChange={(e) => setEditForm({ ...editForm, minimum_stock: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Brand</label>
                <input value={editForm.brand} onChange={(e) => setEditForm({ ...editForm, brand: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">SKU</label>
                <input value={editForm.sku} onChange={(e) => setEditForm({ ...editForm, sku: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Expiry Date</label>
                <input type="date" value={editForm.expiry_date} onChange={(e) => setEditForm({ ...editForm, expiry_date: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Unit Cost (₹)</label>
                <input type="number" value={editForm.unit_cost} onChange={(e) => setEditForm({ ...editForm, unit_cost: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>
              <Button onClick={handleSaveEdit}>Save Changes</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
