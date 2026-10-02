import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { AttentionItem } from "../lib/api";
import { firstOfMonth, formatPeriod, NeedsAttentionPanel, toExclusiveDate, today } from "./AIStrategistPage";

function attentionItems(): AttentionItem[] {
  const categories: AttentionItem["category"][] = [
    "unpaid_balance", "low_stock", "maintenance", "guest_request", "unpaid_balance", "low_stock", "maintenance",
  ];
  return categories.map((category, index) => ({
    category,
    severity: index < 3 ? "critical" : "warning",
    title: `Alert ${index + 1}`,
    reason: "Needs review.",
    action: "Open the relevant OS10 area.",
    reference_id: `ref-${index + 1}`,
    link: ["/finance", "/inventory", "/maintenance", "/guest-requests"][index % 4],
  }));
}

describe("AI Strategist presentation", () => {
  it("formats local date defaults without shifting them through UTC", () => {
    const localDate = new Date(2026, 8, 29, 0, 0, 0);
    expect(firstOfMonth(localDate)).toBe("2026-09-01");
    expect(today(localDate)).toBe("2026-09-29");
  });

  it("maps inclusive date selections to the reports API exclusive end date", () => {
    expect(toExclusiveDate("2026-09-30")).toBe("2026-10-01");
    expect(formatPeriod("2026-09-01", "2026-10-01")).toBe("1 Sept 2026 – 30 Sept 2026");
  });

  it("shows the first five priority-ordered alerts, groups supported categories, and expands to all", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><NeedsAttentionPanel items={attentionItems()} /></MemoryRouter>);

    expect(screen.getAllByText("Payments").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Inventory").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Operations").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link")).toHaveLength(5);
    expect(screen.getByRole("link", { name: "Review Payments: Alert 1" })).toHaveAttribute("href", "/finance");
    expect(screen.queryByText("Alert 6")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "View all (7)" }));
    expect(screen.getAllByRole("link")).toHaveLength(7);
    expect(screen.getByText("Alert 7")).toBeInTheDocument();
  });

  it("explains the no-alert state", () => {
    render(<MemoryRouter><NeedsAttentionPanel items={[]} /></MemoryRouter>);
    expect(screen.getByRole("status")).toHaveTextContent("No current items need attention.");
  });
});
