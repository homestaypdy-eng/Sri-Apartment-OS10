import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import GuestsPage from "../pages/GuestsPage";
import { loginAs } from "./testAuth";

/**
 * Guests page — UI/UX polish verification. Covers exactly what changed in
 * this pass: Indian-grouped currency, explicit English dates, the
 * Guest/Personal/Address/Emergency form grouping, consistent "(optional)"
 * labeling, the "New" segment badge, and the two distinct empty states —
 * plus a regression check that search/filter/sort/actions still work.
 */

const GUEST_A = {
  id: "guest-a", name: "Priya Shah", phone: "9876543210", email: "priya@example.com", address: null,
  id_proof_type: null, id_proof_number: null, id_proof_url: null, date_of_birth: null,
  gender: null, nationality: null, occupation: null, whatsapp_number: null,
  emergency_contact_name: null, emergency_contact_phone: null, photo_url: null, notes: null,
  is_vip: false, is_blacklisted: false, merged_into_guest_id: null,
};
const GUEST_B = {
  ...GUEST_A, id: "guest-b", name: "Ravi Kumar", phone: "9988776655", email: null,
};

const SEGMENT_A: { guest_id: string; name: string; phone: string; email: string | null; is_vip: boolean; is_blacklisted: boolean; is_repeat_guest: boolean; total_bookings: number; total_spent: number; latest_source: string; last_stay: string } = {
  guest_id: GUEST_A.id, name: GUEST_A.name, phone: GUEST_A.phone, email: GUEST_A.email,
  is_vip: false, is_blacklisted: false, is_repeat_guest: false,
  total_bookings: 1, total_spent: 11340, latest_source: "website", last_stay: "2026-08-26",
};
const SEGMENT_B = {
  guest_id: GUEST_B.id, name: GUEST_B.name, phone: GUEST_B.phone, email: null,
  is_vip: false, is_blacklisted: false, is_repeat_guest: true,
  total_bookings: 2, total_spent: 2000, latest_source: "direct", last_stay: "2026-09-16",
};

const apiGet = vi.fn();
const apiPost = vi.fn().mockResolvedValue({ data: {} });

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  extractErrorMessage: (e: unknown) => String(e),
}));

let currentSegments: typeof SEGMENT_A[] = [SEGMENT_A, SEGMENT_B];

function mockRoutesFor(url: string, config?: { params?: Record<string, string> }) {
  if (url === "/guests") return Promise.resolve({ data: [GUEST_A, GUEST_B] });
  if (url === "/guests/segments") {
    const params = config?.params ?? {};
    let rows = currentSegments;
    if (params.search) rows = rows.filter((s) => s.name.toLowerCase().includes(params.search.toLowerCase()));
    if (params.segment === "new") rows = rows.filter((s) => !s.is_repeat_guest);
    if (params.segment === "repeat") rows = rows.filter((s) => s.is_repeat_guest);
    return Promise.resolve({ data: rows });
  }
  if (url === `/guests/${GUEST_A.id}/history`) {
    return Promise.resolve({
      data: {
        guest: { id: GUEST_A.id, name: GUEST_A.name, phone: GUEST_A.phone, email: GUEST_A.email },
        total_bookings: 1, is_repeat_guest: false, total_spent: 11340, bookings: [], requests: [],
      },
    });
  }
  if (url === `/guests/${GUEST_A.id}/documents`) return Promise.resolve({ data: [] });
  return Promise.resolve({ data: [] });
}

async function renderPage() {
  loginAs("admin");
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <GuestsPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("Priya Shah")).toBeInTheDocument());
  return utils;
}

