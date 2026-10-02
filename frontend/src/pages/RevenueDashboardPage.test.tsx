import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import RevenueDashboardPage from "./RevenueDashboardPage";
import { loginAs } from "../test/testAuth";

/**
 * Revenue Dashboard (frontend-only — see the page's own module docstring
 * for exactly which existing backend endpoints this reads and why no new
 * endpoint or formula was introduced). All mocked responses are shaped
 * exactly like the real SummaryReport/RevenuePoint/UnitPerformance/
 * CashFlowReport/OutstandingPaymentLine payloads.
 */

const PROPERTIES = [{ id: "prop-1", name: "Sri Apartment", address: "Puducherry", state_code: "34" }];

function summaryFor(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    date_from: "2026-09-01",
    date_to: "2026-09-24",
    revenue: { total: 90000, by_source: { direct: 60000, airbnb: 20000, zotel: 10000 } },
    total_bookings: 12,
    room_nights_sold: 30,
    available_room_nights: 60,
    occupancy_rate_percent: 50,
    adr: 3000,
    revpar: 1500,
    gst: { cgst: 0, sgst: 0, igst: 0, total: 0, invoice_count: 0 },
    maintenance_cost_total: 0,
    net_revenue_after_maintenance: 90000,
    ...overrides,
  };
}

const ZERO_SUMMARY = summaryFor({
  revenue: { total: 0, by_source: {} },
  total_bookings: 0,
  room_nights_sold: 0,
  occupancy_rate_percent: 0,
  adr: 0,
  revpar: 0,
  net_revenue_after_maintenance: 0,
});

const REVENUE_SERIES = [
  { date: "2026-09-01", revenue: 30000, room_nights_sold: 10 },
  { date: "2026-09-02", revenue: 20000, room_nights_sold: 6 },
  { date: "2026-10-01", revenue: 40000, room_nights_sold: 14 },
];

const UNIT_PERFORMANCE = [
  { unit_id: "unit-a1", unit_name: "A1", room_nights_sold: 15, revenue: 54000, occupancy_rate_percent: 60, adr: 3600 },
  { unit_id: "unit-b3", unit_name: "B3", room_nights_sold: 15, revenue: 36000, occupancy_rate_percent: 40, adr: 2400 },
];

function cashFlowFor(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    date_from: "2026-09-01",
    date_to: "2026-09-24",
    cash_in_payments: 70000,
    cash_in_deposits: 0,
    cash_in_ota_payouts: 0,
    cash_in_zotel_pending: 0,
    cash_in_total: 70000,
    cash_out_expenses: 0,
    cash_out_withdrawals: 0,
    cash_out_deposit_refunds: 0,
    cash_out_total: 0,
    net_cash_flow: 70000,
    cash_in_bank: 50000,
    cash_in_hand: 20000,
    ...overrides,
  };
}

const OUTSTANDING_LINES = [
  {
    booking_id: "b1",
    guest_name: "Priya Shah",
    unit_name: "A1",
    check_in: "2026-09-20",
    check_out: "2026-09-22",
    expected_total: 6000,
    paid_amount: 2000,
    pending_balance: 4000,
    due_date: "2026-09-15",
    is_overdue: true,
  },
  {
    booking_id: "b2",
    guest_name: "Rahul Nair",
    unit_name: "B3",
    check_in: "2026-09-25",
    check_out: "2026-09-27",
    expected_total: 5000,
    paid_amount: 3000,
    pending_balance: 2000,
    due_date: "2026-09-24",
    is_overdue: false,
  },
];

let currentSummary: unknown = summaryFor();
let currentRevenueSeries: unknown[] = REVENUE_SERIES;
let currentUnitPerformance: unknown[] = UNIT_PERFORMANCE;
let currentCashFlow: unknown = cashFlowFor();
let currentOutstanding: unknown[] = OUTSTANDING_LINES;

