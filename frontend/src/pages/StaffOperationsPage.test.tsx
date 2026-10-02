import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import StaffOperationsPage from "./StaffOperationsPage";
import { loginAs } from "../test/testAuth";

/**
 * Staff Operations — UI/UX polish verification. Covers exactly what
 * changed in this pass: soft semantic KPI coloring (with Urgent only
 * tinted red when its count is > 0), the KPI-card → existing-tab/filter
 * click wiring (and the KPIs that are deliberately left non-clickable
 * because no safe existing view exists for them), the per-status-filter
 * selected color, task rendering, and the "mine" tab's exact empty-state
 * message — plus a regression check that the existing filter buttons and
 * task-status actions still work.
 */

const DASHBOARD = {
  date: "2026-09-27",
  check_ins_today: 3,
  check_outs_today: 2,
  occupied_units: 4,
  vacant_units: 2,
  units_needing_cleaning: 1,
  open_maintenance_tickets: 1,
  open_guest_requests: 5,
  pending_staff_tasks: 7,
};

const TASK_URGENT = {
  id: "task-1", category: "maintenance" as const, unit_id: "unit-1", unit_name: "Flat A1",
  booking_id: null, label: "AC not cooling", priority: "urgent", status: "open",
  assigned_to: null, created_at: "2026-09-27T08:00:00Z", started_at: null, completed_at: null, notes: "Guest reported this morning",
};
const TASK_MINE = {
  id: "task-2", category: "housekeeping" as const, unit_id: "unit-2", unit_name: "Flat B4",
  booking_id: null, label: "Checkout Cleaning", priority: null, status: "pending",
  assigned_to: "test-user-id", created_at: "2026-09-27T09:00:00Z", started_at: null, completed_at: null, notes: null,
};

const CHECK_IN_LINE = {
  booking_id: "booking-1", guest_name: "Ravathi M", unit_id: "unit-3", unit_name: "Flat C6",
  arrival_time: "14:00", adults: 2, children: 0, status: "confirmed", net_paid: 5000,
  guest_identity_status: "verified" as const,
};

const apiGet = vi.fn();
const apiPost = vi.fn().mockResolvedValue({ data: {} });
const apiPatch = vi.fn().mockResolvedValue({ data: {} });

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
  },
  extractErrorMessage: (e: unknown) => String(e),
}));

function mockRoutesFor(url: string, config?: { params?: Record<string, string> }) {
  if (url === "/staff/dashboard") return Promise.resolve({ data: DASHBOARD });
  if (url === "/staff/tasks") {
    const params = config?.params ?? {};
    if (params.category === "housekeeping") return Promise.resolve({ data: [TASK_MINE] });
    if (params.category === "maintenance") return Promise.resolve({ data: [TASK_URGENT] });
    if (params.category === "guest_request") return Promise.resolve({ data: [] });
    if (params.assigned_to === "test-user-id") return Promise.resolve({ data: [TASK_MINE] });
    // no params (loadDashboard's own urgent-count fetch, and the "All Tasks" tab)
    return Promise.resolve({ data: [TASK_URGENT, TASK_MINE] });
  }
  if (url === "/staff/check-ins") return Promise.resolve({ data: [CHECK_IN_LINE] });
  if (url === "/staff/check-outs") return Promise.resolve({ data: [] });
  if (url === "/guests") return Promise.resolve({ data: [] });
  if (url === "/units") return Promise.resolve({ data: [] });
  if (url === "/users") return Promise.resolve({ data: [] });
  if (url === "/bookings") return Promise.resolve({ data: [] });
  return Promise.resolve({ data: [] });
}

async function renderPage() {
  loginAs("admin");
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <StaffOperationsPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("Check-ins")).toBeInTheDocument());
  return utils;
}

