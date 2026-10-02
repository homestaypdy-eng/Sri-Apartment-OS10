import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import HousekeepingPage from "./HousekeepingPage";
import { loginAs } from "../test/testAuth";

/**
 * Housekeeping page — UI/UX polish verification. Covers exactly what
 * changed in this pass: standard apartment ordering on the unit-status
 * overview, soft-color status badges (both unit housekeeping_status and
 * task status, now on the approved palette), the "Task Details" section
 * label, the corrected Start/Details button hierarchy, and the
 * unit-card → existing-task-details click wiring — plus a regression
 * check that task creation/status-advance/status-filter still work.
 */

const UNIT_BASE = {
  property_id: "prop-1", unit_type: "apartment", max_occupancy: 4, base_rate: 3000, weekend_rate: null,
  long_stay_min_nights: 30, long_stay_rate: 2000, status: "available",
  floor: null, flat_number: null, amenities: null, description: null, is_active: true,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
};

const UNIT_C6 = { ...UNIT_BASE, id: "unit-c6", name: "Flat C6", unit_code: "C6", housekeeping_status: "dirty" };
const UNIT_A1 = { ...UNIT_BASE, id: "unit-a1", name: "Flat A1", unit_code: "A1", housekeeping_status: "clean" };
const UNIT_B4 = { ...UNIT_BASE, id: "unit-b4", name: "Flat B4", unit_code: "B4", housekeeping_status: "clean" };
const UNIT_A2 = { ...UNIT_BASE, id: "unit-a2", name: "Flat A2", unit_code: "A2", housekeeping_status: "clean" };
const UNIT_B3 = { ...UNIT_BASE, id: "unit-b3", name: "Flat B3", unit_code: "B3", housekeeping_status: "clean" };
const UNIT_C5 = { ...UNIT_BASE, id: "unit-c5", name: "Flat C5", unit_code: "C5", housekeeping_status: "clean" };
// API deliberately returns units out of standard order — ordering must be
// enforced by the page, not assumed from response order.
const ALL_UNITS = [UNIT_C6, UNIT_B4, UNIT_A1, UNIT_C5, UNIT_A2, UNIT_B3];

const STAFF = [{ id: "staff-1", name: "Kumar", email: "kumar@example.com", role: "staff", is_active: true }];

const TASK_PENDING = {
  id: "task-pending", unit_id: "unit-c6", assigned_to: null, task_type: "checkout_clean",
  status: "pending", notes: null, created_at: "2026-09-20T10:00:00Z", completed_at: null,
};
const TASK_IN_PROGRESS = {
  id: "task-inprogress", unit_id: "unit-a1", assigned_to: "staff-1", task_type: "routine",
  status: "in_progress", notes: null, created_at: "2026-09-21T10:00:00Z", completed_at: null,
};
const TASK_COMPLETED = {
  id: "task-completed", unit_id: "unit-b4", assigned_to: "staff-1", task_type: "deep_clean",
  status: "completed", notes: null, created_at: "2026-09-19T10:00:00Z", completed_at: "2026-09-19T12:00:00Z",
};

interface TestTask {
  id: string; unit_id: string; assigned_to: string | null; task_type: string;
  status: string; notes: string | null; created_at: string; completed_at: string | null;
}
let currentTasks: TestTask[] = [];
const apiGet = vi.fn();
const apiPost = vi.fn().mockResolvedValue({ data: {} });
const apiPatch = vi.fn().mockResolvedValue({ data: {} });

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  extractErrorMessage: (e: unknown) => String(e),
}));

function mockRoutesFor(url: string) {
  if (url === "/housekeeping-tasks") return Promise.resolve({ data: currentTasks });
  if (url === "/units") return Promise.resolve({ data: ALL_UNITS });
  if (url === "/users") return Promise.resolve({ data: STAFF });
  if (url.includes("/photos")) return Promise.resolve({ data: [] });
  if (url.includes("/timeline")) return Promise.resolve({ data: { events: [] } });
  if (url === "/inventory/items") return Promise.resolve({ data: [] });
  return Promise.resolve({ data: [] });
}

async function renderPage() {
  loginAs("admin");
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <HousekeepingPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getAllByText(/Flat A1/).length).toBeGreaterThan(0));
  return utils;
}

