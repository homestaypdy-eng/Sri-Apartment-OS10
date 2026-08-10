import { useEffect, useState } from "react";
import { api, extractErrorMessage, type GuestOut } from "../lib/api";
import { Button, Badge } from "./ui";

/**
 * Reusable searchable guest picker — replaces the old flat "load every
 * guest" <select>. Never fetches the full guest list; only searches
 * on-demand via the existing GET /guests?search= endpoint, debounced the
 * same way availability-checking already is elsewhere in this app.
 *
 * Deliberately renders no <form> of its own — this is always embedded
 * inside a parent <form> (New Booking / Edit Booking), and nested <form>
 * elements are invalid HTML.
 */
export default function GuestPicker({
  selectedGuest,
  onSelect,
  onClear,
  setError,
}: {
  selectedGuest: GuestOut | null;
  onSelect: (guest: GuestOut) => void;
  onClear: () => void;
  setError: (e: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GuestOut[]>([]);
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ name: "", phone: "", email: "" });
  const [creating, setCreating] = useState(false);
  const [duplicateMatch, setDuplicateMatch] = useState<GuestOut | null>(null);
  const [checkingDuplicate, setCheckingDuplicate] = useState(false);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      setHasSearched(false);
      return;
    }
    setSearching(true);
    const timeout = setTimeout(async () => {
      try {
        const { data } = await api.get("/guests", { params: { search: query.trim(), limit: 20 } });
        setResults(data);
      } catch (err) {
        setError(extractErrorMessage(err));
        setResults([]);
      } finally {
        setSearching(false);
        setHasSearched(true);
      }
    }, 300);
    return () => clearTimeout(timeout);
  }, [query, setError]);

  // Duplicate-phone check while creating a new guest — a nudge, never a
  // block. A failure here must not prevent guest creation from proceeding.
  useEffect(() => {
    if (!showCreate || createForm.phone.trim().length < 6) {
      setDuplicateMatch(null);
      return;
    }
    setCheckingDuplicate(true);
    const timeout = setTimeout(async () => {
      try {
        const { data } = await api.get("/guests", { params: { search: createForm.phone.trim(), limit: 5 } });
        const normalized = createForm.phone.trim().replace(/\s+/g, "");
        const exact = (data as GuestOut[]).find((g) => g.phone.replace(/\s+/g, "") === normalized);
        setDuplicateMatch(exact ?? null);
      } catch {
        setDuplicateMatch(null);
      } finally {
        setCheckingDuplicate(false);
      }
    }, 300);
    return () => clearTimeout(timeout);
  }, [createForm.phone, showCreate]);

  function resetCreateState() {
    setShowCreate(false);
    setCreateForm({ name: "", phone: "", email: "" });
    setDuplicateMatch(null);
    setQuery("");
    setResults([]);
  }

  async function handleCreateGuest() {
    if (!createForm.name.trim() || !createForm.phone.trim()) {
      setError("Name and phone are required to create a guest.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const { data } = await api.post("/guests", {
        name: createForm.name.trim(),
        phone: createForm.phone.trim(),
        email: createForm.email.trim() || undefined,
      });
      onSelect(data);
      resetCreateState();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  function selectGuest(guest: GuestOut) {
    onSelect(guest);
    setQuery("");
    setResults([]);
  }

  if (selectedGuest) {
    return (
      <div className="rounded-lg border border-slate-300 px-3 py-2 text-sm bg-slate-50">
        <p className="text-xs text-slate-400 mb-0.5">Selected Guest</p>
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="font-medium text-slate-800 flex items-center flex-wrap gap-1.5">
              {selectedGuest.name}
              {selectedGuest.is_vip && <Badge tone="amber">VIP</Badge>}
              {selectedGuest.is_blacklisted && <Badge tone="red">Blacklisted</Badge>}
            </p>
            <p className="text-slate-500 text-xs">
              {selectedGuest.phone}
              {selectedGuest.email ? ` · ${selectedGuest.email}` : ""}
            </p>
          </div>
          <button type="button" onClick={onClear} className="text-xs text-slate-400 hover:text-slate-600 underline whitespace-nowrap">
            Change
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          placeholder="Search guest by name, phone or email"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <Button type="button" variant="secondary" onClick={() => setShowCreate(!showCreate)}>
          + Create New Guest
        </Button>
      </div>

      {!showCreate && (
        <>
          {searching && <p className="text-xs text-slate-400">Searching...</p>}
          {!searching && hasSearched && query.trim() && results.length === 0 && (
            <p className="text-xs text-slate-400">No guests match "{query.trim()}". Try a different search, or create a new guest.</p>
          )}
          {!searching && results.length > 0 && (
            <div className="border border-slate-200 rounded-lg divide-y divide-slate-100 max-h-56 overflow-y-auto">
              {results.map((g) => (
                <button
                  type="button"
                  key={g.id}
                  onClick={() => selectGuest(g)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50"
                >
                  <p className="font-medium text-slate-800 flex items-center flex-wrap gap-1.5">
                    {g.name}
                    {g.is_vip && <Badge tone="amber">VIP</Badge>}
                    {g.is_blacklisted && <Badge tone="red">Blacklisted</Badge>}
                  </p>
                  <p className="text-slate-500 text-xs">
                    {g.phone}
                    {g.email ? ` · ${g.email}` : ""}
                  </p>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {showCreate && (
        <div className="border border-slate-200 rounded-lg p-3 space-y-2 bg-slate-50">
          <p className="text-xs font-semibold text-slate-700 uppercase tracking-wide">New Guest</p>
          <input
            required
            placeholder="Full name"
            value={createForm.name}
            onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <input
            required
            placeholder="Phone"
            value={createForm.phone}
            onChange={(e) => setCreateForm({ ...createForm, phone: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <input
            type="email"
            placeholder="Email (optional)"
            value={createForm.email}
            onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />

          {checkingDuplicate && <p className="text-xs text-slate-400">Checking for existing guests with this phone number...</p>}
          {duplicateMatch && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-2 text-xs">
              <p className="text-amber-700 mb-1">A guest with this phone number already exists.</p>
              <p className="font-medium text-slate-800">{duplicateMatch.name}</p>
              <p className="text-slate-500">
                {duplicateMatch.phone}
                {duplicateMatch.email ? ` · ${duplicateMatch.email}` : ""}
              </p>
              <button type="button" onClick={() => { selectGuest(duplicateMatch); resetCreateState(); }} className="mt-1 text-emerald-600 hover:underline">
                Select Existing Guest
              </button>
              <span className="text-slate-400"> — or continue below to create a new guest anyway (e.g. family members sharing a number).</span>
            </div>
          )}

          <div className="flex gap-3 items-center">
            <Button type="button" onClick={handleCreateGuest} disabled={creating}>
              {creating ? "Creating..." : "Create & Select Guest"}
            </Button>
            <button
              type="button"
              onClick={() => { setShowCreate(false); setCreateForm({ name: "", phone: "", email: "" }); setDuplicateMatch(null); }}
              className="text-xs text-slate-500 hover:underline"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
