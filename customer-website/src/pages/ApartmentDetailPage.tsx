import { useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { Helmet } from '../components/Helmet'
import { BreadcrumbSchema } from '../components/BreadcrumbSchema'
import { ResponsiveImage } from '../components/ResponsiveImage'
import { AsyncBoundary } from '../components/AsyncBoundary'
import { Lightbox } from '../components/Lightbox'
import { useAsync } from '../hooks/useAsync'
import { getPublicUnits } from '../lib/api'
import { APARTMENT_CONTENT, getApartmentContent, normalizeUnitName, MAX_GUESTS_DISPLAY, GUEST_ACCESS_TEXT } from '../content/apartments'
import { resolveUnitPhotos, type ResolvedPhoto } from '../lib/unitPhotos'
import { useBusinessContact } from '../hooks/useBusinessContact'
import { accommodationTypeLabel } from '../lib/accommodationTypes'
import { formatInr } from '../lib/booking'
import type { PublicUnitOut } from '../lib/types'

const DEFAULT_CHECKIN_TIME = '12:30 PM'
const DEFAULT_CHECKOUT_TIME = '11:00 AM'

export function ApartmentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const state = useAsync(getPublicUnits, [])
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  const localContent = APARTMENT_CONTENT.find((a) => a.code === id)

  return (
    <div className="mx-auto max-w-5xl px-4 py-16 sm:px-6">
      <AsyncBoundary state={state} retry={state.status === 'error' ? state.retry : () => {}} loadingLabel="Loading accommodation…">
        {(units) => {
          const unit = units.find((u) => (localContent ? normalizeUnitName(u.name) === normalizeUnitName(localContent.unitName) : u.id === id))
          if (!unit) {
            // Either an unknown URL, or this unit isn't listed by OS10 right now
            // (e.g. deactivated) — don't invent a listing for it.
            return <Navigate to="/apartments" replace />
          }
          return <AccommodationDetail unit={unit} openIndex={openIndex} setOpenIndex={setOpenIndex} />
        }}
      </AsyncBoundary>
    </div>
  )
}

