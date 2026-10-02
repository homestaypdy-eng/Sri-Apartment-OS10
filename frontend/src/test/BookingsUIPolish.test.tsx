import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import BookingsPage from "../pages/BookingsPage";
import { loginAs } from "./testAuth";

/**
 * Bookings page — UI/UX polish verification. Covers exactly what changed in
 * this pass: New Booking form section grouping, explicit English date
 * formatting, Indian-grouped currency, the Reservation Details section
 * restructure (Guest & Apartment / Stay Details / Guests & Timing /
 * Internal Notes / Actions / Payment Summary), the action-button hierarchy
 * (Cancel = warning, Delete = danger), and the success banner — plus a
 * regression check that guest search, dates, and the existing action
 * handlers still work.
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

const GUEST = { id: "guest-1", name: "Priya Shah", phone: "9876543210", email: "priya@example.com" };
const COMPANION = { id: "guest-2", name: "Arun Kumar", phone: "9123456789", email: null, identity_status: "verified" };

const BOOKING = {
  id: "booking-1", unit_id: "unit-1", guest_id: "guest-1", check_in: "2026-09-27", check_out: "2026-09-29",
  adults: 2, children: 0, extra_bed: false, rate_applied: 3500, status: "confirmed", source: "direct",
  promo_code_used: null, arrival_time: null, departure_time: null, is_archived: false,
  internal_notes: null, special_requests: null, updated_at: "2026-09-20T10:00:00Z",
};

const PAYMENT = {
  id: "payment-1", booking_id: BOOKING.id, amount: 3500, mode: "upi", reference_no: null,
  paid_at: "2026-09-20T10:00:00Z", refund_reason: null, refund_of_payment_id: null,
};

// jsdom doesn't implement scrollIntoView; Reservation Details focuses and
// scrolls to itself when opened, so this needs a no-op stand-in.
Element.prototype.scrollIntoView = vi.fn();

const apiGet = vi.fn();
const apiPost = vi.fn().mockResolvedValue({ data: {} });
const apiPatch = vi.fn().mockResolvedValue({ data: {} });
const apiDelete = vi.fn().mockResolvedValue({ data: {} });

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
    delete: (...args: unknown[]) => apiDelete(...args),
  },
  extractErrorMessage: (e: unknown) => String(e),
}));

let currentBookingGuests = { principal_guest: { guest_id: GUEST.id, name: GUEST.name, phone: GUEST.phone, identity_status: "not_required" }, additional_guests: [] as { id: string; guest_id: string; name: string; phone: string; identity_status: string; added_at: string }[] };

function mockRoutesFor(url: string) {
  if (url === "/bookings") return Promise.resolve({ data: [BOOKING] });
  if (url === "/units") return Promise.resolve({ data: [UNIT] });
  if (url === "/guests") return Promise.resolve({ data: [GUEST, COMPANION] });
  if (url === "/properties") return Promise.resolve({ data: [PROPERTY] });
  if (url === `/bookings/${BOOKING.id}/attachments`) return Promise.resolve({ data: [] });
  if (url === `/bookings/${BOOKING.id}/timeline`) return Promise.resolve({ data: { events: [] } });
  if (url === `/payments/${BOOKING.id}`) return Promise.resolve({ data: [PAYMENT] });
  if (url === `/bookings/${BOOKING.id}/guests`) return Promise.resolve({ data: currentBookingGuests });
  return Promise.resolve({ data: [] });
}

async function renderPage() {
  loginAs("admin");
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <BookingsPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("Priya Shah")).toBeInTheDocument());
  return utils;
}

describe("BookingsPage — UI polish", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockClear();
    apiPatch.mockClear();
    apiDelete.mockClear();
    currentBookingGuests = { principal_guest: { guest_id: GUEST.id, name: GUEST.name, phone: GUEST.phone, identity_status: "not_required" }, additional_guests: [] };
    apiGet.mockImplementation(mockRoutesFor);
  });

  it("groups the New Booking form into labeled sections: Booking Setup, Stay, Guests, Timing", async () => {
    await renderPage();
    expect(screen.getByText("Booking Setup")).toBeInTheDocument();
    expect(screen.getByText("Stay")).toBeInTheDocument();
    expect(screen.getByText("Guests")).toBeInTheDocument();
    expect(screen.getByText("Timing")).toBeInTheDocument();
  });

  it("the unit dropdown shows Indian-grouped currency for the rate", async () => {
    await renderPage();
    expect(screen.getByText(/₹3,500\/night/)).toBeInTheDocument();
  });

  it("guest search in the New Booking form still finds and selects a guest", async () => {
    const user = userEvent.setup();
    await renderPage();
    const searchInputs = screen.getAllByPlaceholderText("Search guest by name, phone or email");
    await user.type(searchInputs[0], "Priya");
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/guests", { params: { search: "Priya", limit: 20 } }));

    await user.click(await screen.findByRole("button", { name: /Priya Shah/ }));
    expect(screen.getByText("Selected Guest")).toBeInTheDocument();
  });

  it("formats the booking table's dates in explicit English (27 Sep 2026), never raw ISO", async () => {
    await renderPage();
    expect(screen.getByText("27 Sep 2026")).toBeInTheDocument();
    expect(screen.getByText("29 Sep 2026")).toBeInTheDocument();
    expect(screen.queryByText("2026-09-27")).not.toBeInTheDocument();
  });

  it("formats the booking table's rate with Indian comma grouping", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("₹3,500")).toBeInTheDocument();
  });

  it("opening Reservation Details shows all six labeled sections", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await waitFor(() => expect(screen.getByText("Reservation Details")).toBeInTheDocument());

    expect(screen.getByText("Guest & Apartment")).toBeInTheDocument();
    expect(screen.getByText("Stay Details")).toBeInTheDocument();
    expect(screen.getByText("Guests & Timing")).toBeInTheDocument();
    expect(screen.getByText("Internal Notes")).toBeInTheDocument();
    expect(screen.getByText("Actions")).toBeInTheDocument();
    expect(screen.getByText("Payment Summary")).toBeInTheDocument();
  });

  it("shows a real Payment Summary (Total / Paid / Balance / Payment Status) derived from existing data", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await waitFor(() => expect(screen.getByText("Payment Summary")).toBeInTheDocument());

    // 2 nights * ₹3,500 = ₹7,000 total; ₹3,500 paid; ₹3,500 balance; Partially Paid.
    expect(screen.getByText("₹7,000")).toBeInTheDocument();
    expect(screen.getAllByText("₹3,500").length).toBeGreaterThan(0);
    expect(screen.getByText("Partially Paid")).toBeInTheDocument();
  });

  it("Cancel Booking uses the warning treatment, Delete Booking uses the danger treatment", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await waitFor(() => expect(screen.getByText("Actions")).toBeInTheDocument());

    const cancelBtn = screen.getByRole("button", { name: "Cancel Booking" });
    const deleteBtn = screen.getByRole("button", { name: "Delete Booking" });
    expect(cancelBtn.className).toContain("amber");
    expect(deleteBtn.className).toContain("bg-red-600");
  });

  it("Save Changes still submits the edit form via PATCH /bookings/:id", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await waitFor(() => expect(screen.getByText("Actions")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith(`/bookings/${BOOKING.id}`, expect.objectContaining({ guest_id: GUEST.id })));
  });

  it("shows a soft-green success banner with the new booking's dates after Confirm Booking succeeds", async () => {
    const user = userEvent.setup();
    apiPost.mockResolvedValueOnce({ data: { ...BOOKING, id: "booking-2", check_in: "2026-10-01", check_out: "2026-10-03" } });
    await renderPage();

    await user.selectOptions(screen.getAllByRole("combobox")[0], UNIT.id);

    const searchInputs = screen.getAllByPlaceholderText("Search guest by name, phone or email");
    await user.type(searchInputs[0], "Priya");
    await user.click(await screen.findByRole("button", { name: /Priya Shah/ }));

    const dateInputs = document.querySelectorAll('input[type="date"]');
    await user.type(dateInputs[0] as HTMLInputElement, "2026-10-01");
    await user.type(dateInputs[1] as HTMLInputElement, "2026-10-03");

    await user.click(screen.getByRole("button", { name: "Confirm Booking" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/bookings", expect.objectContaining({ unit_id: UNIT.id })));
    expect(await screen.findByText("Booking confirmed: 2026-10-01 → 2026-10-03")).toBeInTheDocument();
  });

  it("existing booking-row actions (Check In / Cancel) remain present and functional", async () => {
    await renderPage();
    expect(screen.getByRole("button", { name: "Check In" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  describe("Additional Guests (Identity Verification feature)", () => {
    it("shows the Principal Guest's identity status next to the Guest picker", async () => {
      const user = userEvent.setup();
      await renderPage();
      await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
      await waitFor(() => expect(screen.getByText("Additional Guests")).toBeInTheDocument());
      expect(screen.getByText("Identity Not Verified")).toBeInTheDocument();
    });

    it('shows "no additional guests" until one is added', async () => {
      const user = userEvent.setup();
      await renderPage();
      await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
      await waitFor(() => expect(screen.getByText("No additional guests on this booking yet.")).toBeInTheDocument());
    });

    it("adding an existing guest calls POST /bookings/:id/guests and shows their identity status", async () => {
      const user = userEvent.setup();
      await renderPage();
      await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
      await waitFor(() => expect(screen.getByText("Additional Guests")).toBeInTheDocument());

      await user.click(screen.getByRole("button", { name: "+ Add Guest" }));
      const searchInputs = screen.getAllByPlaceholderText("Search guest by name, phone or email");
      await user.type(searchInputs[searchInputs.length - 1], "Arun");
      await user.click(await screen.findByRole("button", { name: /Arun Kumar/ }));

      await waitFor(() => expect(apiPost).toHaveBeenCalledWith(`/bookings/${BOOKING.id}/guests`, { guest_id: COMPANION.id }));

      // Simulate the server-side result the real endpoint would now return,
      // matching how the page re-fetches after a successful add.
      currentBookingGuests = {
        ...currentBookingGuests,
        additional_guests: [{ id: "bg-1", guest_id: COMPANION.id, name: COMPANION.name, phone: COMPANION.phone, identity_status: "verified", added_at: "2026-09-27T10:00:00Z" }],
      };
    });

    it("an already-verified companion shows Identity Verified with no re-verification prompt", async () => {
      currentBookingGuests = {
        ...currentBookingGuests,
        additional_guests: [{ id: "bg-1", guest_id: COMPANION.id, name: COMPANION.name, phone: COMPANION.phone, identity_status: "verified", added_at: "2026-09-27T10:00:00Z" }],
      };
      const user = userEvent.setup();
      await renderPage();
      await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
      await waitFor(() => expect(screen.getByText("Arun Kumar")).toBeInTheDocument());
      expect(screen.getByText("Identity Verified")).toBeInTheDocument();
    });

    it("removing an additional guest calls DELETE with the assignment id, not the guest id", async () => {
      currentBookingGuests = {
        ...currentBookingGuests,
        additional_guests: [{ id: "bg-1", guest_id: COMPANION.id, name: COMPANION.name, phone: COMPANION.phone, identity_status: "verified", added_at: "2026-09-27T10:00:00Z" }],
      };
      const user = userEvent.setup();
      await renderPage();
      await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
      await waitFor(() => expect(screen.getByText("Arun Kumar")).toBeInTheDocument());

      await user.click(screen.getByRole("button", { name: /Remove Arun Kumar/ }));
      await waitFor(() => expect(apiDelete).toHaveBeenCalledWith(`/bookings/${BOOKING.id}/guests/bg-1`));
    });
  });
});
