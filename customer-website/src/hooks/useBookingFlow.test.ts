import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useBookingFlow } from './useBookingFlow'
import * as api from '../lib/api'
import { ApiError } from '../lib/api'
import { loadRazorpayScript } from '../lib/loadRazorpay'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof api>('../lib/api')
  return {
    ...actual,
    getPublicUnits: vi.fn(),
    getAvailability: vi.fn(),
    getQuote: vi.fn(),
    createHold: vi.fn(),
    getPaymentAvailability: vi.fn(),
    customerGetMe: vi.fn(),
    validatePromo: vi.fn(),
  }
})
vi.mock('../lib/loadRazorpay', () => ({ loadRazorpayScript: vi.fn().mockResolvedValue(undefined) }))

const UNIT_A = { id: 'unit-a', name: 'Flat A1', unit_type: 'apartment', base_rate: 3500, max_occupancy: 5, property_name: 'Sri Apartment Homestay', property_type: null, description: null, amenities: null, bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null }
const UNIT_B = { id: 'unit-b', name: 'Flat A2', unit_type: 'apartment', base_rate: 3500, max_occupancy: 2, property_name: 'Sri Apartment Homestay', property_type: null, description: null, amenities: null, bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null }

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  vi.mocked(api.getPaymentAvailability).mockResolvedValue({ available: true })
})

describe('useBookingFlow — availability search (Step 5: OS10 stays the availability authority)', () => {
  it('only includes units OS10 reports as available for the requested guest count', async () => {
    vi.mocked(api.getPublicUnits).mockResolvedValue([UNIT_A, UNIT_B])
    vi.mocked(api.getAvailability).mockImplementation(async ({ unit_id }) => ({
      unit_id,
      check_in: '2099-01-01',
      check_out: '2099-01-03',
      available: unit_id === 'unit-a',
      nights: 2,
      estimated_total: 7000,
    }))

    const { result } = renderHook(() => useBookingFlow())
    await act(async () => {
      await result.current.searchAvailability('2099-01-01', '2099-01-03', 3)
    })

    // unit-b is excluded on two independent grounds: OS10 said unavailable,
    // AND its max_occupancy (2) is below the requested guest count (3).
    expect(result.current.state.availableUnits).toHaveLength(1)
    expect(result.current.state.availableUnits[0].unit.id).toBe('unit-a')
    expect(result.current.state.step).toBe('apartment')
  })

  it('rejects an invalid date range before ever calling OS10', async () => {
    const { result } = renderHook(() => useBookingFlow())
    await act(async () => {
      await result.current.searchAvailability('2020-01-01', '2020-01-02', 2)
    })
    expect(api.getPublicUnits).not.toHaveBeenCalled()
    expect(result.current.state.searchError).toMatch(/past/i)
  })

  it('surfaces a network failure with a retry-friendly message instead of an empty result', async () => {
    vi.mocked(api.getPublicUnits).mockRejectedValue(new ApiError('Could not reach the server. Please check your connection and try again.', 0, true))
    const { result } = renderHook(() => useBookingFlow())
    await act(async () => {
      await result.current.searchAvailability('2099-01-01', '2099-01-03', 2)
    })
    expect(result.current.state.searchError).toMatch(/connection/i)
    expect(result.current.state.step).toBe('dates') // never advances past a failed search
  })
})

