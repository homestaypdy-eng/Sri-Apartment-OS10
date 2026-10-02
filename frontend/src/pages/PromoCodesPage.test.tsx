import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import PromoCodesPage from "./PromoCodesPage";
import { loginAs } from "../test/testAuth";
import type { PromoCodeOut } from "../lib/api";

/**
 * Dynamic Pricing hardening — focused frontend coverage for the Promo
 * Codes admin page. Backend validation (value must be > 0, and <= 100 for
 * "percent" discounts) is the authority; this file verifies the page
 * surfaces backend rejections correctly, shows a real loading state
 * instead of a false "no codes" flash, guards against duplicate
 * create-submissions, and renders active/expired/deactivated state clearly.
 */

const PROMO_A = {
  id: "promo-1", code: "WELCOME10", discount_type: "percent" as const, value: 10,
  valid_from: "2020-01-01", valid_to: "2099-01-01", max_uses: null, used_count: 3, active: true,
};
const PROMO_EXPIRED = {
  id: "promo-2", code: "OLDCODE", discount_type: "flat" as const, value: 500,
  valid_from: "2020-01-01", valid_to: "2020-01-02", max_uses: null, used_count: 1, active: true,
};
const PROMO_DEACTIVATED = {
  id: "promo-3", code: "PAUSED", discount_type: "flat" as const, value: 200,
  valid_from: "2020-01-01", valid_to: "2099-01-01", max_uses: null, used_count: 0, active: false,
};

let currentCodes: PromoCodeOut[] = [PROMO_A, PROMO_EXPIRED, PROMO_DEACTIVATED];

const apiGet = vi.fn((..._args: unknown[]) => Promise.resolve({ data: currentCodes }));
const apiPost = vi.fn((..._args: unknown[]) => Promise.resolve({ data: PROMO_A }));
const apiPatch = vi.fn((..._args: unknown[]) => Promise.resolve({ data: PROMO_A }));

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
  },
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
        <PromoCodesPage />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe("PromoCodesPage", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGet.mockClear();
    apiPost.mockClear();
    apiPatch.mockClear();
    currentCodes = [PROMO_A, PROMO_EXPIRED, PROMO_DEACTIVATED];
    apiGet.mockImplementation(() => Promise.resolve({ data: currentCodes }));
    apiPost.mockImplementation(() => Promise.resolve({ data: PROMO_A }));
    apiPatch.mockImplementation(() => Promise.resolve({ data: PROMO_A }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a loading state instead of a false empty state before the first fetch resolves", async () => {
    let resolveGet: (v: { data: typeof PROMO_A[] }) => void = () => {};
    apiGet.mockImplementation(() => new Promise((resolve) => { resolveGet = resolve; }));
    renderPage();
    expect(screen.getByText("Loading promo codes...")).toBeInTheDocument();
    expect(screen.queryByText("No promo codes yet.")).not.toBeInTheDocument();
    resolveGet({ data: [] });
    await waitFor(() => expect(screen.getByText("No promo codes yet.")).toBeInTheDocument());
  });

  it("renders active, expired, and deactivated states distinctly", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("WELCOME10")).toBeInTheDocument());
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("expired")).toBeInTheDocument();
    expect(screen.getByText("deactivated")).toBeInTheDocument();
  });

  it("rejects an out-of-range percent value client-side via max, and surfaces a backend rejection when submitted anyway", async () => {
    apiPost.mockImplementation(() =>
      Promise.reject({ response: { data: { detail: "value must be at most 100 when discount_type is 'percent'" } } })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("WELCOME10")).toBeInTheDocument());

    const valueInput = screen.getByPlaceholderText("e.g. 10 (%)") as HTMLInputElement;
    expect(valueInput).toHaveAttribute("max", "100");

    await userEvent.type(screen.getByPlaceholderText("CODE"), "BADCODE");
    await userEvent.type(valueInput, "150");
    const dateInputs = document.querySelectorAll('input[type="date"]');
    await userEvent.type(dateInputs[0], "2027-01-01");
    await userEvent.type(dateInputs[1], "2027-06-01");
    await userEvent.click(screen.getByRole("button", { name: "Create Promo Code" }));

    await waitFor(() => expect(screen.getByText(/value must be at most 100/)).toBeInTheDocument());
  });

  it("disables the create button while a submission is in flight, preventing a duplicate create", async () => {
    let resolvePost: (v: { data: typeof PROMO_A }) => void = () => {};
    apiPost.mockImplementation(() => new Promise((resolve) => { resolvePost = resolve; }));
    renderPage();
    await waitFor(() => expect(screen.getByText("WELCOME10")).toBeInTheDocument());

    await userEvent.type(screen.getByPlaceholderText("CODE"), "NEWCODE");
    await userEvent.type(screen.getByPlaceholderText("e.g. 10 (%)"), "15");
    const dateInputs = document.querySelectorAll('input[type="date"]');
    await userEvent.type(dateInputs[0], "2027-01-01");
    await userEvent.type(dateInputs[1], "2027-06-01");

    const createButton = screen.getByRole("button", { name: "Create Promo Code" });
    await userEvent.click(createButton);
    expect(screen.getByRole("button", { name: "Creating..." })).toBeDisabled();
    expect(apiPost).toHaveBeenCalledTimes(1);

    // A second click while still in flight must not fire a second request.
    await userEvent.click(screen.getByRole("button", { name: "Creating..." }));
    expect(apiPost).toHaveBeenCalledTimes(1);

    resolvePost({ data: PROMO_A });
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Promo Code" })).not.toBeDisabled());
  });

  it("requires code, value, and both dates before submitting", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("WELCOME10")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Create Promo Code" }));
    expect(screen.getByText("Code, value, valid-from, and valid-to are required.")).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
  });
});
