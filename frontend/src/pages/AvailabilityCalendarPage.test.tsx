import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import AvailabilityCalendarPage from "./AvailabilityCalendarPage";
import { loginAs } from "../test/testAuth";

/**
 * Availability Calendar (frontend-only, Phase — see the page's own module
 * docstring for exactly which existing backend endpoints this reads and
 * why). All data below is shaped exactly like the real
 * UnitOut/BookingOut/GuestOut/units-availability-batch responses.
 */

const UNIT_BASE = {
  property_id: "prop-1", unit_type: "apartment", max_occupancy: 4, base_rate: 3000, weekend_rate: null,
  long_stay_min_nights: 30, long_stay_rate: 2000, housekeeping_status: "clean",
  floor: null, flat_number: null, amenities: null, description: null, is_active: true,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
};

const UNIT_A1 = { ...UNIT_BASE, id: "unit-a1", name: "A1", unit_code: "A1", status: "available" };
const UNIT_A2 = { ...UNIT_BASE, id: "unit-a2", name: "A2", unit_code: "A2", status: "available" };
const UNIT_B3 = { ...UNIT_BASE, id: "unit-b3", name: "B3", unit_code: "B3", status: "available" };
const UNIT_B4 = { ...UNIT_BASE, id: "unit-b4", name: "B4", unit_code: "B4", status: "maintenance" };
const UNIT_C5 = { ...UNIT_BASE, id: "unit-c5", name: "C5", unit_code: "C5", status: "blocked" };
const UNIT_C6 = { ...UNIT_BASE, id: "unit-c6", name: "C6", unit_code: "C6", status: "available" };
const ALL_UNITS = [UNIT_A1, UNIT_A2, UNIT_B3, UNIT_B4, UNIT_C5, UNIT_C6];
const UNIT_ORDER = ["A1", "A2", "B3", "B4", "C5", "C6"];

const GUEST = { id: "guest-1", name: "Priya Shah", phone: "9876543210", email: "priya@example.com", address: "Somewhere, private", id_proof_type: "aadhaar", id_proof_number: "1234-5678-9012" };

// Matches the page's own fixed "en-US" formatters exactly (see
// AvailabilityCalendarPage.tsx) — asserting against a real, explicit
// English format here too, rather than `toLocaleDateString(undefined, ...)`,
// which would just as easily hide a locale regression in the test itself as
// it did in the page before the fix.
const weekdayShortFormatter = new Intl.DateTimeFormat("en-US", { weekday: "short" });
const monthShortFormatter = new Intl.DateTimeFormat("en-US", { month: "short" });

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function isoPlus(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// A1 is booked for today+1..today+3 by GUEST.
const BOOKING_A1 = {
  id: "booking-1", unit_id: "unit-a1", guest_id: "guest-1",
  check_in: isoPlus(1), check_out: isoPlus(3), status: "confirmed", source: "direct",
  rate_applied: 3000, internal_notes: null, special_requests: null, due_date: null,
  adults: 2, children: 0, extra_bed: false, arrival_time: null, departure_time: null,
  updated_at: todayIso(), is_archived: false, archived_at: null, archive_reason: null,
};
const BOOKING_A1_NEXT = {
  ...BOOKING_A1,
  id: "booking-2",
  check_in: isoPlus(3),
  check_out: isoPlus(6),
  status: "checked_in",
  source: "airbnb",
  adults: 2,
  children: 1,
};

// C6 has no booking and unit.status="available", but the batch endpoint
// reports it unavailable on today+2 — i.e. a hold, by elimination.
function unitsAvailabilityFor(day: string) {
  return ALL_UNITS.filter((u) => u.status === "available").map((u) => ({
    unit_id: u.id,
    name: u.name,
    unit_code: u.unit_code,
    available: !(u.id === "unit-c6" && day === isoPlus(2)),
  }));
}

let currentBookings: typeof BOOKING_A1[] = [];
const apiGet = vi.fn((url: string, config?: { params?: Record<string, string> }): Promise<{ data: unknown[] }> => {
  if (url === "/units") return Promise.resolve({ data: ALL_UNITS });
  if (url === "/guests") return Promise.resolve({ data: [GUEST] });
  if (url === "/bookings") return Promise.resolve({ data: currentBookings });
  if (url === "/units/availability") {
    const day = config?.params?.check_in ?? "";
    return Promise.resolve({ data: unitsAvailabilityFor(day) });
  }
  return Promise.resolve({ data: [] });
});

vi.mock("../lib/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(args[0] as string, args[1] as { params?: Record<string, string> }) },
  extractErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { detail?: string } } };
    return err?.response?.data?.detail ?? "error";
  },
}));

