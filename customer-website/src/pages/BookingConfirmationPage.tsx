import { Link, Navigate, useLocation } from 'react-router-dom'
import { Helmet } from '../components/Helmet'
import { formatDate, formatInr } from '../lib/booking'
import type { PublicBookingConfirmation } from '../lib/types'

interface LocationState {
  confirmation: PublicBookingConfirmation
  unitName?: string
}

export function BookingConfirmationPage() {
  const location = useLocation()
  const state = location.state as LocationState | null

  // No confirmation in router state means this page was reached directly
  // (refresh, bookmarked link) rather than as the result of a real OS10
  // response — we never fabricate a confirmation to show instead.
  if (!state?.confirmation) {
    return <Navigate to="/book" replace />
  }

  const { confirmation, unitName } = state

  return (
    <div className="mx-auto max-w-lg px-4 py-16 text-center sm:px-6">
      {/* SEO audit fix: this page previously had no <Helmet> at all, so it
          silently inherited whatever title/description the PREVIOUS page
          (BookPage) had left behind, and carried no noindex — a real,
          guest-specific, one-time confirmation state should never be
          indexed or show a stale/misleading title. */}
      <Helmet
        title="Booking Confirmed — Sri Apartment Homestay"
        description="Your Sri Apartment Homestay booking confirmation."
        canonicalPath="/booking/confirmation"
        noindex
      />
      <p className="text-4xl" aria-hidden="true">✅</p>
      <h1 className="mt-4 font-serif text-3xl text-brand-green">Booking Confirmed</h1>
      <ConfirmationChannelsLine confirmation={confirmation} />

      <dl className="mt-8 space-y-3 rounded-lg border border-brand-border bg-brand-cream-2 p-6 text-left text-sm">
        <Row label="Booking reference" value={confirmation.booking_id} mono />
        {confirmation.invoice_no && <Row label="Invoice number" value={confirmation.invoice_no} mono />}
        {unitName && <Row label="Apartment" value={unitName} />}
        <Row label="Check-in" value={formatDate(confirmation.check_in)} />
        <Row label="Check-out" value={formatDate(confirmation.check_out)} />
        <Row label="Nights" value={String(confirmation.nights)} />
        <Row label="Total paid" value={formatInr(confirmation.total)} bold />
      </dl>

      <p className="mt-6 text-sm text-brand-muted">
        Check-in is from 12:30 PM, check-out by 11:00 AM. Questions? WhatsApp us anytime — the link is in the footer.
      </p>

      <Link to="/" className="mt-8 inline-block rounded bg-brand-gold px-6 py-3 text-sm font-bold tracking-wide text-brand-green-3 uppercase">
        Back to Home
      </Link>
    </div>
  )
}

// Only claims a channel succeeded when the backend actually confirmed it
// sent — never assumes "sent to your phone/WhatsApp" just because a phone
// number was on file. A failed/unconfigured send must never be reported
// as sent. WhatsApp is included on exactly the same honest footing as SMS
// and email: it only appears here when confirmation_whatsapp_sent is
// explicitly true, which — since no WhatsApp provider is configured in
// this project yet — currently never happens; this line simply omits
// WhatsApp entirely rather than fabricate a success.
function ConfirmationChannelsLine({ confirmation }: { confirmation: PublicBookingConfirmation }) {
  const sent: string[] = []
  if (confirmation.confirmation_sms_sent) sent.push('phone (SMS)')
  if (confirmation.confirmation_whatsapp_sent) sent.push('WhatsApp')
  if (confirmation.confirmation_emailed) sent.push('email')

  if (sent.length === 0) {
    return <p className="mt-2 text-brand-muted">Your booking details are confirmed below.</p>
  }
  return <p className="mt-2 text-brand-muted">A confirmation has been sent to your {sent.join(' and ')}.</p>
}

function Row({ label, value, mono, bold }: { label: string; value: string; mono?: boolean; bold?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b border-brand-border/60 pb-2 last:border-0 last:pb-0">
      <dt className="text-brand-muted">{label}</dt>
      <dd className={`${mono ? 'font-mono text-xs' : ''} ${bold ? 'font-serif text-lg text-brand-gold-4' : 'text-brand-ink'}`}>{value}</dd>
    </div>
  )
}
