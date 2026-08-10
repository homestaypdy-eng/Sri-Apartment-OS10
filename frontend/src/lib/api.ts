import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api/v1";

export const api = axios.create({ baseURL: API_BASE_URL });

// Separate axios instance for the Guest Portal. Guest sessions and staff/
// admin sessions are entirely different token namespaces on the backend
// (see portal/router.py::get_current_guest) — using one shared instance
// with one shared Authorization header would let whichever token was set
// last silently override the other, which is exactly the kind of bug that
// only shows up when someone has both a staff tab and a guest tab open.
export const guestApi = axios.create({ baseURL: API_BASE_URL });
guestApi.interceptors.request.use((config) => {
  const token = localStorage.getItem("guest_access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Attach access token to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401, attempt a refresh once; if that fails, force logout.
let isRefreshing = false;

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry && !isRefreshing) {
      original._retry = true;
      isRefreshing = true;
      try {
        const refreshToken = localStorage.getItem("refresh_token");
        if (!refreshToken) throw new Error("No refresh token");
        const { data } = await axios.post(`${API_BASE_URL}/auth/refresh`, {
          refresh_token: refreshToken,
        });
        localStorage.setItem("access_token", data.access_token);
        localStorage.setItem("refresh_token", data.refresh_token);
        original.headers.Authorization = `Bearer ${data.access_token}`;
        isRefreshing = false;
        return api(original);
      } catch {
        isRefreshing = false;
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// --- Types matching the backend Pydantic schemas ---

export interface UserOut {
  id: string;
  name: string;
  email: string;
  role: "admin" | "staff" | string;
  is_active: boolean;
}

export interface SessionOut {
  id: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  last_used_at: string;
  revoked_at: string | null;
  is_current: boolean;
}

export interface PropertyOut {
  id: string;
  name: string;
  address: string;
  gst_number: string | null;
  state_code: string;
  property_code: string | null;
  property_type: string | null;
  owner_name: string | null;
  owner_phone: string | null;
  owner_email: string | null;
  google_maps_url: string | null;
  is_active: boolean;
}

export interface PropertyFileOut {
  id: string;
  property_id: string;
  file_type: string;
  filename: string;
  content_type: string;
  caption: string | null;
  uploaded_at: string;
}

export interface PropertyPnL {
  property_id: string;
  property_name: string;
  period_year: number;
  period_month: number;
  units: ApartmentPnL[];
  total_revenue: number;
  total_expenses: number;
  net_profit: number;
}

export interface UnitOut {
  id: string;
  property_id: string;
  name: string;
  unit_type: string;
  max_occupancy: number;
  base_rate: number;
  weekend_rate: number | null;
  status: string;
  housekeeping_status: string;
  unit_code: string | null;
  floor: string | null;
  flat_number: string | null;
  amenities: string | null;
  is_active: boolean;
}

export interface UnitPhotoOut {
  id: string;
  unit_id: string;
  filename: string;
  content_type: string;
  caption: string | null;
  uploaded_at: string;
}

export interface SeasonalRateOut {
  id: string;
  unit_id: string;
  name: string;
  start_date: string;
  end_date: string;
  rate_per_night: number;
  created_at: string;
}

export interface LongStayDiscountTierOut {
  id: string;
  unit_id: string;
  min_nights: number;
  discount_percent: number;
}

export interface DayRateOut {
  the_date: string;
  rate: number;
  source: string;
  seasonal_name: string | null;
}

export interface PriceQuoteOut {
  check_in: string;
  check_out: string;
  nights: number;
  daily_breakdown: DayRateOut[];
  subtotal_before_discount: number;
  long_stay_discount_percent: number;
  long_stay_discount_amount: number;
  subtotal_after_discount: number;
  average_nightly_rate: number;
}

export interface GuestOut {
  id: string;
  name: string;
  phone: string;
  email: string | null;
  address: string | null;
  id_proof_type: string | null;
  id_proof_number: string | null;
  id_proof_url: string | null;
  date_of_birth: string | null;
  gender: string | null;
  nationality: string | null;
  occupation: string | null;
  whatsapp_number: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  photo_url: string | null;
  notes: string | null;
  is_vip: boolean;
  is_blacklisted: boolean;
  merged_into_guest_id: string | null;
}

export interface GuestSegment {
  guest_id: string;
  name: string;
  phone: string;
  email: string | null;
  is_vip: boolean;
  is_blacklisted: boolean;
  is_repeat_guest: boolean;
  total_bookings: number;
  total_spent: number;
  latest_source: string | null;
  last_stay: string | null;
}

export interface GuestDocumentOut {
  id: string;
  guest_id: string;
  document_type: string;
  document_number: string | null;
  filename: string;
  content_type: string;
  side: string;
  uploaded_at: string;
}

export interface BookingOut {
  id: string;
  unit_id: string;
  guest_id: string;
  check_in: string;
  check_out: string;
  status: string;
  source: string;
  rate_applied: number;
  internal_notes: string | null;
  special_requests: string | null;
  due_date: string | null;
  adults: number;
  children: number;
  extra_bed: boolean;
  arrival_time: string | null;
  departure_time: string | null;
}

export interface HousekeepingTaskOut {
  id: string;
  unit_id: string;
  assigned_to: string | null;
  task_type: string;
  status: string;
  notes: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface HousekeepingPhotoOut {
  id: string;
  task_id: string;
  filename: string;
  content_type: string;
  caption: string | null;
  uploaded_at: string;
}

export interface TimelineEvent {
  action: string;
  user_name: string;
  details: string | null;
  previous_value: string | null;
  new_value: string | null;
  created_at: string;
}

export interface MaintenanceTicketOut {
  id: string;
  unit_id: string;
  reported_by: string | null;
  assigned_to: string | null;
  priority: string;
  status: string;
  description: string;
  cost: number | null;
  created_at: string;
  resolved_at: string | null;
}

export interface MaintenancePhotoOut {
  id: string;
  ticket_id: string;
  photo_type: string;
  filename: string;
  content_type: string;
  uploaded_at: string;
}

export interface RevenueBreakdown {
  total: number;
  by_source: Record<string, number>;
}

export interface GstBreakdown {
  cgst: number;
  sgst: number;
  igst: number;
  total: number;
  invoice_count: number;
}

export interface SummaryReport {
  date_from: string;
  date_to: string;
  revenue: RevenueBreakdown;
  total_bookings: number;
  room_nights_sold: number;
  available_room_nights: number;
  occupancy_rate_percent: number;
  adr: number;
  revpar: number;
  gst: GstBreakdown;
  maintenance_cost_total: number;
  net_revenue_after_maintenance: number;
}

export interface PeriodRange {
  date_from: string;
  date_to: string;
}

export interface MetricComparison {
  current: number;
  previous: number;
  change_percent: number | null;
  change_percentage_points?: number | null;
}

export interface SummaryComparison {
  current_period: PeriodRange;
  previous_period: PeriodRange;
  occupancy: MetricComparison;
  adr: MetricComparison;
  revpar: MetricComparison;
}

export interface CancellationReport {
  current_period: PeriodRange;
  previous_period: PeriodRange;
  total_bookings_in_period: number;
  cancelled_count: number;
  cancellation_rate_percent: number;
  no_show_count: number;
  no_show_rate_percent: number;
  previous_total_bookings_in_period: number;
  previous_cancelled_count: number;
  previous_cancellation_rate_percent: number;
  previous_no_show_count: number;
  previous_no_show_rate_percent: number;
  cancellation_rate_change_percent: number | null;
  no_show_rate_change_percent: number | null;
}

export interface RevenuePoint {
  date: string;
  revenue: number;
  room_nights_sold: number;
}

export interface UnitPerformance {
  unit_id: string;
  unit_name: string;
  room_nights_sold: number;
  revenue: number;
  occupancy_rate_percent: number;
  adr: number;
}

export interface InquiryOut {
  id: string;
  offer_no: string;
  offer_date: string;
  valid_upto: string;
  client_name: string;
  client_phone: string;
  client_email: string | null;
  client_address: string | null;
  unit_id: string | null;
  flat_room_label: string;
  num_guests: number;
  check_in_date: string;
  check_in_time: string;
  check_out_date: string;
  check_out_time: string;
  tariff_type: string;
  rate_per_night: number;
  nights: number;
  extra_charges: number;
  discount: number;
  taxable_value: number;
  gst_rate_percent: number;
  cgst: number;
  sgst: number;
  total_amount: number;
  rounded_off: number;
  grand_total: number;
  advance_amount: number;
  balance_amount: number;
  payment_mode: string;
  status: string;
  booking_id: string | null;
  guest_id: string | null;
  created_at: string;
  lead_status: string;
  last_contacted_at: string | null;
  next_follow_up_date: string | null;
  follow_up_notes: string | null;
  is_expired: boolean;
  pricing_source: string;
  rate_breakdown: string | null;
  source: string;
  priority: string;
  assigned_to: string | null;
  lost_reason: string | null;
}

export interface InquiryAttachmentOut {
  id: string;
  inquiry_id: string;
  filename: string;
  content_type: string;
  uploaded_at: string;
}

export interface ConvertToBookingResponse {
  inquiry: InquiryOut;
  booking_id: string;
  guest_id: string;
  payment_id: string | null;
  guest_reused: boolean;
}

export interface SendWhatsAppResponse {
  whatsapp_url: string;
  message_preview: string;
}

export interface SalesFunnelStage {
  lead_status: string;
  count: number;
}

export interface SalesFunnelReport {
  date_from: string;
  date_to: string;
  total_inquiries: number;
  stages: SalesFunnelStage[];
  converted_count: number;
  lost_count: number;
  conversion_rate_percent: number;
  total_quoted_value: number;
  total_converted_value: number;
}

export interface PaymentOut {
  id: string;
  booking_id: string;
  amount: number;
  mode: string;
  status: string;
  reference_no: string | null;
  paid_at: string;
  refund_of_payment_id: string | null;
  refund_reason: string | null;
}

export interface InvoiceOut {
  id: string;
  booking_id: string;
  invoice_no: string;
  subtotal: number;
  cgst: number;
  sgst: number;
  igst: number;
  total: number;
  gst_rate_percent: number;
  itc_available: boolean;
  hsn_code: string;
  issued_at: string;
}

export interface CreditNoteOut {
  id: string;
  invoice_id: string;
  credit_note_no: string;
  amount: number;
  cgst: number;
  sgst: number;
  igst: number;
  total: number;
  reason: string;
  issued_at: string;
}

// Mirrors backend app/modules/payments/service.py::determine_gst_slab —
// informational only (shown before invoice generation); the backend is the
// actual source of truth and computes this itself when the invoice is made.
export function estimateGstSlab(tariffPerNight: number): { rate: number; itcAvailable: boolean } {
  if (tariffPerNight <= 1000) return { rate: 0, itcAvailable: false };
  if (tariffPerNight <= 7500) return { rate: 5, itcAvailable: false };
  return { rate: 18, itcAvailable: true };
}

// --- Settings ---
export interface BusinessSettingsOut {
  company_name: string;
  tagline: string;
  address: string;
  contact_phone: string;
  contact_email: string;
  gst_number: string;
  udyam_number: string;
  state_code: string;
  bank_name: string;
  bank_account_name: string;
  bank_account_number: string;
  bank_ifsc: string;
  bank_branch: string;
  upi_id: string;
  signatory_name: string;
  signatory_title: string;
  logo_path: string | null;
  qr_code_path: string | null;
  invoice_terms: string;
  quotation_terms: string;
  house_rules: string;
  cancellation_policy: string;
  default_gst_rate_percent: number | null;
  theme_color: string;
  whatsapp_business_number: string | null;
  updated_at: string;
}

export interface SystemInfoOut {
  environment: string;
  app_version: string;
  server_time: string;
  database_connected: boolean;
  email_configured: boolean;
  sms_configured: boolean;
  whatsapp_configured: boolean;
}

export interface RolePermission {
  role: string;
  description: string;
  can_do: string[];
  cannot_do: string[];
}

export interface RolesPermissionsOut {
  roles: RolePermission[];
}

// --- Inventory ---
export interface SupplierOut {
  id: string;
  name: string;
  contact_person: string | null;
  phone: string | null;
  email: string | null;
  notes: string | null;
  created_at: string;
}

export interface InventoryItemOut {
  id: string;
  name: string;
  category: string;
  unit_of_measure: string;
  current_stock: number;
  minimum_stock: number;
  barcode: string | null;
  qr_code_value: string | null;
  unit_cost: number | null;
  notes: string | null;
  brand: string | null;
  expiry_date: string | null;
  sku: string | null;
  is_low_stock: boolean;
  created_at: string;
}

export interface StockMovementOut {
  id: string;
  item_id: string;
  movement_type: string;
  quantity: number;
  reason: string | null;
  supplier_id: string | null;
  unit_cost: number | null;
  unit_id: string | null;
  housekeeping_task_id: string | null;
  maintenance_ticket_id: string | null;
  linked_purchase_record_id: string | null;
  created_at: string;
}

export interface StockMovementOut {
  id: string;
  item_id: string;
  movement_type: string;
  quantity: number;
  reason: string | null;
  supplier_id: string | null;
  unit_cost: number | null;
  created_at: string;
}

export interface InventoryReport {
  total_items: number;
  low_stock_count: number;
  total_stock_value: number;
  by_category: Record<string, number>;
}

export const INVENTORY_CATEGORIES = [
  "bedsheets", "pillow_covers", "towels", "blankets", "toiletries",
  "cleaning_supplies", "kitchen_items", "appliances", "furniture", "other",
];

// --- Staff management ---
export interface AttendanceOut {
  id: string;
  user_id: string;
  date: string;
  check_in_time: string | null;
  check_out_time: string | null;
  status: string;
  notes: string | null;
  created_at: string;
}

export interface LeaveRequestOut {
  id: string;
  user_id: string;
  from_date: string;
  to_date: string;
  reason: string | null;
  status: string;
  reviewed_by: string | null;
  created_at: string;
}

export interface PayrollOut {
  id: string;
  user_id: string;
  period_year: number;
  period_month: number;
  basic_salary: number;
  deductions: number;
  bonus: number;
  net_pay: number;
  status: string;
  paid_at: string | null;
  created_at: string;
}

export interface ShiftOut {
  id: string;
  user_id: string;
  date: string;
  shift_start: string;
  shift_end: string;
  notes: string | null;
}

export interface HolidayOut {
  id: string;
  date: string;
  name: string;
}

export interface StaffApartmentAssignmentOut {
  id: string;
  user_id: string;
  unit_id: string;
  assigned_at: string;
}

export interface StaffPerformanceNoteOut {
  id: string;
  user_id: string;
  note: string;
  created_by: string | null;
  created_at: string;
}

export interface StaffProfileOut {
  id: string;
  user_id: string;
  employee_id: string | null;
  designation: string | null;
  department: string | null;
  employment_type: string | null;
  join_date: string | null;
  exit_date: string | null;
  is_active: boolean;
  phone: string | null;
  alternate_phone: string | null;
  date_of_birth: string | null;
  address: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  emergency_contact_relationship: string | null;
  id_type: string | null;
  id_number: string | null;
  bank_name: string | null;
  account_holder_name: string | null;
  account_number: string | null;
  ifsc: string | null;
  payment_method: string | null;
  weekly_off_day: string | null;
  supervisor_user_id: string | null;
  notes: string | null;
  created_at: string;
}

export interface StaffAdvanceOut {
  id: string;
  user_id: string;
  date: string;
  amount: number;
  category: string;
  reason: string | null;
  payment_method: string | null;
  reference_number: string | null;
  status: string;
  deducted_amount: number;
  outstanding_amount: number;
  linked_payroll_id: string | null;
  created_by: string | null;
  created_at: string;
}

// --- Audit ---
export interface AuditLogOut {
  id: string;
  user_id: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  details: string | null;
  ip_address: string | null;
  created_at: string;
}

export interface LoginEventOut {
  id: string;
  user_id: string | null;
  email_attempted: string;
  success: boolean;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

// --- Notifications ---
export interface NotificationLogOut {
  id: string;
  event_type: string;
  channel: string;
  recipient: string;
  booking_id: string | null;
  inquiry_id: string | null;
  guest_request_id: string | null;
  maintenance_ticket_id: string | null;
  housekeeping_task_id: string | null;
  inventory_item_id: string | null;
  leave_request_id: string | null;
  payroll_id: string | null;
  user_id: string | null;
  read_at: string | null;
  success: boolean;
  error_message: string | null;
  created_at: string;
}

export interface ReminderScanResult {
  checkin_reminder: number;
  checkout_reminder: number;
  review_request: number;
  payment_reminder: number;
}

// --- Guest Portal ---
export interface GuestProfileOut {
  id: string;
  name: string;
  phone: string;
  email: string | null;
}

export interface GuestBookingOut {
  id: string;
  unit_id: string;
  check_in: string;
  check_out: string;
  status: string;
  rate_applied: number;
}

export interface GuestRequestOut {
  id: string;
  guest_id: string;
  booking_id: string | null;
  request_type: string;
  description: string;
  requested_check_out_date: string | null;
  status: string;
  priority: string;
  assigned_to: string | null;
  staff_notes: string | null;
  guest_feedback_rating: number | null;
  guest_feedback_text: string | null;
  created_at: string;
  resolved_at: string | null;
}

// --- Finance ---
export interface ExpenseOut {
  id: string;
  category: string;
  amount: number;
  expense_date: string;
  unit_id: string | null;
  notes: string | null;
  approval_status: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
}

export interface BudgetOut {
  id: string;
  category: string;
  period_year: number;
  period_month: number;
  budgeted_amount: number;
}

export interface BudgetVsActualLine {
  category: string;
  budgeted_amount: number;
  actual_amount: number;
  variance: number;
  variance_percent: number | null;
}

export interface BudgetVsActualReport {
  period_year: number;
  period_month: number;
  lines: BudgetVsActualLine[];
  total_budgeted: number;
  total_actual: number;
}

export interface SecurityDepositOut {
  id: string;
  booking_id: string;
  amount: number;
  status: string;
  received_at: string;
  refund_amount: number;
  refunded_at: string | null;
  notes: string | null;
  pending_refund_amount: number;
}

export interface OwnerWithdrawalOut {
  id: string;
  amount: number;
  withdrawal_date: string;
  notes: string | null;
  created_at: string;
}

export interface OTACommissionOut {
  id: string;
  booking_id: string;
  channel: string;
  commission_percent: number;
  commission_amount: number;
  ota_gst_amount: number;
  created_at: string;
}

export interface OTACommissionChannelSummary {
  channel: string;
  booking_count: number;
  total_booking_revenue: number;
  total_commission: number;
  total_ota_gst: number;
}

export interface OTACommissionReport {
  date_from: string;
  date_to: string;
  by_channel: OTACommissionChannelSummary[];
}

export interface OutstandingPaymentLine {
  booking_id: string;
  guest_name: string;
  unit_name: string;
  check_in: string;
  check_out: string;
  expected_total: number;
  paid_amount: number;
  pending_balance: number;
  due_date: string | null;
  is_overdue: boolean;
}

export interface CashFlowReport {
  date_from: string;
  date_to: string;
  cash_in_payments: number;
  cash_in_deposits: number;
  cash_in_total: number;
  cash_out_expenses: number;
  cash_out_withdrawals: number;
  cash_out_deposit_refunds: number;
  cash_out_total: number;
  net_cash_flow: number;
  cash_in_bank: number;
  cash_in_hand: number;
}

export interface GstMonthlySummaryLine {
  year: number;
  month: number;
  gst_collected: number;
  invoice_count: number;
}

export interface GstDashboardReport {
  date_from: string;
  date_to: string;
  gst_collected: number;
  gst_input_credit_eligible: number;
  gst_payable: number;
  gst_liability: number;
  monthly_summary: GstMonthlySummaryLine[];
}

export const LAUNDRY_ITEM_TYPES = ["bedsheet", "pillow_cover", "bath_towel", "face_towel", "blanket", "curtains", "other"];

export interface LaundryEntryOut {
  id: string;
  entry_date: string;
  item_type: string;
  quantity: number;
  rate_per_item: number;
  unit_id: string | null;
  notes: string | null;
  created_at: string;
  total_amount: number;
}

export interface LaundrySummaryLine {
  item_type: string;
  total_quantity: number;
  total_amount: number;
}

export interface LaundryMonthlySummary {
  period_year: number;
  period_month: number;
  lines: LaundrySummaryLine[];
  grand_total: number;
}

export const EXPENSE_CATEGORIES = ["electricity", "laundry", "internet", "salary", "maintenance", "purchases", "other"];
export const OTA_CHANNELS = ["booking_com", "airbnb", "agoda", "direct"];

// --- Operational Dashboard ---
export interface ApartmentPerformanceLine {
  unit_id: string;
  unit_name: string;
  month_revenue: number;
  month_expenses: number;
  month_profit: number;
  month_nights_booked: number;
  month_occupancy_percent: number;
}

export interface MonthlyTrendPoint {
  year: number;
  month: number;
  revenue: number;
  expenses: number;
  profit: number;
}

export interface AttentionItem {
  category: "unpaid_balance" | "guest_request" | "maintenance" | "low_stock";
  severity: "critical" | "warning";
  title: string;
  reason: string;
  action: string;
  reference_id: string;
  link: string;
}

export interface OperationalDashboard {
  as_of: string;
  arrivals_today: number;
  departures_today: number;
  in_house_guests: number;
  total_units: number;
  total_guests: number;
  active_bookings: number;
  vacant_units: number;
  occupancy_percent: number;
  pending_checkins: number;
  pending_checkouts: number;
  pending_payments_count: number;
  pending_payments_amount: number;
  housekeeping_pending: number;
  maintenance_open: number;
  guest_requests_open: number;
  low_stock_count: number;
  staff_present_today: number;
  staff_absent_today: number;
  follow_ups_due_today: number;
  today_revenue: number;
  today_expenses: number;
  today_profit: number;
  month_revenue: number;
  month_expenses: number;
  month_profit: number;
  apartment_performance: ApartmentPerformanceLine[];
  monthly_trend: MonthlyTrendPoint[];
  attention_items: AttentionItem[];
}

export interface CalendarDay {
  date: string;
  check_ins: number;
  check_outs: number;
  in_house: number;
}

export interface DashboardCalendar {
  period_year: number;
  period_month: number;
  days: CalendarDay[];
}

// --- Booking management ---
export interface BookingAttachmentOut {
  id: string;
  booking_id: string;
  filename: string;
  content_type: string;
  uploaded_at: string;
}

export interface GuestHistoryBooking {
  booking_id: string;
  unit_name: string;
  check_in: string;
  check_out: string;
  status: string;
  rate_applied: number;
  total_paid: number;
  invoice_no: string | null;
}

export interface GuestHistoryRequest {
  request_type: string;
  description: string;
  status: string;
  created_at: string;
}

export interface GuestHistory {
  guest: { id: string; name: string; phone: string; email: string | null };
  total_bookings: number;
  is_repeat_guest: boolean;
  total_spent: number;
  bookings: GuestHistoryBooking[];
  requests: GuestHistoryRequest[];
}

// --- Excel replacement ---
export interface UtilityAllocationOut {
  unit_id: string;
  unit_name: string;
  share_amount: number;
}

export interface UtilityReadingOut {
  id: string;
  unit_id: string | null;
  utility_type: string;
  meter_number: string | null;
  reading_date: string;
  opening_units: number;
  closing_units: number;
  rate_per_unit: number;
  notes: string | null;
  units_consumed: number;
  amount: number;
  allocations: UtilityAllocationOut[];
}

export interface PurchaseRecordOut {
  id: string;
  purchase_date: string;
  item_description: string;
  amount: number;
  supplier_name: string | null;
  unit_id: string | null;
  notes: string | null;
}

export interface DailyClosingOut {
  id: string;
  closing_date: string;
  opening_balance: number;
  total_cash_in: number;
  total_cash_out: number;
  closing_balance: number;
  notes: string | null;
  closed_at: string;
}

export interface CashbookLine {
  time: string;
  description: string;
  transaction_type: string;
  amount: number;
  running_balance: number;
}

export interface DailyCashbook {
  cashbook_date: string;
  opening_balance: number;
  lines: CashbookLine[];
  closing_balance: number;
  total_in: number;
  total_out: number;
}

export interface ApartmentPnLLine {
  category: string;
  amount: number;
}

export interface ApartmentPnL {
  unit_id: string;
  unit_name: string;
  period_year: number;
  period_month: number;
  revenue: number;
  advance_received: number;
  outstanding_balance: number;
  expense_lines: ApartmentPnLLine[];
  total_expenses: number;
  net_profit: number;
}

export interface AdvanceSummaryLine {
  booking_id: string;
  guest_name: string;
  unit_name: string;
  check_in: string;
  check_out: string;
  advance_paid: number;
  total_due: number;
  balance_remaining: number;
}

export interface IncomeRegisterLine {
  booking_id: string;
  check_in: string;
  guest_name: string;
  no_of_guests: number;
  contact: string;
  amount: number;
  advance: number;
  balance: number;
  status: string;
}

export interface IncomeRegisterUnit {
  unit_id: string;
  unit_name: string;
  lines: IncomeRegisterLine[];
  unit_total: number;
}

export interface IncomeRegister {
  period_year: number;
  period_month: number;
  units: IncomeRegisterUnit[];
  grand_total: number;
  by_source: Record<string, number>;
}

function extractErrorMessage(err: unknown): string {
  const anyErr = err as any;
  const detail = anyErr?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d: any) => d.msg).join(", ");
  return anyErr?.message || "Something went wrong";
}

export { extractErrorMessage };
