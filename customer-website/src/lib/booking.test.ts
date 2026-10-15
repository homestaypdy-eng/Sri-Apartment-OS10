import { describe, it, expect } from 'vitest'
import { addDaysIso, validateDateRange, validateGuestDetails, nightsBetween, normalizePhone, todayIso } from './booking'

describe('addDaysIso', () => {
  it('adds calendar days across month, year and leap-day boundaries', () => {
    expect(addDaysIso('2026-10-09', 1)).toBe('2026-10-10')
    expect(addDaysIso('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDaysIso('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDaysIso('2028-02-28', 1)).toBe('2028-02-29')
    expect(addDaysIso('2027-02-28', 1)).toBe('2027-03-01')
  })
})

describe('validateDateRange', () => {
  it('rejects missing dates', () => {
    expect(validateDateRange('', '')).toEqual({ valid: false, message: expect.stringContaining('both') })
  })

  it('rejects a check-in date in the past', () => {
    const result = validateDateRange('2020-01-01', '2020-01-02')
    expect(result.valid).toBe(false)
    expect(result.message).toMatch(/past/i)
  })

  it('rejects check-out on or before check-in', () => {
    const today = todayIso()
    expect(validateDateRange(today, today).valid).toBe(false)
  })

  it('accepts a valid forward-looking range', () => {
    const future = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10)
    const futurePlus2 = new Date(Date.now() + 32 * 86_400_000).toISOString().slice(0, 10)
    expect(validateDateRange(future, futurePlus2)).toEqual({ valid: true })
  })
})

describe('nightsBetween', () => {
  it('computes whole nights', () => {
    expect(nightsBetween('2026-09-01', '2026-09-03')).toBe(2)
  })
})

describe('validateGuestDetails', () => {
  it('requires a name', () => {
    expect(validateGuestDetails({ name: '', phone: '9876543210' }).valid).toBe(false)
  })

  it('rejects an invalid Indian mobile number', () => {
    expect(validateGuestDetails({ name: 'Test', phone: '12345' }).valid).toBe(false)
  })

  it('accepts a valid 10-digit mobile number, with or without country code', () => {
    expect(validateGuestDetails({ name: 'Test', phone: '9876543210', email: 'guest@example.com' }).valid).toBe(true)
    expect(validateGuestDetails({ name: 'Test', phone: '+91 98765 43210', email: 'guest@example.com' }).valid).toBe(true)
  })

  it('rejects a malformed email', () => {
    expect(validateGuestDetails({ name: 'Test', phone: '9876543210', email: 'not-an-email' }).valid).toBe(false)
  })

  // Correction 5: email is mandatory — the booking confirmation is sent
  // here, so checkout must not proceed without a real address.
  it('rejects a missing email', () => {
    const result = validateGuestDetails({ name: 'Test', phone: '9876543210' })
    expect(result.valid).toBe(false)
    expect(result.message).toMatch(/email/i)
  })

  it('rejects a blank/whitespace-only email', () => {
    expect(validateGuestDetails({ name: 'Test', phone: '9876543210', email: '   ' }).valid).toBe(false)
  })

  it('accepts a valid name, phone, and email together', () => {
    expect(validateGuestDetails({ name: 'Test Guest', phone: '9876543210', email: 'guest@example.com' }).valid).toBe(true)
  })
})

describe('normalizePhone', () => {
  it('strips everything but the last 10 digits', () => {
    expect(normalizePhone('+91 98765 43210')).toBe('9876543210')
  })
})
