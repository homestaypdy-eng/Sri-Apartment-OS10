import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { GuestAuthProvider } from "../context/GuestAuthContext";
import GuestLoginPage from "../pages/GuestLoginPage";

const requestPhoneOtp = vi.fn();
const verifyPhoneOtp = vi.fn();
const requestEmailOtp = vi.fn();
const verifyEmailOtp = vi.fn();

vi.mock("../lib/api", () => ({
  requestPhoneOtp: (...args: unknown[]) => requestPhoneOtp(...args),
  verifyPhoneOtp: (...args: unknown[]) => verifyPhoneOtp(...args),
  requestEmailOtp: (...args: unknown[]) => requestEmailOtp(...args),
  verifyEmailOtp: (...args: unknown[]) => verifyEmailOtp(...args),
  extractErrorMessage: (e: unknown) => (e as Error)?.message ?? "error",
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/guest/login"]}>
      <GuestAuthProvider>
        <Routes>
          <Route path="/guest/login" element={<GuestLoginPage />} />
          <Route path="/guest" element={<div>Guest Dashboard Stub</div>} />
        </Routes>
      </GuestAuthProvider>
    </MemoryRouter>
  );
}

describe("GuestLoginPage — phone/email OTP (replaces the old magic-link-only form)", () => {
  beforeEach(() => {
    localStorage.clear();
    requestPhoneOtp.mockReset();
    verifyPhoneOtp.mockReset();
    requestEmailOtp.mockReset();
    verifyEmailOtp.mockReset();
  });

  it("completes the phone OTP flow end-to-end and lands on the existing guest dashboard route", async () => {
    requestPhoneOtp.mockResolvedValue({ challenge_id: "c1", message: "sent", dev_otp_code: "123456" });
    verifyPhoneOtp.mockResolvedValue({ access_token: "guest-tok", token_type: "bearer" });
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Mobile number"), "9876543210");
    await user.click(screen.getByRole("button", { name: "Send OTP" }));
    await waitFor(() => expect(requestPhoneOtp).toHaveBeenCalledWith("9876543210"));

    // The dev-mode OTP code must be visible, exactly like the public
    // guest website's already-established Phase 2A behavior.
    expect(await screen.findByText("123456")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Verification code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify OTP" }));

    await waitFor(() => expect(verifyPhoneOtp).toHaveBeenCalledWith("c1", "123456"));
    // Reuses the EXISTING guest session store/route (/guest) — not a new,
    // parallel guest token store.
    expect(await screen.findByText("Guest Dashboard Stub")).toBeInTheDocument();
    expect(localStorage.getItem("guest_access_token")).toBe("guest-tok");
  });

  it("switches to Email and requests via requestEmailOtp with the email endpoint", async () => {
    requestEmailOtp.mockResolvedValue({ challenge_id: "c2", message: "sent", dev_otp_code: "654321" });
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("tab", { name: "Email" }));
    await user.type(screen.getByLabelText("Email address"), "guest@example.com");
    await user.click(screen.getByRole("button", { name: "Send OTP" }));

    await waitFor(() => expect(requestEmailOtp).toHaveBeenCalledWith("guest@example.com"));
    expect(requestPhoneOtp).not.toHaveBeenCalled();
  });

  it("shows the backend error message on an incorrect code rather than a technical detail", async () => {
    requestPhoneOtp.mockResolvedValue({ challenge_id: "c1", message: "sent", dev_otp_code: "123456" });
    verifyPhoneOtp.mockRejectedValue(new Error("That code is incorrect or has expired."));
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Mobile number"), "9876543210");
    await user.click(screen.getByRole("button", { name: "Send OTP" }));
    await screen.findByText("123456");

    await user.type(screen.getByLabelText("Verification code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify OTP" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That code is incorrect or has expired.");
  });

  it("explains that Guest Login is for customers with an existing booking", () => {
    renderPage();
    expect(screen.getAllByText(/booking or stay/i).length).toBeGreaterThan(0);
  });

  it("Google button shows an honest not-configured notice, never a fake login", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Continue with Google/i }));
    expect(await screen.findByText(/isn't set up yet/i)).toBeInTheDocument();
    expect(localStorage.getItem("guest_access_token")).toBeNull();
  });
});