function AccommodationDetail({
  unit,
  openIndex,
  setOpenIndex,
}: {
  unit: PublicUnitOut
  openIndex: number | null
  setOpenIndex: (i: number | null) => void
}) {
  const content = getApartmentContent(unit.name)
  const [photos, setPhotos] = useState<ResolvedPhoto[]>(content?.photos ?? [])
  const businessContact = useBusinessContact()

  useEffect(() => {
    let cancelled = false
    resolveUnitPhotos(unit).then((resolved) => {
      if (!cancelled) setPhotos(resolved)
    })
    return () => {
      cancelled = true
    }
  }, [unit])

  // OS10's description/amenities are authoritative when set; the static
  // content file (existing marketing copy for A1-C6) is the fallback until
  // an accommodation has that content entered in OS10 directly. `||`, not
  // `??`: OS10 currently returns "" (empty string) rather than null for an
  // unset description, and "" is meaningfully "nothing entered yet", not a
  // real (empty) description to show — it must still fall back, the same
  // way `amenities` below already does with its own truthy check.
  const description = unit.description || content?.description
  const amenities = unit.amenities ? unit.amenities.split(',').map((a) => a.trim()).filter(Boolean) : (content?.amenities ?? [])
  const typeLabel = accommodationTypeLabel(unit.unit_type)

  // Per-unit values (set from OS10 Admin → Properties → this unit) win when
  // present; otherwise fall back to the business-wide default (house rules)
  // or the site's existing standard time (check-in/out) — same fallback
  // pattern as description/amenities above.
  const checkinTime = unit.checkin_time || DEFAULT_CHECKIN_TIME
  const checkoutTime = unit.checkout_time || DEFAULT_CHECKOUT_TIME
  const houseRules = unit.house_rules || businessContact.houseRules

  // The highlight grid always shows just the first 4 photos (unchanged from
  // the original design); "View Full Gallery" opens the Lightbox over the
  // complete resolved photo set instead.
  const highlightPhotos = photos.slice(0, 4)

  return (
    <>
      <Helmet
        title={`${unit.name} — Sri Apartment Homestay`}
        description={description ?? `${unit.name} (${typeLabel}) at ${unit.property_name}, Puducherry.`}
        canonicalPath={`/apartments/${content?.code ?? unit.id}`}
      />
      <BreadcrumbSchema
        items={[
          { name: 'Home', path: '/' },
          { name: 'Apartments', path: '/apartments' },
          { name: unit.name, path: `/apartments/${content?.code ?? unit.id}` },
        ]}
      />

      <Link to="/apartments" className="text-sm text-brand-muted underline">
        ← All accommodations
      </Link>

      <div className="mt-4 grid grid-cols-1 gap-8 md:grid-cols-2">
        <div>
          <div className="grid grid-cols-2 gap-2">
            {highlightPhotos.length > 0 ? (
              highlightPhotos.map((p, i) => (
                <button
                  key={p.src}
                  type="button"
                  onClick={() => setOpenIndex(i)}
                  aria-label={`Open photo: ${p.alt}`}
                  className="focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-gold"
                >
                  <ResponsiveImage src={p.src} alt={p.alt} width={400} height={300} className="h-40 w-full rounded object-cover sm:h-48" />
                </button>
              ))
            ) : (
              <div className="col-span-2 flex h-48 items-center justify-center rounded bg-brand-cream text-sm font-semibold tracking-wide text-brand-muted uppercase">
                {typeLabel}
              </div>
            )}
          </div>

          {photos.length > highlightPhotos.length && (
            <button
              type="button"
              onClick={() => setOpenIndex(0)}
              className="mt-2 w-full rounded border border-brand-border py-2 text-xs font-bold tracking-widest text-brand-green uppercase hover:border-brand-gold hover:text-brand-gold-4"
            >
              View Full Gallery ({photos.length} photos)
            </button>
          )}
        </div>

        <div>
          {/* text-brand-gold-4, not text-brand-gold — same WCAG AA contrast fix as ApartmentsPage's card label. */}
          <p className="text-xs font-bold tracking-wide text-brand-gold-4 uppercase">{typeLabel}</p>
          <h1 className="font-serif text-3xl text-brand-green">{unit.name}</h1>
          <p className="mt-1 text-sm text-brand-muted">{unit.property_name}</p>
          <p className="mt-2 text-brand-muted">
            Up to {MAX_GUESTS_DISPLAY} guests
            {unit.bedroom_count != null && ` · ${unit.bedroom_count} bedroom${unit.bedroom_count === 1 ? '' : 's'}`}
            {unit.bathroom_count != null && ` · ${unit.bathroom_count} bathroom${unit.bathroom_count === 1 ? '' : 's'}`}
          </p>
          <p className="mt-4 font-serif text-2xl text-brand-gold-4">
            From {formatInr(unit.base_rate)} <span className="text-sm text-brand-muted">/ night, before GST</span>
          </p>

          {description && <p className="mt-4 text-sm leading-relaxed text-brand-ink-2">{description}</p>}

          {amenities.length > 0 && (
            <ul className="mt-4 flex flex-wrap gap-2">
              {amenities.map((a) => (
                <li key={a} className="rounded border border-brand-border bg-brand-cream-2 px-3 py-1 text-xs text-brand-ink-2">
                  {a}
                </li>
              ))}
            </ul>
          )}

          <div className="mt-4">
            <p className="text-xs font-bold tracking-wide text-brand-gold-4 uppercase">Guest Access</p>
            <p className="mt-1 text-sm leading-relaxed text-brand-ink-2">{GUEST_ACCESS_TEXT}</p>
          </div>

          <p className="mt-4 text-sm text-brand-muted">
            Check-in from <span className="font-semibold text-brand-ink-2">{checkinTime}</span> · Check-out by{' '}
            <span className="font-semibold text-brand-ink-2">{checkoutTime}</span>
          </p>

          {houseRules && (
            <div className="mt-4">
              <p className="text-xs font-bold tracking-wide text-brand-gold-4 uppercase">House Rules</p>
              {/* houseRules is a single string with embedded \n line breaks
                  (from OS10's per-unit override or the business-wide
                  default) — split into one list item per rule instead of
                  rendering it as one <p>, where HTML collapses the \n
                  characters into a single run-on paragraph. */}
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm leading-relaxed text-brand-ink-2">
                {houseRules
                  .split('\n')
                  .map((rule) => rule.trim())
                  .filter(Boolean)
                  .map((rule) => (
                    <li key={rule}>{rule}</li>
                  ))}
              </ul>
            </div>
          )}

          <Link to="/book" className="mt-8 block rounded bg-brand-gold py-3 text-center text-sm font-bold tracking-widest text-brand-green-3 uppercase">
            Book Now
          </Link>
        </div>
      </div>

      {openIndex !== null && photos.length > 0 && <Lightbox photos={photos} index={openIndex} onClose={() => setOpenIndex(null)} onNavigate={setOpenIndex} />}
    </>
  )
}