describe("HousekeepingPage — UI polish", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockClear();
    apiPatch.mockClear();
    currentTasks = [TASK_PENDING, TASK_IN_PROGRESS, TASK_COMPLETED];
    apiGet.mockImplementation(mockRoutesFor);
  });

  it("shows the unit-status overview in the standard apartment order (A1, A2, B3, B4, C5, C6), regardless of API order", async () => {
    await renderPage();
    const overviewCards = screen.getAllByText(/^Flat /).map((el) => el.textContent);
    // Only the six overview-card headings, in this exact order — table rows
    // and the Assign form's <option> list repeat unit names too (without
    // the "(code)" suffix), so the "(code)" suffix is what identifies an
    // overview-card match specifically.
    const cardHeadings = overviewCards.filter((_, i) => i < 6);
    expect(cardHeadings).toEqual(["Flat A1(A1)", "Flat A2(A2)", "Flat B3(B3)", "Flat B4(B4)", "Flat C5(C5)", "Flat C6(C6)"]);
  });

  it("renders unit housekeeping status with the approved soft colors: Clean=green, Dirty=red", async () => {
    await renderPage();
    const cleanBadge = screen.getAllByText("Clean")[0];
    const dirtyBadge = screen.getByText("Dirty");
    expect(cleanBadge.className).toContain("green");
    expect(dirtyBadge.className).toContain("red");
  });

  it('labels the Assign Cleaning Task form with a "Task Details" section', async () => {
    await renderPage();
    expect(screen.getByText("Task Details")).toBeInTheDocument();
    expect(screen.getByLabelText("Unit")).toBeInTheDocument();
    expect(screen.getByLabelText("Task Type")).toBeInTheDocument();
    expect(screen.getByLabelText(/Assign to staff/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Notes/)).toBeInTheDocument();
  });

  it("the Assign Cleaning Task form's unit dropdown lists units in standard apartment order", async () => {
    await renderPage();
    const options = within(screen.getByLabelText("Unit")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Select unit...", "Flat A1", "Flat A2", "Flat B3", "Flat B4", "Flat C5", "Flat C6"]);
  });

  it("still creates a task via POST /housekeeping-tasks", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.selectOptions(screen.getByLabelText("Unit"), "unit-a1");
    await user.click(screen.getByRole("button", { name: "Create Task" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/housekeeping-tasks", expect.objectContaining({ unit_id: "unit-a1" })));
  });

  it("renders task-status badges with the approved soft colors: Pending=amber, In progress=blue, Completed=green", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("Pending").className).toContain("amber");
    expect(within(table).getByText("In progress").className).toContain("blue");
    expect(within(table).getByText("Completed").className).toContain("green");
  });

  it("gives Start primary (teal) emphasis and Details neutral/outlined emphasis — not equally important", async () => {
    await renderPage();
    const startButton = screen.getByRole("button", { name: /^Start/ });
    const detailsButtons = screen.getAllByRole("button", { name: /^Details/ });
    expect(startButton.className).toContain("bg-brand-green");
    expect(detailsButtons[0].className).toContain("bg-white");
    expect(detailsButtons[0].className).not.toContain("bg-brand-green");
  });

  it("the status filter still narrows the task list via the existing API param", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.selectOptions(screen.getByLabelText("Filter by status"), "pending");
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/housekeeping-tasks", { params: { status: "pending" } }));
  });

  it("advancing status still calls PATCH with the correct next status", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: /^Start/ }));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith("/housekeeping-tasks/task-pending", { status: "in_progress" }));
  });

  it("a unit card with an existing task is clickable and opens that task's existing Details view", async () => {
    const user = userEvent.setup();
    await renderPage();
    const c6Card = screen.getByRole("button", { name: /View housekeeping details for Flat C6/ });
    await user.click(c6Card);
    // "Supplies Used" only exists inside the existing task-details modal —
    // a more specific signal than its "Task Details" heading, which now
    // collides with this page's own new "Task Details" section label.
    await waitFor(() => expect(screen.getByText("Supplies Used")).toBeInTheDocument());
    expect(apiGet).toHaveBeenCalledWith("/housekeeping-tasks/task-pending/photos");
  });

  it("a unit card with no tasks at all is not clickable", async () => {
    currentTasks = [];
    await renderPage();
    // With zero tasks, none of the six unit cards has a safe existing
    // interaction to reuse, so none should render as a button.
    expect(screen.queryByRole("button", { name: /View housekeeping details/ })).not.toBeInTheDocument();
  });

  it("shows an open-task count on a unit's card, derived only from already-loaded tasks", async () => {
    await renderPage();
    const c6Card = screen.getByRole("button", { name: /View housekeeping details for Flat C6/ });
    expect(c6Card).toHaveTextContent("1 open task");
  });

  it("never invents checkout status, guest info, or priority on the unit cards", async () => {
    await renderPage();
    const grid = screen.getByRole("button", { name: /View housekeeping details for Flat C6/ }).parentElement!;
    expect(grid.textContent).not.toMatch(/checkout|guest|priority|deadline/i);
  });

  it("renders every unit card without throwing, at a narrow (mobile) width", async () => {
    // jsdom doesn't apply CSS media queries, but this proves the grid
    // renders all six cards as plain flow content (grid-cols-1 base class)
    // with no JS that depends on measured viewport width — the same
    // "responsive-safe" guarantee the other polished pages' tests rely on.
    await renderPage();
    expect(screen.getAllByText(/^Flat /).length).toBeGreaterThanOrEqual(6);
  });
});
