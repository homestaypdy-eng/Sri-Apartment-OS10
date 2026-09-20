import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import PropertiesPage from "../pages/PropertiesPage";
import { loginAs } from "./testAuth";

const PROPERTY = {
  id: "prop-1", name: "Test Property", address: "123 Test St", gst_number: null, state_code: "34",
  property_code: null, property_type: null, owner_name: null, owner_phone: null, owner_email: null,
  google_maps_url: null, is_active: true,
};

const UNIT = {
  id: "unit-1", property_id: "prop-1", name: "Test Unit", unit_type: "apartment", max_occupancy: 2,
  base_rate: 2500, weekend_rate: 3000, long_stay_min_nights: 30, long_stay_rate: 2000,
  status: "available", housekeeping_status: "clean",
  unit_code: null, floor: null, flat_number: null, amenities: null, description: null, is_active: true,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
};

const apiGet = vi.fn();
const apiPatch = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
    post: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  extractErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { message?: string; detail?: string } } };
    return err?.response?.data?.message ?? err?.response?.data?.detail ?? "error";
  },
}));

function mockRoutesFor(url: string) {
  if (url === "/properties") return Promise.resolve({ data: [PROPERTY] });
  if (url === "/units") return Promise.resolve({ data: [UNIT] });
  if (url === `/properties/${PROPERTY.id}/files`) return Promise.resolve({ data: [] });
  if (url === `/units/${UNIT.id}/photos`) return Promise.resolve({ data: [] });
  if (url === "/maintenance-tickets") return Promise.resolve({ data: [] });
  if (url === "/housekeeping-tasks") return Promise.resolve({ data: [] });
  if (url === `/units/${UNIT.id}/seasonal-rates`) return Promise.resolve({ data: [] });
  if (url === `/units/${UNIT.id}/long-stay-discounts`) return Promise.resolve({ data: [] });
  return Promise.resolve({ data: [] });
}

async function renderAs(role: string) {
  loginAs(role);
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <PropertiesPage />
      </AuthProvider>
    </MemoryRouter>
  );
  await waitFor(() => expect(screen.getAllByText("Test Unit").length).toBeGreaterThan(0));
  return utils;
}

async function openManageDrawer(user: ReturnType<typeof userEvent.setup>) {
  const buttons = screen.getAllByRole("button", { name: /^(Edit|View|Edit Pricing)$/ });
  await user.click(buttons[0]);
  await waitFor(() => {
    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.some((h) => h.textContent?.includes("Test Unit"))).toBe(true);
  });
}

function getDrawerHeading() {
  return screen.getAllByRole("heading", { level: 2 }).find((h) => h.textContent?.includes("Test Unit"))!;
}

describe("PropertiesPage — Manager pricing UI (RBAC Phase B)", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPatch.mockReset();
    apiGet.mockImplementation(mockRoutesFor);
    apiPatch.mockResolvedValue({ data: { ...UNIT, base_rate: 2800, weekend_rate: 3200 } });
  });

  it("Manager can open the pricing UI (drawer shows editable rate fields + Save Pricing)", async () => {
    const user = userEvent.setup();
    await renderAs("manager");
    await openManageDrawer(user);

    expect(screen.getByRole("button", { name: "Save Pricing" })).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText("Weekday rate")[0]).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText("Weekend rate")[0]).toBeInTheDocument();
    // Unit-identity fields from the full edit form must NOT be present for Manager
    expect(screen.queryByPlaceholderText("Amenities")).not.toBeInTheDocument();
    expect(screen.queryByText("Save Changes")).not.toBeInTheDocument();
  });

  it("Manager can submit a valid pricing payload via PATCH .../pricing (never the full unit-edit endpoint)", async () => {
    const user = userEvent.setup();
    await renderAs("manager");
    await openManageDrawer(user);

    const baseInput = screen.getAllByPlaceholderText("Weekday rate")[0];
    const weekendInput = screen.getAllByPlaceholderText("Weekend rate")[0];
    await user.clear(baseInput);
    await user.type(baseInput, "2800");
    await user.clear(weekendInput);
    await user.type(weekendInput, "3200");
    await user.click(screen.getByRole("button", { name: "Save Pricing" }));

    // UnitPricingUpdate (backend) accepts all four pricing fields, and the
    // form is pre-populated with the unit's real (non-null) long-stay
    // values — so a save legitimately resends all four, not just the two
    // fields the user actually touched. Asserting only base_rate/
    // weekend_rate here would require the fixture to omit NOT-NULL backend
    // fields, which doesn't reflect any real unit.
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith(`/units/${UNIT.id}/pricing`, {
        base_rate: 2800, weekend_rate: 3200, long_stay_min_nights: 30, long_stay_rate: 2000,
      })
    );
    await waitFor(() => expect(screen.getByText("Pricing updated.")).toBeInTheDocument());
  });

  it("Invalid (non-positive) pricing values are rejected client-side without calling the API", async () => {
    const user = userEvent.setup();
    await renderAs("manager");
    await openManageDrawer(user);

    const baseInput = screen.getAllByPlaceholderText("Weekday rate")[0];
    await user.clear(baseInput);
    await user.type(baseInput, "-50");
    await user.click(screen.getByRole("button", { name: "Save Pricing" }));

    expect(apiPatch).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText(/must be a positive number/i)).toBeInTheDocument());
  });

  it("Staff does not see the Manager pricing UI (still the plain read-only view)", async () => {
    const user = userEvent.setup();
    await renderAs("staff");
    await openManageDrawer(user);

    expect(screen.queryByRole("button", { name: "Save Pricing" })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Weekday rate")).not.toBeInTheDocument();
    const drawer = getDrawerHeading().closest("div")!.parentElement!;
    expect(within(drawer).getByText(/Weekday Rate/)).toBeInTheDocument();
  });

  it("Admin retains the full pricing/unit-edit capability (Save Changes, not the narrow Save Pricing)", async () => {
    const user = userEvent.setup();
    await renderAs("admin");
    await openManageDrawer(user);

    expect(screen.getByText("Save Changes")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save Pricing" })).not.toBeInTheDocument();
    expect(screen.getAllByPlaceholderText("Weekday rate")[0]).toBeInTheDocument();
  });
});

