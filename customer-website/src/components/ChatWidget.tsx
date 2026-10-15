import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getPublicUnits, getAvailability, getQuote, ApiError } from '../lib/api'
import { todayIso, validateDateRange, formatInr } from '../lib/booking'
import { getApartmentContent, normalizeUnitName, MAX_GUESTS_DISPLAY } from '../content/apartments'
import { NEARBY_ATTRACTIONS, PROPERTY_ADDRESS, PROPERTY_DIRECTIONS_URL, type NearbyAttraction } from '../content/location'
import { matchIntent, matchAttraction, answerAmenityQuestion, STATIC_ANSWERS, FALLBACK_TEXT } from '../lib/chatIntent'
import { useBusinessContact } from '../hooks/useBusinessContact'
import { useBookingBarVisible } from '../hooks/useBookingBarVisibility'
import type { PublicQuoteOut } from '../lib/types'

const OS10_DOWN_TEXT = "I couldn't confirm live information right now. Please contact us on WhatsApp and we'll help you."
const OS10_DOWN_PRICE_TEXT = "I couldn't confirm the live price right now. Please contact us on WhatsApp and we'll help you."

interface AvailabilityUnitResult {
  name: string
  code: string
  total: number
}

interface ChatMessage {
  id: string
  role: 'user' | 'bot'
  kind: 'text' | 'attraction' | 'attraction_list' | 'property_location' | 'availability_form' | 'availability_result' | 'price_form' | 'price_result' | 'fallback'
  text?: string
  attraction?: NearbyAttraction
  availabilityResult?: { checkIn: string; checkOut: string; guests: number; units: AvailabilityUnitResult[] }
  priceResult?: { unitCode: string; quote: PublicQuoteOut }
}

let nextId = 0
function newId(): string {
  nextId += 1
  return `m${nextId}`
}

// ---------------------------------------------------------------------------
// Small line icons — consistent with the stroke-icon style already used
// elsewhere on the Home page (Phase 1/2), self-contained here since this
// component isn't co-located with HomePage.tsx's icon set.
// ---------------------------------------------------------------------------

function IconChat() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 10a6.5 6.5 0 0 1-9.7 5.66L3 17l1.34-4.3A6.5 6.5 0 1 1 17 10Z" />
      <path d="M6.5 9.5h7M6.5 12h4.5" />
    </svg>
  )
}
function IconClose() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 5l10 10M15 5 5 15" />
    </svg>
  )
}
function IconSend() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 3 3 9.5l6 2.5M17 3l-5.5 14-2.5-6M17 3 8.5 12" />
    </svg>
  )
}

const QUICK_ACTIONS: { label: string; action: string }[] = [
  { label: 'Check Availability', action: 'availability' },
  { label: 'Room & Amenities', action: 'amenities' },
  { label: 'Check-in & Check-out', action: 'checkinout' },
  { label: 'Cancellation Policy', action: 'cancellation' },
  { label: 'Nearby Places', action: 'nearby' },
  { label: 'Get Directions', action: 'directions' },
  { label: 'Talk to Us on WhatsApp', action: 'whatsapp' },
]

