import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import BookingsPage from "./BookingsPage";
import { loginAs } from "../test/testAuth";

/**
 * OS10 All Bookings freshness fix: the page used to fetch /bookings exactly
 * once on mount, so a booking confirmed on the public website while this
 * page was already open never appeared until a full page reload (see the
 * payment->booking->OS10 finalization audit). These tests prove the two
 * fixes — refetch on tab focus/visibility, and a manual Refresh button —
 * without resorting to continuous polling.
 */

const UNIT = {
  id: "unit-1", property_id: "prop-1", name: "Flat B3", unit_type: "apartment", max_occupancy: 5,
  base_rate: 3500, weekend_rate: null, long_stay_min_nights: null, long_stay_rate: null,
  status: "available", housekeeping_status: "clean",
  unit_code: null, floor: null, flat_number: null, amenities: null, description: null, is_active: true,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
};

const PROPERTY = {
  id: "prop-1", name: "Sri Apartment Homestay", address: "Pondicherry", gst_number: null, state_code: "34",
  property_code: null, property_type: null, owner_name: null, owner_phone: null, owner_email: null,
  google_maps_url: null, is_active: true,
};

type Guest = { id: string; name: string; phone: string; email: string | null };
const GUEST_OLD: Guest = { id: "guest-old", name: "Existing Guest", phone: "9000000001", email: null };
const GUEST_NEW: Guest = { id: "guest-new", name: "New Website Guest", phone: "9000000002", email: "new@example.com" };
// Deliberately absent from the INITIAL /guests response — created only
// "between" the initial load and a background refresh, the way a real
// website guest would be, to prove guestName() resolves after refresh
// rather than merely happening to already know the name from mount.
const GUEST_BRAND_NEW: Guest = { id: "guest-brand-new", name: "Brand New Guest", phone: "9000000003", email: "brandnew@example.com" };

const BOOKING_OLD = {
  id: "booking-old", unit_id: "unit-1", guest_id: "guest-old", check_in: "2027-01-01", check_out: "2027-01-03",
  adults: 2, children: 0, extra_bed: false, rate_applied: 3500, status: "confirmed", source: "direct",
  promo_code_used: null, arrival_time: null, departure_time: null, is_archived: false,
};

const BOOKING_NEW = {
  id: "booking-new", unit_id: "unit-1", guest_id: "guest-new", check_in: "2027-02-10", check_out: "2027-02-13",
  adults: 2, children: 0, extra_bed: false, rate_applied: 3600, status: "confirmed", source: "website",
  promo_code_used: null, arrival_time: null, departure_time: null, is_archived: false,
};

const BOOKING_FOR_BRAND_NEW_GUEST = {
  id: "booking-brand-new", unit_id: "unit-1", guest_id: "guest-brand-new", check_in: "2027-03-05", check_out: "2027-03-08",
  adults: 2, children: 0, extra_bed: false, rate_applied: 3700, status: "confirmed", source: "website",
  promo_code_used: null, arrival_time: null, departure_time: null, is_archived: false,
};

let currentBookings: typeof BOOKING_OLD[] = [];
let currentGuests: Guest[] = [];
let bookingsCallCount = 0;
let guestsCallCount = 0;
const apiGet = vi.fn((url: string): Promise<{ data: unknown[] }> => {
  if (url === "/bookings") {
    bookingsCallCount += 1;
    return Promise.resolve({ data: currentBookings });
  }
  if (url === "/units") return Promise.resolve({ data: [UNIT] });
  if (url === "/guests") {
    guestsCallCount += 1;
    return Promise.resolve({ data: currentGuests });
  }
  if (url === "/properties") return Promise.resolve({ data: [PROPERTY] });
  return Promise.resolve({ data: [] });
});

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(args[0] as string),
    post: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  extractErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { message?: string; detail?: string } } };
    return err?.response?.data?.message ?? err?.response?.data?.detail ?? "error";
  },
}));

