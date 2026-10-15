import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useBookingFlow } from '../hooks/useBookingFlow'
import { useUnitPhotos } from '../hooks/useUnitPhotos'
import { formatDate, formatInr, todayIso } from '../lib/booking'
import { INDIAN_STATES } from '../lib/indianStates'
import { ResponsiveImage } from '../components/ResponsiveImage'
import { Helmet } from '../components/Helmet'
import { useCustomerAuth } from '../context/CustomerAuthContext'
import { ApiError, customerGetMe, getPaymentAvailability, getPublicBusinessInfo } from '../lib/api'

/** Passed via router state by the homepage's availability widget — the
 * SAME booking flow, just pre-filled, not a second one. See HomePage.tsx. */
export interface BookPagePrefill {
  checkIn?: string
  checkOut?: string
  guests?: number
}

export function BookPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const prefill = location.state as BookPagePrefill | null
  const flow = useBookingFlow()
  const { state } = flow
  const { token, isInitializing, customerLogout, updateCustomer } = useCustomerAuth()
  const [validatedToken, setValidatedToken] = useState<string | null>(null)
  const [authError, setAuthError] = useState<string | null>(null)
  const [checkingSession, setCheckingSession] = useState(false)
  const protectedStep = ['auth', 'guest', 'payment'].includes(state.step)
  useEffect(() => {
    if (!protectedStep || isInitializing) return
    let active = true
    setValidatedToken(null)
    setAuthError(null)
    setCheckingSession(false)
    if (!token) return
    setCheckingSession(true)
    customerGetMe(token).then((profile) => {
      if (!active) return
      if (!profile.email_verified_at && !profile.phone_verified_at) {
        setAuthError('Please log in and complete contact verification before continuing.')
        return
      }
      updateCustomer(profile)
      setValidatedToken(token)
      if (state.step === 'auth') {
        flow.setGuestDetails({ ...state.guestDetails, name: state.guestDetails.name || `${profile.first_name} ${profile.last_name}`.trim(), phone: profile.phone || '', email: profile.email })
        flow.goToStep('guest')
      }
    }).catch((err: unknown) => {
      if (!active) return
      setAuthError('Your session could not be verified. Please log in again or retry.')
      if (err instanceof ApiError && err.status === 401) void customerLogout()
    }).finally(() => { if (active) setCheckingSession(false) })
    return () => { active = false }
    // Revalidate at every protected step, including after an expired payment request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, isInitializing, state.step])
  const canContinue = !!token && validatedToken === token


  useEffect(() => {
    // Auto-run the search once, only when the homepage widget actually
    // supplied complete dates — never on a plain visit to /book.
    if (prefill?.checkIn && prefill?.checkOut) {
      flow.searchAvailability(prefill.checkIn, prefill.checkOut, prefill.guests ?? 2)
    } else if (state.selectedUnit && !state.quote) {
      // A fast redirect can happen before the initial quote request completed.
      void flow.selectUnit(state.selectedUnit)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
      <Helmet
        title="Book Now — Sri Apartment Homestay Pondicherry"
        description="Check availability and book direct with Sri Apartment Homestay, Puducherry — instant confirmation, no OTA fees."
        canonicalPath="/book"
      />
      <Steps current={state.step} />

      {state.step === 'dates' && <DatesStep flow={flow} />}
      {state.step === 'apartment' && <ApartmentStep flow={flow} />}
      {protectedStep && !canContinue && (
        <section className="mx-auto max-w-md space-y-5 rounded-xl border border-brand-border bg-brand-cream-2 p-6 text-center">
          <h1 className="font-serif text-3xl text-brand-green">Log In / Sign Up</h1>
          <p className="text-sm text-brand-muted">Your apartment selection is saved. Sign in and verify your contact details to continue your booking.</p>
          {state.selectedUnit && <p className="font-semibold">{state.selectedUnit.name} · {formatDate(state.checkIn)} – {formatDate(state.checkOut)} · {state.guests} guests</p>}
          {isInitializing || checkingSession ? <p role="status">Checking your session…</p> : <>
            {authError && <p role="alert" className="text-sm text-red-700">{authError}</p>}
            <Link className="block rounded bg-brand-green px-5 py-3 font-semibold text-brand-gold" to="/login?returnTo=/book" onClick={() => { if (authError && token) void customerLogout() }}>Log In</Link>
            <Link className="block rounded border border-brand-border px-5 py-3 font-semibold text-brand-green" to="/signup?returnTo=/book">Create Account</Link>
          </>}
          {state.quote && <QuoteSummary quote={state.quote} />}
          <button type="button" className="text-sm underline" onClick={() => flow.goToStep('apartment')}>← Back to apartments</button>
        </section>
      )}
      {state.step === 'guest' && canContinue && <GuestStep flow={flow} />}
      {state.step === 'payment' && state.detailsReviewed && canContinue && (
        <PaymentStep
          flow={flow}
          token={token!}
          onConfirmed={(confirmation) => navigate('/booking/confirmation', { state: { confirmation, unitName: state.selectedUnit?.name } })}
        />
      )}
    </div>
  )
}

const STEP_LABELS = { auth: 'Sign In', dates: 'Dates', apartment: 'Apartment', guest: 'Your Details', payment: 'Review & Pay', confirmed: 'Confirmed' } as const

function Steps({ current }: { current: keyof typeof STEP_LABELS }) {
  const order: (keyof typeof STEP_LABELS)[] = ['dates', 'apartment', 'auth', 'guest', 'payment']
  const currentIndex = order.indexOf(current)
  return (
    <ol className="mb-10 grid grid-cols-5 gap-2 text-center text-[10px] font-semibold sm:text-xs text-brand-muted uppercase" aria-label="Booking progress">
      {order.map((step, i) => (
        <li key={step} aria-current={step === current ? 'step' : undefined} className={i <= currentIndex ? 'text-brand-gold-4' : ''}>
          {STEP_LABELS[step]}
        </li>
      ))}
    </ol>
  )
}

function DatesStep({ flow }: { flow: ReturnType<typeof useBookingFlow> }) {
  const { state, searchAvailability } = flow
  const [checkIn, setCheckIn] = useState(state.checkIn)
  const [checkOut, setCheckOut] = useState(state.checkOut)
  const [guests, setGuests] = useState(state.guests)

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault()
        searchAvailability(checkIn, checkOut, guests)
      }}
    >
      <h1 className="font-serif text-3xl text-brand-green">Check Availability</h1>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">Check-in</span>
          <input type="date" required min={todayIso()} value={checkIn} onChange={(e) => setCheckIn(e.target.value)} className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2" />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">Check-out</span>
          <input type="date" required min={checkIn || todayIso()} value={checkOut} onChange={(e) => setCheckOut(e.target.value)} className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2" />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">Guests</span>
          <select value={guests} onChange={(e) => setGuests(Number(e.target.value))} className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2">
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n} Guest{n > 1 ? 's' : ''}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.searchError && (
        <p role="alert" className="rounded bg-red-50 px-4 py-3 text-sm text-red-700">
          {state.searchError}
        </p>
      )}

      <button type="submit" disabled={state.searching} className="w-full rounded bg-brand-gold py-3 font-bold tracking-wide text-brand-green-3 uppercase disabled:opacity-50">
        {state.searching ? 'Checking availability…' : 'Check Availability →'}
      </button>
    </form>
  )
}

