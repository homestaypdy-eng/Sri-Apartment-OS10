import { useEffect, useState, type FormEvent, type ReactNode, type SVGProps } from "react";
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
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Table, Badge, EmptyState, Drawer, type BadgeTone } from "../components/ui";
import { useAuth } from "../context/AuthContext";

// Property.property_type stays an unconstrained backend string — this is a
// suggested vocabulary for the dropdown, not an enforced one. "Other" falls
// through to a free-text input so an existing or future value outside this
// list is never blocked or silently discarded.
const PROPERTY_TYPE_OPTIONS = [
  { value: "homestay", label: "Homestay" },
  { value: "apartment_block", label: "Apartment Block" },
  { value: "villa_resort", label: "Villa Resort" },
  { value: "resort", label: "Resort" },
];

// Matches the backend canonical vocabulary (Unit.unit_type is also an
// unconstrained string — same "Other" fallback reasoning as above). Used
// both for the icon-grid selector and for rendering existing values.
const UNIT_TYPE_OPTIONS = [
  { value: "apartment", label: "Apartment" },
  { value: "villa", label: "Villa" },
  { value: "suite", label: "Suite" },
  { value: "room", label: "Room" },
  { value: "cottage", label: "Cottage" },
  { value: "resort", label: "Resort" },
  { value: "homestay", label: "Homestay" },
];

const UNIT_TYPE_BADGE_TONE: Record<string, BadgeTone> = {
  apartment: "blue",
  villa: "violet",
  suite: "amber",
  room: "cyan",
  cottage: "green",
  resort: "orange",
  homestay: "pink",
};

function unitTypeBadgeTone(type: string): BadgeTone {
  return UNIT_TYPE_BADGE_TONE[type] ?? "slate";
}

function typeLabel(options: { value: string; label: string }[], value: string): string {
  return options.find((o) => o.value === value)?.label ?? value;
}

// ---- Icons (small hand-drawn outline icons, no external icon dependency) ----

function BuildingIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="5" y="3" width="14" height="18" rx="1.5" />
      <path d="M9 7h.01M12 7h.01M15 7h.01M9 11h.01M12 11h.01M15 11h.01M9 15h.01M15 15h.01" />
      <path d="M10.5 21v-3.5a1.5 1.5 0 013 0V21" />
    </svg>
  );
}

function PinIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12 21s7-6.3 7-11.5A7 7 0 105 9.5C5 14.7 12 21 12 21z" />
      <circle cx="12" cy="9.5" r="2.25" />
    </svg>
  );
}

function VillaIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4 11.5 12 5l8 6.5" />
      <path d="M6 10v9.5a1 1 0 001 1h10a1 1 0 001-1V10" />
      <path d="M9.5 20.5v-4a1 1 0 011-1h3a1 1 0 011 1v4" />
      <path d="M16.5 8.5V5h2v2" />
    </svg>
  );
}

function SuiteIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4 17v-4a2 2 0 012-2h12a2 2 0 012 2v4" />
      <path d="M4 17v2M20 17v2" />
      <path d="M5 11V9a1.5 1.5 0 011.5-1.5h11A1.5 1.5 0 0119 9v2" />
    </svg>
  );
}

function RoomIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M3 19v-7a2 2 0 012-2h14a2 2 0 012 2v7" />
      <path d="M3 19v1.5M21 19v1.5" />
      <path d="M3 14h18" />
      <path d="M6 10V8a2 2 0 012-2h2a2 2 0 012 2v2" />
    </svg>
  );
}

function CottageIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4 12 12 5l8 7" />
      <path d="M6 11v8a1 1 0 001 1h10a1 1 0 001-1v-8" />
      <path d="M15 6.5V4h2v4" />
      <path d="M10 20v-4h4v4" />
    </svg>
  );
}

function ResortIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12 21v-9" />
      <path d="M12 12c0-4.5-3-6.5-6-7 .5 4 2.5 6.5 6 7z" />
      <path d="M12 12c0-4.5 3-6.5 6-7-.5 4-2.5 6.5-6 7z" />
      <path d="M7 21h10" />
    </svg>
  );
}

function HomestayIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4 11 12 4l8 7" />
      <path d="M6 10v9a1 1 0 001 1h10a1 1 0 001-1v-9" />
      <path d="M12 17.5c-1.4-1-2.5-1.9-2.5-3a1.5 1.5 0 012.5-1.1 1.5 1.5 0 012.5 1.1c0 1.1-1.1 2-2.5 3z" />
    </svg>
  );
}

function OtherIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="6" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="18" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  );
}

function CameraIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4 8a2 2 0 012-2h1.2l.9-1.5A1 1 0 019 4h6a1 1 0 01.9.5L16.8 6H18a2 2 0 012 2v10a2 2 0 01-2 2H6a2 2 0 01-2-2V8z" />
      <circle cx="12" cy="13" r="3.5" />
    </svg>
  );
}

const UNIT_TYPE_ICON_OPTIONS: { value: string; label: string; Icon: (props: SVGProps<SVGSVGElement>) => ReactNode }[] = [
  { value: "apartment", label: "Apartment", Icon: BuildingIcon },
  { value: "villa", label: "Villa", Icon: VillaIcon },
  { value: "suite", label: "Suite", Icon: SuiteIcon },
  { value: "room", label: "Room", Icon: RoomIcon },
  { value: "cottage", label: "Cottage", Icon: CottageIcon },
  { value: "resort", label: "Resort", Icon: ResortIcon },
  { value: "homestay", label: "Homestay", Icon: HomestayIcon },
];

