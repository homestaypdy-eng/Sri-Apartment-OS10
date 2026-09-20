import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import GuestsPage from "../pages/GuestsPage";
import { loginAs } from "./testAuth";

// RBAC Phase B security fix (finding I-7): POST /guests/merge is, and
// always was, admin-only on the backend — this page previously had no role
// awareness at all, so the "Merge Duplicate" control was visible (and
// clickable, only to fail at submit time with a 403) to Manager and Staff.
// These tests confirm the frontend now hides it for anyone but Admin,
// without touching the backend's existing, already-correct 403 behavior.

const GUEST = {
  id: "guest-1", name: "Test Guest", phone: "9876543210", email: null, address: null,
  id_proof_type: null, id_proof_number: null, id_proof_url: null, date_of_birth: null,
  gender: null, nationality: null, occupation: null, whatsapp_number: null,
  emergency_contact_name: null, emergency_contact_phone: null, photo_url: null, notes: null,
  is_vip: false, is_blacklisted: false, merged_into_guest_id: null,
};

const SEGMENT = {
  guest_id: GUEST.id, name: GUEST.name, phone: GUEST.phone, email: null,
  is_vip: false, is_blacklisted: false, is_repeat_guest: false,
  total_bookings: 1, total_spent: 2500, latest_source: "direct", last_stay: null,
};

const HISTORY = {
  guest: { id: GUEST.id, name: GUEST.name, phone: GUEST.phone, email: null },
  total_bookings: 1, is_repeat_guest: false, total_spent: 2500, bookings: [], requests: [],
};

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  extractErrorMessage: (e: unknown) => String(e),
}));

function mockRoutesFor(url: string) {
  if (url === "/guests") return Promise.resolve({ data: [GUEST] });
  if (url === "/guests/segments") return Promise.resolve({ data: [SEGMENT] });
  if (url === `/guests/${GUEST.id}/history`) return Promise.resolve({ data: HISTORY });
  if (url === `/guests/${GUEST.id}/documents`) return Promise.resolve({ data: [] });
  return Promise.resolve({ data: [] });
}

async function openGuestDetailAs(role: string) {
  loginAs(role);
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <AuthProvider>
        <GuestsPage />
      </AuthProvider>
    </MemoryRouter>
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "View / Edit" })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: "View / Edit" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /Mark VIP/ })).toBeInTheDocument());
  return user;
}

describe("GuestsPage — Guest Merge visibility (RBAC Phase B security fix, finding I-7)", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockImplementation(mockRoutesFor);
  });

  it("Admin sees the Merge Duplicate control", async () => {
    await openGuestDetailAs("admin");
    expect(screen.getByRole("button", { name: "Merge Duplicate" })).toBeInTheDocument();
  });

  it("Manager does not see the Merge Duplicate control", async () => {
    await openGuestDetailAs("manager");
    expect(screen.queryByRole("button", { name: "Merge Duplicate" })).not.toBeInTheDocument();
  });

  it("Staff does not see the Merge Duplicate control", async () => {
    await openGuestDetailAs("staff");
    expect(screen.queryByRole("button", { name: "Merge Duplicate" })).not.toBeInTheDocument();
  });

  it("Existing backend/API behavior is not changed: no /guests/merge call is made merely by viewing the page", async () => {
    await openGuestDetailAs("admin");
    expect(apiPost).not.toHaveBeenCalledWith("/guests/merge", expect.anything());
  });
});
