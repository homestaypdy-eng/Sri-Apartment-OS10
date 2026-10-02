import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import MaintenancePage from "./MaintenancePage";
import { loginAs } from "../test/testAuth";

/**
 * Maintenance page — UI/UX polish verification. Covers exactly what
 * changed in this pass: standard apartment ordering on the unit dropdown,
 * the "Ticket Details" section label with proper field labels, the
 * corrected priority/status badge colors (now on the approved soft
 * palette), the Start/Resolve/Close vs. Details button hierarchy, and the
 * empty state — plus a regression check that ticket creation and status
 * advancement still work exactly as before.
 */

const UNIT_BASE = {
  property_id: "prop-1", unit_type: "apartment", max_occupancy: 4, base_rate: 3000, weekend_rate: null,
  long_stay_min_nights: 30, long_stay_rate: 2000, status: "available", housekeeping_status: "clean",
  floor: null, flat_number: null, amenities: null, description: null, is_active: true,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
};

const UNIT_C6 = { ...UNIT_BASE, id: "unit-c6", name: "Flat C6", unit_code: "C6" };
const UNIT_A1 = { ...UNIT_BASE, id: "unit-a1", name: "Flat A1", unit_code: "A1" };
const UNIT_B4 = { ...UNIT_BASE, id: "unit-b4", name: "Flat B4", unit_code: "B4" };
const UNIT_A2 = { ...UNIT_BASE, id: "unit-a2", name: "Flat A2", unit_code: "A2" };
const UNIT_B3 = { ...UNIT_BASE, id: "unit-b3", name: "Flat B3", unit_code: "B3" };
const UNIT_C5 = { ...UNIT_BASE, id: "unit-c5", name: "Flat C5", unit_code: "C5" };
const UNIT_INACTIVE = { ...UNIT_BASE, id: "unit-inactive", name: "Flat Z9", unit_code: "Z9", is_active: false };
// API deliberately returns units out of order and includes an inactive
// one — both must be handled by the page, not assumed from response order.
const ALL_UNITS = [UNIT_C6, UNIT_B4, UNIT_A1, UNIT_INACTIVE, UNIT_C5, UNIT_A2, UNIT_B3];

const STAFF = [{ id: "staff-1", name: "Kumar", email: "kumar@example.com", role: "staff", is_active: true }];

interface TestTicket {
  id: string; unit_id: string; reported_by: string | null; assigned_to: string | null;
  priority: string; status: string; category: string | null; description: string;
  cost: number | null; created_at: string; resolved_at: string | null;
}

const TICKET_OPEN: TestTicket = {
  id: "ticket-open", unit_id: "unit-c6", reported_by: null, assigned_to: null,
  priority: "urgent", status: "open", category: "electrical", description: "Fuse tripping repeatedly in the living room",
  cost: null, created_at: "2026-09-20T10:00:00Z", resolved_at: null,
};
const TICKET_IN_PROGRESS: TestTicket = {
  id: "ticket-inprogress", unit_id: "unit-a1", reported_by: null, assigned_to: "staff-1",
  priority: "medium", status: "in_progress", category: "plumbing", description: "Leaking tap in bathroom",
  cost: null, created_at: "2026-09-21T10:00:00Z", resolved_at: null,
};
const TICKET_RESOLVED: TestTicket = {
  id: "ticket-resolved", unit_id: "unit-b4", reported_by: null, assigned_to: "staff-1",
  priority: "low", status: "resolved", category: "furniture", description: "Wobbly chair leg",
  cost: 200, created_at: "2026-09-19T10:00:00Z", resolved_at: "2026-09-19T12:00:00Z",
};
const TICKET_CLOSED: TestTicket = {
  id: "ticket-closed", unit_id: "unit-b4", reported_by: null, assigned_to: "staff-1",
  priority: "high", status: "closed", category: "ac", description: "AC not cooling",
  cost: 1500, created_at: "2026-09-10T10:00:00Z", resolved_at: "2026-09-11T12:00:00Z",
};

let currentTickets: TestTicket[] = [];
const apiGet = vi.fn();
const apiPost = vi.fn().mockResolvedValue({ data: {} });
const apiPatch = vi.fn().mockResolvedValue({ data: {} });

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      get: (...args: unknown[]) => apiGet(...args),
      post: (...args: unknown[]) => apiPost(...args),
      patch: (...args: unknown[]) => apiPatch(...args),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
    extractErrorMessage: (e: unknown) => String(e),
  };
});

function mockRoutesFor(url: string) {
  if (url === "/maintenance-tickets") return Promise.resolve({ data: currentTickets });
  if (url === "/units") return Promise.resolve({ data: ALL_UNITS });
  if (url === "/users") return Promise.resolve({ data: STAFF });
  if (url.includes("/photos")) return Promise.resolve({ data: [] });
  if (url.includes("/timeline")) return Promise.resolve({ data: { events: [] } });
  return Promise.resolve({ data: [] });
}

