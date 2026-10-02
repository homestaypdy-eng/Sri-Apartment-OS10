import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import QuotationPopup from "./QuotationPopup";
import type { InquiryOut } from "../lib/api";

vi.mock("../lib/api", () => ({
  api: { get: vi.fn().mockResolvedValue({ data: null }) },
}));

const inquiry = {
  offer_no: "OFFER-1",
  offer_date: "2026-09-29",
  valid_upto: "2026-10-01",
  client_name: "Test Guest",
  client_phone: "9000000000",
  client_email: "guest@example.com",
  client_address: null,
  flat_room_label: "Flat A1",
  num_guests: 2,
  check_in_date: "2026-10-10",
  check_in_time: "12:30 PM",
  check_out_date: "2026-10-12",
  check_out_time: "11:00 AM",
  nights: 2,
  rate_per_night: 3500,
  extra_charges: 0,
  discount: 0,
  taxable_value: 7000,
  gst_rate_percent: 12,
  cgst: 420,
  sgst: 420,
  grand_total: 7840,
  advance_amount: 7840,
  balance_amount: 0,
  payment_mode: "Razorpay",
  lead_status: "new",
  is_expired: false,
  booking_id: null,
} as InquiryOut;

describe("QuotationPopup cancellation policy", () => {
  it("shows the approved refund boundaries and variable posting time", () => {
    render(
      <QuotationPopup
        inquiry={inquiry}
        onClose={vi.fn()}
        onDownloadPdf={vi.fn()}
        downloading={false}
        onConvertToBooking={vi.fn()}
        converting={false}
        onSendWhatsApp={vi.fn()}
        sendingWhatsapp={false}
        onSendEmail={vi.fn()}
        sendingEmail={false}
      />,
    );

    expect(screen.getByText(/more than 48 hours.*100% refund/i)).toBeInTheDocument();
    expect(screen.getByText(/exactly 48 hours before or within 48 hours.*50% refund/i)).toBeInTheDocument();
    expect(screen.getByText(/remaining 50% is retained as the late-cancellation charge/i)).toBeInTheDocument();
    expect(screen.getByText(/at or after scheduled check-in.*no automatic policy refund.*manual\/admin review/i)).toBeInTheDocument();
    expect(screen.getByText(/initiated promptly to the original payment method.*posting time may vary/i)).toBeInTheDocument();
    expect(screen.queryByText(/no refund.*within 48 hours/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/5\s*[–-]\s*7 business days|within\s+\d+ business days/i)).not.toBeInTheDocument();
  });
});
