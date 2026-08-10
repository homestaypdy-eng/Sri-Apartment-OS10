import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  extractErrorMessage,
  estimateGstSlab,
  type PropertyOut,
  type UnitOut,
  type SeasonalRateOut,
  type LongStayDiscountTierOut,
  type PropertyFileOut,
  type PropertyPnL,
  type UnitPhotoOut,
} from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge } from "../components/ui";
import { useAuth } from "../context/AuthContext";

export default function PropertiesPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";

  const [properties, setProperties] = useState<PropertyOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [propForm, setPropForm] = useState({ name: "", address: "", gst_number: "", state_code: "", property_code: "", property_type: "", owner_name: "", owner_phone: "", owner_email: "", google_maps_url: "" });
  const [unitForm, setUnitForm] = useState({ property_id: "", name: "", base_rate: "", weekend_rate: "", unit_code: "", floor: "", flat_number: "", amenities: "" });

  const [pricingUnitId, setPricingUnitId] = useState("");
  const [seasonalRates, setSeasonalRates] = useState<SeasonalRateOut[]>([]);
  const [discountTiers, setDiscountTiers] = useState<LongStayDiscountTierOut[]>([]);
  const [seasonalForm, setSeasonalForm] = useState({ name: "", start_date: "", end_date: "", rate_per_night: "" });
  const [tierForm, setTierForm] = useState({ min_nights: "", discount_percent: "" });
  const [propSearch, setPropSearch] = useState("");
  const [manageProp, setManageProp] = useState<PropertyOut | null>(null);
  const [manageForm, setManageForm] = useState<Record<string, string>>({});
  const [propFiles, setPropFiles] = useState<PropertyFileOut[]>([]);
  const [fileForm, setFileForm] = useState({ file_type: "document", caption: "" });
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [propPnl, setPropPnl] = useState<PropertyPnL | null>(null);
  const [pnlYear, setPnlYear] = useState(new Date().getFullYear());
  const [pnlMonth, setPnlMonth] = useState(new Date().getMonth() + 1);
  const [unitSearch, setUnitSearch] = useState("");
  const [manageUnit, setManageUnit] = useState<UnitOut | null>(null);
  const [manageUnitForm, setManageUnitForm] = useState<Record<string, string>>({});
  const [unitPhotos, setUnitPhotos] = useState<UnitPhotoOut[]>([]);
  const [unitPhotoCaption, setUnitPhotoCaption] = useState("");
  const [unitUploadFile, setUnitUploadFile] = useState<File | null>(null);
  const [unitMaintTickets, setUnitMaintTickets] = useState<{ id: string; description: string; status: string; priority: string }[]>([]);
  const [unitHkTasks, setUnitHkTasks] = useState<{ id: string; task_type: string; status: string }[]>([]);

  async function loadAll() {
    try {
      const [p, u] = await Promise.all([api.get("/properties"), api.get("/units")]);
      setProperties(p.data);
      setUnits(u.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  async function loadPricingRules(unitId: string) {
    if (!unitId) {
      setSeasonalRates([]);
      setDiscountTiers([]);
      return;
    }
    try {
      const [s, t] = await Promise.all([
        api.get(`/units/${unitId}/seasonal-rates`),
        api.get(`/units/${unitId}/long-stay-discounts`),
      ]);
      setSeasonalRates(s.data);
      setDiscountTiers(t.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    loadPricingRules(pricingUnitId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pricingUnitId]);

  async function handleCreateProperty(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/properties", {
        ...propForm,
        property_code: propForm.property_code || undefined,
        property_type: propForm.property_type || undefined,
        owner_name: propForm.owner_name || undefined,
        owner_phone: propForm.owner_phone || undefined,
        owner_email: propForm.owner_email || undefined,
        google_maps_url: propForm.google_maps_url || undefined,
      });
      setSuccess("Property created.");
      setPropForm({ name: "", address: "", gst_number: "", state_code: "", property_code: "", property_type: "", owner_name: "", owner_phone: "", owner_email: "", google_maps_url: "" });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function openManage(prop: PropertyOut) {
    setManageProp(prop);
    setManageForm({
      name: prop.name, address: prop.address, gst_number: prop.gst_number ?? "", state_code: prop.state_code,
      property_code: prop.property_code ?? "", property_type: prop.property_type ?? "",
      owner_name: prop.owner_name ?? "", owner_phone: prop.owner_phone ?? "", owner_email: prop.owner_email ?? "",
      google_maps_url: prop.google_maps_url ?? "",
    });
    setError(null);
    try {
      const [f, p] = await Promise.all([
        api.get(`/properties/${prop.id}/files`),
        api.get(`/finance/property-pnl/${prop.id}`, { params: { period_year: pnlYear, period_month: pnlMonth } }),
      ]);
      setPropFiles(f.data);
      setPropPnl(p.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function closeManage() {
    setManageProp(null);
    setPropFiles([]);
    setPropPnl(null);
  }

  async function handleSaveManage() {
    if (!manageProp) return;
    setError(null);
    setSuccess(null);
    try {
      const payload: Record<string, string | undefined> = {};
      Object.entries(manageForm).forEach(([k, v]) => { payload[k] = v || undefined; });
      const { data } = await api.patch(`/properties/${manageProp.id}`, payload);
      setSuccess("Property updated.");
      setProperties((prev) => prev.map((p) => (p.id === manageProp.id ? data : p)));
      setManageProp(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleUploadFile() {
    if (!manageProp || !uploadFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      await api.post(`/properties/${manageProp.id}/files`, formData, {
        params: { file_type: fileForm.file_type, caption: fileForm.caption || undefined },
        headers: { "Content-Type": "multipart/form-data" },
      });
      setSuccess("File uploaded.");
      setUploadFile(null);
      const { data } = await api.get(`/properties/${manageProp.id}/files`);
      setPropFiles(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadFile(fileId: string, filename: string) {
    if (!manageProp) return;
    try {
      const res = await api.get(`/properties/${manageProp.id}/files/${fileId}/download`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeleteFile(fileId: string) {
    if (!manageProp) return;
    try {
      await api.delete(`/properties/${manageProp.id}/files/${fileId}`);
      setPropFiles((prev) => prev.filter((f) => f.id !== fileId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function reloadPnl() {
    if (!manageProp) return;
    try {
      const { data } = await api.get(`/finance/property-pnl/${manageProp.id}`, { params: { period_year: pnlYear, period_month: pnlMonth } });
      setPropPnl(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleExportPropertyPnl(format: "pdf" | "xlsx") {
    if (!manageProp) return;
    setError(null);
    try {
      const res = await api.get("/finance/reports/export", {
        params: { report: "property_pnl", format, property_id: manageProp.id, period_year: pnlYear, period_month: pnlMonth },
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `property_pnl_${manageProp.name}_${pnlMonth}_${pnlYear}.${format === "pdf" ? "pdf" : "xlsx"}`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleToggleActiveProperty(prop: PropertyOut) {
    const action = prop.is_active ? "deactivate" : "reactivate";
    if (!window.confirm(`${prop.is_active ? "Deactivate" : "Reactivate"} "${prop.name}"? All existing bookings, payments, invoices, units, and history are preserved either way.${prop.is_active ? " It will be excluded from new-booking pickers until reactivated." : ""}`)) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.patch(`/properties/${prop.id}/${action}`);
      setSuccess(`Property ${action}d.`);
      setProperties((prev) => prev.map((p) => (p.id === prop.id ? data : p)));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleToggleActiveUnit(unit: UnitOut) {
    const action = unit.is_active ? "deactivate" : "reactivate";
    if (!window.confirm(`${unit.is_active ? "Deactivate" : "Reactivate"} "${unit.name}"? All existing bookings, payments, invoices, and history are preserved either way.${unit.is_active ? " It will be excluded from new-booking pickers/availability until reactivated." : ""}`)) return;
    setError(null);
    setSuccess(null);
    try {
      const { data } = await api.patch(`/units/${unit.id}/${action}`);
      setSuccess(`Unit ${action}d.`);
      setUnits((prev) => prev.map((u) => (u.id === unit.id ? data : u)));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleSaveManageUnit() {
    if (!manageUnit) return;
    setError(null);
    setSuccess(null);
    try {
      const payload: Record<string, string | number | undefined> = {
        name: manageUnitForm.name || undefined,
        unit_type: manageUnitForm.unit_type || undefined,
        max_occupancy: manageUnitForm.max_occupancy ? Number(manageUnitForm.max_occupancy) : undefined,
        base_rate: manageUnitForm.base_rate ? Number(manageUnitForm.base_rate) : undefined,
        weekend_rate: manageUnitForm.weekend_rate ? Number(manageUnitForm.weekend_rate) : undefined,
        unit_code: manageUnitForm.unit_code || undefined,
        floor: manageUnitForm.floor || undefined,
        flat_number: manageUnitForm.flat_number || undefined,
        amenities: manageUnitForm.amenities || undefined,
      };
      const { data } = await api.patch(`/units/${manageUnit.id}`, payload);
      setSuccess("Unit updated.");
      setUnits((prev) => prev.map((u) => (u.id === manageUnit.id ? data : u)));
      setManageUnit(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function openManageUnit(unit: UnitOut) {
    setManageUnit(unit);
    setManageUnitForm({
      name: unit.name, unit_type: unit.unit_type, max_occupancy: String(unit.max_occupancy),
      base_rate: String(unit.base_rate), weekend_rate: unit.weekend_rate != null ? String(unit.weekend_rate) : "",
      unit_code: unit.unit_code ?? "", floor: unit.floor ?? "", flat_number: unit.flat_number ?? "", amenities: unit.amenities ?? "",
    });
    setError(null);
    try {
      const [photos, tickets, tasks] = await Promise.all([
        api.get(`/units/${unit.id}/photos`),
        api.get("/maintenance-tickets", { params: { unit_id: unit.id } }),
        api.get("/housekeeping-tasks", { params: { unit_id: unit.id } }),
      ]);
      setUnitPhotos(photos.data);
      setUnitMaintTickets(tickets.data);
      setUnitHkTasks(tasks.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function closeManageUnit() {
    setManageUnit(null);
    setManageUnitForm({});
    setUnitPhotos([]);
    setUnitMaintTickets([]);
    setUnitHkTasks([]);
  }

  async function handleUploadUnitPhoto() {
    if (!manageUnit || !unitUploadFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", unitUploadFile);
      await api.post(`/units/${manageUnit.id}/photos`, formData, {
        params: { caption: unitPhotoCaption || undefined },
        headers: { "Content-Type": "multipart/form-data" },
      });
      setSuccess("Photo uploaded.");
      setUnitUploadFile(null);
      setUnitPhotoCaption("");
      const { data } = await api.get(`/units/${manageUnit.id}/photos`);
      setUnitPhotos(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadUnitPhoto(photoId: string, filename: string) {
    if (!manageUnit) return;
    try {
      const res = await api.get(`/units/${manageUnit.id}/photos/${photoId}/download`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeleteUnitPhoto(photoId: string) {
    if (!manageUnit) return;
    try {
      await api.delete(`/units/${manageUnit.id}/photos/${photoId}`);
      setUnitPhotos((prev) => prev.filter((p) => p.id !== photoId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleCreateUnit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post("/units", {
        property_id: unitForm.property_id,
        name: unitForm.name,
        base_rate: Number(unitForm.base_rate),
        weekend_rate: unitForm.weekend_rate ? Number(unitForm.weekend_rate) : undefined,
        unit_code: unitForm.unit_code || undefined,
        floor: unitForm.floor || undefined,
        flat_number: unitForm.flat_number || undefined,
        amenities: unitForm.amenities || undefined,
      });
      setSuccess("Unit created.");
      setUnitForm({ property_id: "", name: "", base_rate: "", weekend_rate: "", unit_code: "", floor: "", flat_number: "", amenities: "" });
      loadAll();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleAddSeasonalRate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/units/${pricingUnitId}/seasonal-rates`, {
        name: seasonalForm.name,
        start_date: seasonalForm.start_date,
        end_date: seasonalForm.end_date,
        rate_per_night: Number(seasonalForm.rate_per_night),
      });
      setSuccess("Seasonal rate added.");
      setSeasonalForm({ name: "", start_date: "", end_date: "", rate_per_night: "" });
      loadPricingRules(pricingUnitId);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeleteSeasonalRate(rateId: string) {
    setError(null);
    try {
      await api.delete(`/units/${pricingUnitId}/seasonal-rates/${rateId}`);
      loadPricingRules(pricingUnitId);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleAddTier(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      await api.post(`/units/${pricingUnitId}/long-stay-discounts`, {
        min_nights: Number(tierForm.min_nights),
        discount_percent: Number(tierForm.discount_percent),
      });
      setSuccess("Discount tier added.");
      setTierForm({ min_nights: "", discount_percent: "" });
      loadPricingRules(pricingUnitId);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDeleteTier(tierId: string) {
    setError(null);
    try {
      await api.delete(`/units/${pricingUnitId}/long-stay-discounts/${tierId}`);
      loadPricingRules(pricingUnitId);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function propertyName(id: string) {
    return properties.find((p) => p.id === id)?.name ?? "—";
  }

  return (
    <div>
      <PageHeader title="Properties & Units" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      {isAdmin && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          <Card>
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Property</h2>
            <form onSubmit={handleCreateProperty} className="space-y-3">
              <input
                required
                placeholder="Property name"
                value={propForm.name}
                onChange={(e) => setPropForm({ ...propForm, name: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                placeholder="Address"
                value={propForm.address}
                onChange={(e) => setPropForm({ ...propForm, address: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                placeholder="GST Number (optional)"
                value={propForm.gst_number}
                onChange={(e) => setPropForm({ ...propForm, gst_number: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                placeholder="State code (e.g. 33)"
                maxLength={2}
                value={propForm.state_code}
                onChange={(e) => setPropForm({ ...propForm, state_code: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <div className="grid grid-cols-2 gap-3">
                <input
                  placeholder="Property code (optional)"
                  value={propForm.property_code}
                  onChange={(e) => setPropForm({ ...propForm, property_code: e.target.value })}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <input
                  placeholder="Property type (e.g. homestay)"
                  value={propForm.property_type}
                  onChange={(e) => setPropForm({ ...propForm, property_type: e.target.value })}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>
              <input
                placeholder="Owner name (optional)"
                value={propForm.owner_name}
                onChange={(e) => setPropForm({ ...propForm, owner_name: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <div className="grid grid-cols-2 gap-3">
                <input
                  placeholder="Owner phone"
                  value={propForm.owner_phone}
                  onChange={(e) => setPropForm({ ...propForm, owner_phone: e.target.value })}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <input
                  placeholder="Owner email"
                  value={propForm.owner_email}
                  onChange={(e) => setPropForm({ ...propForm, owner_email: e.target.value })}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>
              <input
                placeholder="Google Maps link (optional)"
                value={propForm.google_maps_url}
                onChange={(e) => setPropForm({ ...propForm, google_maps_url: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <Button type="submit">Create Property</Button>
            </form>
          </Card>

          <Card>
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Unit</h2>
            <form onSubmit={handleCreateUnit} className="space-y-3">
              <select
                required
                value={unitForm.property_id}
                onChange={(e) => setUnitForm({ ...unitForm, property_id: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">Select property...</option>
                {properties.filter((p) => p.is_active).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <input
                required
                placeholder="Unit name (e.g. Apartment 2B)"
                value={unitForm.name}
                onChange={(e) => setUnitForm({ ...unitForm, name: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Weekday rate / night</label>
                  <input
                    required
                    type="number"
                    placeholder="e.g. 2000"
                    value={unitForm.base_rate}
                    onChange={(e) => setUnitForm({ ...unitForm, base_rate: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Weekend rate (Fri/Sat, optional)</label>
                  <input
                    type="number"
                    placeholder="Same as weekday if blank"
                    value={unitForm.weekend_rate}
                    onChange={(e) => setUnitForm({ ...unitForm, weekend_rate: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
              </div>
              {unitForm.base_rate && (
                <p className="text-xs text-slate-400">
                  {(() => {
                    const { rate, itcAvailable } = estimateGstSlab(Number(unitForm.base_rate));
                    return `Estimated GST slab: ${rate}% ${itcAvailable ? "(ITC available)" : "(ITC not available)"} — the actual rate used depends on the final nightly rate charged (weekday/weekend/seasonal), computed at quotation time`;
                  })()}
                </p>
              )}
              <div className="grid grid-cols-3 gap-3">
                <input
                  placeholder="Unit code"
                  value={unitForm.unit_code}
                  onChange={(e) => setUnitForm({ ...unitForm, unit_code: e.target.value })}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <input
                  placeholder="Floor"
                  value={unitForm.floor}
                  onChange={(e) => setUnitForm({ ...unitForm, floor: e.target.value })}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <input
                  placeholder="Flat number"
                  value={unitForm.flat_number}
                  onChange={(e) => setUnitForm({ ...unitForm, flat_number: e.target.value })}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>
              <input
                placeholder="Amenities (e.g. AC, WiFi, Kitchen)"
                value={unitForm.amenities}
                onChange={(e) => setUnitForm({ ...unitForm, amenities: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <Button type="submit">Create Unit</Button>
            </form>
          </Card>
        </div>
      )}

      <Card className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Properties</h2>
          <input
            placeholder="Search name, code, owner..."
            value={propSearch}
            onChange={(e) => setPropSearch(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs min-w-[220px]"
          />
        </div>
        <div className="overflow-x-auto">
          <Table headers={["Code", "Name", "Address", "State Code", "GSTIN", "Owner", "Status", "Actions"]}>
            {properties
              .filter((p) => !propSearch || p.name.toLowerCase().includes(propSearch.toLowerCase()) || (p.property_code ?? "").toLowerCase().includes(propSearch.toLowerCase()) || (p.owner_name ?? "").toLowerCase().includes(propSearch.toLowerCase()))
              .map((p) => (
              <tr key={p.id}>
                <td className="px-4 py-2.5 text-slate-500">{p.property_code ?? "—"}</td>
                <td className="px-4 py-2.5 font-medium text-slate-800">{p.name}</td>
                <td className="px-4 py-2.5 text-slate-500">{p.address}</td>
                <td className="px-4 py-2.5 text-slate-500">{p.state_code}</td>
                <td className="px-4 py-2.5 text-slate-500">{p.gst_number ?? "—"}</td>
                <td className="px-4 py-2.5 text-slate-500">{p.owner_name ?? "—"}</td>
                <td className="px-4 py-2.5">
                  <Badge tone={p.is_active ? "green" : "slate"}>{p.is_active ? "Active" : "Inactive"}</Badge>
                </td>
                <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                  <Button variant="secondary" onClick={() => openManage(p)}>Manage</Button>
                  {isAdmin && <Button variant="secondary" onClick={() => openManage(p)}>Edit</Button>}
                  {isAdmin && (
                    <Button variant={p.is_active ? "danger" : "secondary"} onClick={() => handleToggleActiveProperty(p)}>
                      {p.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>

      <Card className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Units</h2>
          <input
            placeholder="Search name, code, floor..."
            value={unitSearch}
            onChange={(e) => setUnitSearch(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs min-w-[220px]"
          />
        </div>
        <div className="overflow-x-auto">
          <Table headers={["Name", "Code", "Floor / Flat", "Property", "Weekday Rate", "Weekend Rate", "Amenities", "Status", "Actions"]}>
            {units
              .filter((u) => !unitSearch || u.name.toLowerCase().includes(unitSearch.toLowerCase()) || (u.unit_code ?? "").toLowerCase().includes(unitSearch.toLowerCase()) || (u.floor ?? "").toLowerCase().includes(unitSearch.toLowerCase()))
              .map((u) => {
              return (
                <tr key={u.id}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{u.name}</td>
                  <td className="px-4 py-2.5 text-slate-500">{u.unit_code ?? "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{u.floor || u.flat_number ? `${u.floor ?? ""}${u.floor && u.flat_number ? " / " : ""}${u.flat_number ?? ""}` : "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{propertyName(u.property_id)}</td>
                  <td className="px-4 py-2.5 text-slate-500">₹{u.base_rate}</td>
                  <td className="px-4 py-2.5 text-slate-500">{u.weekend_rate ? `₹${u.weekend_rate}` : "— (same as weekday)"}</td>
                  <td className="px-4 py-2.5 text-slate-500 max-w-[160px] truncate">{u.amenities ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    {u.is_active ? (
                      <Badge tone={u.status === "available" ? "green" : "amber"}>{u.status}</Badge>
                    ) : (
                      <Badge tone="slate">Inactive</Badge>
                    )}
                  </td>
                  <td className="px-4 py-2.5 space-x-2 whitespace-nowrap">
                    <Button variant="secondary" onClick={() => openManageUnit(u)}>Manage</Button>
                    {isAdmin && <Button variant="secondary" onClick={() => openManageUnit(u)}>Edit</Button>}
                    {isAdmin && (
                      <Button variant={u.is_active ? "danger" : "secondary"} onClick={() => handleToggleActiveUnit(u)}>
                        {u.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </Table>
        </div>
      </Card>

      {isAdmin && (
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-1">Dynamic Pricing Rules</h2>
          <p className="text-xs text-slate-400 mb-4">
            Seasonal rates always override weekday/weekend rates for their date range. Long-stay discounts apply the
            highest-qualifying tier's percentage off the pre-discount total — a 30-night stay gets the 30-night
            tier, not a lower one it also happens to qualify for.
          </p>
          <select
            value={pricingUnitId}
            onChange={(e) => setPricingUnitId(e.target.value)}
            className="w-full sm:w-80 rounded-lg border border-slate-300 px-3 py-2 text-sm mb-6"
          >
            <option value="">Select a unit to manage its pricing rules...</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>

          {pricingUnitId && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div>
                <h3 className="text-xs font-semibold text-slate-700 mb-3 uppercase tracking-wide">Seasonal Rates</h3>
                <form onSubmit={handleAddSeasonalRate} className="space-y-2 mb-4">
                  <input
                    required
                    placeholder="Name (e.g. Diwali 2026)"
                    value={seasonalForm.name}
                    onChange={(e) => setSeasonalForm({ ...seasonalForm, name: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      required
                      type="date"
                      value={seasonalForm.start_date}
                      onChange={(e) => setSeasonalForm({ ...seasonalForm, start_date: e.target.value })}
                      className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                    <input
                      required
                      type="date"
                      value={seasonalForm.end_date}
                      onChange={(e) => setSeasonalForm({ ...seasonalForm, end_date: e.target.value })}
                      className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                  </div>
                  <div className="flex gap-2">
                    <input
                      required
                      type="number"
                      placeholder="Rate / night"
                      value={seasonalForm.rate_per_night}
                      onChange={(e) => setSeasonalForm({ ...seasonalForm, rate_per_night: e.target.value })}
                      className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                    <Button type="submit">Add</Button>
                  </div>
                </form>
                <div className="space-y-2">
                  {seasonalRates.length === 0 && <p className="text-xs text-slate-400">No seasonal rates set for this unit.</p>}
                  {seasonalRates.map((s) => (
                    <div key={s.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-2 text-xs">
                      <span>
                        <span className="font-medium text-slate-800">{s.name}</span>
                        <span className="text-slate-500"> · {s.start_date} → {s.end_date} · ₹{s.rate_per_night}/night</span>
                      </span>
                      <button onClick={() => handleDeleteSeasonalRate(s.id)} className="text-red-500 hover:text-red-700 ml-2">
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h3 className="text-xs font-semibold text-slate-700 mb-3 uppercase tracking-wide">Long-Stay Discounts</h3>
                <form onSubmit={handleAddTier} className="space-y-2 mb-4">
                  <div className="flex gap-2">
                    <input
                      required
                      type="number"
                      placeholder="Min nights (e.g. 7)"
                      value={tierForm.min_nights}
                      onChange={(e) => setTierForm({ ...tierForm, min_nights: e.target.value })}
                      className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                    <input
                      required
                      type="number"
                      placeholder="Discount %"
                      value={tierForm.discount_percent}
                      onChange={(e) => setTierForm({ ...tierForm, discount_percent: e.target.value })}
                      className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs"
                    />
                    <Button type="submit">Add</Button>
                  </div>
                </form>
                <div className="space-y-2">
                  {discountTiers.length === 0 && <p className="text-xs text-slate-400">No long-stay discount tiers set for this unit.</p>}
                  {discountTiers
                    .slice()
                    .sort((a, b) => a.min_nights - b.min_nights)
                    .map((t) => (
                      <div key={t.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-2 text-xs">
                        <span>
                          <span className="font-medium text-slate-800">{t.min_nights}+ nights</span>
                          <span className="text-slate-500"> → {t.discount_percent}% off</span>
                        </span>
                        <button onClick={() => handleDeleteTier(t.id)} className="text-red-500 hover:text-red-700 ml-2">
                          Remove
                        </button>
                      </div>
                    ))}
                </div>
              </div>
            </div>
          )}
        </Card>
      )}

      {manageProp && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={closeManage}>
          <div className="bg-white rounded-xl max-w-2xl w-full max-h-[88vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Manage — {manageProp.name}</h2>
              <button onClick={closeManage} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
            </div>

            <h3 className="text-sm font-semibold text-slate-900 mb-2">Edit Details</h3>
            <div className="grid grid-cols-2 gap-2 mb-4 text-xs">
              <input placeholder="Name" value={manageForm.name ?? ""} onChange={(e) => setManageForm({ ...manageForm, name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Property code" value={manageForm.property_code ?? ""} onChange={(e) => setManageForm({ ...manageForm, property_code: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Address" value={manageForm.address ?? ""} onChange={(e) => setManageForm({ ...manageForm, address: e.target.value })} className="col-span-2 rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="GSTIN" value={manageForm.gst_number ?? ""} onChange={(e) => setManageForm({ ...manageForm, gst_number: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Property type" value={manageForm.property_type ?? ""} onChange={(e) => setManageForm({ ...manageForm, property_type: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Owner name" value={manageForm.owner_name ?? ""} onChange={(e) => setManageForm({ ...manageForm, owner_name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Owner phone" value={manageForm.owner_phone ?? ""} onChange={(e) => setManageForm({ ...manageForm, owner_phone: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Owner email" value={manageForm.owner_email ?? ""} onChange={(e) => setManageForm({ ...manageForm, owner_email: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Google Maps link" value={manageForm.google_maps_url ?? ""} onChange={(e) => setManageForm({ ...manageForm, google_maps_url: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            </div>
            <Button variant="secondary" onClick={handleSaveManage}>Save Changes</Button>

            <h3 className="text-sm font-semibold text-slate-900 mt-6 mb-2">Documents &amp; Photos</h3>
            <div className="flex flex-wrap gap-2 mb-3">
              <select value={fileForm.file_type} onChange={(e) => setFileForm({ ...fileForm, file_type: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                <option value="document">Document</option>
                <option value="photo">Photo</option>
              </select>
              <input placeholder="Caption (optional)" value={fileForm.caption} onChange={(e) => setFileForm({ ...fileForm, caption: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
              <input type="file" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} className="text-xs" />
              <Button variant="secondary" onClick={handleUploadFile}>Upload</Button>
            </div>
            {propFiles.length === 0 ? (
              <p className="text-xs text-slate-400 mb-6">No documents or photos uploaded yet.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2 mb-6">
                {propFiles.map((f) => (
                  <div key={f.id} className="border border-slate-100 rounded-lg p-2 text-xs">
                    <p className="font-medium text-slate-700">{f.file_type} {f.caption ? `— ${f.caption}` : ""}</p>
                    <p className="text-slate-500 truncate">{f.filename}</p>
                    <div className="flex gap-2 mt-1">
                      <button onClick={() => handleDownloadFile(f.id, f.filename)} className="text-slate-600 hover:underline">Download</button>
                      <button onClick={() => handleDeleteFile(f.id)} className="text-red-500 hover:underline">Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <h3 className="text-sm font-semibold text-slate-900 mb-2">Property Profit &amp; Loss</h3>
            <div className="flex gap-2 mb-3">
              <input type="number" value={pnlYear} onChange={(e) => { setPnlYear(Number(e.target.value)); }} onBlur={reloadPnl} className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
              <select value={pnlMonth} onChange={(e) => { setPnlMonth(Number(e.target.value)); reloadPnl(); }} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <Button variant="secondary" onClick={reloadPnl}>Refresh</Button>
              <Button variant="secondary" onClick={() => handleExportPropertyPnl("pdf")}>Export PDF</Button>
              <Button variant="secondary" onClick={() => handleExportPropertyPnl("xlsx")}>Export Excel</Button>
            </div>
            {propPnl && (
              <div className="bg-slate-50 rounded-lg p-3 text-xs space-y-1">
                <p>Total Revenue: <span className="font-semibold text-slate-800">₹{propPnl.total_revenue.toLocaleString()}</span></p>
                <p>Total Expenses: <span className="font-semibold text-slate-800">₹{propPnl.total_expenses.toLocaleString()}</span></p>
                <p>Net Profit: <span className={`font-semibold ${propPnl.net_profit >= 0 ? "text-emerald-600" : "text-red-600"}`}>₹{propPnl.net_profit.toLocaleString()}</span></p>
                <p className="text-slate-400">Across {propPnl.units.length} unit(s) this period.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {manageUnit && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={closeManageUnit}>
          <div className="bg-white rounded-xl max-w-2xl w-full max-h-[88vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Manage — {manageUnit.name}</h2>
              <button onClick={closeManageUnit} className="text-slate-400 hover:text-slate-600 text-sm">Close</button>
            </div>

            {isAdmin && (
              <>
                <h3 className="text-sm font-semibold text-slate-900 mb-2">Edit Details</h3>
                <div className="grid grid-cols-2 gap-2 mb-4 text-xs">
                  <input placeholder="Name" value={manageUnitForm.name ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Unit type" value={manageUnitForm.unit_type ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, unit_type: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input type="number" min={1} placeholder="Max occupancy" value={manageUnitForm.max_occupancy ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, max_occupancy: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input type="number" placeholder="Weekday rate / night" value={manageUnitForm.base_rate ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, base_rate: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input type="number" placeholder="Weekend rate (optional)" value={manageUnitForm.weekend_rate ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, weekend_rate: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Unit code" value={manageUnitForm.unit_code ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, unit_code: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Floor" value={manageUnitForm.floor ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, floor: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Flat number" value={manageUnitForm.flat_number ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, flat_number: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
                  <input placeholder="Amenities" value={manageUnitForm.amenities ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, amenities: e.target.value })} className="col-span-2 rounded-lg border border-slate-300 px-2 py-1.5" />
                </div>
                <Button variant="secondary" onClick={handleSaveManageUnit}>Save Changes</Button>
              </>
            )}

            <h3 className="text-sm font-semibold text-slate-900 mt-6 mb-2">Photos</h3>
            <div className="flex flex-wrap gap-2 mb-3">
              <input placeholder="Caption (optional)" value={unitPhotoCaption} onChange={(e) => setUnitPhotoCaption(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
              <input type="file" onChange={(e) => setUnitUploadFile(e.target.files?.[0] ?? null)} className="text-xs" />
              <Button variant="secondary" onClick={handleUploadUnitPhoto}>Upload</Button>
            </div>
            {unitPhotos.length === 0 ? (
              <p className="text-xs text-slate-400 mb-6">No photos uploaded yet.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2 mb-6">
                {unitPhotos.map((photo) => (
                  <div key={photo.id} className="border border-slate-100 rounded-lg p-2 text-xs">
                    <p className="font-medium text-slate-700">{photo.caption || photo.filename}</p>
                    <div className="flex gap-2 mt-1">
                      <button onClick={() => handleDownloadUnitPhoto(photo.id, photo.filename)} className="text-slate-600 hover:underline">Download</button>
                      <button onClick={() => handleDeleteUnitPhoto(photo.id)} className="text-red-500 hover:underline">Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <h3 className="text-sm font-semibold text-slate-900 mb-2">Maintenance History</h3>
            {unitMaintTickets.length === 0 ? (
              <p className="text-xs text-slate-400 mb-6">No maintenance tickets for this unit.</p>
            ) : (
              <div className="space-y-1 mb-6">
                {unitMaintTickets.map((t) => (
                  <div key={t.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-2 text-xs">
                    <span>{t.description}</span>
                    <Badge tone={t.status === "resolved" || t.status === "closed" ? "green" : "amber"}>{t.status}</Badge>
                  </div>
                ))}
              </div>
            )}

            <h3 className="text-sm font-semibold text-slate-900 mb-2">Housekeeping History</h3>
            {unitHkTasks.length === 0 ? (
              <p className="text-xs text-slate-400">No housekeeping tasks for this unit.</p>
            ) : (
              <div className="space-y-1">
                {unitHkTasks.map((t) => (
                  <div key={t.id} className="flex items-center justify-between border border-slate-100 rounded-lg px-3 py-2 text-xs">
                    <span>{t.task_type}</span>
                    <Badge tone={t.status === "completed" ? "green" : "amber"}>{t.status}</Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
