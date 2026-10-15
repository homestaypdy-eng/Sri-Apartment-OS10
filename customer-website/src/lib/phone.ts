// International phone-number helpers for the customer-facing website,
// backed by libphonenumber-js — the actively-maintained JS port of
// Google's own libphonenumber, the same library family the backend's
// customer_auth.phone_service uses server-side. This module never talks
// to the network; it only validates/formats client-side so the customer
// gets an immediate "that doesn't look right" instead of waiting on a
// round-trip, before the backend does its own (authoritative) check.
import {
  AsYouType,
  getCountries,
  getCountryCallingCode,
  parsePhoneNumberFromString,
  type CountryCode,
} from 'libphonenumber-js'

export interface CountryOption {
  code: CountryCode
  name: string
  callingCode: string
  flag: string
}

// Regional indicator symbols spell a flag emoji from an ISO 3166-1 alpha-2
// code (e.g. "IN" -> 🇮🇳) — no image assets or icon font needed.
function flagEmoji(countryCode: string): string {
  return countryCode
    .toUpperCase()
    .replace(/./g, (char) => String.fromCodePoint(127397 + char.charCodeAt(0)))
}

const regionNames = typeof Intl !== 'undefined' && 'DisplayNames' in Intl ? new Intl.DisplayNames(['en'], { type: 'region' }) : null

function countryName(code: string): string {
  return regionNames?.of(code) ?? code
}

// Built once at module load — getCountries() is a static list bundled
// with libphonenumber-js's metadata, not a network call.
export const COUNTRIES: CountryOption[] = getCountries()
  .map((code) => ({
    code,
    name: countryName(code),
    callingCode: getCountryCallingCode(code),
    flag: flagEmoji(code),
  }))
  .sort((a, b) => a.name.localeCompare(b.name))

export const DEFAULT_COUNTRY: CountryCode = 'IN'

/** Combines a selected country with whatever the customer has typed so
 * far into a best-effort E.164-shaped string, so the field always has
 * *something* to submit — validity is checked separately (see
 * isValidPhoneNumber), not by refusing to produce a value at all. */
export function toE164(country: CountryCode, nationalNumber: string): string {
  const digits = nationalNumber.replace(/[^\d]/g, '')
  if (!digits) return ''
  const parsed = parsePhoneNumberFromString(digits, country)
  if (parsed) return parsed.number
  return `+${getCountryCallingCode(country)}${digits}`
}

export function isValidPhoneNumber(e164Value: string): boolean {
  if (!e164Value) return false
  const parsed = parsePhoneNumberFromString(e164Value)
  return parsed?.isValid() ?? false
}

/** Splits an existing E.164 value (e.g. loaded from the customer's saved
 * profile) back into a country + national-number pair for editing. Falls
 * back to DEFAULT_COUNTRY/empty when the value is blank or unparsable —
 * never throws. */
export function splitE164(e164Value: string): { country: CountryCode; nationalNumber: string } {
  if (!e164Value) return { country: DEFAULT_COUNTRY, nationalNumber: '' }
  const parsed = parsePhoneNumberFromString(e164Value)
  if (!parsed) return { country: DEFAULT_COUNTRY, nationalNumber: e164Value.replace(/[^\d]/g, '') }
  return { country: (parsed.country ?? DEFAULT_COUNTRY) as CountryCode, nationalNumber: parsed.formatNational().replace(/[^\d]/g, '') }
}

/** Live-formats a national number as the customer types (e.g.
 * "9876543210" -> "98765 43210" for IN) — purely cosmetic, the value
 * passed to onChange is always the plain digits via toE164 above. */
export function formatAsYouType(country: CountryCode, nationalNumber: string): string {
  return new AsYouType(country).input(nationalNumber)
}
