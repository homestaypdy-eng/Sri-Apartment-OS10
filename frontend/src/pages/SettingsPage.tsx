import { useEffect, useState, type FormEvent } from "react";
import { api, extractErrorMessage, type BankSettingsOut, type BusinessSettingsOut, type SystemInfoOut, type RolesPermissionsOut } from "../lib/api";
import { Card, PageHeader, ErrorBanner, SuccessBanner, Button, Badge } from "../components/ui";

export default function SettingsPage() {
  const [settings, setSettings] = useState<BusinessSettingsOut | null>(null);
  const [form, setForm] = useState<Partial<BusinessSettingsOut>>({});
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [qrFile, setQrFile] = useState<File | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfoOut | null>(null);
  const [rolesInfo, setRolesInfo] = useState<RolesPermissionsOut | null>(null);
  // RBAC Phase A: bank/UPI details are Admin-only (see BankSettings) — kept
  // as their own state/form/save flow, entirely separate from the general
  // settings form above, since they're a different backend resource with
  // their own endpoint and their own access boundary. Staying null (the
  // GET below 403ing) is what keeps this section hidden from Staff/Manager,
  // the same pattern already used for systemInfo above.
  const [bankSettings, setBankSettings] = useState<BankSettingsOut | null>(null);
  const [bankForm, setBankForm] = useState<Partial<BankSettingsOut>>({});
  const [savingBank, setSavingBank] = useState(false);

  async function load() {
    try {
      const { data } = await api.get("/settings");
      setSettings(data);
      setForm(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
    try {
      const { data } = await api.get("/settings/bank");
      setBankSettings(data);
      setBankForm(data);
    } catch {
      // admin-only; staff/manager simply won't see this section
    }
    try {
      const { data } = await api.get("/settings/system-info");
      setSystemInfo(data);
    } catch {
      // admin-only; staff simply won't see this section
    }
    try {
      const { data } = await api.get("/settings/roles-permissions");
      setRolesInfo(data);
    } catch {
      // best-effort
    }
  }

  useEffect(() => {
    load();
  }, []);

  function set<K extends keyof BusinessSettingsOut>(key: K, value: BusinessSettingsOut[K]) {
    setForm({ ...form, [key]: value });
  }

  function setBank<K extends keyof BankSettingsOut>(key: K, value: BankSettingsOut[K]) {
    setBankForm({ ...bankForm, [key]: value });
  }

  async function handleUploadLogo() {
    if (!logoFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", logoFile);
      const { data } = await api.post("/settings/logo", formData, { headers: { "Content-Type": "multipart/form-data" } });
      setSettings(data);
      setLogoFile(null);
      setSuccess("Logo uploaded.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleUploadQr() {
    if (!qrFile) return;
    setError(null);
    setSuccess(null);
    try {
      const formData = new FormData();
      formData.append("file", qrFile);
      const { data } = await api.post("/settings/qr-code", formData, { headers: { "Content-Type": "multipart/form-data" } });
      setSettings(data);
      setQrFile(null);
      setSuccess("QR code uploaded.");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDownloadBackup() {
    setError(null);
    try {
      const res = await api.get("/settings/backup", { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `sri_apartment_backup_${new Date().toISOString().slice(0, 10)}.json`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setSaving(true);
    try {
      const { data } = await api.put("/settings", form);
      setSettings(data);
      setForm(data);
      setSuccess("Settings saved.");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveBank() {
    setError(null);
    setSuccess(null);
    setSavingBank(true);
    try {
      const { data } = await api.put("/settings/bank", bankForm);
      setBankSettings(data);
      setBankForm(data);
      setSuccess("Bank details saved.");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSavingBank(false);
    }
  }

  if (!settings) {
    return (
      <div>
        <PageHeader title="Settings" />
        <ErrorBanner message={error} />
        <p className="text-sm text-slate-400">Loading...</p>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Settings" />
      <ErrorBanner message={error} />
      <SuccessBanner message={success} />

      <Card className="mb-6 bg-emerald-50 border-emerald-200">
        <p className="text-xs text-emerald-800">
          These settings drive the company/bank/document text shown on generated pre-booking quotation PDFs —
          changes here take effect on the next quotation you generate. GST tax invoices intentionally source their
          company/GSTIN details from each booking's own Property record instead (not Settings), since a business
          with multiple properties would otherwise have every invoice show the same single GSTIN regardless of
          which property the guest actually stayed at.
        </p>
      </Card>

      <form onSubmit={handleSave} className="space-y-6">
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Company Details</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Company Name" value={form.company_name} onChange={(v) => set("company_name", v)} />
            <Field label="Tagline" value={form.tagline} onChange={(v) => set("tagline", v)} />
            <Field label="Contact Phone" value={form.contact_phone} onChange={(v) => set("contact_phone", v)} />
            <Field label="Contact Email" value={form.contact_email} onChange={(v) => set("contact_email", v)} />
            <Field label="GST Number" value={form.gst_number} onChange={(v) => set("gst_number", v)} />
            <Field label="UDYAM Number" value={form.udyam_number} onChange={(v) => set("udyam_number", v)} />
            <Field label="State Code" value={form.state_code} onChange={(v) => set("state_code", v)} />
          </div>
          <div className="mt-3">
            <label className="block text-xs text-slate-500 mb-1">Address</label>
            <textarea
              value={form.address || ""}
              onChange={(e) => set("address", e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              rows={2}
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Theme Color</label>
              <input
                type="color"
                value={form.theme_color || "#0f172a"}
                onChange={(e) => set("theme_color", e.target.value)}
                className="w-full h-9 rounded-lg border border-slate-300"
              />
            </div>
            <Field label="WhatsApp Business Number" value={form.whatsapp_business_number ?? undefined} onChange={(v) => set("whatsapp_business_number", v)} />
          </div>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Logo &amp; QR Code</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Logo {settings.logo_path && <span className="text-emerald-600">(uploaded)</span>}</label>
              <div className="flex gap-2">
                <input type="file" accept="image/*" onChange={(e) => setLogoFile(e.target.files?.[0] ?? null)} className="text-xs flex-1" />
                <Button type="button" variant="secondary" onClick={handleUploadLogo}>Upload</Button>
              </div>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Payment QR Code {settings.qr_code_path && <span className="text-emerald-600">(uploaded)</span>}</label>
              <div className="flex gap-2">
                <input type="file" accept="image/*" onChange={(e) => setQrFile(e.target.files?.[0] ?? null)} className="text-xs flex-1" />
                <Button type="button" variant="secondary" onClick={handleUploadQr}>Upload</Button>
              </div>
            </div>
          </div>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Signatory</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Signatory Name" value={form.signatory_name} onChange={(v) => set("signatory_name", v)} />
            <Field label="Signatory Title" value={form.signatory_title} onChange={(v) => set("signatory_title", v)} />
          </div>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Document Text</h2>
          <div className="space-y-3">
            <TextAreaField label="Quotation Terms" value={form.quotation_terms} onChange={(v) => set("quotation_terms", v)} />
            <TextAreaField label="House Rules" value={form.house_rules} onChange={(v) => set("house_rules", v)} />
            <TextAreaField label="Cancellation Policy" value={form.cancellation_policy} onChange={(v) => set("cancellation_policy", v)} />
            <TextAreaField label="Invoice Terms" value={form.invoice_terms} onChange={(v) => set("invoice_terms", v)} />
          </div>
        </Card>

        <Button type="submit" disabled={saving}>
          {saving ? "Saving..." : "Save Settings"}
        </Button>
      </form>

      {/* RBAC Phase A: Admin-only, deliberately separate from the form
          above — this saves to /settings/bank, a different endpoint with
          a stricter access boundary than general settings. Rendering only
          when bankSettings is non-null (i.e. the GET above succeeded) is
          what keeps this section invisible to Staff/Manager, exactly like
          the System Status section below already does for systemInfo. */}
      {bankSettings && (
        <Card className="mt-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Bank &amp; UPI Details</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Bank Name" value={bankForm.bank_name} onChange={(v) => setBank("bank_name", v)} />
            <Field label="Account Name" value={bankForm.bank_account_name} onChange={(v) => setBank("bank_account_name", v)} />
            <Field label="Account Number" value={bankForm.bank_account_number} onChange={(v) => setBank("bank_account_number", v)} />
            <Field label="IFSC Code" value={bankForm.bank_ifsc} onChange={(v) => setBank("bank_ifsc", v)} />
            <Field label="Branch" value={bankForm.bank_branch} onChange={(v) => setBank("bank_branch", v)} />
            <Field label="UPI ID" value={bankForm.upi_id} onChange={(v) => setBank("upi_id", v)} />
          </div>
          <div className="mt-4">
            <Button type="button" onClick={handleSaveBank} disabled={savingBank}>
              {savingBank ? "Saving..." : "Save Bank Details"}
            </Button>
          </div>
        </Card>
      )}

      {systemInfo && (
        <Card className="mt-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">System Status</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <div>
              <p className="text-slate-400 mb-1">Environment</p>
              <Badge tone="slate">{systemInfo.environment}</Badge>
            </div>
            <div>
              <p className="text-slate-400 mb-1">Version</p>
              <span className="text-slate-700">{systemInfo.app_version}</span>
            </div>
            <div>
              <p className="text-slate-400 mb-1">Database</p>
              <Badge tone={systemInfo.database_connected ? "green" : "red"}>{systemInfo.database_connected ? "Connected" : "Disconnected"}</Badge>
            </div>
            <div>
              <p className="text-slate-400 mb-1">Email</p>
              <Badge tone={systemInfo.email_configured ? "green" : "amber"}>{systemInfo.email_configured ? "Configured" : "Not configured"}</Badge>
            </div>
            <div>
              <p className="text-slate-400 mb-1">SMS</p>
              <Badge tone={systemInfo.sms_configured ? "green" : "amber"}>{systemInfo.sms_configured ? "Configured" : "Not configured"}</Badge>
            </div>
            <div>
              <p className="text-slate-400 mb-1">WhatsApp</p>
              <Badge tone="green">Available</Badge>
            </div>
          </div>
        </Card>
      )}

      <Card className="mt-6">
        <h2 className="text-sm font-semibold text-slate-900 mb-2">Backup</h2>
        <p className="text-xs text-slate-400 mb-3">
          Download a full export of all business data as a JSON file. Restoring from a backup is an
          infrastructure-level operation (see deployment docs) — not a self-service action here, since
          reloading the wrong file into a live system could destroy real data.
        </p>
        <Button variant="secondary" onClick={handleDownloadBackup}>Download Backup</Button>
      </Card>

      {rolesInfo && (
        <Card className="mt-6">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Roles &amp; Permissions</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {rolesInfo.roles.map((r) => (
              <div key={r.role} className="border border-slate-100 rounded-lg p-3">
                <p className="text-sm font-semibold text-slate-800 capitalize mb-1">{r.role}</p>
                <p className="text-xs text-slate-400 mb-2">{r.description}</p>
                <p className="text-xs font-medium text-emerald-700 mb-1">Can do:</p>
                <ul className="text-xs text-slate-600 list-disc list-inside mb-2">
                  {r.can_do.map((item, i) => <li key={i}>{item}</li>)}
                </ul>
                {r.cannot_do.length > 0 && (
                  <>
                    <p className="text-xs font-medium text-red-600 mb-1">Cannot do:</p>
                    <ul className="text-xs text-slate-600 list-disc list-inside">
                      {r.cannot_do.map((item, i) => <li key={i}>{item}</li>)}
                    </ul>
                  </>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value?: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs text-slate-500 mb-1">{label}</label>
      <input
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
      />
    </div>
  );
}

function TextAreaField({ label, value, onChange }: { label: string; value?: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs text-slate-500 mb-1">{label}</label>
      <textarea
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        rows={3}
      />
    </div>
  );
}
