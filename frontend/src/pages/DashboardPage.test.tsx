import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import { loginAs } from "../test/testAuth";
import DashboardPage from "./DashboardPage";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("../lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  extractErrorMessage: () => "Request failed",
}));

function dashboard(overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-09-24",
    arrivals_today: 2,
    departures_today: 1,
    in_house_guests: 4,
    total_units: 10,
    total_guests: 20,
    active_bookings: 6,
    vacant_units: 3,
    occupancy_percent: 70,
    pending_checkins: 1,
    pending_checkouts: 0,
    pending_payments_count: 2,
    pending_payments_amount: 12500,
    housekeeping_pending: 3,
    maintenance_open: 1,
    guest_requests_open: 0,
    low_stock_count: 1,
    staff_present_today: 5,
    staff_absent_today: 0,
    follow_ups_due_today: 0,
    today_revenue: 18000,
    today_expenses: 3000,
    today_profit: 15000,
    month_revenue: 240000,
    month_expenses: 40000,
    month_profit: 200000,
    apartment_performance: [
      { unit_id: "u1", unit_name: "A1", month_revenue: 120000, month_expenses: 20000, month_profit: 100000, month_nights_booked: 18, month_occupancy_percent: 75 },
    ],
    monthly_trend: [
      { year: 2026, month: 8, revenue: 210000, expenses: 35000, profit: 175000 },
      { year: 2026, month: 9, revenue: 240000, expenses: 40000, profit: 200000 },
    ],
    attention_items: [
      { category: "unpaid_balance", severity: "critical", title: "Payment overdue", reason: "Due today.", action: "Follow up.", reference_id: "b1", link: "/finance" },
      { category: "maintenance", severity: "critical", title: "Urgent maintenance", reason: "Past response target.", action: "Resolve.", reference_id: "m1", link: "/maintenance" },
      { category: "low_stock", severity: "warning", title: "Linen stock low", reason: "At minimum.", action: "Reorder.", reference_id: "i1", link: "/inventory" },
      { category: "guest_request", severity: "warning", title: "Guest request needs attention", reason: "High priority.", action: "Assign.", reference_id: "g1", link: "/guest-requests" },
    ],
    ...overrides,
  };
}

function renderDashboard(role: string) {
  loginAs(role);
  return render(<MemoryRouter><AuthProvider><DashboardPage /></AuthProvider></MemoryRouter>);
}

describe("DashboardPage", () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.get.mockReset();
    mocks.get.mockImplementation((url: string) => {
      if (url === "/reports/dashboard") return Promise.resolve({ data: dashboard() });
      if (url === "/reports/dashboard/calendar") return Promise.resolve({ data: { period_year: 2026, period_month: 9, days: [] } });
      if (url === "/bookings" || url === "/guests" || url === "/units") return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
  });

  it("renders dashboard KPIs and existing chart data from the operational dashboard response", async () => {
    renderDashboard("admin");

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getAllByText("70%")).toHaveLength(1);
    expect(screen.getByText("7 / 10 units occupied today")).toBeInTheDocument();
    expect(screen.getByText("3 / 10")).toBeInTheDocument();
    expect(screen.getByText("units available")).toBeInTheDocument();
    expect(screen.getAllByText("₹18,000")).toHaveLength(2);
    // 3 occurrences: the Monthly revenue KPI, the Month-to-date financial
    // card, and September's row in the Monthly Trend card (which now shows
    // revenue/expenses/profit per month instead of revenue-only).
    expect(screen.getAllByText("₹2,40,000")).toHaveLength(3);
    expect(screen.getByText("₹12,500 outstanding")).toBeInTheDocument();
    expect(screen.getAllByText("Pending payments")).toHaveLength(2);
    expect(screen.getByText("Occupancy overview")).toBeInTheDocument();
    expect(screen.getByText("Today · 70%")).toBeInTheDocument();
    expect(screen.getByText("Monthly occupancy by unit · Sep 2026")).toBeInTheDocument();
    expect(screen.getByText("Revenue overview")).toBeInTheDocument();
    expect(screen.getByText("Today’s operations")).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith("/reports/dashboard");
  });

  it("limits the attention preview, preserves backend severity, and opens the complete list accessibly", async () => {
    const user = userEvent.setup();
    renderDashboard("admin");

    expect(await screen.findByText("Payment overdue")).toBeInTheDocument();
    expect(screen.getAllByText("Urgent", { selector: "span" })).toHaveLength(2);
    expect(screen.getByText("Attention", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByText("Guest request needs attention")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "View all →" }));
    expect(screen.getByText("Guest request needs attention")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show fewer ↑" })).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps revenue KPIs and amounts hidden for non-admin dashboard roles", async () => {
    renderDashboard("staff");

    await waitFor(() => expect(screen.getByText("Occupancy overview")).toBeInTheDocument());
    expect(screen.queryByText("Today's revenue")).not.toBeInTheDocument();
    expect(screen.queryByText("Monthly revenue")).not.toBeInTheDocument();
    expect(screen.getByText("Financial chart access is restricted")).toBeInTheDocument();
    expect(screen.getAllByText("Pending payments")).toHaveLength(2);
  });
});