function ApartmentStep({ flow }: { flow: ReturnType<typeof useBookingFlow> }) {
  const { state, selectUnit, goToStep } = flow
  useEffect(() => {
    // Returning from a restored draft needs fresh availability, not an empty result list.
    if (state.availableUnits.length === 0 && state.selectedUnit) {
      void flow.searchAvailability(state.checkIn, state.checkOut, state.guests)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.selectedUnit?.id])
  // Memoized so useUnitPhotos' effect only re-runs when the actual
  // available-units list changes, not on every render.
  const units = useMemo(() => state.availableUnits.map((a) => a.unit), [state.availableUnits])
  const photosByUnit = useUnitPhotos(units)

  if (state.searching) return <p role="status" className="py-12 text-center text-brand-muted">Checking availability…</p>
  if (state.availableUnits.length === 0) {
    return (
      <div className="space-y-4 text-center">
        <p className="text-lg text-brand-ink-2">No apartments are available for those dates. Please try different dates.</p>
        <button type="button" onClick={() => goToStep('dates')} className="rounded bg-brand-green px-5 py-2 text-sm font-semibold text-brand-gold uppercase">
          ← Change dates
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-brand-muted">
        {state.availableUnits.length} apartment{state.availableUnits.length > 1 ? 's' : ''} available · {formatDate(state.checkIn)} → {formatDate(state.checkOut)}
      </p>
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        {state.availableUnits.map(({ unit, estimatedTotal }) => {
          const photo = photosByUnit[unit.id]?.[0]
          return (
            <button
              key={unit.id}
              type="button"
              onClick={() => selectUnit(unit, undefined)}
              className="overflow-hidden rounded-lg border border-brand-border bg-brand-cream-2 text-left transition hover:border-brand-gold"
            >
              {photo && <ResponsiveImage src={photo.src} alt={photo.alt} width={400} height={280} className="h-44 w-full object-cover" />}
              <div className="p-4">
                <p className="font-serif text-xl text-brand-green">{unit.name}</p>
                <p className="text-sm text-brand-muted">Up to {unit.max_occupancy} guests</p>
                <p className="mt-2 text-sm font-semibold text-brand-green">Select & Continue →</p>
                <p className="mt-2 font-serif text-lg text-brand-gold-4">{formatInr(estimatedTotal)} <span className="text-xs text-brand-muted">total (estimate)</span></p>
              </div>
            </button>
          )
        })}
      </div>
      <button type="button" onClick={() => goToStep('dates')} className="text-sm text-brand-muted underline">
        ← Change dates
      </button>
    </div>
  )
}

function GuestStep({ flow }: { flow: ReturnType<typeof useBookingFlow> }) {
  const { state, setGuestDetails, proceedToPayment, applyPromo, goToStep } = flow
  const [promoInput, setPromoInput] = useState('')

  if (state.quoteLoading) return <p className="py-12 text-center text-brand-muted">Loading pricing…</p>
  if (state.quoteError) {
    return (
      <div className="space-y-4 text-center">
        <p role="alert" className="text-red-700">{state.quoteError}</p>
        <button type="button" onClick={() => goToStep('apartment')} className="rounded bg-brand-green px-5 py-2 text-sm font-semibold text-brand-gold uppercase">
          ← Back
        </button>
      </div>
    )
  }
  const quote = state.quote
  if (!quote) return null

  return (
    <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          void proceedToPayment()
        }}
      >
        <h1 className="font-serif text-2xl text-brand-green">Your Details</h1>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">Full name *</span>
          <input required value={state.guestDetails.name} onChange={(e) => setGuestDetails({ ...state.guestDetails, name: e.target.value })} className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2" />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">Mobile number *</span>
          <input required readOnly value={state.guestDetails.phone} onChange={(e) => setGuestDetails({ ...state.guestDetails, phone: e.target.value })} className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2" />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">Email *</span>
          <input required readOnly type="email" value={state.guestDetails.email} onChange={(e) => setGuestDetails({ ...state.guestDetails, email: e.target.value })} className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2" />
        </label>
        <p className="text-xs text-brand-muted">Contact details come from your account. <Link to="/account/edit" className="underline">Update your profile</Link> to change them.</p>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">State (optional)</span>
          <select value={state.guestDetails.state ?? ''} onChange={(e) => setGuestDetails({ ...state.guestDetails, state: e.target.value })} className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2">
            <option value="">Select your state (helps us apply the correct GST)</option>
            {INDIAN_STATES.map((s) => (
              <option key={s.code} value={s.code}>{s.name}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">Billing address (optional)</span>
          <textarea
            rows={2}
            maxLength={500}
            placeholder="For your invoice, if you'd like one"
            value={state.guestDetails.address ?? ''}
            onChange={(e) => setGuestDetails({ ...state.guestDetails, address: e.target.value })}
            className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-bold tracking-wide text-brand-muted uppercase">GSTIN (optional)</span>
          <input
            maxLength={20}
            placeholder="For a business invoice, if applicable"
            value={state.guestDetails.gstin ?? ''}
            onChange={(e) => setGuestDetails({ ...state.guestDetails, gstin: e.target.value })}
            className="w-full rounded border border-brand-border bg-brand-cream-2 px-3 py-2 uppercase"
          />
        </label>

        <div className="flex gap-2">
          <input placeholder="Promo code" value={promoInput} onChange={(e) => setPromoInput(e.target.value)} className="flex-1 rounded border border-brand-border bg-brand-cream-2 px-3 py-2 text-sm uppercase" />
          <button type="button" onClick={() => applyPromo(promoInput)} disabled={state.promoStatus === 'checking'} className="rounded bg-brand-green px-4 text-xs font-bold text-brand-gold uppercase">
            Apply
          </button>
        </div>
        {state.promoMessage && <p className={state.promoStatus === 'valid' ? 'text-sm text-green-700' : 'text-sm text-red-700'}>{state.promoMessage}</p>}

        {state.guestError && (
          <p role="alert" className="rounded bg-red-50 px-4 py-3 text-sm text-red-700">
            {state.guestError}
          </p>
        )}

        <button type="submit" className="w-full rounded bg-brand-gold py-3 font-bold tracking-wide text-brand-green-3 uppercase">
          Review Booking →
        </button>
        <button type="button" onClick={() => goToStep('apartment')} className="w-full text-center text-sm text-brand-muted underline">
          ← Back
        </button>
      </form>

      <QuoteSummary quote={quote} promoCode={state.promoStatus === 'valid' ? state.promoCode : undefined} />
    </div>
  )
}

export function QuoteSummary({ quote, promoCode }: { quote: import('../lib/types').PublicQuoteOut; promoCode?: string }) {
  const [nightlyBreakdownOpen, setNightlyBreakdownOpen] = useState(false)

  return (
    // pr-20 below md: this card is full-width there (grid-cols-1), and its
    // right-aligned amounts would otherwise sit under the fixed WhatsApp
    // button (bottom-6 right-6, 56px) — confirmed via live layout testing
    // at ~700px. md:pr-6 restores normal padding once the 2-col grid
    // narrows the card well clear of that corner.
    <aside className="h-fit rounded-lg bg-brand-green p-6 pr-20 text-white md:pr-6">
      <p className="font-serif text-lg">{quote.unit_name}</p>
      <p className="text-sm text-white/50">{quote.nights} night{quote.nights > 1 ? 's' : ''}</p>
      <dl className="mt-4 space-y-2 border-t border-white/10 pt-4 text-sm">
        <div>
          <div className="flex items-center justify-between gap-3 text-white/70">
            <dt>Room total</dt>
            <dd className="flex items-center gap-2 text-right text-white">
              <span>{formatInr(quote.subtotal_before_discount)}</span>
              {quote.daily_breakdown.length > 0 && (
                <button
                  type="button"
                  aria-expanded={nightlyBreakdownOpen}
                  aria-label={`${nightlyBreakdownOpen ? 'Hide' : 'Show'} nightly price breakdown`}
                  onClick={() => setNightlyBreakdownOpen((open) => !open)}
                  className="rounded p-1 text-brand-gold transition-transform duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-gold"
                >
                  <span aria-hidden="true" className={`inline-block transition-transform duration-200 ${nightlyBreakdownOpen ? 'rotate-180' : ''}`}>⌄</span>
                </button>
              )}
            </dd>
          </div>
          {nightlyBreakdownOpen && (
            <div className="mt-2 space-y-2 border-l border-white/15 pl-3 text-xs text-white/65">
              {quote.daily_breakdown.map((night) => (
                <div key={night.the_date} className="flex min-w-0 items-start justify-between gap-3">
                  <span className="min-w-0">{formatNightDate(night.the_date)}</span>
                  <span className="shrink-0 text-right text-white">{formatInr(night.rate)}</span>
                </div>
              ))}
              <div className="flex justify-between gap-3 border-t border-white/10 pt-2 font-medium text-white/85">
                <span>Room total ({quote.nights} night{quote.nights === 1 ? '' : 's'})</span>
                <span className="shrink-0 text-right">{formatInr(quote.subtotal_before_discount)}</span>
              </div>
            </div>
          )}
        </div>
        {quote.long_stay_discount_amount > 0 && <Row label={`Long-stay discount (${quote.long_stay_discount_percent}%)`} value={`− ${formatInr(quote.long_stay_discount_amount)}`} />}
        {quote.promo_discount_amount > 0 && <Row label={promoCode ? `Promo code: ${promoCode}` : 'Promo discount'} value={`− ${formatInr(quote.promo_discount_amount)}`} />}
        <Row label={`GST (${quote.gst_rate_percent}%)`} value={formatInr(quote.cgst + quote.sgst + quote.igst)} />
      </dl>
      <div className="mt-4 flex items-baseline justify-between border-t border-white/10 pt-4">
        <span className="text-sm text-white/60">Total Stay Amount</span>
        <span className="font-serif text-2xl text-brand-gold">{formatInr(quote.total)}</span>
      </div>
      <p className="mt-2 text-xs text-white/40">Taxes included. Availability and the final price are checked before payment.</p>
    </aside>
  )
}

function formatNightDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString('en-IN', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  })
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 text-white/70">
      <span className="min-w-0">{label}</span>
      <span className="shrink-0 text-right text-white">{value}</span>
    </div>
  )
}

