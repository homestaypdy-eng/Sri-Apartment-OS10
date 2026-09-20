import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import BillingPage from "../pages/BillingPage";
import { loginAs } from "./testAuth";

const BOOKING = { id: "b1", check_in: "2030-01-01", check_out: "2030-01-03", rate_applied: 2000, status: "confirmed" };
const INVOICE = { id: "inv-1", invoice_no: "INV/1", subtotal: 4000, cgst: 100, sgst: 100, igst: 0, total: 4200, gst_rate_percent: 5, itc_available: false };

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  extractErrorMessage: () => "error",
}));

async function renderAs(role: string) {
  loginAs(role);
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <BillingPage />
      </AuthProvider>
    </MemoryRouter>
  );
  await waitFor(() => expect(screen.getByText("Generate GST Invoice")).toBeInTheDocument());
  return utils;
}

describe("BillingPage — credit-note visibility (RBAC Phase B)", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockImplementation((url: string) => {
      if (url === "/bookings") return Promise.resolve({ data: [BOOKING] });
      if (url === `/invoices/${INVOICE.id}/credit-notes`) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    apiPost.mockImplementation((url: string) => {
      if (url === `/invoices/generate/${BOOKING.id}`) return Promise.resolve({ data: INVOICE });
      return Promise.resolve({ data: {} });
    });
  });

  async function generateInvoice(user: ReturnType<typeof userEvent.setup>) {
    const generateButton = screen.getByRole("button", { name: "Generate Invoice" });
    const invoiceForm = generateButton.closest("form")!;
    const select = within(invoiceForm).getByDisplayValue("Select booking...");
    await user.selectOptions(select, BOOKING.id);
    await user.click(generateButton);
    await waitFor(() => expect(screen.getByText(INVOICE.invoice_no)).toBeInTheDocument());
  }

  it("Manager does not see the Issue Credit Note control", async () => {
    const user = userEvent.setup();
    await renderAs("manager");
    await generateInvoice(user);
    expect(screen.queryByRole("button", { name: "Issue Credit Note" })).not.toBeInTheDocument();
  });

  it("Staff does not see it either (matches the real backend boundary, admin-only)", async () => {
    const user = userEvent.setup();
    await renderAs("staff");
    await generateInvoice(user);
    expect(screen.queryByRole("button", { name: "Issue Credit Note" })).not.toBeInTheDocument();
  });

  it("Admin retains the Issue Credit Note control (unchanged)", async () => {
    const user = userEvent.setup();
    await renderAs("admin");
    await generateInvoice(user);
    expect(screen.getByRole("button", { name: "Issue Credit Note" })).toBeInTheDocument();
  });
});
