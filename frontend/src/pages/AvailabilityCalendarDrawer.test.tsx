import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import AvailabilityCalendarPage from "./AvailabilityCalendarPage";
import { loginAs } from "../test/testAuth";

/**
 * Focused tests for the two UX-polish additions to the Availability
 * Calendar: the hover-quick-summary tooltip (native `title` attribute) and
 * the click-to-open detail drawer. Deliberately separate from
 * AvailabilityCalendarPage.test.tsx (which is untouched and still covers
 * the pre-existing grid/status/date-range behavior) so that file's own
 * "preserve existing tests" guarantee is visible by not needing a single
 * edit to it.
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

const GUEST = { id: "guest-1", name: "Priya Shah", phone: "9876543210", email: "priya@example.com" };

function isoPlus(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// A1 is booked for today+1..today+3 by GUEST.
const BOOKING_A1 = {
  id: "booking-1234-5678", unit_id: "unit-a1", guest_id: "guest-1",
  check_in: isoPlus(1), check_out: isoPlus(3), status: "confirmed", source: "direct",
  rate_applied: 3000, internal_notes: null, special_requests: null, due_date: null,
  adults: 2, children: 1, extra_bed: false, arrival_time: null, departure_time: null,
  updated_at: isoPlus(0), is_archived: false, archived_at: null, archive_reason: null,
};

// C6 has no booking and unit.status="available", but the batch endpoint
// reports it unavailable on today+2 — i.e. a hold, by elimination (same
// fixture technique as AvailabilityCalendarPage.test.tsx).
function unitsAvailabilityFor(day: string) {
  return ALL_UNITS.filter((u) => u.status === "available").map((u) => ({
    unit_id: u.id, name: u.name, unit_code: u.unit_code,
    available: !(u.id === "unit-c6" && day === isoPlus(2)),
  }));
}

let currentBookings: typeof BOOKING_A1[] = [];
const apiGet = vi.fn();

vi.mock("../lib/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
  extractErrorMessage: () => "error",
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

describe("AvailabilityCalendarPage — hover tooltip and detail drawer", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    currentBookings = [BOOKING_A1];
    apiGet.mockImplementation((url: string, config?: { params?: Record<string, string> }) => {
      if (url === "/units") return Promise.resolve({ data: ALL_UNITS });
      if (url === "/guests") return Promise.resolve({ data: [GUEST] });
      if (url === "/bookings") return Promise.resolve({ data: currentBookings });
      if (url === "/units/availability") return Promise.resolve({ data: unitsAvailabilityFor(config?.params?.check_in ?? "") });
      return Promise.resolve({ data: [] });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not fetch any additional data for the tooltip or drawer beyond the page's existing calls", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    const urls = new Set(apiGet.mock.calls.map((c) => c[0]));
    expect(urls).toEqual(new Set(["/units", "/guests", "/bookings", "/units/availability"]));
  });

  it("a Booked cell's hover title contains unit, guest, dates, nights, guest count, status, and source", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    const cell = screen.getAllByRole("button", { name: /A1,.*Booked/ })[0];
    expect(cell.title).toContain("Unit: A1 (A1)");
    expect(cell.title).toContain("Guest: Priya Shah");
    expect(cell.title).toContain(`Check-in: ${BOOKING_A1.check_in}`);
    expect(cell.title).toContain(`Check-out: ${BOOKING_A1.check_out}`);
    expect(cell.title).toContain("Nights: 2");
    expect(cell.title).toContain("Guests: 3");
    expect(cell.title).toContain("Status: Confirmed");
    expect(cell.title).toContain("Source: direct");
  });

  it("a Held cell's hover title only shows what's actually available (unit + status), no invented guest/date fields", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /C6,.*Held/ }).length).toBeGreaterThan(0));
    const cell = screen.getAllByRole("button", { name: /C6,.*Held/ })[0];
    expect(cell.title).toBe("Unit: C6 (C6)\nStatus: Held");
  });

  it("Maintenance and Blocked cells' hover titles show unit + status only", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /B4,.*Maintenance/ }).length).toBeGreaterThan(0));
    expect(screen.getAllByRole("button", { name: /B4,.*Maintenance/ })[0].title).toBe("Unit: B4 (B4)\nStatus: Maintenance");
    expect(screen.getAllByRole("button", { name: /C5,.*Blocked/ })[0].title).toBe("Unit: C5 (C5)\nStatus: Blocked");
  });

  it("a Free cell keeps a simple title, never a rich tooltip", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A2,.*Free/ }).length).toBeGreaterThan(0));
    const cell = screen.getAllByRole("button", { name: /A2,.*Free/ })[0];
    expect(cell.title).toBe("Free");
  });

  it("does not fetch anything new on hover (title is derived from data already in memory)", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    const callsBefore = apiGet.mock.calls.length;
    fireEvent.mouseOver(screen.getAllByRole("button", { name: /A1,.*Booked/ })[0]);
    expect(apiGet.mock.calls.length).toBe(callsBefore);
  });

  it("clicking a Booked cell opens a dialog drawer with unit, guest, dates, nights, guests, source, status, and a reference", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));

    await user.click(screen.getAllByRole("button", { name: /A1,.*Booked/ })[0]);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Priya Shah")).toBeInTheDocument();
    expect(screen.getByText(`${BOOKING_A1.check_in} → ${BOOKING_A1.check_out}`)).toBeInTheDocument();
    expect(screen.getByText("Duration:").parentElement).toHaveTextContent("2 nights");
    expect(screen.getByText("Guests:").parentElement).toHaveTextContent("3");
    expect(screen.getByText("Source:").parentElement).toHaveTextContent("direct");
    expect(screen.getByText("Booking status:").parentElement).toHaveTextContent("Confirmed");
    expect(screen.getByText("Booking reference:").parentElement).toHaveTextContent(BOOKING_A1.id.slice(0, 8));
  });

  it("clicking a Maintenance cell now opens the drawer too (any non-Free cell, not just Booked/Held)", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /B4,.*Maintenance/ }).length).toBeGreaterThan(0));

    const cell = screen.getAllByRole("button", { name: /B4,.*Maintenance/ })[0];
    expect(cell).not.toBeDisabled();
    await user.click(cell);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("B4");
    expect(dialog).toHaveTextContent(/marked maintenance/i);
  });

  it("clicking a Blocked cell opens the drawer with unit info and no fabricated booking fields", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /C5,.*Blocked/ }).length).toBeGreaterThan(0));
    await user.click(screen.getAllByRole("button", { name: /C5,.*Blocked/ })[0]);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/marked blocked/i);
    expect(dialog).not.toHaveTextContent("Guest:");
  });

  it("a Free cell is still not clickable and never opens the drawer", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A2,.*Free/ }).length).toBeGreaterThan(0));
    const cell = screen.getAllByRole("button", { name: /A2,.*Free/ })[0];
    expect(cell).toBeDisabled();
    await user.click(cell);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("pressing Escape closes the open drawer", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    await user.click(screen.getAllByRole("button", { name: /A1,.*Booked/ })[0]);
    await screen.findByRole("dialog");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("focus moves to the drawer's Close button when it opens", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    await user.click(screen.getAllByRole("button", { name: /A1,.*Booked/ })[0]);

    await screen.findByRole("dialog");
    const closeButton = screen.getByRole("button", { name: "Close" });
    await waitFor(() => expect(closeButton).toHaveFocus());
  });

  it("the Close button closes the drawer", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    await user.click(screen.getAllByRole("button", { name: /A1,.*Booked/ })[0]);
    await screen.findByRole("dialog");

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clicking the backdrop closes the drawer", async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByRole("button", { name: /A1,.*Booked/ })[0]);
    const dialog = await screen.findByRole("dialog");

    // The backdrop is the dialog's own parent; a click there (not on the
    // panel itself) must close it, same as the shared Drawer component.
    fireEvent.click(dialog.parentElement!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("never renders the guest's phone or email in the drawer", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /A1,.*Booked/ }).length).toBeGreaterThan(0));
    await user.click(screen.getAllByRole("button", { name: /A1,.*Booked/ })[0]);

    await screen.findByRole("dialog");
    expect(screen.queryByText(GUEST.phone)).not.toBeInTheDocument();
    expect(screen.queryByText(GUEST.email)).not.toBeInTheDocument();
  });
});
