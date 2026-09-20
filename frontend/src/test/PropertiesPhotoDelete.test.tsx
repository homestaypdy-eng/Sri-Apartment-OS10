import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import PropertiesPage from "../pages/PropertiesPage";
import { loginAs } from "./testAuth";

/**
 * Photo-delete safety review: proves handleDeleteUnitPhoto now requires an
 * explicit window.confirm() before calling the DELETE API, mirroring the
 * existing handleToggleActiveProperty/handleToggleActiveUnit pattern in the
 * same file — same harness shape as PropertiesPricing.test.tsx, kept
 * separate since this file's fixtures need a unit with an existing photo.
 */

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

const PHOTO = {
  id: "photo-1", unit_id: "unit-1", filename: "exterior.jpg", content_type: "image/jpeg",
  caption: "Exterior view", sort_order: 0, is_primary: false, uploaded_at: "2026-01-01T00:00:00Z",
};

const apiGet = vi.fn();
const apiPatch = vi.fn();
const apiDelete = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
    post: vi.fn().mockResolvedValue({ data: {} }),
    delete: (...args: unknown[]) => apiDelete(...args),
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
  if (url === `/units/${UNIT.id}/photos`) return Promise.resolve({ data: [PHOTO] });
  if (url === "/maintenance-tickets") return Promise.resolve({ data: [] });
  if (url === "/housekeeping-tasks") return Promise.resolve({ data: [] });
  if (url === `/units/${UNIT.id}/seasonal-rates`) return Promise.resolve({ data: [] });
  if (url === `/units/${UNIT.id}/long-stay-discounts`) return Promise.resolve({ data: [] });
  return Promise.resolve({ data: [] });
}

async function renderAsAdmin() {
  loginAs("admin");
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <PropertiesPage />
      </AuthProvider>
    </MemoryRouter>,
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
  await waitFor(() => expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument());
}

describe("PropertiesPage — photo Delete requires confirmation (photo-delete safety fix)", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPatch.mockReset();
    apiDelete.mockReset();
    apiGet.mockImplementation(mockRoutesFor);
    apiDelete.mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not call the DELETE API when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderAsAdmin();
    await openManageDrawer(user);

    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/permanently delete/i));
    expect(apiDelete).not.toHaveBeenCalled();
    // The photo must still be visible — cancelling must leave it unchanged.
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("calls the DELETE API only after the confirmation is accepted", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await renderAsAdmin();
    await openManageDrawer(user);

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith(`/units/${UNIT.id}/photos/${PHOTO.id}`));
  });

  it("surfaces a delete failure through the existing error banner without crashing", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    apiDelete.mockRejectedValue({ response: { data: { detail: "Photo not found" } } });
    await renderAsAdmin();
    await openManageDrawer(user);

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.getByText("Photo not found")).toBeInTheDocument());
  });
});
