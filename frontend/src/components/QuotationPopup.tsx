import { useEffect, useState } from "react";
import { api, type BankSettingsOut, type InquiryOut } from "../lib/api";
import { Button } from "./ui";
import logo from "../assets/logo.png";
import qrCode from "../assets/payment_qr.png";

const TERMS = [
  "This is a pre-booking offer, valid up to the date shown above.",
  "Rates are subject to validity & availability.",
  "Booking is confirmed only after receipt of required advance payment.",
  "Check-in: 12:30 PM  |  Check-out: 11:00 AM.",
  "Early check-in / late check-out subject to availability & charges.",
  "Valid government-issued ID proof is mandatory at check-in.",
];
const HOUSE_RULES = [
  "No smoking inside the property.",
  "No parties or loud music allowed.",
  "Maintain cleanliness & respect the property.",
];
const CANCELLATION_POLICY = [
  "Cancellation requests must be submitted through WhatsApp or email.",
  "Full refund for cancellations received at least 48 hours before check-in.",
  "No refund for cancellations received within 48 hours before check-in or after check-in.",
];

export default function QuotationPopup({
  inquiry,
  onClose,
  onDownloadPdf,
  downloading,
  onConvertToBooking,
  converting,
  onSendWhatsApp,
  sendingWhatsapp,
  onSendEmail,
  sendingEmail,
}: {
  inquiry: InquiryOut;
  onClose: () => void;
  onDownloadPdf: () => void;
  downloading: boolean;
  onConvertToBooking: () => void;
  converting: boolean;
  onSendWhatsApp: () => void;
  sendingWhatsapp: boolean;
  onSendEmail: () => void;
  sendingEmail: boolean;
}) {
  // RBAC Phase A: bank details are Admin-only (GET /settings/bank) — this
  // on-screen preview fetches the current, real values instead of the old
  // hardcoded literals so it can never drift from what's actually
  // configured. Staying null (the GET 403ing for Staff/Manager) is what
  // keeps this section hidden from them here, matching the same boundary
  // already enforced everywhere else BankSettings is read.
  const [bank, setBank] = useState<BankSettingsOut | null>(null);
  useEffect(() => {
    api
      .get("/settings/bank")
      .then(({ data }) => setBank(data))
      .catch(() => setBank(null));
  }, []);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        <div className="p-6 text-xs text-slate-800" id="quotation-content">
          {/* Header */}
          <div className="flex justify-between items-start border-b border-amber-600 pb-3 mb-3">
            <div className="flex gap-3">
              <img src={logo} alt="Sri Apartment Homestay" className="w-16 h-16 object-contain" />
              <div>
                <p className="font-bold text-sm text-slate-900">SRI APARTMENT HOMESTAY</p>
                <p className="italic text-amber-700 text-[11px]">Comfortable Stays in the Heart of Puducherry</p>
                <p className="text-[10px] text-slate-500 max-w-xs">
                  163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010
                </p>
                <p className="text-[10px] text-slate-500">+91 88702 37237 | homestaypdy@gmail.com</p>
                <p className="text-[10px] text-slate-500">GSTIN: 34PACPS5159H1Z5</p>
              </div>
            </div>
            <div className="text-right text-[10px]">
              <p className="font-bold text-slate-900 mb-1">PRE-BOOKING OFFER</p>
              <p>
                <span className="text-slate-500">Offer No.: </span>
                {inquiry.offer_no}
              </p>
              <p>
                <span className="text-slate-500">Offer Date: </span>
                {inquiry.offer_date}
              </p>
              <p>
                <span className="text-slate-500">Valid Upto: </span>
                {inquiry.valid_upto}
                {inquiry.is_expired && <span className="text-red-600 font-semibold"> (EXPIRED)</span>}
              </p>
              <p>
                <span className="text-slate-500">Payment Mode: </span>
                {inquiry.payment_mode}
              </p>
              <p>
                <span className="text-slate-500">Lead Status: </span>
                <span className="font-medium capitalize">{inquiry.lead_status.replace("_", " ")}</span>
              </p>
            </div>
          </div>

          <p className="text-center italic text-slate-500 text-[9px] mb-3">
            This quotation is not a tax invoice. A tax invoice will be issued as applicable.
          </p>

          {/* Client / Stay details */}
          <div className="grid grid-cols-2 gap-6 mb-3">
            <div>
              <p className="font-bold text-slate-900 mb-1">CLIENT DETAILS</p>
              <Row label="Client Name" value={inquiry.client_name} />
              <Row label="Phone No." value={inquiry.client_phone} />
              <Row label="Email ID" value={inquiry.client_email || "-"} />
              <Row label="Address" value={inquiry.client_address || "-"} />
            </div>
            <div>
              <p className="font-bold text-slate-900 mb-1">STAY DETAILS</p>
              <Row label="Flat / Room" value={inquiry.flat_room_label} />
              <Row label="No. of Guests" value={String(inquiry.num_guests)} />
              <Row label="Check-in" value={`${inquiry.check_in_date} ${inquiry.check_in_time}`} />
              <Row label="Check-out" value={`${inquiry.check_out_date} ${inquiry.check_out_time}`} />
              <Row label="No. of Nights" value={String(inquiry.nights)} />
              <Row label="Rate / Night" value={`Rs. ${inquiry.rate_per_night}`} />
            </div>
          </div>

          {/* Accommodation charges */}
          <p className="font-bold text-slate-900 mb-1">ACCOMMODATION CHARGES</p>
          <table className="w-full border-collapse mb-2">
            <thead>
              <tr className="bg-slate-100">
                <th className="border border-slate-300 px-2 py-1 text-left">Description</th>
                <th className="border border-slate-300 px-2 py-1">Nights</th>
                <th className="border border-slate-300 px-2 py-1">Rate</th>
                <th className="border border-slate-300 px-2 py-1">Amount</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="border border-slate-300 px-2 py-1">Accommodation Charges</td>
                <td className="border border-slate-300 px-2 py-1 text-center">{inquiry.nights}</td>
                <td className="border border-slate-300 px-2 py-1 text-right">{inquiry.rate_per_night.toFixed(2)}</td>
                <td className="border border-slate-300 px-2 py-1 text-right">
                  {(inquiry.rate_per_night * inquiry.nights).toFixed(2)}
                </td>
              </tr>
              {inquiry.extra_charges > 0 && (
                <TotalsRow label="Extra Charges" value={inquiry.extra_charges} />
              )}
              {inquiry.discount > 0 && <TotalsRow label="Discount" value={-inquiry.discount} />}
              <TotalsRow label="TAXABLE VALUE" value={inquiry.taxable_value} bold />
              <TotalsRow label={`CGST @ ${(inquiry.gst_rate_percent / 2).toFixed(2)}%`} value={inquiry.cgst} />
              <TotalsRow label={`SGST @ ${(inquiry.gst_rate_percent / 2).toFixed(2)}%`} value={inquiry.sgst} />
              <tr className="bg-slate-900 text-white font-bold">
                <td className="border border-slate-300 px-2 py-1.5" colSpan={3}>
                  GRAND TOTAL (Rs.)
                </td>
                <td className="border border-slate-300 px-2 py-1.5 text-right">{inquiry.grand_total.toFixed(2)}</td>
              </tr>
            </tbody>
          </table>

          {/* Advance / Balance */}
          <div className="grid grid-cols-2 gap-3 mb-3">
            <div className="border border-emerald-300 bg-emerald-50 rounded p-2">
              <p className="font-bold text-emerald-800 text-[10px] mb-1">ADVANCE PAYMENT (for confirmation)</p>
              <p className="text-sm font-semibold">Rs. {inquiry.advance_amount.toFixed(2)}</p>
            </div>
            <div className="border border-amber-300 bg-amber-50 rounded p-2">
              <p className="font-bold text-amber-800 text-[10px] mb-1">BALANCE (payable at check-in)</p>
              <p className="text-sm font-semibold">Rs. {inquiry.balance_amount.toFixed(2)}</p>
            </div>
          </div>

          {/* Terms / House Rules / Cancellation */}
          <div className="grid grid-cols-3 gap-4 mb-3 text-[9.5px]">
            <div>
              <p className="font-bold text-slate-900 mb-1">TERMS & CONDITIONS</p>
              <ul className="space-y-0.5 text-slate-600">
                {TERMS.map((t) => (
                  <li key={t}>- {t}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="font-bold text-slate-900 mb-1">HOUSE RULES</p>
              <ul className="space-y-0.5 text-slate-600">
                {HOUSE_RULES.map((t) => (
                  <li key={t}>- {t}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="font-bold text-slate-900 mb-1">CANCELLATION POLICY</p>
              <ul className="space-y-0.5 text-slate-600">
                {CANCELLATION_POLICY.map((t) => (
                  <li key={t}>- {t}</li>
                ))}
              </ul>
            </div>
          </div>

          {/* Bank / Signatory / QR */}
          <div className="grid grid-cols-3 gap-4 items-start border-t border-amber-600 pt-3">
            {bank && (
              <div>
                <p className="font-bold text-slate-900 mb-1">BANK DETAILS</p>
                <p className="text-slate-600 text-[9.5px]">Bank: {bank.bank_name}</p>
                <p className="text-slate-600 text-[9.5px]">A/C Name: {bank.bank_account_name}</p>
                <p className="text-slate-600 text-[9.5px]">A/C No: {bank.bank_account_number}</p>
                <p className="text-slate-600 text-[9.5px]">IFSC: {bank.bank_ifsc}</p>
              </div>
            )}
            <div>
              <p className="font-bold text-slate-900 mb-1">AUTHORISED SIGNATORY</p>
              <p className="text-slate-600 text-[9.5px]">For SRI APARTMENT HOMESTAY</p>
              <p className="mt-4 text-slate-800 text-[9.5px] font-medium">S. Sundaramurthy</p>
              <p className="text-slate-500 text-[9px]">Proprietor</p>
            </div>
            {bank && (
              <div className="text-center">
                <p className="font-bold text-slate-900 mb-1">SCAN &amp; PAY</p>
                <img src={qrCode} alt="Payment QR code" className="w-20 h-20 object-contain mx-auto" />
                <p className="text-slate-500 text-[9px] mt-1">UPI: {bank.upi_id}</p>
              </div>
            )}
          </div>

          <p className="text-center italic text-slate-900 font-semibold text-[10px] mt-4 pt-2 border-t border-amber-600">
            THANK YOU FOR CHOOSING SRI APARTMENT HOMESTAY - Feel at Home, Away from Home
          </p>
        </div>

        <div className="flex items-center justify-between border-t border-slate-200 px-6 py-4">
          <div className="text-xs">
            {inquiry.booking_id ? (
              <span className="text-emerald-600 font-medium">✓ Converted to a confirmed booking</span>
            ) : inquiry.advance_amount <= 0 ? (
              <span className="text-amber-600">Record an advance payment before converting to a booking</span>
            ) : (
              <span className="text-slate-400">Advance recorded — ready to convert</span>
            )}
          </div>
          <div className="flex gap-3">
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
            <Button variant="secondary" onClick={onSendWhatsApp} disabled={sendingWhatsapp}>
              {sendingWhatsapp ? "Opening..." : "Send WhatsApp"}
            </Button>
            <Button variant="secondary" onClick={onSendEmail} disabled={sendingEmail || !inquiry.client_email}>
              {sendingEmail ? "Sending..." : "Send Email"}
            </Button>
            {!inquiry.booking_id && (
              <Button onClick={onConvertToBooking} disabled={converting || inquiry.advance_amount <= 0}>
                {converting ? "Converting..." : "Convert to Booking"}
              </Button>
            )}
            <Button onClick={onDownloadPdf} disabled={downloading}>
              {downloading ? "Generating PDF..." : "Download PDF"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <p className="mb-0.5">
      <span className="text-slate-500">{label}: </span>
      <span className="text-slate-800">{value}</span>
    </p>
  );
}

function TotalsRow({ label, value, bold = false }: { label: string; value: number; bold?: boolean }) {
  return (
    <tr className={bold ? "font-semibold" : ""}>
      <td className="border border-slate-300 px-2 py-1" colSpan={3}>
        {label}
      </td>
      <td className="border border-slate-300 px-2 py-1 text-right">{value.toFixed(2)}</td>
    </tr>
  );
}
