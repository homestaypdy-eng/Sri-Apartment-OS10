import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import Layout from "../components/Layout";
import { loginAs } from "./testAuth";

// RBAC Phase B: NotificationBell (rendered inside Layout) calls
// GET /notifications/inbox on mount — mocked here so this is a pure
// navigation-visibility test, not an integration test of notifications.
vi.mock("../lib/api", () => ({
  api: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  extractErrorMessage: (e: unknown) => String(e),
}));

function renderLayoutAs(role: string) {
  loginAs(role);
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Layout />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe("Layout navigation — RBAC Phase B Manager awareness", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("Manager session renders Manager navigation (login/session -> nav)", async () => {
    renderLayoutAs("manager");
    await waitFor(() => expect(screen.getByText(/Signed in as manager/i)).toBeInTheDocument());
  });

  it("Manager sees operational navigation items", async () => {
    renderLayoutAs("manager");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    for (const label of [
      "Dashboard", "Properties & Units", "Guests", "Bookings",
      "Inquiries", "Housekeeping", "Maintenance", "Inventory", "Guest Requests",
      "Billing", "Finance", "Reports Center", "Notifications",
    ]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getAllByRole("link", { name: "Staff Operations" })).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Payment Reconciliation" })).toBeInTheDocument();
  });

  it("Manager does not see Settings (BankSettings/backups live there)", async () => {
    renderLayoutAs("manager");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Settings")).not.toBeInTheDocument();
  });

  it("Manager does not see Staff (HR/payroll administration)", async () => {
    renderLayoutAs("manager");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Staff", { selector: "a" })).not.toBeInTheDocument();
  });

  it("Manager does not see user administration", async () => {
    renderLayoutAs("manager");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
  });

  it("Manager does not see audit/security administration", async () => {
    renderLayoutAs("manager");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Audit")).not.toBeInTheDocument();
  });

  it("Manager does not see AI Strategist (admin-only strategic area)", async () => {
    renderLayoutAs("manager");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("AI Strategist")).not.toBeInTheDocument();
  });

  it("Admin still sees every nav item, including Settings/Staff/Users/Audit (unchanged behavior)", async () => {
    renderLayoutAs("admin");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    for (const label of ["Settings", "Users", "Audit", "AI Strategist"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getAllByRole("link", { name: "Staff Operations" })).toHaveLength(2);
  });

  it("Staff sees assigned operations but not owner settings or finance administration", async () => {
    renderLayoutAs("staff");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Settings")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Finance" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Staff Operations" })).toHaveLength(2);
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(screen.queryByText("Audit")).not.toBeInTheDocument();
    expect(screen.queryByText("AI Strategist")).not.toBeInTheDocument();
    expect(screen.queryByText("Payment Reconciliation")).not.toBeInTheDocument();
  });
});
