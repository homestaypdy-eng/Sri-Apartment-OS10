import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import PaymentReconciliationPage from "./PaymentReconciliationPage";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../lib/api", () => ({ api: { get: (...args: unknown[]) => get(...args) }, extractErrorMessage: () => "Unable to load cases" }));

describe("PaymentReconciliationPage", () => {
  beforeEach(() => get.mockReset());

  it("shows sanitized unresolved payment cases without actions", async () => {
    get.mockResolvedValue({ data: [{
      id: "event-1", case_type: "webhook_event", event_or_payment_reference: "pay_safe_ref",
      booking_id: null, hold_id: "hold-abcdef00", order_id: null,
      reason: "Captured payment has no matching OS10 order", provider_event_type: "payment.captured",
      amount: 1200, currency: "INR", created_at: "2026-01-01T00:00:00Z", status: "unresolved",
    }] });
    render(<PaymentReconciliationPage />);
    expect(await screen.findByText("Payment Reconciliation")).toBeInTheDocument();
    expect(await screen.findByText("Captured payment has no matching OS10 order")).toBeInTheDocument();
    expect(screen.getByText("₹1,200.00")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry|refund|capture/i })).not.toBeInTheDocument();
  });
});
