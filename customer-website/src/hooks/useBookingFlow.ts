import { useCallback, useRef, useState } from 'react'
import * as api from '../lib/api'
import { ApiError } from '../lib/api'
import { validateDateRange, validateGuestDetails, normalizePhone } from '../lib/booking'
import { loadRazorpayScript, type RazorpayInstance } from '../lib/loadRazorpay'
import type { HoldOut, PublicBookingConfirmation, PublicQuoteOut, PublicUnitOut } from '../lib/types'

export type BookingStep = 'auth' | 'dates' | 'apartment' | 'guest' | 'payment' | 'confirmed'

export interface AvailableUnit {
  unit: PublicUnitOut
  estimatedTotal: number
}

export interface GuestDetails {
  name: string
  phone: string
  email: string
  /** Optional 2-letter GST state code — place-of-supply only, see CreateHoldRequest. */
  state?: string
  /** Optional billing address — free text, never required to complete a booking. */
  address?: string
  /** Optional GSTIN — free text, never required to complete a booking. */
  gstin?: string
}

interface State {
  step: BookingStep
  checkIn: string
  checkOut: string
  guests: number
  searching: boolean
  searchError: string | null
  availableUnits: AvailableUnit[]
  selectedUnit: PublicUnitOut | null
  quote: PublicQuoteOut | null
  quoteLoading: boolean
  quoteError: string | null
  promoCode: string
  promoStatus: 'idle' | 'checking' | 'valid' | 'invalid'
  promoMessage: string | null
  guestDetails: GuestDetails
  guestError: string | null
  detailsReviewed: boolean
  paymentUnavailable: boolean
  hold: HoldOut | null
  payment: 'idle' | 'creating_hold' | 'awaiting_payment' | 'verifying' | 'failed'
  paymentError: string | null
  confirmation: PublicBookingConfirmation | null
}

const initialState: State = {
  step: 'dates',
  checkIn: '',
  checkOut: '',
  guests: 2,
  searching: false,
  searchError: null,
  availableUnits: [],
  selectedUnit: null,
  quote: null,
  quoteLoading: false,
  quoteError: null,
  promoCode: '',
  promoStatus: 'idle',
  promoMessage: null,
  guestDetails: { name: '', phone: '', email: '', state: '', address: '', gstin: '' },
  guestError: null,
  detailsReviewed: false,
  paymentUnavailable: false,
  hold: null,
  payment: 'idle',
  paymentError: null,
  confirmation: null,
}

function apiErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback
  // Covers non-ApiError failures too — e.g. loadRazorpayScript()'s timeout/
  // network Error — so a specific, useful message isn't silently swapped
  // for the generic fallback.
  if (err instanceof Error && err.message) return err.message
  return fallback
}