function renderPage() {
  loginAs("admin");
  return render(
    <MemoryRouter>
      <AuthProvider>
        <BookingsPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("BookingsPage — All Bookings freshness (live refresh fix)", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockClear();
    apiGet.mockImplementation((url: string): Promise<{ data: unknown[] }> => {
      if (url === "/bookings") {
        bookingsCallCount += 1;
        return Promise.resolve({ data: currentBookings });
      }
      if (url === "/units") return Promise.resolve({ data: [UNIT] });
      if (url === "/guests") {
        guestsCallCount += 1;
        return Promise.resolve({ data: currentGuests });
      }
      if (url === "/properties") return Promise.resolve({ data: [PROPERTY] });
      return Promise.resolve({ data: [] });
    });
    bookingsCallCount = 0;
    guestsCallCount = 0;
    currentBookings = [BOOKING_OLD];
    currentGuests = [GUEST_OLD, GUEST_NEW];
    Object.defineProperty(document, "visibilityState", { value: "visible", writable: true, configurable: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches bookings once on initial page load", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());
    expect(bookingsCallCount).toBe(1);
  });

  it("explains that OS10 controls cancellation refund policy and may require manual review", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());

    await userEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("OS10 determines refund eligibility and any policy refund amount"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("manual review or reconciliation"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Staff cannot set the policy refund amount"));
    expect(confirm).not.toHaveBeenCalledWith(expect.stringContaining("only changes its status"));
    expect(confirm).not.toHaveBeenCalledWith(expect.stringContaining("handled separately from the payment's own Refund action"));
  });

  it("refetches bookings when the tab becomes visible/focused again, and shows a newly created booking", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());
    expect(screen.queryByText("New Website Guest")).not.toBeInTheDocument();

    // Simulate a booking confirmed on the website while this page was open,
    // then the staff member returning to this tab.
    currentBookings = [BOOKING_NEW, BOOKING_OLD];
    document.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => expect(screen.getByText("New Website Guest")).toBeInTheDocument());
    expect(screen.getByText("Existing Guest")).toBeInTheDocument(); // no rows lost
  });

  it("resolves a brand-new guest's name (not just the booking row) after a background refresh", async () => {
    // Regression test for the exact gap this fix closes: refreshBookings()
    // used to re-fetch only /bookings, so a guest who didn't exist at the
    // last full page load (e.g. a first-time website guest) rendered as
    // "—" until a full reload, even though the booking itself was already
    // showing correctly.
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());
    expect(screen.queryByText("Brand New Guest")).not.toBeInTheDocument();

    currentBookings = [BOOKING_FOR_BRAND_NEW_GUEST, BOOKING_OLD];
    currentGuests = [GUEST_OLD, GUEST_NEW, GUEST_BRAND_NEW];
    document.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => expect(screen.getByText("Brand New Guest")).toBeInTheDocument());
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("does not send two requests for one focus event (visibilitychange + focus firing together)", async () => {
    renderPage();
    await waitFor(() => expect(bookingsCallCount).toBe(1));
    expect(guestsCallCount).toBe(1);

    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));

    await waitFor(() => expect(bookingsCallCount).toBe(2)); // 1 initial + 1 coalesced refresh, not 3
    // Give any accidental second refetch a moment to have fired if it were going to.
    await new Promise((r) => setTimeout(r, 50));
    expect(bookingsCallCount).toBe(2);
    expect(guestsCallCount).toBe(2); // guests refetch is coalesced together with bookings, same guard
  });

  it("ignores focus/visibility events while the tab is not actually visible", async () => {
    renderPage();
    await waitFor(() => expect(bookingsCallCount).toBe(1));

    Object.defineProperty(document, "visibilityState", { value: "hidden", writable: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));

    await new Promise((r) => setTimeout(r, 50));
    expect(bookingsCallCount).toBe(1); // still just the initial load
  });

  it("the manual Refresh button fetches the latest bookings immediately", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());

    currentBookings = [BOOKING_NEW, BOOKING_OLD];
    await user.click(screen.getByRole("button", { name: /^Refresh$/ }));

    await waitFor(() => expect(screen.getByText("New Website Guest")).toBeInTheDocument());
    expect(bookingsCallCount).toBe(2);
  });

  it("does not duplicate rows across repeated refreshes", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());

    currentBookings = [BOOKING_NEW, BOOKING_OLD];
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(screen.getByText("New Website Guest")).toBeInTheDocument());

    expect(screen.getAllByText("New Website Guest")).toHaveLength(1);
    expect(screen.getAllByText("Existing Guest")).toHaveLength(1);
  });

  it("status filter still applies after a background refresh", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());

    currentBookings = [BOOKING_NEW, { ...BOOKING_OLD, status: "cancelled" }];
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(screen.getByText("New Website Guest")).toBeInTheDocument());

    const statusSelect = screen.getByDisplayValue("All statuses");
    await userEvent.selectOptions(statusSelect, "confirmed");

    expect(screen.getByText("New Website Guest")).toBeInTheDocument();
    expect(screen.queryByText("Existing Guest")).not.toBeInTheDocument(); // now cancelled, filtered out
  });

  it("showArchived toggle still requests archived bookings, independent of the refresh fix", async () => {
    renderPage();
    await waitFor(() => expect(bookingsCallCount).toBe(1));

    const archivedCheckbox = screen.getByRole("checkbox", { name: /show archived/i });
    await userEvent.click(archivedCheckbox);

    await waitFor(() => expect(bookingsCallCount).toBe(2));
    const archivedCall = apiGet.mock.calls.find((c) => c[0] === "/bookings");
    expect(archivedCall).toBeTruthy();
  });

  it("shows a distinct 'Refreshing...' state on the button without blanking the existing table", async () => {
    type ApiResult = { data: unknown[] };
    let resolveBookings: ((v: ApiResult) => void) | undefined;
    apiGet.mockImplementation((url: string): Promise<ApiResult> => {
      if (url === "/bookings") {
        bookingsCallCount += 1;
        if (bookingsCallCount === 1) return Promise.resolve({ data: currentBookings });
        return new Promise<ApiResult>((resolve) => {
          resolveBookings = resolve;
        });
      }
      if (url === "/units") return Promise.resolve({ data: [UNIT] });
      if (url === "/guests") return Promise.resolve({ data: [GUEST_OLD, GUEST_NEW] });
      if (url === "/properties") return Promise.resolve({ data: [PROPERTY] });
      return Promise.resolve({ data: [] });
    });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Existing Guest")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /^Refresh$/ }));
    expect(await screen.findByRole("button", { name: /Refreshing/i })).toBeInTheDocument();
    // The previously loaded row must stay visible during the refresh, not
    // be replaced by the full-page loading skeleton.
    expect(screen.getByText("Existing Guest")).toBeInTheDocument();

    resolveBookings?.({ data: [BOOKING_NEW, BOOKING_OLD] });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Refresh$/ })).toBeInTheDocument());
  });
});