function renderPage() {
  loginAs("admin");
  return render(
    <MemoryRouter>
      <AuthProvider>
        <AvailabilityCalendarPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("AvailabilityCalendarPage", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiGet.mockImplementation((url: string, config?: { params?: Record<string, string> }): Promise<{ data: unknown[] }> => {
      if (url === "/units") return Promise.resolve({ data: ALL_UNITS });
      if (url === "/guests") return Promise.resolve({ data: [GUEST] });
      if (url === "/bookings") return Promise.resolve({ data: currentBookings });
      if (url === "/units/availability") {
        const day = config?.params?.check_in ?? "";
        return Promise.resolve({ data: unitsAvailabilityFor(day) });
      }
      return Promise.resolve({ data: [] });
    });
    currentBookings = [BOOKING_A1];
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders all six units returned by the API as rows, using their real names", async () => {
    renderPage();
    for (const u of ALL_UNITS) {
      await waitFor(() => expect(screen.getAllByText(u.name, { exact: false }).length).toBeGreaterThan(0));
    }
    // Specifically, each unit name appears as a table row header.
    for (const u of ALL_UNITS) {
      expect(screen.getByRole("rowheader", { name: new RegExp(u.name) })).toBeInTheDocument();
    }
  });

  it("keeps the requested apartment order even when the API returns units in reverse", async () => {
    apiGet.mockImplementation((url: string, config?: { params?: Record<string, string> }) => {
      if (url === "/units") return Promise.resolve({ data: [...ALL_UNITS].reverse() });
      if (url === "/guests") return Promise.resolve({ data: [GUEST] });
      if (url === "/bookings") return Promise.resolve({ data: currentBookings });
      if (url === "/units/availability") return Promise.resolve({ data: unitsAvailabilityFor(config?.params?.check_in ?? "") });
      return Promise.resolve({ data: [] });
    });
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("rowheader")).toHaveLength(6));
    expect(screen.getAllByRole("rowheader").map((row) => row.textContent?.trim().slice(0, 2))).toEqual(UNIT_ORDER);
  });

  it("renders the visible date range as column headers", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole("columnheader", { name: /unit/i })).toBeInTheDocument());
    const headers = screen.getAllByRole("columnheader");
    // 1 "Unit" header + 14 day headers
    expect(headers.length).toBe(15);
  });

  it("shows available, booked, maintenance, and blocked cells correctly from real backend data", async () => {
    renderPage();
    await waitFor(() => expect(screen.queryAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));

    // A1 is booked on its booked night
    expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0);
    // B4 is under maintenance for every visible day
    expect(screen.getAllByRole("button", { name: /B4,.*Maintenance/ }).length).toBe(14);
    // C5 is blocked for every visible day
    expect(screen.getAllByRole("button", { name: /C5,.*Blocked/ }).length).toBe(14);
    // A2 has no booking and is not maintenance/blocked/held on most days
    expect(screen.getAllByRole("button", { name: /A2,.*Free/ }).length).toBeGreaterThan(0);
  });

  it("uses matching legend and cell colors for every supported status", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    const checks = [
      ["Free", /A2,.*Free/, "bg-emerald-50"],
      ["Booked", /A1,.*Booked/, "bg-rose-100"],
      ["Held", /C6,.*Held/, "bg-amber-50"],
      ["Maintenance", /B4,.*Maintenance/, "bg-slate-600"],
      ["Blocked", /C5,.*Blocked/, "bg-slate-200"],
    ] as const;
    for (const [label, cellPattern, colorClass] of checks) {
      const legendItem = Array.from(screen.getByRole("list").querySelectorAll<HTMLElement>('[role="listitem"]'))
        .find((item) => item.textContent?.trim() === label)!;
      expect(legendItem.className).toContain(colorClass);
      expect(screen.getAllByRole("button", { name: cellPattern })[0].className).toContain(colorClass);
    }
  });

  it("uses backend check-in inclusive and check-out exclusive ranges, including same-day turnover", async () => {
    const user = userEvent.setup();
    currentBookings = [BOOKING_A1, BOOKING_A1_NEXT, { ...BOOKING_A1, id: "cancelled", check_in: isoPlus(7), check_out: isoPlus(9), status: "cancelled" }];
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));

    const aNights = [isoPlus(1), isoPlus(2)];
    for (const date of aNights) {
      expect(screen.getByRole("button", { name: new RegExp(`A1,.*${formatForRegex(date)}.*Booked`) })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: new RegExp(`A1,.*${formatForRegex(isoPlus(3))}.*Booked`) })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: new RegExp(`A1,.*${formatForRegex(isoPlus(7))}.*Free`) })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: new RegExp(`A1,.*${formatForRegex(isoPlus(3))}.*Booked`) }));
    expect(screen.getByText(`${BOOKING_A1_NEXT.check_in} → ${BOOKING_A1_NEXT.check_out}`)).toBeInTheDocument();
    expect(screen.getByText("Duration:").parentElement).toHaveTextContent("3 nights");
    expect(screen.getByText("Guests:").parentElement).toHaveTextContent("3");
    expect(screen.getByText("Booking status:").parentElement).toHaveTextContent("Checked in");
    expect(screen.getByText("Source:").parentElement).toHaveTextContent("airbnb");
  });

  it("shows the checkout day itself as Free when nothing else starts that same day", async () => {
    // Isolated from the same-day-turnover scenario above: a booking with a
    // clear gap after it (nothing else begins on its checkout day), so this
    // proves check_out day is free on its own terms, not just "not
    // conflicting with a same-day next booking".
    currentBookings = [{ ...BOOKING_A1, id: "isolated", unit_id: "unit-a2", check_in: isoPlus(10), check_out: isoPlus(12) }];
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: new RegExp(`A2,.*${formatForRegex(isoPlus(10))}.*Booked`) }).length).toBeGreaterThan(0));

    expect(screen.getByRole("button", { name: new RegExp(`A2,.*${formatForRegex(isoPlus(11))}.*Booked`) })).toBeInTheDocument();
    // isoPlus(12) is the checkout day — must be Free, not Booked.
    expect(screen.getByRole("button", { name: new RegExp(`A2,.*${formatForRegex(isoPlus(12))}.*Free`) })).toBeInTheDocument();
  });

  it("stays date-safe across a month/year boundary (no shifted or duplicated days)", async () => {
    // 28 Feb of a non-leap year, so the visible 14-day window crosses both
    // a month boundary AND — starting the day before — would cross a year
    // boundary too if rangeStart were e.g. 31 Dec. Using local-time
    // system-time mocking (not UTC) matches this page's own isoDate(),
    // which deliberately reads getFullYear/getMonth/getDate rather than
    // toISOString() for exactly this reason (see the page's own comment).
    // toFake: ["Date"] only — leaving setTimeout/setInterval real so
    // @testing-library's waitFor (which polls via real timers internally)
    // keeps working normally; faking those too made every test after this
    // one hang until Vitest's own test timeout.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2027, 1, 28, 10, 0, 0)); // month is 0-indexed: 1 = February
    try {
      renderPage();
      await waitFor(() => expect(screen.getAllByRole("columnheader").length).toBe(15));
      const dayHeaders = screen.getAllByRole("columnheader").slice(1).map((h) => h.textContent);
      // 14 consecutive calendar days from 28 Feb 2027, no duplicates, no gaps.
      const expected = Array.from({ length: 14 }, (_, i) => {
        const d = new Date(2027, 1, 28);
        d.setDate(d.getDate() + i);
        const label = `${weekdayShortFormatter.format(d)} ${d.getDate()} ${monthShortFormatter.format(d)}`;
        return i === 0 ? `${label}Today` : label; // the current-day header appends "Today"
      });
      expect(dayHeaders).toEqual(expected);
      expect(new Set(dayHeaders).size).toBe(14); // no duplicate dates from a rollover bug
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a held cell by elimination when the batch endpoint reports unavailable with no covering booking", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: new RegExp(`C6,.*${formatForRegex(isoPlus(2))}.*Held`) }).length).toBeGreaterThan(0),
    );
  });

  it("date navigation (Next) requests a new range and re-renders it", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("columnheader").length).toBe(15));

    const callsBefore = apiGet.mock.calls.filter((c) => c[0] === "/units/availability").length;
    await user.click(screen.getByRole("button", { name: /next →/i }));

    await waitFor(() => {
      const callsAfter = apiGet.mock.calls.filter((c) => c[0] === "/units/availability").length;
      expect(callsAfter).toBeGreaterThan(callsBefore);
    });
  });

  it("the unit filter narrows the grid to a single unit", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByRole("rowheader", { name: /A1/ })).toBeInTheDocument());
    expect(screen.getByRole("rowheader", { name: /B4/ })).toBeInTheDocument();

    // The filter <select> itself always lists every unit as an option —
    // only the TABLE rows should narrow, so row headers are what this
    // asserts against, not text anywhere on the page.
    await user.selectOptions(screen.getByLabelText(/filter by unit/i), "unit-a1");

    await waitFor(() => expect(screen.queryByRole("rowheader", { name: /B4/ })).not.toBeInTheDocument());
    expect(screen.getByRole("rowheader", { name: /A1/ })).toBeInTheDocument();
    expect(screen.getAllByRole("rowheader").length).toBe(1);
  });

  it("uses explicit English locale for weekday/month labels and the date range, regardless of system locale", async () => {
    // The exact real-world date the French-locale bug was reported against:
    // 27 Sep 2026 is a Sunday, and the 14-day window from there runs to
    // 10 Oct 2026 — matching "27 Sep 2026 – 10 Oct 2026" precisely.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 8, 27, 10, 0, 0)); // month is 0-indexed: 8 = September
    try {
      renderPage();
      await waitFor(() => expect(screen.getAllByRole("columnheader").length).toBe(15));

      const headerTexts = screen.getAllByRole("columnheader").slice(1).map((h) => h.textContent ?? "");
      expect(headerTexts[0]).toBe("Sun 27 SepToday");
      expect(headerTexts.some((t) => t.startsWith("Mon "))).toBe(true);
      expect(headerTexts.some((t) => t.includes("Sep"))).toBe(true);
      expect(headerTexts.some((t) => t.includes("Oct"))).toBe(true);

      // Never any French weekday/month abbreviation, on the headers or the
      // date-range label.
      const rangeLabel = screen.getByText("27 Sep 2026 – 10 Oct 2026");
      expect(rangeLabel).toBeInTheDocument();
      const pageText = `${headerTexts.join(" ")} ${rangeLabel.textContent}`;
      expect(pageText).not.toMatch(/dim\.|lun\.|mar\.|mer\.|jeu\.|ven\.|sam\.|sept\.|oct\./);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a loading state before data arrives", () => {
    renderPage();
    expect(screen.getByText(/loading units and bookings/i)).toBeInTheDocument();
  });

  it("shows an error state when the API call fails", async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === "/units") return Promise.reject({ response: { data: { detail: "Server exploded" } } });
      return Promise.resolve({ data: [] });
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("Server exploded")).toBeInTheDocument());
  });

  it("shows an empty state when there are no active units", async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === "/units") return Promise.resolve({ data: [] });
      if (url === "/guests") return Promise.resolve({ data: [] });
      if (url === "/bookings") return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no active units found/i)).toBeInTheDocument());
  });

  it("never renders guest phone number, email, or ID proof anywhere on the page", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));

    const bookedCell = screen.getAllByRole("button", { name: /A1,.*Booked/ })[0];
    await user.click(bookedCell);

    await waitFor(() => expect(screen.getByText(GUEST.name)).toBeInTheDocument());
    expect(screen.queryByText(GUEST.phone)).not.toBeInTheDocument();
    expect(screen.queryByText(GUEST.email!)).not.toBeInTheDocument();
    expect(screen.queryByText(GUEST.id_proof_number)).not.toBeInTheDocument();
    expect(screen.queryByText(GUEST.address)).not.toBeInTheDocument();
  });
});

function formatForRegex(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.getDate()} ${monthShortFormatter.format(d)}`;
}
