import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import PropertiesPage from "../pages/PropertiesPage";
import { loginAs } from "./testAuth";

/**
 * Properties & Accommodations — UI/UX polish verification.
 *
 * Covers exactly the fixes made in this pass: fixed apartment ordering
 * (matching the Availability Calendar's own order, not backend-return
 * order), Indian-grouped currency formatting, and Title-Case status
 * presentation — plus a regression check that existing search/filter/
 * action functionality was not disturbed by any of it.
 */

const PROPERTY = {
  id: "prop-1", name: "Sri Apartment Homestay", address: "Moolakulam, Puducherry - 605010",
  gst_number: null, state_code: "34", property_code: "SRI-APT", property_type: "homestay",
  owner_name: null, owner_phone: null, owner_email: null, google_maps_url: "https://maps.google.com/?q=test",
  is_active: true,
};

const UNIT_BASE = {
  property_id: "prop-1", unit_type: "apartment", max_occupancy: 5, base_rate: 3500, weekend_rate: 3800,
  long_stay_min_nights: 30, long_stay_rate: 2000, status: "available", housekeeping_status: "clean",
  floor: null, flat_number: null, amenities: null, description: null, is_active: true,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
};

const UNIT_A1 = { ...UNIT_BASE, id: "unit-a1", name: "Flat A1", unit_code: "A1" };
const UNIT_A2 = { ...UNIT_BASE, id: "unit-a2", name: "Flat A2", unit_code: "A2" };
const UNIT_B3 = { ...UNIT_BASE, id: "unit-b3", name: "Flat B3", unit_code: "B3" };
const UNIT_B4 = { ...UNIT_BASE, id: "unit-b4", name: "Flat B4", unit_code: "B4" };
const UNIT_C5 = { ...UNIT_BASE, id: "unit-c5", name: "Flat C5", unit_code: "C5" };
const UNIT_C6 = { ...UNIT_BASE, id: "unit-c6", name: "Flat C6", unit_code: "C6" };

// Deliberately NOT in A1..C6 order — matches the exact shuffled order the
// real backend returned in the reported bug (C6, A1, B3, B4, C5, A2), to
// prove the page reorders it rather than merely happening to already be
// in order.
const SHUFFLED_UNITS = [UNIT_C6, UNIT_A1, UNIT_B3, UNIT_B4, UNIT_C5, UNIT_A2];

const apiGet = vi.fn();
const apiPost = vi.fn().mockResolvedValue({ data: {} });

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  extractErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { message?: string; detail?: string } } };
    return err?.response?.data?.message ?? err?.response?.data?.detail ?? "error";
  },
}));

function mockRoutesFor(url: string) {
  if (url === "/properties") return Promise.resolve({ data: [PROPERTY] });
  if (url === "/units") return Promise.resolve({ data: SHUFFLED_UNITS });
  if (url === `/properties/${PROPERTY.id}/files`) return Promise.resolve({ data: [] });
  if (url.startsWith("/units/") && url.endsWith("/photos")) return Promise.resolve({ data: [] });
  if (url === "/maintenance-tickets") return Promise.resolve({ data: [] });
  if (url === "/housekeeping-tasks") return Promise.resolve({ data: [] });
  if (url.endsWith("/seasonal-rates")) return Promise.resolve({ data: [] });
  if (url.endsWith("/long-stay-discounts")) return Promise.resolve({ data: [] });
  return Promise.resolve({ data: [] });
}

async function renderPage() {
  loginAs("admin");
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <PropertiesPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getAllByText("Flat A1").length).toBeGreaterThan(0));
  return utils;
}

describe("PropertiesPage — UI polish", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockClear();
    apiGet.mockImplementation(mockRoutesFor);
  });

  it("orders accommodations A1 → A2 → B3 → B4 → C5 → C6 regardless of backend return order", async () => {
    await renderPage();
    const rowNames = screen.getAllByRole("row")
      .map((row) => row.querySelector("td p.font-medium")?.textContent)
      .filter((t): t is string => Boolean(t));
    expect(rowNames).toEqual(["Flat A1", "Flat A2", "Flat B3", "Flat B4", "Flat C5", "Flat C6"]);
  });

  it("formats currency with Indian comma grouping (₹3,500 / ₹3,800), not ₹3500/₹3800", async () => {
    await renderPage();
    expect(screen.getAllByText("₹3,500").length).toBeGreaterThan(0);
    expect(screen.getAllByText("₹3,800").length).toBeGreaterThan(0);
    expect(screen.queryByText("₹3500")).not.toBeInTheDocument();
    expect(screen.queryByText("₹3800")).not.toBeInTheDocument();
  });

  it("presents status as Title Case (\"Available\"), not the raw lowercase backend value", async () => {
    await renderPage();
    expect(screen.getAllByText("Available").length).toBeGreaterThan(0);
    expect(screen.queryByText("available")).not.toBeInTheDocument();
  });

  it("search still narrows the accommodation table", async () => {
    const user = userEvent.setup();
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    await user.type(screen.getByPlaceholderText("Search name, code, floor..."), "B3");
    await waitFor(() => expect(within(table).queryByText("Flat A1")).not.toBeInTheDocument());
    expect(within(table).getByText("Flat B3")).toBeInTheDocument();
  });

  it("the type/status filters still narrow the accommodation table", async () => {
    const user = userEvent.setup();
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    const selects = screen.getAllByRole("combobox").filter((el) => el.tagName === "SELECT");
    const statusFilter = selects.find((el) => within(el).queryByText("All Status")) as HTMLSelectElement;
    await user.selectOptions(statusFilter, "maintenance");
    await waitFor(() => expect(within(table).queryByText("Flat A1")).not.toBeInTheDocument());
  });

  it("Edit still opens the manage-unit drawer with real unit data", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await waitFor(() => expect(screen.getAllByRole("heading", { level: 2 }).some((h) => h.textContent?.includes("Flat"))).toBe(true));
  });

  it("+ Add Property still opens the add-property drawer", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: "+ Add Property" }));
    await waitFor(() => expect(screen.getByText("Add Property")).toBeInTheDocument());
  });

  it("+ Add Accommodation still opens the add-accommodation drawer", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: "+ Add Accommodation" }));
    await waitFor(() => expect(screen.getAllByText(/Add Accommodation/).length).toBeGreaterThan(0));
  });

  it("the Dynamic Pricing unit selector still lists every unit, in the same fixed order", async () => {
    await renderPage();
    const selects = screen.getAllByRole("combobox").filter((el) => el.tagName === "SELECT");
    const pricingSelect = selects.find((el) => within(el).queryByText(/Select a unit to manage its pricing rules/)) as HTMLSelectElement;
    expect(pricingSelect).toBeTruthy();
    const optionLabels = Array.from(pricingSelect.querySelectorAll("option")).map((o) => o.textContent);
    expect(optionLabels).toEqual([
      "Select a unit to manage its pricing rules...",
      "Flat A1", "Flat A2", "Flat B3", "Flat B4", "Flat C5", "Flat C6",
    ]);
  });

  it("shows the simplified Dynamic Pricing helper text", async () => {
    await renderPage();
    expect(screen.getByText("Manage seasonal, weekend and long-stay pricing rules for each apartment.")).toBeInTheDocument();
  });

  it("the property summary card shows Indian-formatted avg. weekday rate", async () => {
    await renderPage();
    expect(screen.getAllByText("₹3,500").length).toBeGreaterThan(0);
    expect(screen.getByText("Avg. Weekday Rate")).toBeInTheDocument();
  });
});