// RBAC Phase B security fix (finding I-6, NOW action): unit-photo
// Upload/Delete controls, previously Admin-only, are now also available to
// Manager (backend already granted POST/DELETE .../photos to Manager in
// Phase B — this only closes the matching frontend gap). Staff is
// deliberately left unchanged (still no Upload/Delete), and property-file
// upload/delete (a separate, ungated section covering both photos and
// documents like ownership papers/tax receipts) is untouched by this fix —
// see the dedicated regression test below.
const UNIT_PHOTO = {
  id: "photo-1", unit_id: UNIT.id, filename: "room.jpg", content_type: "image/jpeg",
  file_path: "/tmp/room.jpg", caption: null, uploaded_by: null, uploaded_at: "2026-01-01T00:00:00Z",
};

function mockRoutesWithUnitPhoto(url: string) {
  if (url === `/units/${UNIT.id}/photos`) return Promise.resolve({ data: [UNIT_PHOTO] });
  if (url === `/finance/property-pnl/${PROPERTY.id}`) {
    return Promise.resolve({
      data: {
        property_id: PROPERTY.id, property_name: PROPERTY.name, period_year: 2026, period_month: 1,
        units: [], total_revenue: 0, central_expenses: 0, total_expenses: 0, net_profit: 0,
      },
    });
  }
  return mockRoutesFor(url);
}

describe("PropertiesPage — Manager unit-photo Upload/Delete (RBAC Phase B security fix, finding I-6)", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPatch.mockReset();
    apiGet.mockImplementation(mockRoutesWithUnitPhoto);
    apiPatch.mockResolvedValue({ data: { ...UNIT, base_rate: 2800, weekend_rate: 3200 } });
  });

  it("Manager sees the Upload control for unit photos", async () => {
    const user = userEvent.setup();
    await renderAs("manager");
    await openManageDrawer(user);

    const drawer = getDrawerHeading().closest("div")!.parentElement!;
    expect(within(drawer).getByRole("button", { name: "Upload" })).toBeInTheDocument();
  });

  it("Manager sees the Delete control for an existing unit photo", async () => {
    const user = userEvent.setup();
    await renderAs("manager");
    await openManageDrawer(user);

    const drawer = getDrawerHeading().closest("div")!.parentElement!;
    expect(within(drawer).getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("Manager can trigger the existing upload API call via the Upload control", async () => {
    const user = userEvent.setup();
    const { api } = await import("../lib/api");
    const apiPost = api.post as ReturnType<typeof vi.fn>;
    apiPost.mockClear();
    await renderAs("manager");
    await openManageDrawer(user);

    const drawer = getDrawerHeading().closest("div")!.parentElement!;
    const fileInput = drawer.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeTruthy();
    await user.upload(fileInput, new File(["fake-image-bytes"], "new-room.jpg", { type: "image/jpeg" }));
    await user.click(within(drawer).getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      `/units/${UNIT.id}/photos`,
      expect.anything(),
      expect.anything(),
    ));
  });

  it("Staff does not see Upload or Delete controls for unit photos (unchanged)", async () => {
    const user = userEvent.setup();
    await renderAs("staff");
    await openManageDrawer(user);

    const drawer = getDrawerHeading().closest("div")!.parentElement!;
    expect(within(drawer).queryByRole("button", { name: "Upload" })).not.toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("Admin still sees Upload and Delete controls for unit photos (unchanged)", async () => {
    const user = userEvent.setup();
    await renderAs("admin");
    await openManageDrawer(user);

    const drawer = getDrawerHeading().closest("div")!.parentElement!;
    expect(within(drawer).getByRole("button", { name: "Upload" })).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("Property-file upload/delete UI remains unchanged (still visible to Manager, this fix did not touch it)", async () => {
    const user = userEvent.setup();
    await renderAs("manager");
    // Open the Manage Property drawer (not the unit drawer) — its
    // Documents & Photos section was never gated and must stay that way.
    await user.click(screen.getAllByRole("button", { name: "Manage Property" })[0]);
    await waitFor(() => expect(screen.getByText(/Manage — Test Property/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
  });
});
