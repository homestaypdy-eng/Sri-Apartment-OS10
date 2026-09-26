import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import { loginAs } from "../test/testAuth";
import MessagesPage from "./MessagesPage";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
  },
  extractErrorMessage: () => "Request failed",
}));

const CONVERSATION_A = {
  id: "c1",
  guest_name: "Pranay Kumar",
  guest_phone: "9876543210",
  guest_email: "pranay@example.com",
  unit_name: "Flat C5",
  check_in: "2026-10-02",
  check_out: "2026-10-04",
  nights: 2,
  guests: 4,
  booking_reference: "SA-2026-1F3A9C",
  display_status: { label: "CONFIRMED", tone: "green" },
  last_message_preview: "Thank you, we will arrive around 2 PM",
  last_message_at: "2026-09-26T10:00:00Z",
  unread_count: 2,
  created_at: "2026-09-20T10:00:00Z",
};

const CONVERSATION_B = {
  id: "c2",
  guest_name: "Enquiry Guest",
  guest_phone: "9123456780",
  guest_email: null,
  unit_name: "Flat A1",
  check_in: "2026-11-01",
  check_out: "2026-11-03",
  nights: 2,
  guests: 2,
  booking_reference: null,
  display_status: { label: "ENQUIRY", tone: "amber" },
  last_message_preview: null,
  last_message_at: null,
  unread_count: 0,
  created_at: "2026-09-22T10:00:00Z",
};

const CONVERSATION_A_DETAIL = {
  ...CONVERSATION_A,
  booking_id: "b1",
  hold_id: null,
  inquiry_id: null,
  guest_id: "g1",
  messages: [
    { id: "m1", sender_type: "guest", sender_name: null, body: "Hello, is the apartment available for 4 people?", message_type: "text", created_at: "2026-09-25T10:00:00Z", read_at: null },
    { id: "m2", sender_type: "staff", sender_name: "Admin User", body: "Yes, C5 is available for your dates.", message_type: "text", created_at: "2026-09-25T10:05:00Z", read_at: "2026-09-25T10:06:00Z" },
  ],
};

const BOOKING_DETAILS = {
  booking_reference: "SA-2026-1F3A9C",
  booking_id: "b1",
  guest_name: "Pranay Kumar",
  guest_phone: "9876543210",
  guest_email: "pranay@example.com",
  unit_name: "Flat C5",
  check_in: "2026-10-02",
  check_out: "2026-10-04",
  checkin_time: "12:30 PM",
  checkout_time: "11:00 AM",
  nights: 2,
  guests: 4,
  booking_status: "confirmed",
  display_status: { label: "CONFIRMED", tone: "green" },
  payment_status: "PAID",
  total_amount: 7350,
  paid_amount: 7350,
  balance_due: 0,
};

const CONFIRMATION_PREVIEW = {
  subject: "Booking Confirmed — Sri Apartment Homestay",
  body: "Hi Pranay Kumar,\n\nYour booking is confirmed.\nGuests: 4\nTotal: Rs. 7350.00\nPaid: Rs. 7350.00\nBalance: Rs. 0.00\n",
  html_body: "<p>Hi Pranay Kumar</p>",
};