const DRAFT_KEY = 'sri_booking_selection'
function restoreSelection(): State {
  try {
    const draft = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || 'null')
    if (draft?.selectedUnit?.id && typeof draft.checkIn === 'string' && typeof draft.checkOut === 'string' && Number.isInteger(draft.guests) && draft.guests > 0 && Date.now() - draft.savedAt < 86400000) {
      return { ...initialState, selectedUnit: draft.selectedUnit, checkIn: draft.checkIn, checkOut: draft.checkOut, guests: draft.guests, quote: draft.quote, step: 'auth' }
    }
  } catch { /* Invalid/unavailable storage starts a fresh search. */ }
  return initialState
}
export function useBookingFlow() {
  const [state, setState] = useState<State>(restoreSelection)
  // Guards against a second click firing a second hold/verify while the
  // first is still in flight (Step 18's "duplicate submission" case).
  const inFlight = useRef(false)
  const holdOwnerToken = useRef<string | null>(null)

  const searchAvailability = useCallback(async (checkIn: string, checkOut: string, guests: number) => {
    const validation = validateDateRange(checkIn, checkOut)
    if (!validation.valid) {
      setState((s) => ({ ...s, searchError: validation.message ?? 'Invalid dates.' }))
      return
    }

    sessionStorage.removeItem(DRAFT_KEY)
    setState((s) => ({ ...s, checkIn, checkOut, guests, selectedUnit: null, quote: null, hold: null, detailsReviewed: false, paymentUnavailable: false, searching: true, searchError: null }))
    try {
      const units = await api.getPublicUnits()
      const eligible = units.filter((u) => u.max_occupancy >= guests)
      const results = await Promise.all(
        eligible.map(async (unit) => {
          try {
            const avail = await api.getAvailability({ unit_id: unit.id, check_in: checkIn, check_out: checkOut })
            return avail.available ? { unit, estimatedTotal: avail.estimated_total } : null
          } catch {
            return null // one unit failing shouldn't blank the whole search
          }
        }),
      )
      const availableUnits = results.filter((r): r is AvailableUnit => r !== null)
      setState((s) => ({ ...s, searching: false, availableUnits, step: 'apartment' }))
    } catch (err) {
      setState((s) => ({ ...s, searching: false, searchError: apiErrorMessage(err, 'Could not check availability. Please try again.') }))
    }
  }, [])

  const selectUnit = useCallback(async (unit: PublicUnitOut, promoCode?: string) => {
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ selectedUnit: unit, checkIn: state.checkIn, checkOut: state.checkOut, guests: state.guests, quote: null, savedAt: Date.now() })) } catch { /* Storage may be disabled. */ }
    setState((s) => ({ ...s, selectedUnit: unit, quote: null, hold: null, detailsReviewed: false, paymentUnavailable: false, quoteLoading: true, quoteError: null, step: 'auth' }))
    try {
      const quote = await api.getQuote({
        unit_id: unit.id,
        check_in: state.checkIn,
        check_out: state.checkOut,
        promo_code: promoCode || undefined,
      })
      try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ selectedUnit: unit, checkIn: state.checkIn, checkOut: state.checkOut, guests: state.guests, quote, savedAt: Date.now() })) } catch { /* Booking still works without storage. */ }
      setState((s) => ({ ...s, quote, quoteLoading: false }))
    } catch (err) {
      setState((s) => ({ ...s, quoteLoading: false, quoteError: apiErrorMessage(err, 'Could not load pricing. Please try again.') }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.checkIn, state.checkOut, state.guests])

  const applyPromo = useCallback(
    async (code: string) => {
      if (!code.trim() || !state.selectedUnit || !state.quote) return
      setState((s) => ({ ...s, promoCode: code, promoStatus: 'checking', promoMessage: null }))
      try {
        const result = await api.validatePromo(code.trim(), state.quote.subtotal_before_discount)
        if (!result.valid) {
          setState((s) => ({ ...s, promoStatus: 'invalid', promoMessage: result.reason ?? 'This code is not valid.' }))
          return
        }
        // Re-quote with the promo applied — OS10 recomputes the authoritative total, we never do.
        const quote = await api.getQuote({
          unit_id: state.selectedUnit.id,
          check_in: state.checkIn,
          check_out: state.checkOut,
          promo_code: code.trim(),
        })
        setState((s) => ({ ...s, quote, promoStatus: 'valid', promoMessage: `Promo applied — ₹${result.discount_amount.toLocaleString('en-IN')} off` }))
      } catch (err) {
        setState((s) => ({ ...s, promoStatus: 'invalid', promoMessage: apiErrorMessage(err, 'Could not validate this code.') }))
      }
    },
    [state.selectedUnit, state.quote, state.checkIn, state.checkOut],
  )

  const setGuestDetails = useCallback((details: GuestDetails) => {
    setState((s) => ({ ...s, guestDetails: details, guestError: null, detailsReviewed: false, hold: null, step: s.step === 'payment' ? 'guest' : s.step }))
  }, [])

  const proceedToPayment = useCallback(async () => {
    const validation = validateGuestDetails(state.guestDetails)
    if (!validation.valid) {
      setState((s) => ({ ...s, guestError: validation.message ?? 'Please check your details.' }))
      return
    }
    if (!state.selectedUnit || state.quoteLoading) return
    setState((s) => ({ ...s, quoteLoading: true, guestError: null }))
    try {
      const quote = await api.getQuote({ unit_id: state.selectedUnit.id, check_in: state.checkIn, check_out: state.checkOut, promo_code: state.promoStatus === 'valid' ? state.promoCode : undefined, guest_state: state.guestDetails.state || undefined })
      if (quote.available === false) throw new Error('This apartment is no longer available. Please choose another apartment.')
      setState((s) => ({ ...s, quote, quoteLoading: false, step: 'payment', guestError: null, detailsReviewed: true }))
    } catch (err) {
      setState((s) => ({ ...s, quoteLoading: false, guestError: apiErrorMessage(err, 'Could not recheck your booking. Please try again.') }))
    }
  }, [state.guestDetails, state.selectedUnit, state.quoteLoading, state.checkIn, state.checkOut, state.promoStatus, state.promoCode])

  const startPayment = useCallback(
    async (onConfirmed: (confirmation: PublicBookingConfirmation) => void, token: string, termsAccepted = false) => {
      if (inFlight.current || !state.selectedUnit) return
      if (!token) { setState((s) => ({ ...s, step: 'auth', detailsReviewed: false })); return }
      if (state.step !== 'payment' || !state.detailsReviewed || !validateGuestDetails(state.guestDetails).valid) {
        setState((s) => ({ ...s, step: 'guest', guestError: 'Complete your customer details before reviewing payment.' }))
        return
      }
      if (!termsAccepted) { setState((s) => ({ ...s, paymentError: 'Accept the booking terms before paying.' })); return }
      if (state.paymentUnavailable) return
      inFlight.current = true
      setState((s) => ({ ...s, payment: 'creating_hold', paymentError: null }))

      try {
        const provider = await api.getPaymentAvailability(token)
        if (!provider.available) {
          setState((s) => ({ ...s, payment: 'idle', paymentUnavailable: true, paymentError: null }))
          inFlight.current = false
          return
        }
        const reuseHold = state.hold && holdOwnerToken.current === token && new Date(state.hold.expires_at).getTime() > Date.now()
        if (reuseHold) {
          const profile = await api.customerGetMe(token)
          if (!profile.email_verified_at && !profile.phone_verified_at) throw new ApiError('Please verify your contact details before payment.', 403)
        }
        const hold = reuseHold ? state.hold! : await api.createHold({
          terms_accepted: termsAccepted,
          unit_id: state.selectedUnit.id,
          check_in: state.checkIn,
          check_out: state.checkOut,
          guest_name: state.guestDetails.name.trim(),
          guest_phone: normalizePhone(state.guestDetails.phone),
          // Guaranteed non-empty by validateGuestDetails (Correction 5:
          // email is now mandatory before proceedToPayment allows this
          // step to be reached) — always sent, never omitted.
          guest_email: state.guestDetails.email.trim(),
          guest_state: state.guestDetails.state || undefined,
          guest_address: state.guestDetails.address?.trim() || undefined,
          guest_gstin: state.guestDetails.gstin?.trim().toUpperCase() || undefined,
          promo_code: state.promoStatus === 'valid' ? state.promoCode.trim() : undefined,
          // Correction 3: the guest count selected on the Dates step
          // (already captured in state.guests for unit-eligibility
          // filtering) must also reach the actual booking — sent exactly
          // as selected, no adult/child split invented.
          adults: state.guests,
        }, token)
        holdOwnerToken.current = token
        setState((s) => ({ ...s, hold, payment: 'awaiting_payment' }))

        if (!hold.razorpay_order_id || !hold.razorpay_key_id) {
          // Defensive: verified live against a real OS10 instance that a
          // misconfigured gateway actually makes createHold() reject with
          // 501 (handled in the catch block below) rather than resolve
          // with null fields — but the response type still allows null,
          // so this stays as a second line of defense against that case.
          setState((s) => ({ ...s, payment: 'failed', paymentUnavailable: true, paymentError: null }))
          inFlight.current = false
          return
        }

        if (state.quote && Math.abs(hold.amount - state.quote.total) > 0.01) {
          const quote = await api.getQuote({ unit_id: state.selectedUnit.id, check_in: state.checkIn, check_out: state.checkOut, promo_code: state.promoStatus === 'valid' ? state.promoCode : undefined, guest_state: state.guestDetails.state || undefined })
          setState((s) => ({ ...s, quote: { ...quote, total: hold.amount, advance_amount: hold.amount }, payment: 'failed', paymentError: 'The final price has changed. Review the updated total before paying.' }))
          inFlight.current = false
          return
        }
        await loadRazorpayScript()
        const rzp: RazorpayInstance = new window.Razorpay!({
          key: hold.razorpay_key_id, // from OS10's response — never a locally configured key
          amount: Math.round(hold.amount * 100),
          currency: hold.currency,
          name: 'Sri Apartment Homestay',
          description: `${state.selectedUnit.name} · ${hold.quote_summary.nights} night(s)`,
          order_id: hold.razorpay_order_id,
          prefill: { name: state.guestDetails.name, contact: normalizePhone(state.guestDetails.phone), email: state.guestDetails.email || undefined },
          theme: { color: '#0f3d3a' },
          modal: {
            ondismiss: () => {
              inFlight.current = false
              setState((s) => (s.payment === 'awaiting_payment' ? { ...s, payment: 'idle', paymentError: 'Payment was cancelled. You can try again — your hold is still active.' } : s))
            },
          },
          handler: (response) => {
            setState((s) => ({ ...s, payment: 'verifying' }))
            api
              .verifyPayment(hold.hold_id, {
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_order_id: response.razorpay_order_id,
                razorpay_signature: response.razorpay_signature,
              }, token)
              .then((confirmation) => {
                sessionStorage.removeItem(DRAFT_KEY)
                setState((s) => ({ ...s, payment: 'idle', confirmation, step: 'confirmed' }))
                onConfirmed(confirmation)
              })
              .catch((err: unknown) => {
                setState((s) => ({ ...s, step: err instanceof ApiError && [401, 403].includes(err.status) ? 'auth' : s.step, payment: 'failed', paymentError: apiErrorMessage(err, 'We could not verify your payment. If money was deducted, contact us with your phone number — do not pay again.') }))
              })
              .finally(() => {
                inFlight.current = false
              })
          },
        })
        rzp.on('payment.failed', (response) => {
          inFlight.current = false
          setState((s) => ({ ...s, payment: 'failed', paymentError: response.error?.description || 'Payment failed. Please try again.' }))
        })
        rzp.open()
      } catch (err) {
        inFlight.current = false
        const isExpired = err instanceof ApiError && err.status === 410
        const isConflict = err instanceof ApiError && err.status === 409
        // 501 = OS10 itself couldn't create a gateway order (Razorpay not
        // configured server-side). Its raw error message names env var
        // names — useful to an operator, not to a guest — so this is
        // deliberately not passed through via apiErrorMessage.
        const isGatewayNotConfigured = err instanceof ApiError && [501, 502, 503].includes(err.status)
        setState((s) => ({
          ...s,
          payment: 'failed',
          paymentUnavailable: isGatewayNotConfigured,
          step: err instanceof ApiError && [401, 403].includes(err.status) ? 'auth' : s.step,
          paymentError: isExpired
            ? 'This booking hold expired. Please search again.'
            : isConflict
              ? apiErrorMessage(err, 'This apartment is no longer available for these dates.')
              : isGatewayNotConfigured
                ? null
                : apiErrorMessage(err, 'Could not start payment. Please try again.'),
        }))
      }
    },
    [state.selectedUnit, state.checkIn, state.checkOut, state.guestDetails, state.promoStatus, state.promoCode, state.quote, state.hold, state.step, state.detailsReviewed, state.paymentUnavailable],
  )

  const resetPaymentError = useCallback(() => setState((s) => ({ ...s, payment: 'idle', paymentError: null })), [])

  const goToStep = useCallback((step: BookingStep) => setState((s) => {
    if (step === 'confirmed') return s // Only verified payment can confirm a booking.
    if (step === 'payment' && (!s.detailsReviewed || !validateGuestDetails(s.guestDetails).valid)) {
      return { ...s, step: 'guest', guestError: 'Complete your customer details before reviewing payment.' }
    }
    return { ...s, step, detailsReviewed: step === 'payment' ? s.detailsReviewed : false }
  }), [])

  return {
    state,
    searchAvailability,
    selectUnit,
    applyPromo,
    setGuestDetails,
    proceedToPayment,
    startPayment,
    resetPaymentError,
    goToStep,
  }
}