describe('useBookingFlow — payment (Step 7/8: hold → Razorpay → verify, no local confirmation)', () => {
  async function setUpAtGuestStep() {
    const { result } = renderHook(() => useBookingFlow())
    await act(async () => { await result.current.selectUnit(UNIT_A) })
    act(() => { result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' }) })
    await act(async () => { await result.current.proceedToPayment() })
    return result
  }

  it('does not fire a second hold-creation call while the first is still in flight (duplicate submission)', async () => {
    let resolveHold!: (v: Awaited<ReturnType<typeof api.createHold>>) => void
    vi.mocked(api.createHold).mockReturnValue(new Promise((resolve) => { resolveHold = resolve }))
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })

    const result = await setUpAtGuestStep()
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    const onConfirmed = vi.fn()
    await act(async () => {
      void result.current.startPayment(onConfirmed, 'test_customer_token', true)
      result.current.startPayment(onConfirmed, 'test_customer_token', true) // fired again before the first hold resolves
    })

    expect(api.createHold).toHaveBeenCalledTimes(1)
    resolveHold({ hold_id: 'h1', razorpay_order_id: null, razorpay_key_id: null, amount: 7350, currency: 'INR', expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 0, total: 7350 } })
  })

  it('shows a visible, actionable error — not a silent dead end — when OS10 could not create a gateway order', async () => {
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })
    vi.mocked(api.createHold).mockResolvedValue({
      hold_id: 'h1', razorpay_order_id: null, razorpay_key_id: null, amount: 7350, currency: 'INR',
      expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 0, total: 7350 },
    })

    const result = await setUpAtGuestStep()
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    await act(async () => {
      await result.current.startPayment(vi.fn(), 'test_customer_token', true)
    })

    expect(loadRazorpayScript).not.toHaveBeenCalled()
    expect(result.current.state.payment).toBe('failed')
    expect(result.current.state.paymentUnavailable).toBe(true)
  })

  it('surfaces an expired hold (410) with a clear message telling the guest to search again', async () => {
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })
    vi.mocked(api.createHold).mockRejectedValue(new ApiError('This booking hold has expired. Please start a new booking.', 410))

    const result = await setUpAtGuestStep()
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    await act(async () => {
      await result.current.startPayment(vi.fn(), 'test_customer_token', true)
    })

    expect(result.current.state.payment).toBe('failed')
    expect(result.current.state.paymentError).toMatch(/expired/i)
  })

  // Correction 5: email must always reach OS10 non-empty. The three tests
  // above use setUpAtGuestStep()'s email: '' fixture deliberately (they're
  // testing unrelated hold/payment error paths) — proceedToPayment's own
  // validateGuestDetails gate is what actually prevents an empty email
  // from reaching this point in the real app, so it's exercised here via
  // the full step transition instead of by calling startPayment directly.
  it('always sends a non-empty guest_email to createHold once past the mandatory-email guest step', async () => {
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })
    vi.mocked(api.createHold).mockResolvedValue({
      hold_id: 'h1', razorpay_order_id: 'order_1', razorpay_key_id: 'rzp_test_1', amount: 7350, currency: 'INR',
      expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 0, total: 7350 },
    })

    const { result } = renderHook(() => useBookingFlow())
    act(() => {
      result.current.selectUnit(UNIT_A)
    })
    act(() => {
      result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' })
    })
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    await act(async () => {
      await result.current.proceedToPayment()
    })
    expect(result.current.state.step).toBe('payment') // the mandatory-email guard let it through

    await act(async () => {
      await result.current.startPayment(vi.fn(), 'test_customer_token', true)
    })

    expect(api.createHold).toHaveBeenCalledWith(expect.objectContaining({ guest_email: 'guest@example.com' }), 'test_customer_token')
  })

  it('carries an optional billing address and GSTIN into createHold when supplied (GSTIN normalized to uppercase), and omits them when not', async () => {
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })
    vi.mocked(api.createHold).mockResolvedValue({
      hold_id: 'h1', razorpay_order_id: 'order_1', razorpay_key_id: 'rzp_test_1', amount: 7350, currency: 'INR',
      expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 0, total: 7350 },
    })

    const { result } = renderHook(() => useBookingFlow())
    act(() => {
      result.current.selectUnit(UNIT_A)
    })
    act(() => {
      result.current.setGuestDetails({
        name: 'Test User', phone: '9876543210', email: 'guest@example.com',
        address: '12 MG Road, Chennai, Tamil Nadu - 600001', gstin: '33abcde1234f1z5',
      })
    })
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    await act(async () => {
      await result.current.proceedToPayment()
    })
    await act(async () => {
      await result.current.startPayment(vi.fn(), 'test_customer_token', true)
    })

    // Lowercase input must reach OS10 uppercased — GSTINs are conventionally all-caps.
    expect(api.createHold).toHaveBeenCalledWith(
      expect.objectContaining({ guest_address: '12 MG Road, Chennai, Tamil Nadu - 600001', guest_gstin: '33ABCDE1234F1Z5' }), 'test_customer_token',
    )
  })

  it('does not require a billing address or GSTIN to reach payment or complete createHold', async () => {
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })
    vi.mocked(api.createHold).mockResolvedValue({
      hold_id: 'h1', razorpay_order_id: 'order_1', razorpay_key_id: 'rzp_test_1', amount: 7350, currency: 'INR',
      expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 0, total: 7350 },
    })

    const { result } = renderHook(() => useBookingFlow())
    act(() => {
      result.current.selectUnit(UNIT_A)
    })
    act(() => {
      result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' })
    })
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    await act(async () => {
      await result.current.proceedToPayment()
    })
    expect(result.current.state.step).toBe('payment') // no address/GSTIN entered, still allowed through

    await act(async () => {
      await result.current.startPayment(vi.fn(), 'test_customer_token', true)
    })

    expect(api.createHold).toHaveBeenCalledWith(expect.objectContaining({ guest_address: undefined, guest_gstin: undefined }), 'test_customer_token')
  })

  it('proceedToPayment blocks advancing to the payment step when email is missing', async () => {
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })

    const result = await setUpAtGuestStep()
    act(() => { result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: '' }) })
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    await act(async () => {
      await result.current.proceedToPayment()
    })

    expect(result.current.state.step).toBe('guest') // did NOT advance
    expect(result.current.state.guestError).toMatch(/email/i)
    expect(api.createHold).not.toHaveBeenCalled()
  })
})

