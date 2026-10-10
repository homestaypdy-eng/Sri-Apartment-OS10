import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import { RequireAdmin, RequireNotManager, RequireRoles } from "../components/RequireAuth";
import { loginAs } from "./testAuth";

function AdminOnlyPage() {
  return <div>Admin Only Content</div>;
}

function DashboardPage() {
  return <div>Dashboard</div>;
}

function renderGuardedRoute(role: string, Guard: typeof RequireAdmin) {
  loginAs(role);
  return render(
    <MemoryRouter initialEntries={["/protected"]}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route
            path="/protected"
            element={
              <Guard>
                <AdminOnlyPage />
              </Guard>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

function renderAllowedRolesRoute(role: string) {
  loginAs(role);
  return render(
    <MemoryRouter initialEntries={["/protected"]}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/protected" element={<RequireRoles allowed={["admin", "manager"]}><AdminOnlyPage /></RequireRoles>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe("Route guards — RBAC Phase B", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("RequireAdmin: Manager manually navigating to an Admin-only route is redirected, not shown the page", async () => {
    renderGuardedRoute("manager", RequireAdmin);
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Admin Only Content")).not.toBeInTheDocument();
  });

  it("RequireAdmin: Staff is also redirected (unchanged existing behavior)", async () => {
    renderGuardedRoute("staff", RequireAdmin);
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Admin Only Content")).not.toBeInTheDocument();
  });

  it("RequireAdmin: Admin can still reach the page (unchanged existing behavior)", async () => {
    renderGuardedRoute("admin", RequireAdmin);
    await waitFor(() => expect(screen.getByText("Admin Only Content")).toBeInTheDocument());
  });

  it("RequireNotManager: Manager manually navigating to /settings or /staff is redirected", async () => {
    renderGuardedRoute("manager", RequireNotManager);
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Admin Only Content")).not.toBeInTheDocument();
  });

  it("RequireNotManager: Staff and Admin are unaffected", async () => {
    renderGuardedRoute("staff", RequireNotManager);
    await waitFor(() => expect(screen.getByText("Admin Only Content")).toBeInTheDocument());
  });

  it("RequireRoles: Staff cannot directly open Manager/Admin reconciliation routes", async () => {
    renderAllowedRolesRoute("staff");
    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByText("Admin Only Content")).not.toBeInTheDocument();
  });

  it("RequireRoles: Manager can open operational Manager/Admin routes", async () => {
    renderAllowedRolesRoute("manager");
    await waitFor(() => expect(screen.getByText("Admin Only Content")).toBeInTheDocument());
  });
});
