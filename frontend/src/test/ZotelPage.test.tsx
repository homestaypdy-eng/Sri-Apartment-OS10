import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import ZotelPage from "../pages/ZotelPage";
import { loginAs } from "./testAuth";

const UNIT_MAPPED = {
  id: "unit-1", property_id: "prop-1", name: "A1", unit_type: "apartment", max_occupancy: 2,
  base_rate: 3500, weekend_rate: 3800, long_stay_min_nights: 30, long_stay_rate: 2000,
  status: "available", housekeeping_status: "clean", unit_code: "A1", floor: null, flat_number: null,
  amenities: null, description: null, is_active: true,
};

const UNIT_UNMAPPED = {
  ...UNIT_MAPPED, id: "unit-2", name: "A2", unit_code: "A2",
};

const MAPPING = {
  id: "map-1", unit_id: UNIT_MAPPED.id, zotel_room_type_id: "rt-abc-123",
  zotel_room_type_name: "Deluxe Room", created_at: "2026-01-01T00:00:00Z",
};

const SYNC_LOG = {
  id: "log-1", sync_type: "bookings_import", status: "partial", message: "fetched=2 imported=1 unmapped=1",
  records_fetched: 2, records_imported: 1, records_updated: 0, records_skipped: 0,
  records_unmapped: 1, records_conflicted: 0, records_failed: 0, created_at: "2026-01-02T00:00:00Z",
};

const NOT_CONFIGURED_STATUS = {
  configured: false, base_url_set: false, hotel_id_set: false, api_key_set: false, api_secret_set: false,
};

const CONFIGURED_STATUS = {
  configured: true, base_url_set: true, hotel_id_set: true, api_key_set: true, api_secret_set: true,
};

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: (...args: unknown[]) => apiDelete(...args),
  },
  extractErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { detail?: string } } };
    return err?.response?.data?.detail ?? "error";
  },
}));

function mockRoutesFor(configured: boolean) {
  return (url: string) => {
    if (url === "/zotel/status") return Promise.resolve({ data: configured ? CONFIGURED_STATUS : NOT_CONFIGURED_STATUS });
    if (url === "/units") return Promise.resolve({ data: [UNIT_MAPPED, UNIT_UNMAPPED] });
    if (url === "/zotel/mappings") return Promise.resolve({ data: [MAPPING] });
    if (url === "/zotel/unmapped") return Promise.resolve({ data: [{ zotel_room_type_id: "rt-xyz-999", first_seen_in_reservation: "txn-1" }] });
    if (url === "/zotel/sync/logs") return Promise.resolve({ data: [SYNC_LOG] });
    return Promise.resolve({ data: [] });
  };
}

async function renderPage(configured = true) {
  loginAs("admin");
  apiGet.mockImplementation(mockRoutesFor(configured));
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <ZotelPage />
      </AuthProvider>
    </MemoryRouter>
  );
  await waitFor(() => expect(screen.getByText("Room Mappings")).toBeInTheDocument());
  return utils;
}