function PaymentStep({ flow, onConfirmed, token }: { token: string; flow: ReturnType<typeof useBookingFlow>; onConfirmed: (c: import('../lib/types').PublicBookingConfirmation) => void }) {
  const { state, startPayment, resetPaymentError, goToStep } = flow
  const [termsAccepted, setTermsAccepted] = useState(false)
  const [provider, setProvider] = useState<'checking' | 'available' | 'unavailable' | 'error'>('checking')
  const [whatsappUrl, setWhatsappUrl] = useState<string | null>(null)
  const { customerLogout } = useCustomerAuth()
  useEffect(() => {
    let active = true
    setProvider('checking')
    getPaymentAvailability(token).then((status) => {
      if (active) setProvider(status.available ? 'available' : 'unavailable')
    }).catch((err: unknown) => {
      if (!active) return
      setProvider('error')
      if (err instanceof ApiError && [401, 403].includes(err.status)) {
        void customerLogout()
        flow.goToStep('auth')
      }
    })
    // Only an explicitly configured, validated business number becomes a CTA.
    // No fallback phone number is substituted on failure or missing config.
    getPublicBusinessInfo().then((info) => {
      const number = info.whatsapp_business_number?.trim().replace(/^\+/, '')
      if (active && number && /^[1-9]\d{7,14}$/.test(number)) setWhatsappUrl(`https://wa.me/${number}`)
    }).catch(() => {})
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])
  useEffect(() => setTermsAccepted(false), [state.quote?.total])
  const quote = state.quote
  if (!quote) return null

  const canPay = provider === 'available' && !state.paymentUnavailable
  const busy = state.payment === 'creating_hold' || state.payment === 'awaiting_payment' || state.payment === 'verifying'

  return (
    <div className="mx-auto max-w-md space-y-6 text-center">
      <h1 className="font-serif text-2xl text-brand-green">Booking Review</h1>
      <p>{state.selectedUnit?.name} · {formatDate(state.checkIn)} – {formatDate(state.checkOut)} · {state.guests} guests</p>
      <QuoteSummary quote={quote} />
      <p className="text-sm text-brand-muted">Check-in: 12:30 PM · Check-out: 11:00 AM</p>
      <p className="text-sm text-brand-muted">Please review our <Link to="/terms" className="underline">booking and cancellation policies</Link> before paying.</p>
      <label className="flex items-start gap-3 text-left text-sm"><input type="checkbox" checked={termsAccepted} onChange={(e) => setTermsAccepted(e.target.checked)} />I accept the booking terms and cancellation policy.</label>
      <p className="font-serif text-3xl text-brand-gold-4">{formatInr(quote.total)}</p>
      {canPay && <p className="text-sm text-brand-muted">Full payment is collected upfront to confirm your booking.</p>}

      {canPay && state.paymentError && (
        <p role="alert" className="rounded bg-red-50 px-4 py-3 text-sm text-red-700">
          {state.paymentError}
        </p>
      )}

      {!canPay && <section className="space-y-4 rounded-lg border border-brand-border bg-brand-cream-2 p-5" aria-live="polite">
        <h2 className="font-serif text-xl text-brand-green">{provider === 'checking' ? 'Checking online payment availability…' : 'Online payment is currently unavailable'}</h2>
        {provider !== 'checking' && <>
          <p className="text-sm text-brand-muted">{provider === 'error' ? 'We could not check online payment availability. Please try again later.' : 'You can review your stay, but online payment cannot be taken at the moment.'} Your booking has not been confirmed and no payment has been taken.</p>
          {whatsappUrl && <a href={whatsappUrl} target="_blank" rel="noopener noreferrer" className="block rounded bg-brand-green px-5 py-3 font-semibold text-brand-gold">Contact on WhatsApp</a>}
        </>}
      </section>}
      {canPay && <button
        type="button"
        disabled={busy || !termsAccepted}
        onClick={() => {
          resetPaymentError()
          void startPayment(onConfirmed, token, termsAccepted)
        }}
        className="w-full rounded bg-green-700 py-4 text-sm font-bold tracking-widest text-white uppercase disabled:opacity-60"
      >
        {state.payment === 'creating_hold' && 'Reserving your apartment…'}
        {state.payment === 'awaiting_payment' && 'Waiting for payment…'}
        {state.payment === 'verifying' && 'Confirming your booking…'}
        {(state.payment === 'idle' || state.payment === 'failed') && '🔒 Pay Securely'}
      </button>}
      <button type="button" onClick={() => goToStep('guest')} disabled={busy} className="text-sm text-brand-muted underline disabled:opacity-40">
        ← Back
      </button>
    </div>
  )
}