describe('useBookingFlow — promo code (Correction 4: OS10 stays the pricing authority)', () => {
  function setUpWithQuote() {
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })
    const { result } = renderHook(() => useBookingFlow())
    act(() => {
      result.current.selectUnit(UNIT_A)
    })
    return result
  }

  it('applies a valid promo code by re-quoting OS10 for the authoritative discounted total — never computing it locally', async () => {
    const result = setUpWithQuote()
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    vi.mocked(api.validatePromo).mockResolvedValue({ valid: true, discount_amount: 500, reason: null })
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 500, subtotal_after_discount: 6500, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 325,
      total: 6825, advance_amount: 6825, currency: 'INR', available: true,
    })

    await act(async () => {
      await result.current.applyPromo('SAVE500')
    })

    expect(api.validatePromo).toHaveBeenCalledWith('SAVE500', 7000)
    // Re-quoted with the code, not a client-side subtraction:
    expect(api.getQuote).toHaveBeenLastCalledWith(expect.objectContaining({ promo_code: 'SAVE500' }))
    expect(result.current.state.promoStatus).toBe('valid')
    expect(result.current.state.quote?.total).toBe(6825) // OS10's number, displayed as-is
    expect(result.current.state.promoMessage).toMatch(/500/)
  })

  it('rejects an invalid promo code and leaves the original total untouched', async () => {
    const result = setUpWithQuote()
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    vi.mocked(api.validatePromo).mockResolvedValue({ valid: false, discount_amount: 0, reason: 'This code has expired.' })

    await act(async () => {
      await result.current.applyPromo('EXPIRED')
    })

    expect(result.current.state.promoStatus).toBe('invalid')
    expect(result.current.state.promoMessage).toMatch(/expired/i)
    expect(result.current.state.quote?.total).toBe(7350) // unchanged — no re-quote happened
    expect(api.getQuote).toHaveBeenCalledTimes(1) // only the initial quote; no re-quote for an invalid code
  })

  it('carries a valid promo code into createHold so OS10 applies the same discount at booking time', async () => {
    vi.mocked(api.createHold).mockResolvedValue({
      hold_id: 'h1', razorpay_order_id: 'order_1', razorpay_key_id: 'rzp_test_1', amount: 6825, currency: 'INR',
      expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 500, total: 6825 },
    })

    const result = setUpWithQuote()
    await waitFor(() => expect(result.current.state.quote).not.toBeNull())

    vi.mocked(api.validatePromo).mockResolvedValue({ valid: true, discount_amount: 500, reason: null })
    vi.mocked(api.getQuote).mockResolvedValue({
      unit_id: UNIT_A.id, unit_name: UNIT_A.name, unit_type: UNIT_A.unit_type, check_in: '2099-01-01', check_out: '2099-01-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 500, subtotal_after_discount: 6500, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 325,
      total: 6825, advance_amount: 6825, currency: 'INR', available: true,
    })
    await act(async () => {
      await result.current.applyPromo('SAVE500')
    })

    act(() => {
      result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' })
    })
    await act(async () => {
      await result.current.proceedToPayment()
    })
    await act(async () => {
      await result.current.startPayment(vi.fn(), 'test_customer_token', true)
    })

    expect(api.createHold).toHaveBeenCalledWith(expect.objectContaining({ promo_code: 'SAVE500' }), 'test_customer_token')
  })
})