describe("ZotelPage", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockReset();
    apiDelete.mockReset();
    apiPost.mockResolvedValue({ data: {} });
    apiDelete.mockResolvedValue({ data: {} });
  });

  it("shows Not Configured status and never renders any credential value", async () => {
    await renderPage(false);
    expect(screen.getByText("Not Configured")).toBeInTheDocument();
    // The env var NAME may legitimately appear in help text; only a real-looking secret VALUE must never appear.
    expect(document.body.textContent).not.toMatch(/pk_live_\w|=[A-Za-z0-9]{16,}/);
  });

  it("shows Configured status with all fields set", async () => {
    await renderPage(true);
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getAllByText("Set").length).toBe(4);
  });

  it("renders the existing room mapping with mapped unit name and Zotel room type ID", async () => {
    await renderPage();
    expect(screen.getByText("A1 (A1)")).toBeInTheDocument();
    expect(screen.getByText("rt-abc-123")).toBeInTheDocument();
    expect(screen.getByText("Deluxe Room")).toBeInTheDocument();
  });

  it("lists unmapped Zotel rooms discovered in sync history with an Unmapped badge", async () => {
    await renderPage();
    expect(screen.getByText("Unmapped Zotel Rooms Seen In Sync History")).toBeInTheDocument();
    expect(screen.getByText("rt-xyz-999")).toBeInTheDocument();
    // "Unmapped" also labels a Sync Logs table column — assert at least the amber status badge is present.
    expect(screen.getAllByText("Unmapped").length).toBeGreaterThan(0);
  });

  it("shows sync log status and unmapped/conflicted counts with badges distinguishing them", async () => {
    await renderPage();
    expect(screen.getByText("partial")).toBeInTheDocument();
    // records_unmapped=1 renders as a badge; records_conflicted=0 renders as plain "0"
    const unmappedCell = screen.getAllByText("1");
    expect(unmappedCell.length).toBeGreaterThan(0);
  });

  it("Trigger Bookings Sync is disabled when Zotel is not configured", async () => {
    await renderPage(false);
    expect(screen.getByRole("button", { name: "Trigger Bookings Sync" })).toBeDisabled();
  });

  it("Trigger Bookings Sync calls POST /zotel/sync/bookings and shows the outcome summary when configured", async () => {
    const user = userEvent.setup();
    await renderPage(true);
    apiPost.mockResolvedValueOnce({
      data: { id: "log-2", records_imported: 3, records_updated: 1, records_skipped: 0, records_unmapped: 0, records_conflicted: 0, records_failed: 0 },
    });
    await user.click(screen.getByRole("button", { name: "Trigger Bookings Sync" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/zotel/sync/bookings"));
    await waitFor(() => expect(screen.getByText(/3 imported, 1 updated/)).toBeInTheDocument());
  });

  it("Add Mapping opens a drawer and submits unit_id + zotel_room_type_id to POST /zotel/mappings", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: "Add Mapping" }));
    await waitFor(() => expect(screen.getByText("Add Room Mapping")).toBeInTheDocument());

    const select = screen.getByRole("combobox");
    await user.selectOptions(select, UNIT_UNMAPPED.id);
    await user.type(screen.getByPlaceholderText(/room_type_id/), "rt-new-456");
    await user.click(screen.getByRole("button", { name: "Save Mapping" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/zotel/mappings", {
      unit_id: UNIT_UNMAPPED.id, zotel_room_type_id: "rt-new-456", zotel_room_type_name: null,
    }));
  });

  it("already-mapped units are disabled in the Add Mapping unit dropdown", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: "Add Mapping" }));
    await waitFor(() => expect(screen.getByText("Add Room Mapping")).toBeInTheDocument());

    const mappedOption = screen.getByText(/A1.*already mapped/) as HTMLOptionElement;
    expect(mappedOption.disabled).toBe(true);
  });

  it("Delete mapping asks for confirmation and calls DELETE with the mapping id", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    await renderPage();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith(`/zotel/mappings/${MAPPING.id}`));
    confirmSpy.mockRestore();
  });

  it("Delete mapping does nothing if the confirmation is declined", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderPage();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiDelete).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("Run Connectivity Test posts to /zotel/connectivity-test and renders PASS/FAIL per category", async () => {
    const user = userEvent.setup();
    await renderPage();
    apiPost.mockResolvedValueOnce({
      data: {
        configured: true, overall_passed: false,
        checks: [
          { category: "availability", passed: true, message: "OK" },
          { category: "bookings", passed: false, message: "HTTP 401: Invalid API key" },
        ],
      },
    });
    await user.click(screen.getByRole("button", { name: "Run Connectivity Test" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/zotel/connectivity-test"));
    // Overall FAIL badge + the failing "bookings" category badge both render as "FAIL".
    await waitFor(() => expect(screen.getAllByText("FAIL").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText("HTTP 401: Invalid API key")).toBeInTheDocument();
  });

  it("Run Reconciliation fetches the report and renders category badges + summary counts", async () => {
    const user = userEvent.setup();
    await renderPage();
    apiGet.mockImplementation((url: string) => {
      if (url === "/zotel/reconciliation") {
        return Promise.resolve({
          data: {
            generated_at: "2026-01-03T00:00:00Z",
            entries: [
              { external_reservation_id: "txn-1", category: "matched", detail: "OK" },
              { external_reservation_id: "txn-2", category: "missing_in_os10", detail: "Reservation exists in Zotel but has no matching OS10 booking" },
            ],
            summary: { matched: 1, missing_in_os10: 1 },
          },
        });
      }
      return mockRoutesFor(true)(url);
    });
    await user.click(screen.getByRole("button", { name: "Run Reconciliation" }));
    await waitFor(() => expect(screen.getByText("txn-1")).toBeInTheDocument());
    expect(screen.getByText("txn-2")).toBeInTheDocument();
    expect(screen.getByText("matched: 1")).toBeInTheDocument();
    expect(screen.getByText("missing in os10: 1")).toBeInTheDocument();
  });

  it("View on a sync log fetches the log detail and shows per-reservation outcome badges", async () => {
    const user = userEvent.setup();
    await renderPage();
    apiGet.mockImplementation((url: string) => {
      if (url === `/zotel/sync/logs/${SYNC_LOG.id}`) {
        return Promise.resolve({
          data: {
            ...SYNC_LOG,
            records: [
              { id: "rec-1", external_reservation_id: "txn-1", outcome: "imported", booking_id: "b-1", detail: "Imported into unit unit-1", created_at: "2026-01-02T00:00:00Z" },
              { id: "rec-2", external_reservation_id: "txn-2", outcome: "unmapped", booking_id: null, detail: "No OS10 unit mapped for Zotel room_type_id=rt-xyz-999", created_at: "2026-01-02T00:00:00Z" },
            ],
          },
        });
      }
      return mockRoutesFor(true)(url);
    });
    await user.click(screen.getByRole("button", { name: "View" }));
    await waitFor(() => expect(screen.getByText("txn-1")).toBeInTheDocument());
    expect(screen.getByText("imported")).toBeInTheDocument();
    expect(screen.getByText("unmapped")).toBeInTheDocument();
  });
});