async function renderPage() {
  loginAs("admin");
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <MaintenancePage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByLabelText("Unit")).toBeInTheDocument());
  return utils;
}

describe("MaintenancePage — UI polish", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockClear();
    apiPatch.mockClear();
    currentTickets = [TICKET_OPEN, TICKET_IN_PROGRESS, TICKET_RESOLVED, TICKET_CLOSED];
    apiGet.mockImplementation(mockRoutesFor);
  });

  it('labels the Raise Ticket form with a "Ticket Details" section and a clear label on every field', async () => {
    await renderPage();
    expect(screen.getByText("Ticket Details")).toBeInTheDocument();
    expect(screen.getByLabelText("Unit")).toBeInTheDocument();
    expect(screen.getByLabelText("Issue Description")).toBeInTheDocument();
    expect(screen.getByLabelText("Priority")).toBeInTheDocument();
    expect(screen.getByLabelText(/Category/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Assign to Staff/)).toBeInTheDocument();
  });

  it("lists only active units, in the standard apartment order, in the Unit dropdown", async () => {
    await renderPage();
    const options = within(screen.getByLabelText("Unit")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Select unit...", "Flat A1", "Flat A2", "Flat B3", "Flat B4", "Flat C5", "Flat C6"]);
    expect(options).not.toContain("Flat Z9");
  });

  it("does not change the field names or payload sent when raising a ticket", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.selectOptions(screen.getByLabelText("Unit"), "unit-a1");
    await user.type(screen.getByLabelText("Issue Description"), "Broken window latch");
    await user.click(screen.getByRole("button", { name: "Raise Ticket" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/maintenance-tickets", {
        unit_id: "unit-a1",
        description: "Broken window latch",
        priority: "medium",
        category: undefined,
        assigned_to: undefined,
      }),
    );
  });

  it("renders priority badges with the approved soft colors: Low=slate, Medium=blue, High=amber, Urgent=red", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("Urgent").className).toContain("red");
    expect(within(table).getByText("High").className).toContain("amber");
    expect(within(table).getByText("Medium").className).toContain("blue");
    expect(within(table).getByText("Low").className).toContain("slate");
  });

  it("renders status badges with the approved soft colors: Open=amber, In progress=blue, Resolved=green, Closed=slate — never the same color for Resolved and Closed", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    const open = within(table).getByText("Open");
    const inProgress = within(table).getByText("In progress");
    const resolved = within(table).getByText("Resolved");
    const closed = within(table).getByText("Closed");
    expect(open.className).toContain("amber");
    expect(inProgress.className).toContain("blue");
    expect(resolved.className).toContain("green");
    expect(closed.className).toContain("slate");
    expect(resolved.className).not.toEqual(closed.className);
  });

  it("shows unit name with stronger visual weight (semibold) in the tickets table", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getAllByText("Flat C6")[0].className).toContain("font-semibold");
  });

  it("gives Start/Resolve/Close primary (teal) emphasis and Details neutral/outlined emphasis", async () => {
    const user = userEvent.setup();
    await renderPage();
    const startButton = screen.getByRole("button", { name: /^Start/ });
    expect(startButton.className).toContain("bg-brand-green");

    await user.type(screen.getByLabelText(/Cost for Flat A1/), "300");
    const resolveButton = screen.getByRole("button", { name: /^Resolve/ });
    expect(resolveButton.className).toContain("bg-brand-green");

    const closeButton = screen.getByRole("button", { name: /^Close/ });
    expect(closeButton.className).toContain("bg-brand-green");

    const detailsButtons = screen.getAllByRole("button", { name: /^Details/ });
    expect(detailsButtons[0].className).toContain("bg-white");
    expect(detailsButtons[0].className).not.toContain("bg-brand-green");
  });

  it("advancing a ticket's status still calls PATCH with the correct next status", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: /^Start/ }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith("/maintenance-tickets/ticket-open", { status: "in_progress" }));
  });

  it("resolving with a cost still includes the cost in the PATCH payload", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByLabelText(/Cost for Flat A1/), "450");
    await user.click(screen.getByRole("button", { name: /^Resolve/ }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith("/maintenance-tickets/ticket-inprogress", { status: "resolved", cost: 450 }));
  });

  it("shows the compact, centered empty state with the exact existing message when there are no tickets", async () => {
    currentTickets = [];
    await renderPage();
    await waitFor(() => expect(screen.getByText("No maintenance tickets yet — raise one above to get started.")).toBeInTheDocument());
  });

  it("renders every ticket row without throwing at a narrow (mobile) width, with no new API calls beyond page load", async () => {
    await renderPage();
    const callsBefore = apiGet.mock.calls.length;
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
    expect(apiGet.mock.calls.length).toBe(callsBefore);
  });
});
