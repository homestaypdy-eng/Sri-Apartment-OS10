/**
 * Types mirror OS10's actual Pydantic response models field-for-field.
 * Source of truth (verified by reading the live source, not assumed):
 *   backend/app/modules/public_booking/schemas.py
 *   backend/app/modules/customer_auth/schemas.py
 * FastAPI serializes Pydantic models as snake_case JSON — these interfaces
 * intentionally keep snake_case rather than remapping to camelCase, so a
 * type mismatch here shows up as a TS error instead of a silent `undefined`.
 */

// ---------------------------------------------------------------------------
// Public booking — units, availability, quote, promo, holds
// ---------------------------------------------------------------------------

export interface PublicUnitOut {
  id: string
  name: string
  unit_type: string
  base_rate: number
  max_occupancy: number
  property_name: string
  property_type: string | null
  description: string | null
  amenities: string | null
  bedroom_count: number | null
  bathroom_count: number | null
  house_rules: string | null
  checkin_time: string | null
  checkout_time: string | null
}

export interface PublicUnitPhotoOut {
  id: string
  caption: string | null
  url: string
}

export interface PublicBusinessInfoOut {
  company_name: string
  address: string
  contact_phone: string
  contact_email: string
  whatsapp_business_number: string | null
  house_rules: string | null
}

export interface PublicAvailabilityOut {
  unit_id: string
  check_in: string // ISO date
  check_out: string
  available: boolean
  nights: number
  estimated_total: number
}

export interface PromoValidateResponse {
  valid: boolean
  discount_amount: number
  reason: string | null
}

export interface DayRateOut {
  the_date: string
  rate: number
  source: string
  seasonal_name: string | null
}

export interface PublicQuoteOut {
  unit_id: string
  unit_name: string
  unit_type: string
  check_in: string
  check_out: string
  nights: number
  daily_breakdown: DayRateOut[]
  subtotal_before_discount: number
  long_stay_discount_percent: number
  long_stay_discount_amount: number
  promo_discount_amount: number
  subtotal_after_discount: number
  gst_rate_percent: number
  cgst: number
  sgst: number
  igst: number
  total: number
  advance_amount: number
  currency: string
  available: boolean
}

export interface CreateHoldRequest {
  terms_accepted?: true
  unit_id: string
  check_in: string
  check_out: string
  guest_name: string
  guest_phone: string
  /** Mandatory (Correction 5) — OS10's CreateHoldRequest now requires this too. */
  guest_email: string
  /** Optional 2-letter GST state code — place-of-supply only. */
  guest_state?: string
  promo_code?: string
  /** Total guest count from the Dates step's guest selector (Correction 3) — OS10 copies this straight to Booking.adults. */
  adults: number
  /** Optional billing address — applied to Guest.address on conversion, shown on the invoice when supplied. Never required. */
  guest_address?: string
  /** Optional GSTIN — applied to Guest.gst_number on conversion, shown on the invoice when supplied. Never required. */
  guest_gstin?: string
}

export interface QuoteSummaryOut {
  nights: number
  subtotal: number
  discount_amount: number
  total: number
}

export interface HoldOut {
  hold_id: string
  razorpay_order_id: string | null
  razorpay_key_id: string | null
  amount: number
  currency: string
  expires_at: string // ISO datetime
  quote_summary: QuoteSummaryOut
}

export interface VerifyPaymentRequest {
  razorpay_payment_id: string
  razorpay_order_id: string
  razorpay_signature: string
}

export interface PublicBookingConfirmation {
  booking_id: string
  guest_id: string
  check_in: string
  check_out: string
  nights: number
  subtotal: number
  discount_amount: number
  total: number
  invoice_id: string | null
  invoice_no: string | null
  confirmation_emailed: boolean | null
  confirmation_sms_sent: boolean | null
  confirmation_whatsapp_sent: boolean | null
}

// ---------------------------------------------------------------------------
// Customer account auth — register / two-step (password + OTP) login /
// forgot+reset password. Entirely separate trust boundary from the OS10
// guest-booking system (Guest/GuestIdentity/GuestSession) and from staff
// Users — see backend app/modules/customer_auth's own module docstrings.
// ---------------------------------------------------------------------------

export interface CustomerAccountOut {
  id: string
  first_name: string
  last_name: string
  email: string
  phone: string | null
  email_verified_at: string | null
  phone_verified_at?: string | null
  created_at: string
}

export type CustomerIdentityStatus = 'not_required' | 'pending' | 'submitted' | 'under_review' | 'verified' | 'rejected' | 'expired'

export interface CustomerIdentityOut {
  status: CustomerIdentityStatus
  document_type: string | null
  document_id: string | null
  verified_at: string | null
  message: string | null
}

export interface CustomerIdentityDocumentOut {
  id: string
  document_type: string
  filename: string
  uploaded_at: string
}

/** Step 1 of login (POST /customer-auth/login): password has already been
 * verified — this is only proof a one-time code was dispatched, never a
 * session. See step 2, CustomerLoginResponse. */
/** What actually happened to the OTP email/SMS, per OS10's customer_auth/
 * router.py — "sent" is production's only value (backgrounded, outcome
 * unknown at response time, same meaning this response always had). The
 * "dev_*" values only ever come back outside production, from OS10's
 * LOCAL Email OTP testing controls (CUSTOMER_AUTH_EMAIL_OTP_MODE). */
export type OtpDeliveryStatus =
  | 'sent'
  | 'dev_code_only'
  | 'dev_real_email_sent'
  | 'dev_real_email_failed'
  | 'dev_real_email_not_allowlisted'

export interface CustomerLoginOtpRequestResponse {
  challenge_id: string
  message: string
  // Only ever populated outside production — see OS10's customer_auth/router.py.
  dev_otp_code?: string | null
  otp_delivery_status: OtpDeliveryStatus
}

/** Step 2 of login (POST /customer-auth/login/verify-otp) — the only
 * response that ever carries a real session token. */
export interface CustomerLoginResponse {
  access_token: string
  token_type: string
  customer: CustomerAccountOut
}

export interface CustomerForgotPasswordResponse {
  message: string
  // Only ever populated outside production — see OS10's customer_auth/router.py.
  dev_reset_token?: string | null
}

export interface CustomerResetPasswordResponse {
  message: string
}

/** POST /customer-auth/change-password — the backend also revokes every
 * active CustomerSession on success, so the caller's own token stops
 * working right after this resolves. */
export interface CustomerChangePasswordResponse {
  message: string
}

// ---------------------------------------------------------------------------
// Customer social login (Google — the only provider this project
// supports) — GET /customer-auth/oauth/status reveals only whether Google
// has credentials configured, never the credentials themselves. See OS10's
// customer_auth/oauth_router.py.
// ---------------------------------------------------------------------------

export interface CustomerOAuthStatus {
  google: boolean
}

/** POST /customer-auth/oauth/{provider}/confirm-link — completes linking
 * a social identity to the caller's own (already-authenticated) account,
 * after the account's normal password+OTP login proved ownership. */
export interface CustomerOAuthLinkConfirmResponse {
  message: string
}

// ---------------------------------------------------------------------------
// Error shape — FastAPI's default HTTPException body
// ---------------------------------------------------------------------------

export interface ApiErrorBody {
  detail: string
}