// Shared dropdown-plus-custom-value control for property_type.
// `isOther`/`setIsOther` is owned by the parent (not internal state) so a
// "Manage" modal can initialize it correctly per-record: a value already
// outside the known list (a legacy free-text entry) opens straight into
// custom-text mode instead of silently blanking it.
function TypeField({
  options, value, onChange, isOther, setIsOther, placeholder, customPlaceholder,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  isOther: boolean;
  setIsOther: (b: boolean) => void;
  placeholder: string;
  customPlaceholder: string;
}) {
  return (
    <div>
      <select
        value={isOther ? "__other__" : value}
        onChange={(e) => {
          if (e.target.value === "__other__") {
            setIsOther(true);
            onChange("");
          } else {
            setIsOther(false);
            onChange(e.target.value);
          }
        }}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
        <option value="__other__">Other (specify)</option>
      </select>
      {isOther && (
        <input
          placeholder={customPlaceholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
      )}
    </div>
  );
}

// Visual icon-grid selector for accommodation type — same underlying
// unit_type string field/values as TypeField, just a friendlier control
// for the future-ready vocabulary (Apartment/Villa/Suite/Room/Cottage/
// Resort/Homestay/Other) called for in the Phase 7A design reference.
function UnitTypeIconSelector({
  value, onChange, isOther, setIsOther,
}: {
  value: string;
  onChange: (v: string) => void;
  isOther: boolean;
  setIsOther: (b: boolean) => void;
}) {
  const tileClass = (selected: boolean) =>
    `flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-[11px] font-medium transition ${
      selected
        ? "border-blue-500 bg-blue-50 text-blue-700 ring-1 ring-blue-500"
        : "border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50"
    }`;
  return (
    <div>
      <div className="grid grid-cols-4 gap-2">
        {UNIT_TYPE_ICON_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => { setIsOther(false); onChange(opt.value); }}
            className={tileClass(!isOther && value === opt.value)}
          >
            <opt.Icon className="h-5 w-5" />
            {opt.label}
          </button>
        ))}
        <button type="button" onClick={() => { setIsOther(true); onChange(""); }} className={tileClass(isOther)}>
          <OtherIcon className="h-5 w-5" />
          Other
        </button>
      </div>
      {isOther && (
        <input
          placeholder="Custom accommodation type"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
      )}
    </div>
  );
}

function PropertyCard({
  property, unitCount, maxGuests, avgRate, thumbUrl, onManage,
}: {
  property: PropertyOut;
  unitCount: number;
  maxGuests: number;
  avgRate: number;
  thumbUrl?: string;
  onManage: () => void;
}) {
  const locationParts = property.address ? property.address.split(",").map((s) => s.trim()).filter(Boolean) : [];
  const location = locationParts.length > 0 ? locationParts.slice(-2).join(", ") : null;

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          {thumbUrl ? (
            <img src={thumbUrl} alt={`${property.name} photo`} className="h-10 w-10 shrink-0 rounded-xl object-cover" />
          ) : (
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
              <BuildingIcon className="h-5 w-5" />
            </div>
          )}
          <div className="min-w-0">
            <h3 title={property.name} className="truncate text-sm font-semibold text-slate-900">{property.name}</h3>
            <p className="truncate text-xs text-slate-500">
              {property.property_type ? typeLabel(PROPERTY_TYPE_OPTIONS, property.property_type) : "Property"}
              {property.property_code ? ` · ${property.property_code}` : ""}
            </p>
          </div>
        </div>
        <Badge tone={property.is_active ? "green" : "slate"}>{property.is_active ? "Active" : "Inactive"}</Badge>
      </div>

      {thumbUrl && (
        <img src={thumbUrl} alt={`${property.name}`} className="h-32 w-full rounded-xl object-cover" />
      )}

      {location && (
        <p className="flex items-center gap-1.5 text-xs text-slate-500">
          <PinIcon className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{location}</span>
        </p>
      )}

      <div className="grid grid-cols-3 gap-2 border-t border-slate-100 pt-4">
        <div>
          <p className="text-lg font-semibold text-slate-900">{unitCount}</p>
          <p className="text-[11px] leading-tight text-slate-500">Accommodation{unitCount === 1 ? "" : "s"}</p>
        </div>
        <div>
          <p className="text-lg font-semibold text-slate-900">{maxGuests}</p>
          <p className="text-[11px] leading-tight text-slate-500">Max Guest Capacity</p>
        </div>
        <div>
          <p className="text-lg font-semibold text-slate-900">{avgRate > 0 ? `₹${avgRate.toLocaleString()}` : "—"}</p>
          <p className="text-[11px] leading-tight text-slate-500">Avg. Weekday Rate</p>
        </div>
      </div>

      {property.google_maps_url && (
        <a
          href={property.google_maps_url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 text-xs font-medium text-blue-600 hover:underline"
        >
          <PinIcon className="h-3.5 w-3.5" />
          View on Google Maps
        </a>
      )}

      <Button variant="secondary" onClick={onManage} className="flex w-full items-center justify-center">
        Manage Property
      </Button>
    </div>
  );
}