function renderPage() {
  loginAs("admin");
  return render(
    <MemoryRouter>
      <AuthProvider>
        <MessagesPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("MessagesPage", () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.get.mockReset();
    mocks.post.mockReset();
    mocks.get.mockImplementation((url: string) => {
      if (url === "/messages/conversations") return Promise.resolve({ data: [CONVERSATION_A, CONVERSATION_B] });
      if (url === "/messages/conversations/c1") return Promise.resolve({ data: CONVERSATION_A_DETAIL });
      if (url === "/messages/conversations/c1/booking-details") return Promise.resolve({ data: BOOKING_DETAILS });
      return Promise.resolve({ data: [] });
    });
    mocks.post.mockResolvedValue({ data: {} });
  });

  it("renders the conversation list with real guest/booking data and status badges", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Pranay Kumar")).toBeInTheDocument());
    expect(screen.getByText("Enquiry Guest")).toBeInTheDocument();
    expect(screen.getByText("CONFIRMED")).toBeInTheDocument();
    expect(screen.getByText("ENQUIRY")).toBeInTheDocument();
    expect(screen.getByText(/Thank you, we will arrive around 2 PM/)).toBeInTheDocument();
  });

  it("filter bar re-requests conversations with the selected filter", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Pranay Kumar")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Unread" }));
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/messages/conversations", { params: { filter: "unread", q: "", limit: 100 } }),
    );
  });

  it("opening a conversation loads messages, marks it read, and loads booking details", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Pranay Kumar")).toBeInTheDocument());

    await user.click(screen.getByText("Pranay Kumar"));

    await waitFor(() => expect(screen.getByText("Hello, is the apartment available for 4 people?")).toBeInTheDocument());
    expect(screen.getByText("Yes, C5 is available for your dates.")).toBeInTheDocument();
    // Booking Details panel populated from the real booking.
    await waitFor(() => expect(screen.getByText("SA-2026-1F3A9C")).toBeInTheDocument());
    expect(screen.getAllByText("₹7,350")).toHaveLength(2); // Total and Paid
    expect(screen.getByText("₹0")).toBeInTheDocument(); // Balance
    // Unread conversation was marked read.
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/messages/conversations/c1/read"));
  });

  it("shows the empty booking-details state for an enquiry with no booking", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Enquiry Guest")).toBeInTheDocument());
    mocks.get.mockImplementation((url: string) => {
      if (url === "/messages/conversations") return Promise.resolve({ data: [CONVERSATION_A, CONVERSATION_B] });
      if (url === "/messages/conversations/c2") return Promise.resolve({ data: { ...CONVERSATION_B, booking_id: null, hold_id: null, inquiry_id: "i1", guest_id: null, messages: [] } });
      return Promise.resolve({ data: [] });
    });

    await user.click(screen.getByText("Enquiry Guest"));
    await waitFor(() => expect(screen.getByText("No booking linked to this conversation.")).toBeInTheDocument());
  });

  it("auto-loads the confirmation preview with the real guest count as soon as the booking is selected — no button click needed", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true });
    mocks.post.mockImplementation((url: string) => {
      if (url === "/messages/conversations/c1/confirmation-preview") return Promise.resolve({ data: CONFIRMATION_PREVIEW });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await waitFor(() => expect(screen.getByText("Pranay Kumar")).toBeInTheDocument());
    await user.click(screen.getByText("Pranay Kumar"));

    // No "Load Confirmation Message" click — it must appear on its own.
    await waitFor(() => expect(screen.getByText(/Guests: 4/)).toBeInTheDocument());
    expect(screen.queryByText(/Guests: 1/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Load Confirmation Message/i })).not.toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledWith("/messages/conversations/c1/confirmation-preview");

    await user.click(screen.getByRole("button", { name: "Copy Message" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(CONFIRMATION_PREVIEW.body);
    expect(await screen.findByText("Copied ✓")).toBeInTheDocument();
  });

  it("shows a manual retry button only if the automatic confirmation-preview load fails", async () => {
    const user = userEvent.setup();
    mocks.post.mockImplementation((url: string) => {
      if (url === "/messages/conversations/c1/confirmation-preview") return Promise.reject(new Error("boom"));
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await waitFor(() => expect(screen.getByText("Pranay Kumar")).toBeInTheDocument());
    await user.click(screen.getByText("Pranay Kumar"));

    expect(await screen.findByRole("button", { name: /Retry Loading Confirmation Message/i })).toBeInTheDocument();
  });

  it("shows an empty state when there is no selected conversation", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Select a conversation to view messages and booking details.")).toBeInTheDocument());
  });

  it("shows a 'no conversations found' empty state when the filtered list is empty", async () => {
    mocks.get.mockImplementation((url: string) => {
      if (url === "/messages/conversations") return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("No conversations found.")).toBeInTheDocument());
  });
});
