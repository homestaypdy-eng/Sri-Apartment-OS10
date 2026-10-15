import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Helmet } from '../components/Helmet'
import { BreadcrumbSchema } from '../components/BreadcrumbSchema'
import { ResponsiveImage } from '../components/ResponsiveImage'
import { AsyncBoundary } from '../components/AsyncBoundary'
import { useAsync } from '../hooks/useAsync'
import { useUnitPhotos } from '../hooks/useUnitPhotos'
import { getPublicUnits } from '../lib/api'
import { getApartmentContent, MAX_GUESTS_DISPLAY } from '../content/apartments'
import type { ResolvedPhoto } from '../lib/unitPhotos'
import { accommodationTypeLabel, accommodationTypeLabelPlural } from '../lib/accommodationTypes'
import { formatInr } from '../lib/booking'
import type { PublicUnitOut } from '../lib/types'

export function ApartmentsPage() {
  const state = useAsync(getPublicUnits, [])

  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <Helmet
        title="Accommodations — Sri Apartment Homestay Pondicherry"
        description="Comfortable accommodations in Moolakulam, Puducherry. Compare and book direct."
        canonicalPath="/apartments"
      />
      <BreadcrumbSchema items={[{ name: 'Home', path: '/' }, { name: 'Apartments', path: '/apartments' }]} />
      <h1 className="text-center font-serif text-4xl text-brand-green">Our Accommodations</h1>
      <p className="mt-2 text-center text-brand-muted">Live availability and pricing, direct from our booking system.</p>

      <div className="mt-10">
        <AsyncBoundary state={state} retry={state.status === 'error' ? state.retry : () => {}} loadingLabel="Loading accommodations…">
          {(units) => <AccommodationsGrid units={units} />}
        </AsyncBoundary>
      </div>
    </div>
  )
}

function AccommodationsGrid({ units }: { units: PublicUnitOut[] }) {
  const [activeType, setActiveType] = useState<string>('all')
  const photosByUnit = useUnitPhotos(units)

  // Only types that actually exist in today's live inventory get a filter
  // pill — with today's production data that's just "Apartments"; a Villa
  // filter appears automatically the moment a villa is created in OS10.
  const presentTypes = useMemo(() => Array.from(new Set(units.map((u) => u.unit_type))).sort(), [units])

  const filtered = activeType === 'all' ? units : units.filter((u) => u.unit_type === activeType)

  // Multiple properties: group and label each section. A single property
  // (today's reality) stays a flat list — no redundant "Sri Apartment
  // Homestay" heading repeated over and over.
  const propertyNames = useMemo(() => Array.from(new Set(filtered.map((u) => u.property_name))), [filtered])
  const groupByProperty = propertyNames.length > 1

  if (units.length === 0) {
    return <p className="text-center text-brand-muted">No accommodations are currently listed. Please check back soon or contact us on WhatsApp.</p>
  }

  return (
    <div>
      {presentTypes.length >= 1 && (
        <div className="mb-8 flex flex-wrap justify-center gap-2">
          <FilterPill label="All" active={activeType === 'all'} onClick={() => setActiveType('all')} />
          {presentTypes.map((t) => (
            <FilterPill key={t} label={accommodationTypeLabelPlural(t)} active={activeType === t} onClick={() => setActiveType(t)} />
          ))}
        </div>
      )}

      {filtered.length === 0 ? (
        <p className="text-center text-brand-muted">No accommodations match this filter right now.</p>
      ) : groupByProperty ? (
        propertyNames.map((propertyName) => {
          const group = filtered.filter((u) => u.property_name === propertyName)
          if (group.length === 0) return null
          return (
            <div key={propertyName} className="mb-12">
              <h2 className="mb-4 font-serif text-2xl text-brand-green">{propertyName}</h2>
              <CardGrid units={group} photosByUnit={photosByUnit} />
            </div>
          )
        })
      ) : (
        <CardGrid units={filtered} photosByUnit={photosByUnit} />
      )}
    </div>
  )
}

function FilterPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-4 py-1.5 text-xs font-semibold tracking-wide uppercase transition ${
        active ? 'border-brand-green bg-brand-green text-brand-gold' : 'border-brand-border bg-white text-brand-muted hover:border-brand-green'
      }`}
    >
      {label}
    </button>
  )
}

function CardGrid({ units, photosByUnit }: { units: PublicUnitOut[]; photosByUnit: Record<string, ResolvedPhoto[]> }) {
  return (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 md:grid-cols-3">
      {units.map((unit) => {
        const content = getApartmentContent(unit.name)
        const photo = photosByUnit[unit.id]?.[0]
        return (
          <Link
            key={unit.id}
            to={`/apartments/${content?.code ?? unit.id}`}
            className="group overflow-hidden rounded-lg border border-brand-border bg-brand-cream-2 transition hover:border-brand-gold hover:shadow-lg"
          >
            {photo ? (
              <ResponsiveImage src={photo.src} alt={photo.alt} width={400} height={280} className="h-48 w-full object-cover transition group-hover:scale-105" />
            ) : (
              <div className="flex h-48 w-full items-center justify-center bg-brand-cream text-xs font-semibold tracking-wide text-brand-muted uppercase">
                {accommodationTypeLabel(unit.unit_type)}
              </div>
            )}
            <div className="p-4">
              {/* text-brand-gold-4, not text-brand-gold: Lighthouse measured the
                  lighter gold at 2.17-2.42:1 on this card's cream background,
                  well under WCAG AA's 4.5:1 for this small bold text —
                  brand-gold-4 clears it at 5.57-6.22:1. */}
              <p className="text-xs font-bold tracking-wide text-brand-gold-4 uppercase">{accommodationTypeLabel(unit.unit_type)}</p>
              <p className="font-serif text-xl text-brand-green">{unit.name}</p>
              <p className="text-xs text-brand-muted">{unit.property_name}</p>
              <p className="mt-1 text-sm text-brand-muted">Up to {MAX_GUESTS_DISPLAY} guests</p>
              {unit.description && <p className="mt-2 line-clamp-2 text-xs text-brand-ink-2">{unit.description}</p>}
              <div className="mt-3 flex items-center justify-between">
                <p className="font-serif text-lg text-brand-gold-4">
                  From {formatInr(unit.base_rate)} <span className="text-xs text-brand-muted">/ night</span>
                </p>
                <span className="text-xs font-bold tracking-wide text-brand-green uppercase group-hover:text-brand-gold-4">View Details →</span>
              </div>
            </div>
          </Link>
        )
      })}
    </div>
  )
}
