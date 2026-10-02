import { useEffect, useMemo, useRef, useState } from "react";
import { api, extractErrorMessage } from "../lib/api";
import type { UnitOut, BookingOut, GuestOut } from "../lib/api";
import { Card, PageHeader, Button, ErrorBanner, EmptyState, SkeletonLine } from "../components/ui";

/**
 * Per-unit Availability Calendar (OS10 Phase — frontend only).
 *
 * Replaces the Dashboard's aggregate check-in/check-out COUNT widget (which
 * stays exactly as-is — this is a new, separate page) with a real per-unit,
 * per-day grid, built entirely from data the backend already computes:
 *
 *  - unit.status ("available" / "maintenance" / "blocked") from GET /units
 *    — a whole-unit, indefinite state (Phase 5A), so it applies to every
 *    day in a maintenance/blocked unit's row, never invented per-day.
 *  - confirmed/checked_in Bookings from GET /bookings, matched to a day by
 *    date-range containment — the exact same statuses
 *    app/shared/availability.py's own overlap query treats as occupying.
 *  - GET /units/availability (the existing Phase 5A-aware batch endpoint),
 *    called once per visible day (not once for the whole range, since a
 *    single call for a multi-day range only returns one aggregate
 *    true/false covering that ENTIRE span — not what a day-by-day grid
 *    needs). Every value shown is what this endpoint actually returned;
 *    nothing here recomputes availability.
 *
 * HELD: no endpoint exists anywhere for OS10 staff to list individual
 * active BookingHold rows (confirmed by reading public_booking/router.py
 * and bookings/router.py — there is no such GET endpoint). A "held" cell
 * is therefore shown by elimination, using only real backend facts: the
 * unit's status is "available", no fetched Booking covers that day, yet
 * GET /units/availability reports the day unavailable for that unit — the
 * backend's own documented logic (see shared/availability.py) means the
 * only remaining cause is an active hold. This is reading three real
 * backend answers together, not a frontend-invented availability rule.
 * Guest-level hold detail (who, contact info) is not available from any
 * endpoint and is correctly never shown.
 */

const WINDOW_DAYS = 14;
const UNIT_ORDER = ["A1", "A2", "B3", "B4", "C5", "C6"];

type CellStatus = "available" | "booked" | "held" | "maintenance" | "blocked" | "loading" | "unknown";

interface DayAvailabilityRow {
  unit_id: string;
  name: string;
  unit_code: string | null;
  available: boolean;
}

interface SelectedCell {
  unitId: string;
  unitName: string;
  date: string;
  // Never "loading" — openCell() only ever sets this once cellStatus() has
  // resolved to a real, displayable status.
  status: Exclude<CellStatus, "loading">;
}

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

function startOfToday(): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

// Locale-independent formatting: `toLocaleDateString(undefined, ...)` falls
// back to the browser/OS locale, which produced French weekday/month names
// ("dim. 27 sept.") on a fr-* machine. A fixed "en-US" Intl formatter always
// gives English 3-letter names regardless of the viewer's system locale, and
// the day-before-month order this page wants ("Sun 27 Sep") is assembled
// explicitly rather than trusting any one locale's own part ordering (en-US
// itself would render month-before-day with a comma: "Sun, Sep 27").
const weekdayShortFormatter = new Intl.DateTimeFormat("en-US", { weekday: "short" });
const monthShortFormatter = new Intl.DateTimeFormat("en-US", { month: "short" });

function formatDayHeader(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return `${weekdayShortFormatter.format(d)} ${d.getDate()} ${monthShortFormatter.format(d)}`;
}

function formatRangeLabel(start: Date, days: string[]): string {
  const end = new Date(`${days[days.length - 1]}T00:00:00`);
  const fmt = (d: Date) => `${d.getDate()} ${monthShortFormatter.format(d)} ${d.getFullYear()}`;
  return `${fmt(start)} – ${fmt(end)}`;
}

