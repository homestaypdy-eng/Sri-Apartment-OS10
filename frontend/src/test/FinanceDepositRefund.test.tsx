import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import FinancePage from "../pages/FinancePage";
import { loginAs } from "./testAuth";

// RBAC Phase B security fix (finding I-1): POST /finance/deposits/{id}/refund
// was mechanically granted to Manager during Phase B along with many
// legitimately-operational finance routes, contradicting the stated
// "Manager must not have payment refunds" policy. Fixed to admin+staff only
// on the backend; these tests pin down the matching frontend behavior.

const DEPOSIT = {
  id: "dep-1", booking_id: "11111111-22222222", amount: 5000, status: "held",
  received_at: "2026-01-01T00:00:00Z", refund_amount: 0, refunded_at: null, notes: null,
  pending_refund_amount: 3000,
};

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  extractErrorMessage: (e: unknown) => String(e),
}));

function mockRoutesFor(url: string) {
  if (url === "/finance/deposits") return Promise.resolve({ data: [DEPOSIT] });
  // FinancePage defaults to the GST Dashboard tab, which fetches this on
  // mount before the test ever switches to the Deposits tab.
  if (url === "/finance/gst-dashboard") {
    return Promise.resolve({
      data: {
        date_from: "2026-01-01", date_to: "2026-01-31",
        gst_collected: 0, gst_input_credit_eligible: 0, gst_payable: 0, gst_liability: 0,
        monthly_summary: [],
      },
    });
  }
  return Promise.resolve({ data: [] });
}

async function renderFinanceAs(role: string) {
  loginAs(role);
  const user = userEvent.setup();
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <FinancePage />
      </AuthProvider>
    </MemoryRouter>
  );
  await user.click(screen.getByRole("button", { name: "Security Deposits" }));
  await waitFor(() => expect(screen.getByText("Deposit Ledger")).toBeInTheDocument());
  return { user, ...utils };
}

describe("FinancePage — Deposits tab refund visibility (RBAC Phase B security fix, finding I-1)", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockImplementation(mockRoutesFor);
    apiPost.mockResolvedValue({ data: { ...DEPOSIT, refund_amount: 500, pending_refund_amount: 4500 } });
  });

  it("Manager does not see the Refund control", async () => {
    await renderFinanceAs("manager");
    await waitFor(() => expect(screen.getByText("₹5,000")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Refund" })).not.toBeInTheDocument();
  });

  it("Manager cannot trigger a refund even if attempted directly (no API call fires)", async () => {
    await renderFinanceAs("manager");
    await waitFor(() => expect(screen.getByText("₹5,000")).toBeInTheDocument());
    // The Deposit Ledger table row has no refund-amount input or button for
    // Manager at all (the "Record Deposit Received" form's own "Amount"
    // field is unrelated and stays visible — recording a deposit is still
    // a permitted Manager action, only refunding one is not).
    const ledgerRow = screen.getByText("₹5,000").closest("tr")!;
    expect(within(ledgerRow).queryByPlaceholderText("Amount")).not.toBeInTheDocument();
    expect(within(ledgerRow).queryByRole("button")).not.toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalledWith(expect.stringContaining("/refund"), expect.anything());
  });

  it("Admin retains the Refund control and can submit a refund", async () => {
    const { user } = await renderFinanceAs("admin");
    await waitFor(() => expect(screen.getByText("₹5,000")).toBeInTheDocument());
    const amountInputs = screen.getAllByPlaceholderText("Amount");
    const refundAmountInput = amountInputs[amountInputs.length - 1];
    await user.type(refundAmountInput, "500");
    await user.click(screen.getByRole("button", { name: "Refund" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(`/finance/deposits/${DEPOSIT.id}/refund`, { refund_amount: 500 }));
  });

  it("Staff retains the Refund control (existing behavior unchanged)", async () => {
    await renderFinanceAs("staff");
    await waitFor(() => expect(screen.getByText("₹5,000")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Refund" })).toBeInTheDocument();
  });
});