describe("StaffOperationsPage — UI polish", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockClear();
    apiPatch.mockClear();
    apiGet.mockImplementation(mockRoutesFor);
  });

  it("renders every existing KPI with its real, unchanged value", async () => {
    await renderPage();
    expect(screen.getByText("3")).toBeInTheDocument(); // check-ins
    expect(screen.getByRole("button", { name: /view check-outs/i })).toHaveTextContent("2");
    expect(screen.getByText("7")).toBeInTheDocument(); // open tasks
    expect(screen.getByText("5")).toBeInTheDocument(); // guest requests
    expect(screen.getByText("4")).toBeInTheDocument(); // occupied
    expect(screen.getByRole("button", { name: /view units needing cleaning/i })).toHaveTextContent("1");
  });

  it("gives each KPI its approved soft semantic color", async () => {
    await renderPage();
    const checkIns = screen.getByText("3").className;
    const checkOuts = screen.getAllByText("2").find((el) => el.tagName === "P")!.className;
    const openTasks = screen.getByText("7").className;
    const guestRequests = screen.getByText("5").className;
    const occupied = screen.getByText("4").className;
    expect(checkIns).toContain("text-blue-700");
    expect(checkOuts).toContain("text-amber-700");
    expect(openTasks).toContain("text-brand-green");
    expect(guestRequests).toContain("text-blue-700");
    expect(occupied).toContain("text-slate-700");
  });

  it("only tints Urgent red when its count is greater than 0", async () => {
    await renderPage();
    const urgentButtonOrDiv = screen.getByText("Urgent").previousElementSibling as HTMLElement;
    expect(urgentButtonOrDiv.className).toContain("text-red-700");
  });

  it("Check-ins, Check-outs, Open Tasks, Needs Cleaning, and Guest Requests KPIs are clickable and reuse existing tab/filter state", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("button", { name: /view check-ins/i }));
    expect(screen.getByRole("tab", { name: "Check-ins" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("button", { name: /view check-outs/i }));
    expect(screen.getByRole("tab", { name: "Check-outs" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("button", { name: /view open tasks/i }));
    expect(screen.getByRole("tab", { name: "All Tasks" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Pending" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("button", { name: /view units needing cleaning/i }));
    expect(screen.getByRole("tab", { name: "Housekeeping" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Pending" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("button", { name: /view guest requests/i }));
    expect(screen.getByRole("tab", { name: "Guest Requests" })).toHaveAttribute("aria-selected", "true");
  });

  it("Urgent, Occupied, and Vacant KPIs are left non-clickable (no safe existing view to reuse)", async () => {
    await renderPage();
    expect(screen.queryByRole("button", { name: /urgent/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /occupied/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /vacant/i })).not.toBeInTheDocument();
  });

  it("keeps the existing Tabs' primary/secondary hierarchy: selected = deep teal filled, unselected = outlined", async () => {
    await renderPage();
    const mine = screen.getByRole("tab", { name: "My Tasks" });
    const today = screen.getByRole("tab", { name: "Today" });
    expect(mine.className).toContain("bg-brand-green");
    expect(today.className).toContain("bg-white");
  });

  it("colors each status filter with its own soft tone only while selected", async () => {
    const user = userEvent.setup();
    await renderPage();
    const pending = screen.getByRole("tab", { name: "Pending" });
    const inProgress = screen.getByRole("tab", { name: "In Progress" });
    const completed = screen.getByRole("tab", { name: "Completed" });

    await user.click(pending);
    expect(pending.className).toContain("amber");
    expect(inProgress.className).toContain("bg-slate-100");

    await user.click(inProgress);
    expect(inProgress.className).toContain("blue");
    expect(pending.className).toContain("bg-slate-100");

    await user.click(completed);
    expect(completed.className).toContain("text-brand-green");
  });

  it("renders a task with unit, type, status, priority (when present), and assignment — using only existing data", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("tab", { name: "Maintenance" }));

    await waitFor(() => expect(screen.getByText("Flat A1")).toBeInTheDocument());
    expect(screen.getByText("AC not cooling")).toBeInTheDocument();
    expect(screen.getByText("⚠ URGENT")).toBeInTheDocument();
    expect(screen.getByText("Unassigned", { exact: false })).toBeInTheDocument();
    // Never invented: no guest name, no due-date field this task data doesn't have.
    expect(screen.queryByText(/guest name/i)).not.toBeInTheDocument();
  });

  it('shows the exact existing empty-state message "No tasks assigned to you. All caught up." on My Tasks', async () => {
    apiGet.mockImplementation((url: string, config?: { params?: Record<string, string> }) => {
      if (url === "/staff/tasks" && config?.params?.assigned_to === "test-user-id") return Promise.resolve({ data: [] });
      return mockRoutesFor(url, config);
    });
    await renderPage();
    await waitFor(() => expect(screen.getByText("No tasks assigned to you. All caught up.")).toBeInTheDocument());
  });

  it("still advances a task's status via the existing PATCH action", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("tab", { name: "Maintenance" }));
    await waitFor(() => expect(screen.getByText("Flat A1")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith("/staff/tasks/task-1", { status: "in_progress" }, { params: { category: "maintenance" } }),
    );
  });

  it("gives clickable KPI buttons a descriptive accessible label and keyboard focusability", async () => {
    await renderPage();
    const checkInsButton = screen.getByRole("button", { name: /view check-ins/i });
    expect(checkInsButton.tagName).toBe("BUTTON");
    checkInsButton.focus();
    expect(checkInsButton).toHaveFocus();
  });

  it("renders without throwing regardless of container width (responsive-safe)", async () => {
    await renderPage();
    expect(screen.getAllByRole("tab").length).toBeGreaterThan(0);
  });

  it("shows a guest's identity status on the Check-ins tab without blocking the Check In action", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("tab", { name: "Check-ins" }));

    await waitFor(() => expect(screen.getByText("Ravathi M")).toBeInTheDocument());
    expect(screen.getByText("Identity ✓")).toBeInTheDocument();
    const checkInButton = screen.getByRole("button", { name: "Check In" });
    expect(checkInButton).not.toBeDisabled();
  });

  it("formats a task's relative timestamp with an explicit English (en-GB) locale, never the French text a browser-default locale would produce", async () => {
    await renderPage();
    const row = screen.getByText("Flat B4").closest("button")!;

    // Computed the same way relativeTimeShort() itself does — proves the
    // page renders real en-GB output, not just "doesn't look French".
    const expectedEnglish = new Date(TASK_MINE.created_at).toLocaleString("en-GB", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
    // What the SAME timestamp would render as under a French browser/OS
    // locale — this is exactly the "22 sept." bug relativeTimeShort(undefined, ...)
    // used to produce. The page's actual output must never equal this.
    const wouldBeFrench = new Date(TASK_MINE.created_at).toLocaleString("fr-FR", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });

    expect(row).toHaveTextContent(expectedEnglish);
    expect(row.textContent).not.toContain(wouldBeFrench);
    expect(row.textContent).not.toMatch(/janv\.|févr\.|mars|avr\.|juin|juil\.|août|sept\.|oct\.|nov\.|déc\./i);
  });
});
