import { useEffect, useRef, useState } from 'react'
import type { CountryCode } from 'libphonenumber-js'
import { COUNTRIES, formatAsYouType, splitE164, toE164 } from '../lib/phone'

const FIELD_CLASSES =
  'rounded-md border border-brand-border bg-brand-cream text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none'

// Country selector + national-number field, producing a single combined
// E.164 value (e.g. "+919876543210") — the same shape customer_auth.
// phone_service expects and stores, so a customer anywhere in the world
// (not just India) can register and log in with their own country code.
export function PhoneInput({
  id,
  label,
  value,
  onChange,
  required,
  autoComplete,
}: {
  id?: string
  label: string
  value: string
  onChange: (value: string) => void
  required?: boolean
  autoComplete?: string
}) {
  const [{ country, nationalNumber }, setState] = useState(() => splitE164(value))
  // Starts as `null` (never a real value) so the effect below always
  // runs at least once on mount — including immediately self-healing a
  // legacy/un-normalized incoming value (e.g. an existing profile's bare
  // "9876543210" with no country code) into full E.164, rather than
  // silently displaying a normalized-looking field while the parent's
  // own state stays in the old shape until the customer happens to edit it.
  const lastEmitted = useRef<string | null>(null)

  // Re-syncs from an externally-changed value (e.g. the customer's saved
  // profile finishes loading after this field already mounted empty) —
  // but never overwrites what the customer is actively typing, since
  // every value THIS component itself produces is recorded in
  // lastEmitted first.
  useEffect(() => {
    if (value === lastEmitted.current) return
    const next = splitE164(value)
    setState(next)
    const normalized = toE164(next.country, next.nationalNumber)
    lastEmitted.current = normalized
    if (normalized !== value) onChange(normalized)
    // onChange is expected to be a stable callback (a useState setter or
    // equivalent) — including it would re-run this on every parent
    // render, fighting the customer's own typing via `emit` below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  function emit(nextCountry: CountryCode, rawNational: string) {
    // Stored as plain digits only — formatAsYouType below derives the
    // formatted display from these digits fresh on every render, rather
    // than re-feeding an already-formatted string back into itself
    // (which AsYouType isn't designed to do idempotently).
    const nextNational = rawNational.replace(/[^\d]/g, '')
    setState({ country: nextCountry, nationalNumber: nextNational })
    const combined = toE164(nextCountry, nextNational)
    lastEmitted.current = combined
    onChange(combined)
  }

  return (
    <div className="flex gap-2">
      <label className="block">
        <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Country</span>
        <select
          aria-label="Country code"
          value={country}
          onChange={(e) => emit(e.target.value as CountryCode, nationalNumber)}
          className={`${FIELD_CLASSES} h-[46px] w-[6.5rem] px-2`}
        >
          {COUNTRIES.map((c) => (
            <option key={c.code} value={c.code}>
              {c.flag} +{c.callingCode}
            </option>
          ))}
        </select>
      </label>
      <label className="block flex-1">
        <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">{label}</span>
        <input
          id={id}
          type="tel"
          autoComplete={autoComplete}
          inputMode="tel"
          required={required}
          value={formatAsYouType(country, nationalNumber)}
          onChange={(e) => emit(country, e.target.value)}
          className={`${FIELD_CLASSES} h-[46px] w-full px-3.5`}
        />
      </label>
    </div>
  )
}