export default function PropertiesPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  // RBAC Phase B: Manager gets pricing management (base/weekend rate via
  // the dedicated pricing endpoint below, plus the existing Seasonal
  // Rates / Long-Stay Discount Tiers section) but not full unit/property
  // editing — kept as its own flag rather than folding into isAdmin so
  // each gate below stays explicit about which access it's granting.
  const isManager = role === "manager";

  const [properties, setProperties] = useState<PropertyOut[]>([]);
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [propertyThumbs, setPropertyThumbs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [propForm, setPropForm] = useState({ name: "", address: "", gst_number: "", state_code: "", property_code: "", property_type: "", owner_name: "", owner_phone: "", owner_email: "", google_maps_url: "" });
  const [propTypeIsOther, setPropTypeIsOther] = useState(false);
  const [addPropOpen, setAddPropOpen] = useState(false);

  const [unitForm, setUnitForm] = useState({ property_id: "", name: "", unit_type: "", max_occupancy: "", base_rate: "", weekend_rate: "", unit_code: "", floor: "", flat_number: "", amenities: "", description: "" });
  const [unitTypeIsOther, setUnitTypeIsOther] = useState(false);
  const [addUnitOpen, setAddUnitOpen] = useState(false);

  const [savingManagerPricing, setSavingManagerPricing] = useState(false);
  const [pricingUnitId, setPricingUnitId] = useState("");
  const [seasonalRates, setSeasonalRates] = useState<SeasonalRateOut[]>([]);
  const [discountTiers, setDiscountTiers] = useState<LongStayDiscountTierOut[]>([]);
  const [seasonalForm, setSeasonalForm] = useState({ name: "", start_date: "", end_date: "", rate_per_night: "" });
  const [tierForm, setTierForm] = useState({ min_nights: "", discount_percent: "" });
  const [propSearch, setPropSearch] = useState("");
  const [manageProp, setManageProp] = useState<PropertyOut | null>(null);
  const [manageForm, setManageForm] = useState<Record<string, string>>({});
  const [managePropTypeIsOther, setManagePropTypeIsOther] = useState(false);
  const [propFiles, setPropFiles] = useState<PropertyFileOut[]>([]);
  const [fileForm, setFileForm] = useState({ file_type: "document", caption: "" });
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [propPnl, setPropPnl] = useState<PropertyPnL | null>(null);
  const [pnlYear, setPnlYear] = useState(new Date().getFullYear());
  const [pnlMonth, setPnlMonth] = useState(new Date().getMonth() + 1);

  const [unitSearch, setUnitSearch] = useState("");
  const [unitTypeFilter, setUnitTypeFilter] = useState("");
  const [unitStatusFilter, setUnitStatusFilter] = useState("");
  const [manageUnit, setManageUnit] = useState<UnitOut | null>(null);
  const [manageUnitForm, setManageUnitForm] = useState<Record<string, string>>({});
  const [manageUnitTypeIsOther, setManageUnitTypeIsOther] = useState(false);
  const [unitPhotos, setUnitPhotos] = useState<UnitPhotoOut[]>([]);
  const [unitPhotoThumbs, setUnitPhotoThumbs] = useState<Record<string, string>>({});
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

  // Property cards show a real photo when one exists (a PropertyFile with
  // file_type "photo") instead of always falling back to the generic icon.
  // Cheap even at scale — one file-list call per property, one blob fetch
  // only for properties that actually have a photo. Object URLs are
  // revoked on cleanup to avoid leaking memory as the property list changes.
  useEffect(() => {
    if (properties.length === 0) {
      setPropertyThumbs({});
      return;
    }
    let cancelled = false;
    const createdUrls: string[] = [];
    (async () => {
      const entries = await Promise.all(
        properties.map(async (p) => {
          try {
            const { data: files } = await api.get(`/properties/${p.id}/files`);
            const photo = (files as PropertyFileOut[]).find((f) => f.file_type === "photo");
            if (!photo) return null;
            const res = await api.get(`/properties/${p.id}/files/${photo.id}/download`, { responseType: "blob" });
            const url = URL.createObjectURL(res.data);
            createdUrls.push(url);
            return [p.id, url] as const;
          } catch {
            return null;
          }
        })
      );
      if (!cancelled) {
        setPropertyThumbs(Object.fromEntries(entries.filter((e): e is [string, string] => e !== null)));
      }
    })();
    return () => {
      cancelled = true;
      createdUrls.forEach((u) => URL.revokeObjectURL(u));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [properties]);

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

  // Fetches actual image bytes for the currently-open unit's photos so the
  // Edit drawer can show real thumbnails rather than filename-only rows.
  // Scoped to one unit at a time (not the whole accommodations list) to
  // keep this cheap; object URLs are revoked on cleanup to avoid leaking.
  useEffect(() => {
    if (!manageUnit || unitPhotos.length === 0) {
      setUnitPhotoThumbs({});
      return;
    }
    let cancelled = false;
    const createdUrls: string[] = [];
    Promise.all(
      unitPhotos.map(async (p) => {
        const res = await api.get(`/units/${manageUnit.id}/photos/${p.id}/download`, { responseType: "blob" });
        const url = URL.createObjectURL(res.data);
        createdUrls.push(url);
        return [p.id, url] as const;
      })
    )
      .then((entries) => {
        if (!cancelled) setUnitPhotoThumbs(Object.fromEntries(entries));
      })
      .catch(() => {
        /* thumbnail loading is a nice-to-have; leave placeholders on failure */
      });
    return () => {
      cancelled = true;
      createdUrls.forEach((u) => URL.revokeObjectURL(u));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manageUnit, unitPhotos]);

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
      setPropTypeIsOther(false);
      setAddPropOpen(false);
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
    setManagePropTypeIsOther(!!prop.property_type && !PROPERTY_TYPE_OPTIONS.some((o) => o.value === prop.property_type));
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
      setManageUnit((prev) => (prev && prev.id === unit.id ? data : prev));
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
        long_stay_min_nights: manageUnitForm.long_stay_min_nights ? Number(manageUnitForm.long_stay_min_nights) : undefined,
        long_stay_rate: manageUnitForm.long_stay_rate ? Number(manageUnitForm.long_stay_rate) : undefined,
        // These five are legitimately clearable (backend columns are
        // nullable) — `|| undefined` would silently drop an intentional
        // clear-to-empty from the payload (falsy ""), leaving the old
        // value in the database while the form itself shows empty. Sent
        // as their literal value (including "") so PATCH .../units/{id}'s
        // exclude_unset=True sees the field and actually applies it.
        unit_code: manageUnitForm.unit_code,
        floor: manageUnitForm.floor,
        flat_number: manageUnitForm.flat_number,
        amenities: manageUnitForm.amenities,
        description: manageUnitForm.description,
        house_rules: manageUnitForm.house_rules,
        checkin_time: manageUnitForm.checkin_time,
        checkout_time: manageUnitForm.checkout_time,
        status: manageUnitForm.status || undefined,
        bedroom_count: manageUnitForm.bedroom_count ? Number(manageUnitForm.bedroom_count) : undefined,
        bathroom_count: manageUnitForm.bathroom_count ? Number(manageUnitForm.bathroom_count) : undefined,
      };
      const { data } = await api.patch(`/units/${manageUnit.id}`, payload);
      setSuccess("Unit updated.");
      setUnits((prev) => prev.map((u) => (u.id === manageUnit.id ? data : u)));
      setManageUnit(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  // RBAC Phase B: Manager's narrow counterpart to handleSaveManageUnit —
  // calls PATCH /units/{id}/pricing (base_rate/weekend_rate only) instead
  // of the full PATCH /units/{id}, so Manager can never send unit-identity
  // fields even by accident. Reuses manageUnitForm's existing base_rate/
  // weekend_rate fields (already populated by openManageUnit for every
  // role) rather than a separate form state.
  async function handleSaveManagerPricing() {
    if (!manageUnit) return;
    setError(null);
    setSuccess(null);

    const baseRateStr = manageUnitForm.base_rate ?? "";
    const weekendRateStr = manageUnitForm.weekend_rate ?? "";
    const longStayMinNightsStr = manageUnitForm.long_stay_min_nights ?? "";
    const longStayRateStr = manageUnitForm.long_stay_rate ?? "";
    const payload: { base_rate?: number; weekend_rate?: number; long_stay_min_nights?: number; long_stay_rate?: number } = {};

    if (baseRateStr !== "") {
      const n = Number(baseRateStr);
      if (!Number.isFinite(n) || n <= 0) {
        setError("Weekday rate must be a positive number.");
        return;
      }
      payload.base_rate = n;
    }
    if (weekendRateStr !== "") {
      const n = Number(weekendRateStr);
      if (!Number.isFinite(n) || n <= 0) {
        setError("Weekend rate must be a positive number.");
        return;
      }
      payload.weekend_rate = n;
    }
    if (longStayMinNightsStr !== "") {
      const n = Number(longStayMinNightsStr);
      if (!Number.isInteger(n) || n <= 0) {
        setError("Long-stay minimum nights must be a positive whole number.");
        return;
      }
      payload.long_stay_min_nights = n;
    }
    if (longStayRateStr !== "") {
      const n = Number(longStayRateStr);
      if (!Number.isFinite(n) || n <= 0) {
        setError("Long-stay nightly rate must be a positive number.");
        return;
      }
      payload.long_stay_rate = n;
    }

    setSavingManagerPricing(true);
    try {
      const { data } = await api.patch(`/units/${manageUnit.id}/pricing`, payload);
      setSuccess("Pricing updated.");
      setUnits((prev) => prev.map((u) => (u.id === manageUnit.id ? data : u)));
      setManageUnit(data);
      setManageUnitForm((prev) => ({
        ...prev,
        base_rate: String(data.base_rate),
        weekend_rate: data.weekend_rate != null ? String(data.weekend_rate) : "",
        long_stay_min_nights: String(data.long_stay_min_nights),
        long_stay_rate: String(data.long_stay_rate),
      }));
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSavingManagerPricing(false);
    }
  }

  async function openManageUnit(unit: UnitOut) {
    setManageUnit(unit);
    setManageUnitForm({
      name: unit.name, unit_type: unit.unit_type, max_occupancy: String(unit.max_occupancy),
      base_rate: String(unit.base_rate), weekend_rate: unit.weekend_rate != null ? String(unit.weekend_rate) : "",
      long_stay_min_nights: String(unit.long_stay_min_nights), long_stay_rate: String(unit.long_stay_rate),
      unit_code: unit.unit_code ?? "", floor: unit.floor ?? "", flat_number: unit.flat_number ?? "", amenities: unit.amenities ?? "",
      description: unit.description ?? "", status: unit.status,
      bedroom_count: unit.bedroom_count != null ? String(unit.bedroom_count) : "",
      bathroom_count: unit.bathroom_count != null ? String(unit.bathroom_count) : "",
      house_rules: unit.house_rules ?? "", checkin_time: unit.checkin_time ?? "", checkout_time: unit.checkout_time ?? "",
    });
    setManageUnitTypeIsOther(!UNIT_TYPE_OPTIONS.some((o) => o.value === unit.unit_type));
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
    setManageUnitTypeIsOther(false);
    setUnitPhotos([]);
    setUnitPhotoThumbs({});
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
    // Same window.confirm() pattern as handleToggleActiveProperty/
    // handleToggleActiveUnit above — unlike those two (reversible
    // deactivate/reactivate), this delete is permanent: it removes both
    // the database record and the file on disk, with no recovery path.
    if (!window.confirm("Permanently delete this photo? This cannot be undone.")) return;
    setError(null);
    try {
      await api.delete(`/units/${manageUnit.id}/photos/${photoId}`);
      setUnitPhotos((prev) => prev.filter((p) => p.id !== photoId));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  // The raw write, with no refetch — callers that fire more than one write
  // (handleMoveUnitPhoto's swap) await all of them first, then refetch
  // exactly once, so a concurrent refetch can never read a half-applied
  // swap and briefly show the wrong order.
  async function patchUnitPhoto(photoId: string, patch: { caption?: string; sort_order?: number; is_primary?: boolean }) {
    if (!manageUnit) return;
    await api.patch(`/units/${manageUnit.id}/photos/${photoId}`, patch);
  }

  async function refetchUnitPhotos() {
    if (!manageUnit) return;
    const { data } = await api.get(`/units/${manageUnit.id}/photos`);
    setUnitPhotos(data);
  }

  async function handleUpdateUnitPhoto(photoId: string, patch: { caption?: string; sort_order?: number; is_primary?: boolean }) {
    setError(null);
    try {
      await patchUnitPhoto(photoId, patch);
      await refetchUnitPhotos();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function handleSetPrimaryPhoto(photoId: string) {
    return handleUpdateUnitPhoto(photoId, { is_primary: true });
  }

  // Move this photo earlier/later by swapping ARRAY-INDEX positions (not
  // raw sort_order values) with its neighbor in the current display order.
  // Every pre-migration photo starts at sort_order=0, so swapping the raw
  // values would be a no-op the first time anyone reorders — using the
  // index guarantees the two photos always end up with distinct values.
  async function handleMoveUnitPhoto(photoId: string, direction: "up" | "down") {
    const index = unitPhotos.findIndex((p) => p.id === photoId);
    const neighborIndex = direction === "up" ? index - 1 : index + 1;
    if (index === -1 || neighborIndex < 0 || neighborIndex >= unitPhotos.length) return;
    const current = unitPhotos[index];
    const neighbor = unitPhotos[neighborIndex];
    setError(null);
    try {
      await Promise.all([
        patchUnitPhoto(current.id, { sort_order: neighborIndex }),
        patchUnitPhoto(neighbor.id, { sort_order: index }),
      ]);
      await refetchUnitPhotos();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function openAddUnitDrawer() {
    setUnitForm({ property_id: "", name: "", unit_type: "", max_occupancy: "", base_rate: "", weekend_rate: "", unit_code: "", floor: "", flat_number: "", amenities: "", description: "" });
    setUnitTypeIsOther(false);
    setError(null);
    setAddUnitOpen(true);
  }

  async function handleCreateUnit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!unitForm.unit_type.trim()) {
      setError("Accommodation Type is required.");
      return;
    }
    const maxOccupancy = Number(unitForm.max_occupancy);
    if (!unitForm.max_occupancy || !Number.isInteger(maxOccupancy) || maxOccupancy < 1) {
      setError("Maximum Occupancy must be a whole number of 1 or more.");
      return;
    }

    try {
      await api.post("/units", {
        property_id: unitForm.property_id,
        name: unitForm.name,
        unit_type: unitForm.unit_type,
        max_occupancy: maxOccupancy,
        base_rate: Number(unitForm.base_rate),
        weekend_rate: unitForm.weekend_rate ? Number(unitForm.weekend_rate) : undefined,
        unit_code: unitForm.unit_code || undefined,
        floor: unitForm.floor || undefined,
        flat_number: unitForm.flat_number || undefined,
        amenities: unitForm.amenities || undefined,
        description: unitForm.description || undefined,
      });
      setSuccess("Accommodation created.");
      setUnitForm({ property_id: "", name: "", unit_type: "", max_occupancy: "", base_rate: "", weekend_rate: "", unit_code: "", floor: "", flat_number: "", amenities: "", description: "" });
      setUnitTypeIsOther(false);
      setAddUnitOpen(false);
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

  const filteredProperties = properties.filter(
    (p) =>
      !propSearch ||
      p.name.toLowerCase().includes(propSearch.toLowerCase()) ||
      (p.property_code ?? "").toLowerCase().includes(propSearch.toLowerCase()) ||
      (p.owner_name ?? "").toLowerCase().includes(propSearch.toLowerCase())
  );

  // Type filter options are derived from whatever unit_type values actually
  // exist right now — never a hardcoded list — so a brand-new future type
  // shows up automatically the moment a unit of that type is created.
  const presentUnitTypes = Array.from(new Set(units.map((u) => u.unit_type))).sort();

  const filteredUnits = units.filter((u) => {
    const q = unitSearch.toLowerCase();
    const matchesSearch =
      !unitSearch ||
      u.name.toLowerCase().includes(q) ||
      (u.unit_code ?? "").toLowerCase().includes(q) ||
      (u.floor ?? "").toLowerCase().includes(q);
    const matchesType = !unitTypeFilter || u.unit_type === unitTypeFilter;
    const matchesStatus =
      !unitStatusFilter ||
      (unitStatusFilter === "active" && u.is_active) ||
      (unitStatusFilter === "inactive" && !u.is_active) ||
      (["available", "maintenance", "blocked"].includes(unitStatusFilter) && u.is_active && u.status === unitStatusFilter);
    return matchesSearch && matchesType && matchesStatus;
  });

  return (
    <div>
      <PageHeader
        title="Properties & Accommodations"
        action={isAdmin && <Button onClick={() => setAddPropOpen(true)}>+ Add Property</Button>}
      />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      {/* ---- Properties ---- */}
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Properties</h2>
        <input
          placeholder="Search name, code, owner..."
          value={propSearch}
          onChange={(e) => setPropSearch(e.target.value)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs sm:min-w-[220px]"
        />
      </div>
      {filteredProperties.length === 0 ? (
        <Card className="mb-8">
          <EmptyState message={properties.length === 0 ? "No properties yet." : "No properties match your search."} />
        </Card>
      ) : (
        <div className="mb-8 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filteredProperties.map((p) => {
            const propUnits = units.filter((u) => u.property_id === p.id && u.is_active);
            const maxGuests = propUnits.reduce((sum, u) => sum + u.max_occupancy, 0);
            const avgRate = propUnits.length > 0 ? Math.round(propUnits.reduce((sum, u) => sum + Number(u.base_rate), 0) / propUnits.length) : 0;
            return (
              <PropertyCard
                key={p.id}
                property={p}
                unitCount={propUnits.length}
                maxGuests={maxGuests}
                avgRate={avgRate}
                thumbUrl={propertyThumbs[p.id]}
                onManage={() => openManage(p)}
              />
            );
          })}
        </div>
      )}

      {/* ---- Accommodations ---- */}
      <Card className="mb-8">
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <h2 className="text-sm font-semibold text-slate-900">Accommodations</h2>
          <div className="flex flex-wrap items-center gap-2">
            <input
              placeholder="Search name, code, floor..."
              value={unitSearch}
              onChange={(e) => setUnitSearch(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs sm:min-w-[200px]"
            />
            <select
              value={unitTypeFilter}
              onChange={(e) => setUnitTypeFilter(e.target.value)}
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
            >
              <option value="">All Types</option>
              {presentUnitTypes.map((t) => (
                <option key={t} value={t}>{typeLabel(UNIT_TYPE_OPTIONS, t)}</option>
              ))}
            </select>
            <select
              value={unitStatusFilter}
              onChange={(e) => setUnitStatusFilter(e.target.value)}
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
            >
              <option value="">All Status</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="available">Available</option>
              <option value="maintenance">Maintenance</option>
              <option value="blocked">Blocked</option>
            </select>
            {isAdmin && <Button onClick={openAddUnitDrawer}>+ Add Accommodation</Button>}
          </div>
        </div>

        {filteredUnits.length === 0 ? (
          <EmptyState message={units.length === 0 ? "No accommodations yet." : "No accommodations match your filters."} />
        ) : (
          <>
            {/* Desktop: efficient table layout */}
            <div className="hidden overflow-x-auto md:block">
              <Table headers={["Accommodation", "Property", "Type", "Max Guests", "Weekday Rate", "Weekend Rate", "Status", "Actions"]}>
                {filteredUnits.map((u) => (
                  <tr key={u.id}>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-400">
                          <CameraIcon className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                          <p className="truncate font-medium text-slate-800">{u.name}</p>
                          <p className="truncate text-xs text-slate-400">
                            {u.unit_code ?? (u.floor || u.flat_number ? `${u.floor ?? ""}${u.floor && u.flat_number ? " / " : ""}${u.flat_number ?? ""}` : "—")}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-slate-500">{propertyName(u.property_id)}</td>
                    <td className="px-4 py-2.5">
                      <Badge tone={unitTypeBadgeTone(u.unit_type)}>{typeLabel(UNIT_TYPE_OPTIONS, u.unit_type)}</Badge>
                    </td>
                    <td className="px-4 py-2.5 text-slate-500">{u.max_occupancy} Guests</td>
                    <td className="px-4 py-2.5 text-slate-500">₹{u.base_rate}</td>
                    <td className="px-4 py-2.5 text-slate-500">{u.weekend_rate ? `₹${u.weekend_rate}` : "—"}</td>
                    <td className="px-4 py-2.5">
                      {u.is_active ? (
                        <Badge tone={u.status === "available" ? "green" : "amber"}>{u.status}</Badge>
                      ) : (
                        <Badge tone="slate">Inactive</Badge>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5">
                      <Button variant="secondary" onClick={() => openManageUnit(u)}>{isAdmin ? "Edit" : isManager ? "Edit Pricing" : "View"}</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            </div>

            {/* Mobile: stacked cards */}
            <div className="space-y-3 md:hidden">
              {filteredUnits.map((u) => (
                <div key={u.id} className="rounded-xl border border-slate-200 p-3">
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-medium text-slate-800">{u.name}</p>
                      <p className="text-xs text-slate-400">{propertyName(u.property_id)}</p>
                    </div>
                    {u.is_active ? (
                      <Badge tone={u.status === "available" ? "green" : "amber"}>{u.status}</Badge>
                    ) : (
                      <Badge tone="slate">Inactive</Badge>
                    )}
                  </div>
                  <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <Badge tone={unitTypeBadgeTone(u.unit_type)}>{typeLabel(UNIT_TYPE_OPTIONS, u.unit_type)}</Badge>
                    <span>{u.max_occupancy} Guests</span>
                    <span>₹{u.base_rate} weekday</span>
                    {u.weekend_rate && <span>₹{u.weekend_rate} weekend</span>}
                  </div>
                  <Button variant="secondary" onClick={() => openManageUnit(u)} className="flex w-full items-center justify-center">
                    {isAdmin ? "Edit" : isManager ? "Edit Pricing" : "View"}
                  </Button>
                </div>
              ))}
            </div>
          </>
        )}
      </Card>

      {(isAdmin || isManager) && (
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

      {/* ---- Add Property drawer ---- */}
      {addPropOpen && (
        <Drawer title="Add Property" onClose={() => setAddPropOpen(false)}>
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
              <TypeField
                options={PROPERTY_TYPE_OPTIONS}
                value={propForm.property_type}
                onChange={(v) => setPropForm({ ...propForm, property_type: v })}
                isOther={propTypeIsOther}
                setIsOther={setPropTypeIsOther}
                placeholder="Property type (optional)"
                customPlaceholder="Custom property type"
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
            <div className="flex gap-2 pt-2">
              <Button type="button" variant="secondary" onClick={() => setAddPropOpen(false)} className="flex-1 flex items-center justify-center">
                Cancel
              </Button>
              <Button type="submit" className="flex-1 flex items-center justify-center">Create Property</Button>
            </div>
          </form>
        </Drawer>
      )}

      {/* ---- Add Accommodation drawer ---- */}
      {addUnitOpen && (
        <Drawer title="Add New Accommodation" onClose={() => setAddUnitOpen(false)}>
          <form onSubmit={handleCreateUnit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Property</label>
              <select
                required
                value={unitForm.property_id}
                onChange={(e) => setUnitForm({ ...unitForm, property_id: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">Select property...</option>
                {properties.filter((p) => p.is_active).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Accommodation Type</label>
              <UnitTypeIconSelector
                value={unitForm.unit_type}
                onChange={(v) => setUnitForm({ ...unitForm, unit_type: v })}
                isOther={unitTypeIsOther}
                setIsOther={setUnitTypeIsOther}
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Accommodation Name / Code</label>
              <input
                required
                placeholder="e.g. A1"
                value={unitForm.name}
                onChange={(e) => setUnitForm({ ...unitForm, name: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>

            <div className="grid grid-cols-3 gap-3">
              <input
                placeholder="Accommodation code"
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

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Maximum Occupancy</label>
              <input
                required
                type="number"
                min={1}
                step={1}
                placeholder="e.g. 2"
                value={unitForm.max_occupancy}
                onChange={(e) => setUnitForm({ ...unitForm, max_occupancy: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Weekday Rate / night</label>
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
                <label className="mb-1 block text-xs font-medium text-slate-500">Weekend Rate (optional)</label>
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

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Amenities</label>
              <input
                placeholder="e.g. AC, WiFi, Kitchen"
                value={unitForm.amenities}
                onChange={(e) => setUnitForm({ ...unitForm, amenities: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Description</label>
              <textarea
                placeholder="Optional long-form listing copy"
                value={unitForm.description}
                onChange={(e) => setUnitForm({ ...unitForm, description: e.target.value })}
                rows={3}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
            </div>

            <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-2.5 text-xs text-slate-500">
              Photos can be added after this accommodation is created — open it from the list and use the Photos section.
            </div>

            <div className="flex gap-2 pt-2">
              <Button type="button" variant="secondary" onClick={() => setAddUnitOpen(false)} className="flex-1 flex items-center justify-center">
                Cancel
              </Button>
              <Button type="submit" className="flex-1 flex items-center justify-center">Save Unit</Button>
            </div>
          </form>
        </Drawer>
      )}

      {/* ---- Manage Property modal ---- */}
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
              <TypeField
                options={PROPERTY_TYPE_OPTIONS}
                value={manageForm.property_type ?? ""}
                onChange={(v) => setManageForm({ ...manageForm, property_type: v })}
                isOther={managePropTypeIsOther}
                setIsOther={setManagePropTypeIsOther}
                placeholder="Property type"
                customPlaceholder="Custom property type"
              />
              <input placeholder="Owner name" value={manageForm.owner_name ?? ""} onChange={(e) => setManageForm({ ...manageForm, owner_name: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Owner phone" value={manageForm.owner_phone ?? ""} onChange={(e) => setManageForm({ ...manageForm, owner_phone: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Owner email" value={manageForm.owner_email ?? ""} onChange={(e) => setManageForm({ ...manageForm, owner_email: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
              <input placeholder="Google Maps link" value={manageForm.google_maps_url ?? ""} onChange={(e) => setManageForm({ ...manageForm, google_maps_url: e.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5" />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="secondary" onClick={handleSaveManage}>Save Changes</Button>
              <Badge tone={manageProp.is_active ? "green" : "slate"}>{manageProp.is_active ? "Active" : "Inactive"}</Badge>
              {isAdmin && (
                <Button variant={manageProp.is_active ? "danger" : "secondary"} onClick={() => handleToggleActiveProperty(manageProp)}>
                  {manageProp.is_active ? "Deactivate" : "Reactivate"}
                </Button>
              )}
            </div>

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
                <p>
                  Central / Property-wide Expenses: <span className="font-semibold text-slate-800">₹{propPnl.central_expenses.toLocaleString()}</span>
                  <span className="text-slate-400"> (not allocated to any apartment)</span>
                </p>
                <p>Total Expenses: <span className="font-semibold text-slate-800">₹{propPnl.total_expenses.toLocaleString()}</span></p>
                <p>Net Profit: <span className={`font-semibold ${propPnl.net_profit >= 0 ? "text-emerald-600" : "text-red-600"}`}>₹{propPnl.net_profit.toLocaleString()}</span></p>
                <p className="text-slate-400">Across {propPnl.units.length} unit(s) this period.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ---- Edit Accommodation drawer ---- */}
      {manageUnit && (
        <Drawer title={`${isAdmin ? "Edit" : isManager ? "Edit Pricing" : "View"} — ${manageUnit.name}`} onClose={closeManageUnit}>
          {isAdmin && (
            <div className="mb-6 space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Name</label>
                <input placeholder="Name" value={manageUnitForm.name ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, name: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Accommodation Type</label>
                <UnitTypeIconSelector
                  value={manageUnitForm.unit_type ?? ""}
                  onChange={(v) => setManageUnitForm({ ...manageUnitForm, unit_type: v })}
                  isOther={manageUnitTypeIsOther}
                  setIsOther={setManageUnitTypeIsOther}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Max Occupancy</label>
                  <input type="number" min={1} placeholder="Max occupancy" value={manageUnitForm.max_occupancy ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, max_occupancy: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Availability Status</label>
                  <select value={manageUnitForm.status ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, status: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                    <option value="available">Available</option>
                    <option value="maintenance">Maintenance</option>
                    <option value="blocked">Blocked</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Weekday Rate / night</label>
                  <input type="number" placeholder="Weekday rate" value={manageUnitForm.base_rate ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, base_rate: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Weekend Rate (optional)</label>
                  <input type="number" placeholder="Weekend rate" value={manageUnitForm.weekend_rate ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, weekend_rate: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Long-Stay Min. Nights</label>
                  <input type="number" min={1} step="1" placeholder="30" value={manageUnitForm.long_stay_min_nights ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, long_stay_min_nights: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Long-Stay Rate / night</label>
                  <input type="number" min={0} step="0.01" placeholder="2000" value={manageUnitForm.long_stay_rate ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, long_stay_rate: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                  <p className="mt-1 text-xs text-slate-400">Flat rate for every night once a stay reaches the minimum — not a discount.</p>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <input placeholder="Accommodation code" value={manageUnitForm.unit_code ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, unit_code: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                <input placeholder="Floor" value={manageUnitForm.floor ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, floor: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                <input placeholder="Flat number" value={manageUnitForm.flat_number ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, flat_number: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Amenities</label>
                <input placeholder="Amenities" value={manageUnitForm.amenities ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, amenities: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Description</label>
                <textarea
                  placeholder="Description (long-form listing copy)"
                  value={manageUnitForm.description ?? ""}
                  onChange={(e) => setManageUnitForm({ ...manageUnitForm, description: e.target.value })}
                  rows={3}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Bedrooms</label>
                  <input type="number" min={0} step="1" placeholder="e.g. 2" value={manageUnitForm.bedroom_count ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, bedroom_count: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Bathrooms</label>
                  <input type="number" min={0} step="1" placeholder="e.g. 2" value={manageUnitForm.bathroom_count ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, bathroom_count: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Check-in Time</label>
                  <input placeholder="e.g. 12:30 PM" value={manageUnitForm.checkin_time ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, checkin_time: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                  <p className="mt-1 text-xs text-slate-400">Leave blank to use the site-wide default.</p>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Check-out Time</label>
                  <input placeholder="e.g. 11:00 AM" value={manageUnitForm.checkout_time ?? ""} onChange={(e) => setManageUnitForm({ ...manageUnitForm, checkout_time: e.target.value })} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
                  <p className="mt-1 text-xs text-slate-400">Leave blank to use the site-wide default.</p>
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">House Rules (this accommodation)</label>
                <textarea
                  placeholder="Leave blank to use the business-wide house rules from Settings"
                  value={manageUnitForm.house_rules ?? ""}
                  onChange={(e) => setManageUnitForm({ ...manageUnitForm, house_rules: e.target.value })}
                  rows={2}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </div>

              <div className="flex flex-wrap items-center gap-3 pt-1">
                <Button onClick={handleSaveManageUnit}>Save Changes</Button>
                <Badge tone={manageUnit.is_active ? "green" : "slate"}>{manageUnit.is_active ? "Active" : "Inactive"}</Badge>
                <Button variant={manageUnit.is_active ? "danger" : "secondary"} onClick={() => handleToggleActiveUnit(manageUnit)}>
                  {manageUnit.is_active ? "Deactivate" : "Reactivate"}
                </Button>
              </div>
            </div>
          )}

          {!isAdmin && !isManager && (
            <div className="mb-6 space-y-2 text-sm">
              <p><span className="text-slate-500">Type:</span> {typeLabel(UNIT_TYPE_OPTIONS, manageUnit.unit_type)}</p>
              <p><span className="text-slate-500">Max Guests:</span> {manageUnit.max_occupancy}</p>
              <p><span className="text-slate-500">Weekday Rate:</span> ₹{manageUnit.base_rate}</p>
              {manageUnit.weekend_rate && <p><span className="text-slate-500">Weekend Rate:</span> ₹{manageUnit.weekend_rate}</p>}
              <p><span className="text-slate-500">Long-Stay Rate:</span> ₹{manageUnit.long_stay_rate} / night at {manageUnit.long_stay_min_nights}+ nights</p>
              {manageUnit.amenities && <p><span className="text-slate-500">Amenities:</span> {manageUnit.amenities}</p>}
              {manageUnit.description && <p className="text-slate-500">{manageUnit.description}</p>}
            </div>
          )}

          {/* RBAC Phase B: Manager pricing UI — everything else about the
              unit stays read-only (same info Staff sees above); only
              base_rate/weekend_rate are editable, and only through
              PATCH /units/{id}/pricing (handleSaveManagerPricing), never
              the full unit-edit endpoint. */}
          {isManager && (
            <div className="mb-6 space-y-4 text-sm">
              <p><span className="text-slate-500">Type:</span> {typeLabel(UNIT_TYPE_OPTIONS, manageUnit.unit_type)}</p>
              <p><span className="text-slate-500">Max Guests:</span> {manageUnit.max_occupancy}</p>
              {manageUnit.amenities && <p><span className="text-slate-500">Amenities:</span> {manageUnit.amenities}</p>}
              {manageUnit.description && <p className="text-slate-500">{manageUnit.description}</p>}

              <div className="grid grid-cols-2 gap-3 border-t border-slate-100 pt-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Weekday Rate / night</label>
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    placeholder="Weekday rate"
                    value={manageUnitForm.base_rate ?? ""}
                    onChange={(e) => setManageUnitForm({ ...manageUnitForm, base_rate: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Weekend Rate (optional)</label>
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    placeholder="Weekend rate"
                    value={manageUnitForm.weekend_rate ?? ""}
                    onChange={(e) => setManageUnitForm({ ...manageUnitForm, weekend_rate: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Long-Stay Min. Nights</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    placeholder="30"
                    value={manageUnitForm.long_stay_min_nights ?? ""}
                    onChange={(e) => setManageUnitForm({ ...manageUnitForm, long_stay_min_nights: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">Long-Stay Rate / night</label>
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    placeholder="2000"
                    value={manageUnitForm.long_stay_rate ?? ""}
                    onChange={(e) => setManageUnitForm({ ...manageUnitForm, long_stay_rate: e.target.value })}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </div>
              </div>
              <p className="text-xs text-slate-400">Long-stay rate applies to every night once a stay reaches the minimum — a flat override, not a discount.</p>
              <Button onClick={handleSaveManagerPricing} disabled={savingManagerPricing}>
                {savingManagerPricing ? "Saving..." : "Save Pricing"}
              </Button>
            </div>
          )}

          <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-900">
            <CameraIcon className="h-4 w-4 text-slate-400" /> Photos
          </h3>
          {unitPhotos.length === 0 ? (
            <EmptyState message="No photos uploaded yet." />
          ) : (
            <div className="mb-3 grid grid-cols-3 gap-3">
              {/* unitPhotos is already server-ordered (primary first, then
                  sort_order) — the same order the public website displays,
                  so what the admin sees here is exactly what visitors see. */}
              {unitPhotos.map((photo, i) => (
                <div key={photo.id} className="group relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
                  <div className="relative aspect-square">
                    {unitPhotoThumbs[photo.id] ? (
                      <img src={unitPhotoThumbs[photo.id]} alt={photo.caption ?? photo.filename} className="h-full w-full object-cover" />
                    ) : (
                      <div className="flex h-full items-center justify-center text-slate-300">
                        <CameraIcon className="h-6 w-6" />
                      </div>
                    )}
                    {photo.is_primary && (
                      <span className="absolute top-1 left-1 rounded bg-amber-400 px-1.5 py-0.5 text-[10px] font-semibold text-amber-950">Cover photo</span>
                    )}
                    <div className="absolute inset-x-0 bottom-0 flex flex-wrap justify-end gap-1 bg-gradient-to-t from-black/60 to-transparent p-1 opacity-0 transition group-hover:opacity-100">
                      <button onClick={() => handleDownloadUnitPhoto(photo.id, photo.filename)} className="rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-slate-700 hover:bg-white">
                        Download
                      </button>
                      {/* RBAC Phase B security fix (finding I-6, NOW action):
                          unit photos are benign, operational content — Manager
                          already has POST/DELETE .../photos on the backend;
                          this just exposes the matching UI control. Property
                          files/documents are untouched (separate section,
                          separate — unresolved — finding I-6 FUTURE item).

                          Photo-delete safety review: the backend DELETE
                          .../photos/{id} route also grants "staff" (see
                          require_role("admin", "staff", "manager") in
                          properties/router.py), but this UI has never shown
                          Delete to Staff — that gap predates this review, and
                          no RBAC test or spec found here establishes Staff
                          should be excluded from deleting photos specifically.
                          Left exactly as-is (not widened to Staff, not
                          narrowed on the backend) since resolving that
                          discrepancy is a real authorization decision, not a
                          side effect of adding a delete confirmation. */}
                      {(isAdmin || isManager) && (
                        <>
                          {!photo.is_primary && (
                            <button onClick={() => handleSetPrimaryPhoto(photo.id)} className="rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-slate-700 hover:bg-white">
                              Set as cover
                            </button>
                          )}
                          <button
                            disabled={i === 0}
                            onClick={() => handleMoveUnitPhoto(photo.id, "up")}
                            className="rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-slate-700 hover:bg-white disabled:opacity-40"
                          >
                            ↑
                          </button>
                          <button
                            disabled={i === unitPhotos.length - 1}
                            onClick={() => handleMoveUnitPhoto(photo.id, "down")}
                            className="rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-slate-700 hover:bg-white disabled:opacity-40"
                          >
                            ↓
                          </button>
                          <button onClick={() => handleDeleteUnitPhoto(photo.id)} className="rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-red-600 hover:bg-white">
                            Delete
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  {(isAdmin || isManager) && (
                    <input
                      defaultValue={photo.caption ?? ""}
                      onBlur={(e) => {
                        if (e.target.value !== (photo.caption ?? "")) handleUpdateUnitPhoto(photo.id, { caption: e.target.value });
                      }}
                      placeholder="Alt text / caption"
                      className="w-full border-t border-slate-200 px-1.5 py-1 text-[10px] text-slate-600 focus:outline-none"
                    />
                  )}
                </div>
              ))}
            </div>
          )}
          {(isAdmin || isManager) && (
            <div className="mb-6 flex flex-wrap gap-2">
              <input placeholder="Caption (optional)" value={unitPhotoCaption} onChange={(e) => setUnitPhotoCaption(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs" />
              <input type="file" accept="image/*" onChange={(e) => setUnitUploadFile(e.target.files?.[0] ?? null)} className="text-xs" />
              <Button variant="secondary" onClick={handleUploadUnitPhoto}>Upload</Button>
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
        </Drawer>
      )}
    </div>
  );
}
