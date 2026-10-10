import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import { RealtimeProvider } from "../context/RealtimeContext";
import Layout from "../components/Layout";
import { RequireAuth, RequireRoles } from "../components/RequireAuth";
import { fakeToken, loginAs } from "./testAuth";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("axios", () => ({ default: { post } }));
vi.mock("../lib/api", () => ({
  api: { post: vi.fn() },
  extractErrorMessage: (error: unknown) => String(error),
}));
vi.mock("../components/NotificationBell", () => ({ default: () => null }));

function streamResponse(eventName: string) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`event: ${eventName}\ndata: {}\n\n`));
      controller.close();
    },
  });
  return { ok: true, status: 200, body } as Response;
}

function renderRoleSession(initialRole: "manager" | "staff", updatedRole: "manager" | "staff", route: string) {
  loginAs(initialRole);
  post.mockResolvedValue({
    data: { access_token: fakeToken(updatedRole), refresh_token: "updated-refresh-token" },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse("auth_changed")));

  return render(
    <AuthProvider>
      <RealtimeProvider>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
              <Route index element={<div>Dashboard page</div>} />
              <Route path="finance" element={<RequireRoles allowed={["admin", "manager"]}><div>Finance page</div></RequireRoles>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </RealtimeProvider>
    </AuthProvider>,
  );
}

describe("live role changes from the realtime auth_changed event", () => {
  beforeEach(() => {
    localStorage.clear();
    post.mockReset();
    vi.unstubAllGlobals();
  });

  it("immediately demotes an idle Manager session and redirects a now-forbidden page", async () => {
    renderRoleSession("manager", "staff", "/finance");

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      expect.stringMatching(/\/auth\/refresh$/),
      { refresh_token: "irrelevant-for-tests" },
    ));
    expect(await screen.findByText("Signed in as staff")).toBeInTheDocument();
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
    expect(screen.queryByText("Finance page")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Finance" })).not.toBeInTheDocument();
  });

  it("immediately promotes an idle Staff session and updates navigation without interaction", async () => {
    renderRoleSession("staff", "manager", "/");

    expect(await screen.findByText("Signed in as manager")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Finance" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Payment Reconciliation" })).toBeInTheDocument();
    expect(screen.getByText("Dashboard page")).toBeInTheDocument();
  });
});