const STATUS_META: Record<Exclude<CellStatus, "loading">, { label: string; classes: string }> = {
  available: { label: "Free", classes: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  booked: { label: "Booked", classes: "bg-rose-100 text-rose-900 border-rose-300" },
  held: { label: "Held", classes: "bg-amber-50 text-amber-700 border-amber-200" },
  maintenance: { label: "Maintenance", classes: "bg-slate-600 text-white border-slate-700" },
  blocked: { label: "Blocked", classes: "bg-slate-200 text-slate-700 border-slate-300" },
  unknown: { label: "—", classes: "bg-slate-50 text-slate-400 border-slate-200" },
};

const LEGEND_ITEMS: Exclude<CellStatus, "loading" | "unknown">[] = ["available", "booked", "held", "maintenance", "blocked"];

// Single source of truth for "which booking (if any) covers this unit+day" —
// previously duplicated inline in both cellStatus() and the selectedBooking
// derivation below; now also reused by the hover-tooltip builder. Same exact
// predicate as before, just factored out — no behavior change.
function findCoveringBooking(bookings: BookingOut[], unitId: string, day: string): BookingOut | undefined {
  return bookings.find(
    (b) => b.unit_id === unitId && (b.status === "confirmed" || b.status === "checked_in") && day >= b.check_in && day < b.check_out,
  );
}

// Same literal expression the existing inline detail view already computed
// for "Duration:" — factored out so the new hover tooltip and detail drawer
// compute the identical number from the identical formula, not two
// independently-maintained copies.
function nightsBetween(checkIn: string, checkOut: string): number {
  return (new Date(`${checkOut}T00:00:00`).getTime() - new Date(`${checkIn}T00:00:00`).getTime()) / 86_400_000;
}

function cellStatus(
  unit: UnitOut,
  day: string,
  bookings: BookingOut[],
  dayAvailability: Record<string, Record<string, boolean>>,
): CellStatus {
  if (unit.status === "maintenance") return "maintenance";
  if (unit.status === "blocked") return "blocked";
  if (unit.status !== "available") return "unknown"; // defensive only — backend now validates status to these three values

  if (findCoveringBooking(bookings, unit.id, day)) return "booked";

  const dayRow = dayAvailability[day];
  if (!dayRow || !(unit.id in dayRow)) return "loading";
  return dayRow[unit.id] ? "available" : "held";
}

// Hover-quick-summary content (UX polish item 1) — a plain multi-line
// string for the cell button's native `title` attribute, deliberately NOT a
// custom floating-tooltip component: "small lightweight tooltip" per the
// brief, reusing only data already loaded on this page (no new fetch, no
// new API call). Only ever includes fields that are genuinely available for
// that cell's status — e.g. a Held day has no guest/dates/source available
// anywhere on this page (see this file's own module docstring), so its
// tooltip is deliberately short rather than showing invented placeholders.
function buildHoverTitle(
  unit: UnitOut,
  status: Exclude<CellStatus, "loading" | "available">,
  booking: BookingOut | undefined,
  guestNameById: Record<string, string>,
): string {
  const unitLabel = unit.unit_code ? `${unit.name} (${unit.unit_code})` : unit.name;
  if (status === "booked" && booking) {
    const nights = nightsBetween(booking.check_in, booking.check_out);
    return [
      `Unit: ${unitLabel}`,
      `Guest: ${guestNameById[booking.guest_id] || "Unknown guest"}`,
      `Check-in: ${booking.check_in}`,
      `Check-out: ${booking.check_out}`,
      `Nights: ${nights}`,
      `Guests: ${booking.adults + booking.children}`,
      `Status: ${booking.status === "checked_in" ? "Checked in" : "Confirmed"}`,
      `Source: ${booking.source}`,
    ].join("\n");
  }
  return [`Unit: ${unitLabel}`, `Status: ${STATUS_META[status].label}`].join("\n");
}

export default function AvailabilityCalendarPage() {
  const [units, setUnits] = useState<UnitOut[]>([]);
  const [bookings, setBookings] = useState<BookingOut[]>([]);
  const [guestNameById, setGuestNameById] = useState<Record<string, string>>({});
  const [rangeStart, setRangeStart] = useState<Date>(() => startOfToday());
  const [dayAvailability, setDayAvailability] = useState<Record<string, Record<string, boolean>>>({});

  const [loadingBase, setLoadingBase] = useState(true);
  const [loadingAvailability, setLoadingAvailability] = useState(true);
  const [baseError, setBaseError] = useState<string | null>(null);
  const [availabilityError, setAvailabilityError] = useState<string | null>(null);

  const [unitFilter, setUnitFilter] = useState<string>("all");
  const [selectedCell, setSelectedCell] = useState<SelectedCell | null>(null);

  const days = useMemo(() => Array.from({ length: WINDOW_DAYS }, (_, i) => isoDate(addDays(rangeStart, i))), [rangeStart]);
  const [refreshKey, setRefreshKey] = useState(0);

  // Units + guests (for name-only lookup) + bookings — loaded once on
  // mount. Re-fetched only via the explicit Refresh button, matching the
  // existing pattern used elsewhere in this app for "who's in the data
  // could have changed since I loaded the page" pages.
  useEffect(() => {
    let cancelled = false;
    setLoadingBase(true);
    setBaseError(null);
    Promise.all([
      api.get<UnitOut[]>("/units"),
      api.get<GuestOut[]>("/guests"),
      // 500 is this endpoint's own documented ceiling (see
      // bookings/router.py::list_bookings) — more than enough for a
      // 6-unit homestay's full booking history; no new backend query
      // shape is introduced here.
      api.get<BookingOut[]>("/bookings", { params: { limit: 500 } }),
    ])
      .then(([unitsRes, guestsRes, bookingsRes]) => {
        if (cancelled) return;
        setUnits(unitsRes.data.filter((u) => u.is_active));
        const names: Record<string, string> = {};
        guestsRes.data.forEach((g) => {
          names[g.id] = g.name;
        });
        setGuestNameById(names);
        setBookings(bookingsRes.data);
      })
      .catch((e) => {
        if (!cancelled) setBaseError(extractErrorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoadingBase(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // Per-day batch availability — re-fetched whenever the visible date
  // window changes. One call per visible day (see module docstring above
  // for why one call per RANGE would not give per-day granularity).
  useEffect(() => {
    let cancelled = false;
    setLoadingAvailability(true);
    setAvailabilityError(null);
    Promise.all(
      days.map((day) =>
        api
          .get<DayAvailabilityRow[]>("/units/availability", {
            params: { check_in: day, check_out: isoDate(addDays(new Date(`${day}T00:00:00`), 1)) },
          })
          .then((res) => ({ day, rows: res.data })),
      ),
    )
      .then((results) => {
        if (cancelled) return;
        const next: Record<string, Record<string, boolean>> = {};
        for (const { day, rows } of results) {
          next[day] = {};
          for (const row of rows) next[day][row.unit_id] = row.available;
        }
        setDayAvailability(next);
      })
      .catch((e) => {
        if (!cancelled) setAvailabilityError(extractErrorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoadingAvailability(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const orderedUnits = useMemo(() => {
    return [...units].sort((a, b) => {
      const aCode = a.unit_code || a.name;
      const bCode = b.unit_code || b.name;
      const aIndex = UNIT_ORDER.indexOf(aCode);
      const bIndex = UNIT_ORDER.indexOf(bCode);
      if (aIndex >= 0 || bIndex >= 0) return (aIndex < 0 ? UNIT_ORDER.length : aIndex) - (bIndex < 0 ? UNIT_ORDER.length : bIndex);
      return aCode.localeCompare(bCode);
    });
  }, [units]);
  const visibleUnits = unitFilter === "all" ? orderedUnits : orderedUnits.filter((u) => u.id === unitFilter);
  const today = isoDate(startOfToday());

  function handlePrevious() {
    setSelectedCell(null);
    setRangeStart((s) => addDays(s, -WINDOW_DAYS));
  }
  function handleNext() {
    setSelectedCell(null);
    setRangeStart((s) => addDays(s, WINDOW_DAYS));
  }
  function handleToday() {
    setSelectedCell(null);
    setRangeStart(startOfToday());
  }
  function handleRefresh() {
    setSelectedCell(null);
    setRefreshKey((k) => k + 1);
  }

  function openCell(unit: UnitOut, day: string) {
    const status = cellStatus(unit, day, bookings, dayAvailability);
    if (status === "loading") return;
    setSelectedCell({ unitId: unit.id, unitName: unit.name, date: day, status });
  }

  const selectedBooking =
    selectedCell && selectedCell.status === "booked"
      ? findCoveringBooking(bookings, selectedCell.unitId, selectedCell.date)
      : undefined;
  const selectedUnitCode = selectedCell ? units.find((u) => u.id === selectedCell.unitId)?.unit_code : undefined;

  // Detail-drawer keyboard behavior (UX polish item 2): Escape closes it,
  // matching every other modal/drawer surface's expected behavior, and
  // focus moves to its Close button on open so keyboard users land
  // somewhere sensible instead of focus staying on the (now covered) grid
  // cell behind it.
  const drawerCloseButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!selectedCell) return;
    drawerCloseButtonRef.current?.focus();
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setSelectedCell(null);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [selectedCell]);

  return (
    <div>
      <PageHeader
        title="Availability Calendar"
        action={
          <Button variant="secondary" onClick={handleRefresh} disabled={loadingBase}>
            Refresh
          </Button>
        }
      />

      <ErrorBanner message={baseError} />
      {!baseError && <ErrorBanner message={availabilityError} />}

      <Card className="!rounded-[14px] mb-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {/* Button (components/ui.tsx) doesn't forward aria-label — its
                accessible name comes from visible text, which is already
                clear here, so no extra prop is needed. */}
            <Button variant="secondary" onClick={handlePrevious}>
              ← Previous
            </Button>
            <Button variant="secondary" onClick={handleToday}>
              Today
            </Button>
            <Button variant="secondary" onClick={handleNext}>
              Next →
            </Button>
          </div>
          <p className="text-sm font-medium text-slate-700" aria-live="polite">
            {formatRangeLabel(rangeStart, days)}
          </p>
          <div>
            <label htmlFor="unit-filter" className="sr-only">
              Filter by unit
            </label>
            <select
              id="unit-filter"
              value={unitFilter}
              onChange={(e) => setUnitFilter(e.target.value)}
              disabled={loadingBase || units.length === 0}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900"
            >
              <option value="all">All Units</option>
              {orderedUnits.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Legend — text label on every swatch, never color alone */}
        <div className="mt-4 flex flex-wrap gap-3 border-t border-slate-100 pt-3" role="list" aria-label="Status legend">
          {LEGEND_ITEMS.map((s) => (
            <span
              key={s}
              role="listitem"
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${STATUS_META[s].classes}`}
            >
              <span className="h-2 w-2 rounded-full bg-current opacity-70" aria-hidden="true" />
              {STATUS_META[s].label}
            </span>
          ))}
        </div>
      </Card>

      {loadingBase ? (
        <Card className="!rounded-[14px] shadow-sm">
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <SkeletonLine key={i} />
            ))}
          </div>
          <p className="mt-4 text-center text-sm text-slate-400">Loading units and bookings…</p>
        </Card>
      ) : baseError ? null : units.length === 0 ? (
        <EmptyState message="No active units found. Add a unit under Properties & Units first." />
      ) : (
        <Card className="!rounded-[14px] !p-0 overflow-hidden shadow-sm">
          <div className="overflow-x-auto" role="region" aria-label="Per-unit availability grid" tabIndex={0}>
            <table className="min-w-full text-sm border-collapse">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th scope="col" className="sticky left-0 z-10 bg-slate-50 text-left px-4 py-2.5 font-medium text-slate-500 min-w-[140px]">
                    Unit
                  </th>
                  {days.map((day) => (
                    <th key={day} scope="col" aria-current={day === today ? "date" : undefined} className={`text-center px-2 py-2.5 font-medium whitespace-nowrap min-w-[92px] ${day === today ? "bg-emerald-50 text-emerald-900 ring-inset ring-1 ring-emerald-200" : "text-slate-500"}`}>
                      {formatDayHeader(day)}
                      {day === today && <span className="block text-[10px] font-semibold uppercase tracking-wide">Today</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visibleUnits.map((unit) => (
                  <tr key={unit.id}>
                    <th
                      scope="row"
                      className="sticky left-0 z-10 bg-white text-left px-4 py-2.5 font-medium text-slate-800 whitespace-nowrap"
                    >
                      {unit.name}
                      {unit.unit_code && <span className="ml-1.5 text-xs font-normal text-slate-400">({unit.unit_code})</span>}
                    </th>
                    {days.map((day) => {
                      const status = cellStatus(unit, day, bookings, dayAvailability);
                      if (status === "loading") {
                        return (
                          <td key={day} className="px-1.5 py-1.5 text-center">
                            <div className="h-8 rounded-md bg-slate-100 animate-pulse" aria-hidden="true" />
                          </td>
                        );
                      }
                      const meta = STATUS_META[status];
                      const label = `${unit.name}, ${formatDayHeader(day)}: ${meta.label}`;
                      const isSelected = selectedCell?.unitId === unit.id && selectedCell?.date === day;
                      // Every non-Free cell now opens the detail drawer on
                      // click (UX polish item 2 — "non-Free availability
                      // cell", not just Booked/Held as before); Free stays
                      // non-interactive, unchanged.
                      const interactive = status !== "available";
                      const hoverTitle =
                        status === "available" ? meta.label : buildHoverTitle(unit, status, findCoveringBooking(bookings, unit.id, day), guestNameById);
                      return (
                        <td key={day} className="px-1.5 py-1.5 text-center">
                          <button
                            type="button"
                            onClick={() => openCell(unit, day)}
                            aria-label={label}
                            aria-pressed={isSelected}
                            disabled={!interactive}
                            title={hoverTitle}
                            className={`h-8 w-full rounded-md border text-[11px] font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-slate-900 ${meta.classes} ${
                              interactive ? "hover:brightness-95 cursor-pointer" : "cursor-default"
                            } ${isSelected ? "ring-2 ring-slate-900" : ""}`}
                          >
                            {meta.label}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {loadingAvailability && !loadingBase && (
        <p className="mt-2 text-xs text-slate-400" role="status">
          Refreshing availability for this range…
        </p>
      )}

      {/*
        Detail drawer (UX polish item 2) — a right-side panel replacing the
        page's previous inline detail card as the way a selected cell's
        information is shown, rather than adding a second, duplicate
        surface alongside it. Deliberately a small bespoke panel here
        (not the shared components/ui.tsx Drawer used elsewhere) so this
        page-scoped change can add Escape-to-close, autofocus, and dialog
        ARIA semantics without touching that shared component's behavior
        for every other page that already uses it — its visual language
        (backdrop, panel width/shadow, header row, close button styling)
        is copied verbatim from it, so it still looks identical to every
        other OS10 drawer. w-full capped at max-w-md already means it's
        full-width on a narrow/mobile viewport with zero horizontal
        overflow, satisfying the "full-width on small screens" requirement
        without inventing a separate bottom-sheet layout.
      */}
      {selectedCell && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/40"
          onClick={() => setSelectedCell(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="availability-drawer-title"
            className="flex h-full w-full max-w-md flex-col bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-start justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h2 id="availability-drawer-title" className="text-base font-semibold text-slate-900">
                  {selectedCell.unitName} — {formatDayHeader(selectedCell.date)}
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">Status: {STATUS_META[selectedCell.status].label}</p>
              </div>
              <button
                ref={drawerCloseButtonRef}
                type="button"
                onClick={() => setSelectedCell(null)}
                aria-label="Close"
                className="text-xl leading-none text-slate-400 hover:text-slate-600"
              >
                &times;
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-5">
              <p className="mb-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">Unit</p>
              <p className="mb-4 text-sm text-slate-700">
                {selectedCell.unitName}
                {selectedUnitCode && ` (${selectedUnitCode})`}
              </p>

              {selectedCell.status === "booked" && selectedBooking && (
                <>
                  <p className="mb-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">Booking</p>
                  <div className="text-sm text-slate-700 space-y-1">
                    <p>
                      <span className="text-slate-400">Guest: </span>
                      {guestNameById[selectedBooking.guest_id] || "Unknown guest"}
                    </p>
                    <p>
                      <span className="text-slate-400">Stay: </span>
                      {selectedBooking.check_in} → {selectedBooking.check_out}
                    </p>
                    <p>
                      <span className="text-slate-400">Duration: </span>
                      {nightsBetween(selectedBooking.check_in, selectedBooking.check_out)} nights
                    </p>
                    <p>
                      <span className="text-slate-400">Guests: </span>
                      {selectedBooking.adults + selectedBooking.children}
                    </p>
                    <p>
                      <span className="text-slate-400">Source: </span>
                      {selectedBooking.source}
                    </p>
                    <p>
                      <span className="text-slate-400">Booking status: </span>
                      {selectedBooking.status === "checked_in" ? "Checked in" : "Confirmed"}
                    </p>
                    <p>
                      <span className="text-slate-400">Booking reference: </span>
                      {selectedBooking.id.slice(0, 8)}
                    </p>
                  </div>
                </>
              )}

              {selectedCell.status === "held" && (
                <p className="text-sm text-slate-500">
                  A payment is currently in progress for this date on the public website. No further detail (guest identity) is available to
                  OS10 for an in-progress hold — it will appear here as a confirmed booking once payment completes, or clear automatically if
                  the hold expires unpaid.
                </p>
              )}

              {(selectedCell.status === "maintenance" || selectedCell.status === "blocked") && (
                <p className="text-sm text-slate-500">
                  This unit is marked {STATUS_META[selectedCell.status].label.toLowerCase()} and is not bookable for this date. No
                  guest or reservation data applies.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