describe("GuestsPage — UI polish", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockClear();
    currentSegments = [SEGMENT_A, SEGMENT_B];
    apiGet.mockImplementation(mockRoutesFor);
  });

  it("groups the Add Guest form into labeled sections: Guest Information, Personal Details, Address, Emergency Contact", async () => {
    await renderPage();
    expect(screen.getByText("Guest Information")).toBeInTheDocument();
    expect(screen.getByText("Personal Details")).toBeInTheDocument();
    expect(screen.getByText("Address")).toBeInTheDocument();
    expect(screen.getByText("Emergency Contact")).toBeInTheDocument();
  });

  it("consistently marks every non-required Add Guest field as optional", async () => {
    await renderPage();
    expect(screen.getByPlaceholderText("Full name")).toBeRequired();
    expect(screen.getByPlaceholderText("Phone")).toBeRequired();
    expect(screen.getByPlaceholderText("Email (optional)")).not.toBeRequired();
    expect(screen.getByPlaceholderText("WhatsApp (optional)")).not.toBeRequired();
    expect(screen.getByPlaceholderText("Nationality (optional)")).not.toBeRequired();
    expect(screen.getByPlaceholderText("Emergency contact name (optional)")).not.toBeRequired();
    expect(screen.getByPlaceholderText("Emergency contact phone (optional)")).not.toBeRequired();
    expect(screen.getByText("Gender (optional)...")).toBeInTheDocument();
  });

  it("formats currency with Indian comma grouping (₹11,340), not ₹11340", async () => {
    await renderPage();
    expect(screen.getByText("₹11,340")).toBeInTheDocument();
    expect(screen.queryByText("₹11340")).not.toBeInTheDocument();
  });

  it("formats Last Stay using explicit English day/month/year, never a raw ISO string", async () => {
    await renderPage();
    expect(screen.getByText("26 Aug 2026")).toBeInTheDocument();
    expect(screen.queryByText("2026-08-26")).not.toBeInTheDocument();
  });

  it("shows a soft-blue \"New\" badge and a green \"Repeat\" badge, never inventing new segment values", async () => {
    await renderPage();
    const newBadge = screen.getByText("New", { selector: "span" });
    const repeatBadge = screen.getByText("Repeat", { selector: "span" });
    expect(newBadge.className).toContain("blue");
    expect(repeatBadge.className).toContain("green");
  });

  it("guest name renders as the strongest (semibold) element in its row", async () => {
    await renderPage();
    expect(screen.getByText("Priya Shah").className).toContain("font-semibold");
  });

  it("search still narrows the guest list", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByPlaceholderText("Search name, phone, or email..."), "Ravi");
    await waitFor(() => expect(screen.queryByText("Priya Shah")).not.toBeInTheDocument());
    expect(screen.getByText("Ravi Kumar")).toBeInTheDocument();
  });

  it("the segment filter still narrows the guest list", async () => {
    const user = userEvent.setup();
    await renderPage();
    const filterSelect = screen.getAllByRole("combobox").find((el) => within(el).queryByText("All guests")) as HTMLSelectElement;
    await user.selectOptions(filterSelect, "repeat");
    await waitFor(() => expect(screen.queryByText("Priya Shah")).not.toBeInTheDocument());
    expect(screen.getByText("Ravi Kumar")).toBeInTheDocument();
  });

  it("sort-by and sort-direction controls are still present and functional", async () => {
    const user = userEvent.setup();
    await renderPage();
    const sortSelect = screen.getAllByRole("combobox").find((el) => within(el).queryByText("Total Spent")) as HTMLSelectElement;
    expect(sortSelect).toBeTruthy();
    await user.selectOptions(sortSelect, "total_spent");
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/guests/segments", expect.objectContaining({ params: expect.objectContaining({ sort_by: "total_spent" }) })));

    const dirButton = screen.getByTitle("Toggle sort direction");
    expect(dirButton).toHaveTextContent("Asc");
    await user.click(dirButton);
    expect(dirButton).toHaveTextContent("Desc");
  });

  it("View / Edit still opens the guest detail view", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getAllByRole("button", { name: "View / Edit →" })[0]);
    await waitFor(() => expect(screen.getByRole("button", { name: /Mark VIP/ })).toBeInTheDocument());
  });

  it("Add Guest still submits a new guest via POST /guests", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByPlaceholderText("Full name"), "New Guest");
    await user.type(screen.getByPlaceholderText("Phone"), "9000000000");
    await user.click(screen.getByRole("button", { name: "Add Guest" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/guests", expect.objectContaining({ name: "New Guest", phone: "9000000000" })));
  });

  it("shows the truly-empty-system empty state when there are no guests and no filters are active", async () => {
    currentSegments = [];
    await renderPage_noWait();
    await waitFor(() => expect(screen.getByText(/Add your first guest above/)).toBeInTheDocument());
  });

  it("shows the filtered-to-zero empty state when a search/filter matches nothing", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByPlaceholderText("Search name, phone, or email..."), "Nobody Matches This");
    await waitFor(() => expect(screen.getByText("No guests match your current filters.")).toBeInTheDocument());
  });
});

async function renderPage_noWait() {
  loginAs("admin");
  render(
    <MemoryRouter>
      <AuthProvider>
        <GuestsPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}
