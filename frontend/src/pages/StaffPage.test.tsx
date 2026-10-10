import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import { loginAs } from "../test/testAuth";
import StaffPage from "./StaffPage";

const { get, post, patch, del } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() }));

vi.mock("../lib/api", () => ({
  api: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args), patch: (...args: unknown[]) => patch(...args), delete: (...args: unknown[]) => del(...args) },
  extractErrorMessage: (error: unknown) => String(error),
}));

describe("Staff Management foundation", () => {
  beforeEach(() => {
    localStorage.clear();
    get.mockReset(); post.mockReset(); patch.mockReset(); del.mockReset();
    get.mockImplementation((url: string) => Promise.resolve({ data: url === "/users" ? [{ id: "admin-1", name: "Owner", email: "owner@example.com", role: "admin", is_active: true }] : [] }));
  });

  it("creates a Staff account and linked profile through the single Add Staff workflow", async () => {
    const user = userEvent.setup();
    const createdUser = { id: "new-staff-id", name: "Anita Kumar", email: "anita@example.com", role: "staff", is_active: true };
    const createdProfile = { id: "profile-id", user_id: createdUser.id, employee_id: "EMP-101", is_active: true };
    let profiles: unknown[] = [];
    get.mockImplementation((url: string) => Promise.resolve({ data: url === "/users" ? [{ id: "admin-1", name: "Owner", email: "owner@example.com", role: "admin", is_active: true }] : url === "/staff/profiles" ? profiles : [] }));
    post.mockImplementation(async (url: string, body: Record<string, unknown>) => {
      expect(url).toBe("/staff/onboard");
      expect(body).toMatchObject({ name: "Anita Kumar", email: "anita@example.com", assigned_unit_ids: [] });
      expect(body).not.toHaveProperty("role");
      profiles = [createdProfile];
      return { data: { user: createdUser, profile: createdProfile } };
    });
    loginAs("admin");
    render(<MemoryRouter><AuthProvider><StaffPage /></AuthProvider></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "+ Add Staff" }));
    await user.type(screen.getByLabelText("Full name"), "Anita Kumar");
    await user.type(screen.getByLabelText("Login email"), "anita@example.com");
    await user.type(screen.getByLabelText("Initial password"), "SafePass123");
    await user.type(screen.getByLabelText("Employee ID"), "EMP-101");
    await user.click(screen.getByRole("button", { name: "Create Staff Account & Profile" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Staff account and profile created.")).toBeInTheDocument();
    expect(await screen.findByText("Anita Kumar")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/staff/profiles");
  });

  it("keeps the profile directory mounted on the existing User + StaffProfile flow", async () => {
    loginAs("admin");
    get.mockImplementation((url: string) => Promise.resolve({ data: url === "/users" ? [{ id: "staff-2", name: "Ravi", email: "ravi@example.com", role: "staff", is_active: true }] : [] }));
    render(<MemoryRouter><AuthProvider><StaffPage /></AuthProvider></MemoryRouter>);
    expect(await screen.findByText("Ravi")).toBeInTheDocument();
    expect(screen.getByText("Staff accounts and their HR profiles share the existing User ID.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add Staff" })).toBeInTheDocument();
  });

  it.each([
    ["Staff Directory", "/staff/profiles"],
    ["Designations", "/staff/designations"],
    ["Leave", "/staff/leave"],
    ["Payroll", "/staff/payroll"],
    ["Performance Notes", "/staff/performance-notes"],
  ])("refetches %s after the existing staff realtime invalidation", async (tab, endpoint) => {
    const user = userEvent.setup();
    loginAs("admin");
    get.mockImplementation((url: string) => Promise.resolve({ data: url === "/users" ? [{ id: "staff-3", name: "Ravi", email: "ravi@example.com", role: "staff", is_active: true }] : [] }));
    render(<MemoryRouter><AuthProvider><StaffPage /></AuthProvider></MemoryRouter>);
    if (tab !== "Staff Directory") await user.click(await screen.findByRole("button", { name: tab }));
    await waitFor(() => expect(get.mock.calls.some(([url]) => url === endpoint)).toBe(true));
    const before = get.mock.calls.filter(([url]) => url === endpoint).length;
    window.dispatchEvent(new CustomEvent("os10:invalidate", { detail: { topic: "staff_operations.changed" } }));
    await waitFor(() => expect(get.mock.calls.filter(([url]) => url === endpoint).length).toBeGreaterThan(before));
  });

  it("refetches an open staff profile's advances after the existing staff realtime invalidation", async () => {
    const user = userEvent.setup();
    const staffUser = { id: "staff-4", name: "Anita", email: "anita@example.com", role: "staff", is_active: true };
    const profile = {
      id: "profile-4", user_id: staffUser.id, employee_id: "EMP-4", designation: "Attendant", department: "Housekeeping",
      employment_type: "full_time", join_date: null, exit_date: null, is_active: true, phone: null, alternate_phone: null,
      date_of_birth: null, address: null, emergency_contact_name: null, emergency_contact_phone: null,
      emergency_contact_relationship: null, id_type: null, id_number: null, bank_name: null, account_holder_name: null,
      account_number: null, ifsc: null, payment_method: null, weekly_off_day: null, supervisor_user_id: null, notes: null,
      created_at: "2026-01-01T00:00:00Z", designation_id: null, salary_override: null, shift_start_override: null,
      shift_end_override: null, lunch_break_override_minutes: null, standard_working_hours_override: null,
      leave_entitlement_override: null,
    };
    loginAs("admin");
    get.mockImplementation((url: string) => Promise.resolve({ data:
      url === "/users" ? [staffUser]
        : url === "/staff/profiles" ? [profile]
          : [],
    }));
    render(<MemoryRouter><AuthProvider><StaffPage /></AuthProvider></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "View" }));
    await user.click(screen.getByRole("button", { name: "Advances" }));
    await waitFor(() => expect(get.mock.calls.some(([url]) => url === "/staff/advances")).toBe(true));
    const before = get.mock.calls.filter(([url]) => url === "/staff/advances").length;
    window.dispatchEvent(new CustomEvent("os10:invalidate", { detail: { topic: "staff_operations.changed" } }));
    await waitFor(() => expect(get.mock.calls.filter(([url]) => url === "/staff/advances").length).toBeGreaterThan(before));
  });
});
