/**
 * Pure, unit-testable helpers. These validate the SHAPE of guest input
 * (is check-out after check-in, is it in the past) — they never decide
 * price or availability. That stays with OS10.
 */

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

/** Calendar-day arithmetic on a YYYY-MM-DD string — done in UTC so the
 * result never shifts with the browser's timezone or DST. */
export function addDaysIso(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

export function nightsBetween(checkIn: string, checkOut: string): number {
  const a = new Date(`${checkIn}T00:00:00`)
  const b = new Date(`${checkOut}T00:00:00`)
  return Math.round((b.getTime() - a.getTime()) / 86_400_000)
}

export interface DateValidationResult {
  valid: boolean
  message?: string
}

export function validateDateRange(checkIn: string, checkOut: string): DateValidationResult {
  if (!checkIn || !checkOut) return { valid: false, message: 'Please choose both check-in and check-out dates.' }
  if (checkIn < todayIso()) return { valid: false, message: 'Check-in cannot be in the past.' }
  if (checkOut <= checkIn) return { valid: false, message: 'Check-out must be after check-in.' }
  return { valid: true }
}

export function formatInr(amount: number): string {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(amount)
}

export function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}

const PHONE_RE = /^[6-9]\d{9}$/ // Indian mobile — 10 digits, starts 6-9, matching guest_phone's real-world usage

export function validateGuestDetails(input: { name: string; phone: string; email?: string }): DateValidationResult {
  if (!input.name.trim()) return { valid: false, message: 'Please enter your name.' }
  const digitsOnly = input.phone.replace(/\D/g, '').slice(-10)
  if (!PHONE_RE.test(digitsOnly)) return { valid: false, message: 'Please enter a valid 10-digit mobile number.' }
  // Email is mandatory (Correction 5): the booking confirmation is sent
  // here, so an empty or malformed address must block checkout rather
  // than silently produce an unreachable guest.
  if (!input.email?.trim()) return { valid: false, message: 'Please enter your email address.' }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.email.trim())) return { valid: false, message: 'Please enter a valid email address.' }
  return { valid: true }
}

export function normalizePhone(phone: string): string {
  return phone.replace(/\D/g, '').slice(-10)
}