export function ChatWidget() {
  const { whatsappNumber } = useBusinessContact()
  const whatsappHref = whatsappNumber ? `https://wa.me/${whatsappNumber}` : null
  const [open, setOpen] = useState(false)
  // Mobile only (below sm, via max-sm:): step aside while the homepage
  // booking bar is on screen, so the launcher never covers its fields or
  // submit button. Never hidden while the chat is open — it's the close
  // button then.
  const hideLauncherOnMobile = useBookingBarVisible() && !open
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const toggleButtonRef = useRef<HTMLButtonElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  function pushMessage(msg: Omit<ChatMessage, 'id'>) {
    setMessages((prev) => [...prev, { ...msg, id: newId() }])
  }

  useEffect(() => {
    if (open && messages.length === 0) {
      pushMessage({
        role: 'bot',
        kind: 'text',
        text: "Hi! I'm Ask Sri Apartment, here to help with check-in times, nearby places, availability, and pricing. What would you like to know?",
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (open) {
      inputRef.current?.focus()
    }
  }, [open])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        toggleButtonRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  function respondToText(text: string) {
    const intent = matchIntent(text)

    if (intent === 'attraction') {
      const attraction = matchAttraction(text)
      if (attraction) {
        pushMessage({ role: 'bot', kind: 'attraction', attraction })
        return
      }
    }

    if (intent === 'property_location') {
      pushMessage({ role: 'bot', kind: 'property_location' })
      return
    }

    if (intent === 'amenity') {
      const { confirmed, text: answer } = answerAmenityQuestion(text)
      if (confirmed) {
        pushMessage({ role: 'bot', kind: 'text', text: answer })
      } else {
        pushMessage({ role: 'bot', kind: 'fallback' })
      }
      return
    }

    if (intent === 'availability') {
      pushMessage({ role: 'bot', kind: 'availability_form' })
      return
    }

    if (intent === 'price') {
      pushMessage({ role: 'bot', kind: 'price_form' })
      return
    }

    if (intent === 'whatsapp') {
      pushMessage({ role: 'bot', kind: 'fallback', text: 'Sure — you can reach our team directly on WhatsApp:' })
      return
    }

    const staticAnswer = STATIC_ANSWERS[intent]
    if (staticAnswer) {
      pushMessage({ role: 'bot', kind: 'text', text: staticAnswer })
      return
    }

    pushMessage({ role: 'bot', kind: 'fallback' })
  }

  function handleSend() {
    const text = input.trim()
    if (!text) return
    pushMessage({ role: 'user', kind: 'text', text })
    setInput('')
    respondToText(text)
  }

  function handleQuickAction(action: string) {
    switch (action) {
      case 'availability':
        pushMessage({ role: 'bot', kind: 'availability_form' })
        break
      case 'amenities':
        pushMessage({
          role: 'bot',
          kind: 'text',
          text: `Every Sri Apartment unit has 2 AC bedrooms, 2 en-suite bathrooms, a full kitchen, free WiFi, and free parking — and sleeps up to ${MAX_GUESTS_DISPLAY} guests.`,
        })
        break
      case 'checkinout':
        pushMessage({ role: 'bot', kind: 'text', text: `${STATIC_ANSWERS.checkin_time} ${STATIC_ANSWERS.checkout_time}` })
        break
      case 'cancellation':
        pushMessage({ role: 'bot', kind: 'text', text: STATIC_ANSWERS.cancellation })
        break
      case 'nearby':
      case 'directions':
        pushMessage({ role: 'bot', kind: 'attraction_list' })
        break
      case 'whatsapp': {
        pushMessage({ role: 'bot', kind: 'fallback', text: 'Sure — you can reach our team directly on WhatsApp:' })
        if (whatsappHref) window.open(whatsappHref, '_blank', 'noreferrer')
        break
      }
    }
  }

  async function submitAvailability(checkIn: string, checkOut: string, guests: number) {
    const validation = validateDateRange(checkIn, checkOut)
    if (!validation.valid) {
      pushMessage({ role: 'bot', kind: 'text', text: validation.message ?? 'Please check your dates.' })
      return
    }
    try {
      const units = await getPublicUnits()
      const eligible = units.filter((u) => u.max_occupancy >= guests)
      const results = await Promise.all(
        eligible.map(async (unit) => {
          try {
            const avail = await getAvailability({ unit_id: unit.id, check_in: checkIn, check_out: checkOut })
            if (!avail.available) return null
            const code = getApartmentContent(unit.name)?.code ?? unit.name
            return { name: unit.name, code, total: avail.estimated_total } as AvailabilityUnitResult
          } catch {
            return null
          }
        }),
      )
      const available = results.filter((r): r is AvailabilityUnitResult => r !== null)
      pushMessage({ role: 'bot', kind: 'availability_result', availabilityResult: { checkIn, checkOut, guests, units: available } })
    } catch {
      pushMessage({ role: 'bot', kind: 'fallback', text: OS10_DOWN_TEXT })
    }
  }

  async function submitPrice(unitCode: string, checkIn: string, checkOut: string) {
    const validation = validateDateRange(checkIn, checkOut)
    if (!validation.valid) {
      pushMessage({ role: 'bot', kind: 'text', text: validation.message ?? 'Please check your dates.' })
      return
    }
    try {
      const units = await getPublicUnits()
      const unit = units.find((u) => normalizeUnitName(u.name) === normalizeUnitName(unitCode))
      if (!unit) {
        pushMessage({ role: 'bot', kind: 'text', text: `I don't recognize "${unitCode}" — please choose A1, A2, B3, B4, C5, or C6.` })
        return
      }
      const quote = await getQuote({ unit_id: unit.id, check_in: checkIn, check_out: checkOut })
      pushMessage({ role: 'bot', kind: 'price_result', priceResult: { unitCode, quote } })
    } catch (err) {
      pushMessage({ role: 'bot', kind: 'fallback', text: err instanceof ApiError ? OS10_DOWN_PRICE_TEXT : OS10_DOWN_PRICE_TEXT })
    }
  }

  return (
    <>
      <button
        ref={toggleButtonRef}
        type="button"
        aria-label={open ? 'Close Ask Sri Apartment chat' : 'Open Ask Sri Apartment chat'}
        aria-expanded={open}
        aria-controls="ask-sri-apartment-panel"
        onClick={() => setOpen((v) => !v)}
        // Icon-only (compact circle) below sm, full icon+label pill from sm
        // up. On mobile it also hides while the homepage booking bar is
        // visible (hideLauncherOnMobile) — `invisible` removes it from the
        // tab order and accessibility tree, not just from view.
        className={`fixed right-6 bottom-6 z-40 flex items-center gap-2 rounded-full bg-brand-green p-4 text-sm font-bold tracking-wide text-brand-gold shadow-lg transition hover:bg-brand-green-3 sm:px-5 sm:py-3 ${
          hideLauncherOnMobile ? 'max-sm:invisible max-sm:opacity-0' : ''
        }`}
      >
        <IconChat />
        <span className="hidden sm:inline">Ask Sri Apartment</span>
      </button>

      {open && (
        <div
          id="ask-sri-apartment-panel"
          role="dialog"
          aria-label="Ask Sri Apartment chat"
          className="fixed right-4 bottom-24 z-40 flex max-h-[70vh] w-[calc(100vw-2rem)] max-w-sm flex-col overflow-hidden rounded-lg border border-brand-border bg-white shadow-2xl sm:right-6"
        >
          <div className="flex items-center justify-between bg-brand-green px-4 py-3">
            <p className="font-serif text-base text-brand-gold">Ask Sri Apartment</p>
            <button
              type="button"
              aria-label="Close chat"
              onClick={() => {
                setOpen(false)
                toggleButtonRef.current?.focus()
              }}
              className="flex h-7 w-7 items-center justify-center rounded-full text-white/80 hover:bg-white/10 hover:text-white"
            >
              <IconClose />
            </button>
          </div>

          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
            {messages.map((m, i) => (
              <MessageBubble key={m.id} message={m} showQuickActions={i === 0 && m.role === 'bot'} onQuickAction={handleQuickAction} onSubmitAvailability={submitAvailability} onSubmitPrice={submitPrice} whatsappHref={whatsappHref} />
            ))}
          </div>

          <form
            className="flex items-center gap-2 border-t border-brand-border p-3"
            onSubmit={(e) => {
              e.preventDefault()
              handleSend()
            }}
          >
            <label htmlFor="ask-sri-apartment-input" className="sr-only">
              Type your question
            </label>
            <input
              id="ask-sri-apartment-input"
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question…"
              maxLength={300}
              className="flex-1 rounded border border-brand-border px-3 py-2 text-sm"
            />
            <button
              type="submit"
              aria-label="Send"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-gold text-brand-green-3 hover:bg-brand-gold-2"
            >
              <IconSend />
            </button>
          </form>
        </div>
      )}
    </>
  )
}

function MessageBubble({
  message,
  showQuickActions,
  onQuickAction,
  onSubmitAvailability,
  onSubmitPrice,
  whatsappHref,
}: {
  message: ChatMessage
  showQuickActions: boolean
  onQuickAction: (action: string) => void
  onSubmitAvailability: (checkIn: string, checkOut: string, guests: number) => void
  onSubmitPrice: (unitCode: string, checkIn: string, checkOut: string) => void
  whatsappHref: string | null
}) {
  const isUser = message.role === 'user'
  const bubbleClass = isUser ? 'ml-auto bg-brand-gold text-brand-green-3' : 'mr-auto bg-brand-cream-2 text-brand-ink-2'

  return (
    <div className={isUser ? 'flex justify-end' : 'flex flex-col gap-2'}>
      {message.kind === 'text' && message.text && <p className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${bubbleClass}`}>{message.text}</p>}

      {message.kind === 'fallback' && (
        <div className="mr-auto max-w-[90%] space-y-2 rounded-lg bg-brand-cream-2 px-3 py-2 text-sm text-brand-ink-2">
          <p>{message.text ?? FALLBACK_TEXT}</p>
          {whatsappHref && (
            // #075E54 not #25D366 — WCAG AA contrast fix, see WhatsAppFloat.tsx
            <a href={whatsappHref} target="_blank" rel="noreferrer" className="inline-block rounded bg-[#075E54] px-3 py-1.5 text-xs font-bold text-white uppercase">
              💬 WhatsApp Us
            </a>
          )}
        </div>
      )}

      {message.kind === 'attraction' && message.attraction && (
        <div className="mr-auto max-w-[90%] space-y-1 rounded-lg bg-brand-cream-2 px-3 py-2 text-sm text-brand-ink-2">
          <p className="font-semibold text-brand-green">{message.attraction.name}</p>
          <p>~{message.attraction.driveTimeMin} min drive</p>
          <p className="text-xs text-brand-muted">Drive times are approximate and may vary with traffic.</p>
          <a href={message.attraction.directionsUrl} target="_blank" rel="noreferrer" className="inline-block text-xs font-bold tracking-wide text-brand-gold-4 uppercase hover:underline">
            Get Directions →
          </a>
        </div>
      )}

      {message.kind === 'property_location' && (
        <div className="mr-auto max-w-[90%] space-y-1 rounded-lg bg-brand-cream-2 px-3 py-2 text-sm text-brand-ink-2">
          <p className="font-semibold text-brand-green">Sri Apartment Homestay</p>
          <p>{PROPERTY_ADDRESS}</p>
          <a href={PROPERTY_DIRECTIONS_URL} target="_blank" rel="noreferrer" className="inline-block text-xs font-bold tracking-wide text-brand-gold-4 uppercase hover:underline">
            Get Directions →
          </a>
        </div>
      )}

      {message.kind === 'attraction_list' && (
        <div className="mr-auto max-w-[95%] space-y-2 rounded-lg bg-brand-cream-2 px-3 py-2 text-sm text-brand-ink-2">
          {NEARBY_ATTRACTIONS.map((a) => (
            <div key={a.name} className="border-b border-brand-border pb-2 last:border-0 last:pb-0">
              <p className="font-semibold text-brand-green">{a.name}</p>
              <p>
                ~{a.driveTimeMin} min drive ·{' '}
                <a href={a.directionsUrl} target="_blank" rel="noreferrer" className="font-bold tracking-wide text-brand-gold-4 uppercase hover:underline">
                  Get Directions →
                </a>
              </p>
            </div>
          ))}
          <p className="text-xs text-brand-muted">Drive times are approximate and may vary with traffic.</p>
        </div>
      )}

      {message.kind === 'availability_form' && <AvailabilityForm onSubmit={onSubmitAvailability} />}
      {message.kind === 'price_form' && <PriceForm onSubmit={onSubmitPrice} />}

      {message.kind === 'availability_result' && message.availabilityResult && (
        <div className="mr-auto max-w-[95%] space-y-2 rounded-lg bg-brand-cream-2 px-3 py-2 text-sm text-brand-ink-2">
          {message.availabilityResult.units.length === 0 ? (
            <p>No apartments are available for those dates. Please try different dates or contact us on WhatsApp.</p>
          ) : (
            <>
              <p className="font-semibold text-brand-green">
                {message.availabilityResult.units.length} apartment{message.availabilityResult.units.length === 1 ? '' : 's'} available
              </p>
              {message.availabilityResult.units.map((u) => (
                <p key={u.code}>
                  Flat {u.code} — {formatInr(u.total)} total
                </p>
              ))}
              <Link to="/book" className="mt-1 inline-block rounded bg-brand-gold px-3 py-1.5 text-xs font-bold tracking-wide text-brand-green-3 uppercase">
                Continue to Book
              </Link>
            </>
          )}
        </div>
      )}

      {message.kind === 'price_result' && message.priceResult && (
        <div className="mr-auto max-w-[90%] space-y-1 rounded-lg bg-brand-cream-2 px-3 py-2 text-sm text-brand-ink-2">
          <p className="font-semibold text-brand-green">Flat {message.priceResult.unitCode}</p>
          <p>{message.priceResult.quote.nights} night(s)</p>
          <p>Subtotal: {formatInr(message.priceResult.quote.subtotal_after_discount)}</p>
          <p>GST: {formatInr(message.priceResult.quote.cgst + message.priceResult.quote.sgst + message.priceResult.quote.igst)}</p>
          <p className="font-semibold text-brand-gold-4">Total: {formatInr(message.priceResult.quote.total)}</p>
          <Link to="/book" className="mt-1 inline-block rounded bg-brand-gold px-3 py-1.5 text-xs font-bold tracking-wide text-brand-green-3 uppercase">
            Continue to Book
          </Link>
        </div>
      )}

      {showQuickActions && (
        <div className="flex flex-wrap gap-1.5">
          {QUICK_ACTIONS.map((qa) => (
            <button
              key={qa.action}
              type="button"
              onClick={() => onQuickAction(qa.action)}
              className="rounded-full border border-brand-border px-3 py-1 text-xs font-semibold text-brand-green hover:border-brand-gold hover:text-brand-gold-4"
            >
              {qa.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function AvailabilityForm({ onSubmit }: { onSubmit: (checkIn: string, checkOut: string, guests: number) => void }) {
  const [checkIn, setCheckIn] = useState('')
  const [checkOut, setCheckOut] = useState('')
  const [guests, setGuests] = useState(2)
  const [submitting, setSubmitting] = useState(false)

  return (
    <form
      className="mr-auto w-[90%] space-y-2 rounded-lg bg-brand-cream-2 px-3 py-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault()
        setSubmitting(true)
        onSubmit(checkIn, checkOut, guests)
      }}
    >
      <p className="text-brand-ink-2">Let me check — what dates and how many guests?</p>
      <label className="block">
        <span className="mb-1 block text-xs font-bold text-brand-muted uppercase">Check-in</span>
        <input type="date" required min={todayIso()} value={checkIn} onChange={(e) => setCheckIn(e.target.value)} className="w-full rounded border border-brand-border px-2 py-1.5" />
      </label>
      <label className="block">
        <span className="mb-1 block text-xs font-bold text-brand-muted uppercase">Check-out</span>
        <input type="date" required min={checkIn || todayIso()} value={checkOut} onChange={(e) => setCheckOut(e.target.value)} className="w-full rounded border border-brand-border px-2 py-1.5" />
      </label>
      <label className="block">
        <span className="mb-1 block text-xs font-bold text-brand-muted uppercase">Guests</span>
        <select value={guests} onChange={(e) => setGuests(Number(e.target.value))} className="w-full rounded border border-brand-border px-2 py-1.5">
          {[1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={n}>
              {n} Guest{n > 1 ? 's' : ''}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={submitting} className="w-full rounded bg-brand-green px-3 py-2 text-xs font-bold tracking-wide text-brand-gold uppercase disabled:opacity-50">
        {submitting ? 'Checking…' : 'Check Availability'}
      </button>
    </form>
  )
}

function PriceForm({ onSubmit }: { onSubmit: (unitCode: string, checkIn: string, checkOut: string) => void }) {
  const [unitCode, setUnitCode] = useState('A1')
  const [checkIn, setCheckIn] = useState('')
  const [checkOut, setCheckOut] = useState('')
  const [submitting, setSubmitting] = useState(false)

  return (
    <form
      className="mr-auto w-[90%] space-y-2 rounded-lg bg-brand-cream-2 px-3 py-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault()
        setSubmitting(true)
        onSubmit(unitCode, checkIn, checkOut)
      }}
    >
      <p className="text-brand-ink-2">Which apartment and dates would you like priced?</p>
      <label className="block">
        <span className="mb-1 block text-xs font-bold text-brand-muted uppercase">Apartment</span>
        <select value={unitCode} onChange={(e) => setUnitCode(e.target.value)} className="w-full rounded border border-brand-border px-2 py-1.5">
          {['A1', 'A2', 'B3', 'B4', 'C5', 'C6'].map((c) => (
            <option key={c} value={c}>
              Flat {c}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="mb-1 block text-xs font-bold text-brand-muted uppercase">Check-in</span>
        <input type="date" required min={todayIso()} value={checkIn} onChange={(e) => setCheckIn(e.target.value)} className="w-full rounded border border-brand-border px-2 py-1.5" />
      </label>
      <label className="block">
        <span className="mb-1 block text-xs font-bold text-brand-muted uppercase">Check-out</span>
        <input type="date" required min={checkIn || todayIso()} value={checkOut} onChange={(e) => setCheckOut(e.target.value)} className="w-full rounded border border-brand-border px-2 py-1.5" />
      </label>
      <button type="submit" disabled={submitting} className="w-full rounded bg-brand-green px-3 py-2 text-xs font-bold tracking-wide text-brand-gold uppercase disabled:opacity-50">
        {submitting ? 'Checking…' : 'Get Price'}
      </button>
    </form>
  )
}