const apiGet = vi.fn((url: string, _config?: { params?: Record<string, string> }): Promise<{ data: unknown }> => {
  if (url === "/properties") return Promise.resolve({ data: PROPERTIES });
  if (url === "/reports/summary") return Promise.resolve({ data: currentSummary });
  if (url === "/reports/revenue-series") return Promise.resolve({ data: currentRevenueSeries });
  if (url === "/reports/unit-performance") return Promise.resolve({ data: currentUnitPerformance });
  if (url === "/finance/cash-flow") return Promise.resolve({ data: currentCashFlow });
  if (url === "/finance/outstanding-payments") return Promise.resolve({ data: currentOutstanding });
  return Promise.resolve({ data: [] });
});

vi.mock("../lib/api", () => ({
  api: { get: (...args: unknown[]) => apiGet(args[0] as string, args[1] as { params?: Record<string, string> }) },
  extractErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { detail?: string } } };
    return err?.response?.data?.detail ?? "error";
  },
}));

function renderPage() {
  loginAs("admin");
  return render(
    <MemoryRouter>
      <AuthProvider>
        <RevenueDashboardPage />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe("RevenueDashboardPage", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockClear();
    currentSummary = summaryFor();
    currentRevenueSeries = REVENUE_SERIES;
    currentUnitPerformance = UNIT_PERFORMANCE;
    currentCashFlow = cashFlowFor();
    currentOutstanding = OUTSTANDING_LINES;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders normal revenue KPIs exactly as returned by the backend, without recomputing totals", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("₹90,000")).toBeInTheDocument());
    expect(screen.getByText("12")).toBeInTheDocument(); // bookings
    expect(screen.getByText("50.0%")).toBeInTheDocument(); // occupancy
    expect(screen.getByText("₹3,000")).toBeInTheDocument(); // ADR
    expect(screen.getByText("₹1,500")).toBeInTheDocument(); // RevPAR

    // Total revenue must come straight from summary.revenue.total (90000),
    // NOT be recomputed as the sum of unit-performance rows (54000+36000=90000
    // happens to match here on purpose — see the next test for a case where
    // it deliberately does NOT match, to prove the page never recomputes it).
    expect(apiGet).toHaveBeenCalledWith("/reports/summary", expect.anything());
  });

  it("never substitutes a recomputed total when unit-performance rows don't sum to the summary total", async () => {
    // Deliberately inconsistent fixture: unit rows sum to 40000, but the
    // authoritative summary total is 90000 (e.g. a property-filtered vs
    // portfolio-wide mismatch). The displayed Total Revenue KPI must still
    // be the summary's own figure, never a client-side recomputation.
    currentUnitPerformance = [
      { unit_id: "unit-a1", unit_name: "A1", room_nights_sold: 10, revenue: 25000, occupancy_rate_percent: 60, adr: 2500 },
      { unit_id: "unit-b3", unit_name: "B3", room_nights_sold: 6, revenue: 15000, occupancy_rate_percent: 40, adr: 2500 },
    ];
    renderPage();
    await waitFor(() => expect(screen.getByText("₹90,000")).toBeInTheDocument());
    expect(screen.queryByText("₹40,000")).not.toBeInTheDocument();
  });

  it("shows a per-unit revenue breakdown for multiple units with % of total", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("A1")).toBeInTheDocument());
    expect(screen.getByText("B3")).toBeInTheDocument();
    // 54000/90000 = 60.0% (also A1's own occupancy_rate_percent, coincidentally
    // the same figure — both render in A1's row), 36000/90000 = 40.0%.
    expect(screen.getAllByText("60.0%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("40.0%").length).toBeGreaterThanOrEqual(1);
  });

  it("re-fetches every dataset with the new date range when filters are applied", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("₹90,000")).toBeInTheDocument());
    apiGet.mockClear();

    const fromInput = screen.getByLabelText("From") as HTMLInputElement;
    const toInput = screen.getByLabelText("To") as HTMLInputElement;
    await userEvent.clear(fromInput);
    await userEvent.type(fromInput, "2026-08-01");
    await userEvent.clear(toInput);
    await userEvent.type(toInput, "2026-08-31");
    await userEvent.click(screen.getByRole("button", { name: "Apply Filters" }));

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith("/reports/summary", { params: { date_from: "2026-08-01", date_to: "2026-08-31" } })
    );
    expect(apiGet).toHaveBeenCalledWith("/reports/revenue-series", { params: { date_from: "2026-08-01", date_to: "2026-08-31" } });
    expect(apiGet).toHaveBeenCalledWith("/finance/cash-flow", { params: { date_from: "2026-08-01", date_to: "2026-08-31" } });
  });

  it("handles a zero-revenue period without crashing on division and shows an explicit empty state", async () => {
    currentSummary = ZERO_SUMMARY;
    currentRevenueSeries = [];
    currentUnitPerformance = [];
    currentCashFlow = cashFlowFor({ cash_in_total: 0, cash_in_bank: 0, cash_in_hand: 0 });
    currentOutstanding = [];
    renderPage();
    await waitFor(() => expect(screen.getAllByText("₹0").length).toBeGreaterThan(0));
    expect(screen.getByText("No bookings or revenue recorded for this date range.")).toBeInTheDocument();
    // Occupancy/ADR/RevPAR must render as 0, not NaN or a crash.
    expect(screen.getByText("0.0%")).toBeInTheDocument();
  });

  it("toggles the revenue trend between daily and monthly aggregation without altering total revenue shown elsewhere", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("₹90,000")).toBeInTheDocument());

    // Daily view: 3 distinct dates in REVENUE_SERIES.
    const dailyButton = screen.getByRole("button", { name: "Daily" });
    const monthlyButton = screen.getByRole("button", { name: "Monthly" });
    expect(dailyButton).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(monthlyButton);
    expect(monthlyButton).toHaveAttribute("aria-pressed", "true");
    expect(dailyButton).toHaveAttribute("aria-pressed", "false");
    // Switching the trend granularity must not change the KPI above.
    expect(screen.getByText("₹90,000")).toBeInTheDocument();
  });

  it("surfaces Zotel-reported cash as informational only, never folded into Cash Collected", async () => {
    currentCashFlow = cashFlowFor({ cash_in_total: 70000, cash_in_zotel_pending: 15000 });
    renderPage();
    await waitFor(() => expect(screen.getByText("₹70,000")).toBeInTheDocument());
    expect(screen.getByText(/Zotel-reported/)).toBeInTheDocument();
    // The zotel-pending amount must be visible but not added into the collected figure.
    expect(screen.queryByText("₹85,000")).not.toBeInTheDocument();
  });

  it("does not show the Zotel note when there is no pending zotel-mode cash", async () => {
    currentCashFlow = cashFlowFor({ cash_in_zotel_pending: 0 });
    renderPage();
    await waitFor(() => expect(screen.getByText("₹70,000")).toBeInTheDocument());
    expect(screen.queryByText(/Zotel-reported/)).not.toBeInTheDocument();
  });

  it("shows outstanding balance as a portfolio snapshot, separate from and not netted against revenue", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("₹6,000")).toBeInTheDocument()); // 4000 + 2000
    expect(screen.getByText(/2 booking\(s\) with a balance/)).toBeInTheDocument();
    expect(screen.getByText(/₹4,000 overdue/)).toBeInTheDocument();
    // Revenue total is untouched by outstanding/collected figures.
    expect(screen.getByText("₹90,000")).toBeInTheDocument();
  });

  it("shows the raw per-source revenue breakdown without collapsing it into an OTA/Direct binary", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("direct")).toBeInTheDocument());
    expect(screen.getByText("airbnb")).toBeInTheDocument();
    expect(screen.getByText("zotel")).toBeInTheDocument();
    expect(screen.queryByText(/^OTA$/)).not.toBeInTheDocument();
  });

  it("shows an error banner when a request fails", async () => {
    apiGet.mockImplementationOnce(() => Promise.resolve({ data: PROPERTIES })); // /properties
    apiGet.mockImplementationOnce(() => Promise.reject({ response: { data: { detail: "boom" } } }));
    renderPage();
    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
  });
});