it('requires a current backend session before reusing a cancelled-payment hold', async () => {
  vi.mocked(api.getQuote).mockResolvedValue({ unit_id: UNIT_A.id, total: 7350 } as never)
  vi.mocked(api.createHold).mockResolvedValue({ hold_id: 'synthetic-hold', razorpay_order_id: 'order_synthetic', razorpay_key_id: 'rzp_test_synthetic', amount: 7350, currency: 'INR', expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 0, total: 7350 } })
  const { result } = renderHook(() => useBookingFlow())
  await act(async () => { await result.current.selectUnit(UNIT_A) })
  act(() => { result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' }) })
  await act(async () => { await result.current.proceedToPayment() })
  // With no Razorpay constructor installed in this unit test, the first attempt
  // stops before external payment; its reserved hold remains available for retry.
  await act(async () => { await result.current.startPayment(vi.fn(), 'synthetic-token', true) })
  vi.mocked(api.customerGetMe).mockRejectedValue(new ApiError('Session expired', 401))
  vi.mocked(loadRazorpayScript).mockClear()
  await act(async () => { await result.current.startPayment(vi.fn(), 'synthetic-token', true) })
  expect(api.customerGetMe).toHaveBeenCalledWith('synthetic-token')
  expect(api.createHold).toHaveBeenCalledTimes(1)
  expect(loadRazorpayScript).not.toHaveBeenCalled()
  expect(result.current.state.step).toBe('auth')
})


it('blocks payment and review navigation until customer details are submitted', async () => {
  vi.mocked(api.getQuote).mockResolvedValue({ unit_id: UNIT_A.id, total: 7350 } as never)
  const { result } = renderHook(() => useBookingFlow())
  await act(async () => { await result.current.selectUnit(UNIT_A) })
  act(() => { result.current.goToStep('payment') })
  expect(result.current.state.step).toBe('guest')
  await act(async () => { await result.current.startPayment(vi.fn(), 'synthetic-token', true) })
  expect(api.createHold).not.toHaveBeenCalled()
  expect(api.getPaymentAvailability).not.toHaveBeenCalled()
})

it('requires explicit terms acceptance even for a completed review', async () => {
  vi.mocked(api.getQuote).mockResolvedValue({ unit_id: UNIT_A.id, total: 7350 } as never)
  const { result } = renderHook(() => useBookingFlow())
  await act(async () => { await result.current.selectUnit(UNIT_A) })
  act(() => { result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' }) })
  await act(async () => { await result.current.proceedToPayment() })
  await act(async () => { await result.current.startPayment(vi.fn(), 'synthetic-token', false) })
  expect(result.current.state.paymentError).toMatch(/terms/i)
  expect(api.createHold).not.toHaveBeenCalled()
})

it('does not initiate or retry payment when the server says the provider is unavailable', async () => {
  vi.mocked(api.getQuote).mockResolvedValue({ unit_id: UNIT_A.id, total: 7350 } as never)
  vi.mocked(api.getPaymentAvailability).mockResolvedValue({ available: false })
  const { result } = renderHook(() => useBookingFlow())
  await act(async () => { await result.current.selectUnit(UNIT_A) })
  act(() => { result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' }) })
  await act(async () => { await result.current.proceedToPayment() })
  await act(async () => { await result.current.startPayment(vi.fn(), 'synthetic-token', true) })
  expect(result.current.state.paymentUnavailable).toBe(true)
  act(() => { result.current.resetPaymentError() })
  await act(async () => { await result.current.startPayment(vi.fn(), 'synthetic-token', true) })
  expect(api.createHold).not.toHaveBeenCalled()
  expect(loadRazorpayScript).not.toHaveBeenCalled()
  expect(result.current.state.confirmation).toBeNull()
})

it('opens secure checkout only after server availability and one authenticated order, without confirming locally', async () => {
  const previous = window.Razorpay
  const open = vi.fn()
  class SyntheticCheckout {
    open = open
    on = vi.fn()
  }
  window.Razorpay = SyntheticCheckout as unknown as typeof window.Razorpay
  try {
    vi.mocked(api.getQuote).mockResolvedValue({ unit_id: UNIT_A.id, total: 7350 } as never)
    vi.mocked(api.createHold).mockResolvedValue({ hold_id: 'synthetic-hold', razorpay_order_id: 'order_synthetic', razorpay_key_id: 'rzp_test_synthetic', amount: 7350, currency: 'INR', expires_at: '2099-01-01T00:15:00Z', quote_summary: { nights: 2, subtotal: 7000, discount_amount: 0, total: 7350 } })
    const { result } = renderHook(() => useBookingFlow())
    await act(async () => { await result.current.selectUnit(UNIT_A) })
    act(() => { result.current.setGuestDetails({ name: 'Test User', phone: '9876543210', email: 'guest@example.com' }) })
    await act(async () => { await result.current.proceedToPayment() })
    const confirmed = vi.fn()
    await act(async () => { await result.current.startPayment(confirmed, 'synthetic-token', true) })
    await act(async () => { await result.current.startPayment(confirmed, 'synthetic-token', true) })
    expect(api.getPaymentAvailability).toHaveBeenCalledWith('synthetic-token')
    expect(api.createHold).toHaveBeenCalledOnce()
    expect(open).toHaveBeenCalledOnce()
    expect(confirmed).not.toHaveBeenCalled()
    expect(result.current.state.confirmation).toBeNull()
    expect(result.current.state.step).toBe('payment')
  } finally {
    window.Razorpay = previous
  }
})
