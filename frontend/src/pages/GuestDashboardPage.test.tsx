import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { GuestAuthProvider } from "../context/GuestAuthContext";
import GuestDashboardPage from "./GuestDashboardPage";

/**
 * Guest-portal Identity Verification section — covers exactly what this
 * feature added to the guest-facing dashboard: the not-required/pending/
 * submitted/verified/rejected states, the upload+submit flow, and that a
 * rejected guest only ever sees the fixed generic message, never any
 * internal admin reason (which this endpoint's own response never even
 * includes — see backend GuestIdentityStatusOut).
 */

const PROFILE = { id: "guest-1", name: "Priya Shah", phone: "9876543210", email: "priya@example.com", identity_status: "not_required" };
const BOOKINGS: unknown[] = [];
const REQUESTS: unknown[] = [];

let currentIdentity = { status: "not_required", document_type: null as string | null, verified_at: null as string | null, message: null as string | null };

const guestApiGet = vi.fn();
const guestApiPost = vi.fn();

vi.mock("../lib/api", () => ({
  guestApi: {
    get: (...args: unknown[]) => guestApiGet(...args),
    post: (...args: unknown[]) => guestApiPost(...args),
  },
  extractErrorMessage: (e: unknown) => String(e),
}));

function mockRoutesFor(url: string) {
  if (url === "/portal/me") return Promise.resolve({ data: PROFILE });
  if (url === "/portal/bookings") return Promise.resolve({ data: BOOKINGS });
  if (url === "/portal/requests") return Promise.resolve({ data: REQUESTS });
  if (url === "/portal/me/identity") return Promise.resolve({ data: currentIdentity });
  return Promise.resolve({ data: [] });
}

function renderPage() {
  localStorage.setItem("guest_access_token", "fake-guest-token");
  return render(
    <MemoryRouter>
      <GuestAuthProvider>
        <GuestDashboardPage />
      </GuestAuthProvider>
    </MemoryRouter>,
  );
}

describe("GuestDashboardPage — Identity Verification", () => {
  beforeEach(() => {
    localStorage.clear();
    guestApiGet.mockReset();
    guestApiPost.mockReset();
    currentIdentity = { status: "not_required", document_type: null, verified_at: null, message: null };
    guestApiGet.mockImplementation(mockRoutesFor);
    guestApiPost.mockResolvedValue({ data: {} });
  });

  it('shows "Identity Verification Required" and the upload form when not_required', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("⚠ Identity Verification Required")).toBeInTheDocument());
    expect(screen.getByText(/Please verify your identity before completing your stay registration/)).toBeInTheDocument();
    expect(screen.getByLabelText("Document type")).toBeInTheDocument();
    expect(screen.getByLabelText("Identity document file")).toBeInTheDocument();
  });

  it("uploading and submitting a document calls both endpoints and then shows Pending", async () => {
    const user = userEvent.setup();
    guestApiPost.mockImplementation((url: string) => {
      if (url === "/portal/me/identity/documents") return Promise.resolve({ data: { id: "doc-1", document_type: "passport", filename: "p.jpg", uploaded_at: "2026-09-27T10:00:00Z" } });
      if (url === "/portal/me/identity/submit") return Promise.resolve({ data: { status: "submitted" } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await waitFor(() => expect(screen.getByLabelText("Identity document file")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Document type"), "passport");
    const file = new File(["fake-image-bytes"], "passport.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("Identity document file"), file);

    currentIdentity = { status: "submitted", document_type: "passport", verified_at: null, message: null };
    const submitButton = screen.getByRole("button", { name: "Verify Identity" });
    await waitFor(() => expect(submitButton).toBeEnabled());
    fireEvent.submit(submitButton.closest("form")!);

    await waitFor(() => expect(guestApiPost).toHaveBeenCalledWith(
      "/portal/me/identity/documents",
      expect.anything(),
      expect.objectContaining({ params: { document_type: "passport" } }),
    ));
    await waitFor(() => expect(guestApiPost).toHaveBeenCalledWith("/portal/me/identity/submit", { document_id: "doc-1" }));
    await waitFor(() => expect(screen.getAllByText(/Verification Pending/)[0]).toBeInTheDocument());
  });

  it("shows Identity Verified with the document type and no upload form once verified", async () => {
    currentIdentity = { status: "verified", document_type: "aadhaar", verified_at: "2026-09-27T10:00:00Z", message: null };
    renderPage();
    await waitFor(() => expect(screen.getByText("✓ Identity Verified")).toBeInTheDocument());
    expect(screen.getByText("Aadhaar")).toBeInTheDocument();
    expect(screen.queryByLabelText("Identity document file")).not.toBeInTheDocument();
  });

  it("a rejected guest sees only the fixed generic message and can resubmit", async () => {
    currentIdentity = { status: "rejected", document_type: "pan", verified_at: null, message: "Your identity verification could not be completed. Please submit a valid identity document." };
    renderPage();
    await waitFor(() => expect(screen.getByText("✕ Verification Rejected")).toBeInTheDocument());
    expect(screen.getByText("Your identity verification could not be completed. Please submit a valid identity document.")).toBeInTheDocument();
    // Can try again — the upload form is shown for a rejected guest.
    expect(screen.getByLabelText("Identity document file")).toBeInTheDocument();
  });

  it("never displays an internal admin rejection reason (the endpoint itself never returns one)", async () => {
    currentIdentity = { status: "rejected", document_type: "pan", verified_at: null, message: "Your identity verification could not be completed. Please submit a valid identity document." };
    renderPage();
    await waitFor(() => expect(screen.getByText("✕ Verification Rejected")).toBeInTheDocument());
    expect(screen.queryByText(/forged|blurry|internal/i)).not.toBeInTheDocument();
  });
});
