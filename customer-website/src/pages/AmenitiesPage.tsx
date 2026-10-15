import { Link } from 'react-router-dom'
import { Helmet } from '../components/Helmet'
import { AMENITY_GROUPS, apartmentsWithAmenity } from '../content/amenities'

const ICONS: Record<string, string> = {
  '2 AC Bedrooms': '❄️',
  '2 Attached Bathrooms': '🚿',
  'Family-Friendly Environment': '👨‍👩‍👧',
  'High-speed WiFi': '📶',
  'Smart TV': '📺',
  Kitchen: '🍳',
  'Living/Dining': '🛋️',
  'LPG Kitchen': '🔥',
  Refrigerator: '🧊',
  'RO Water Purifier': '💧',
  'Hot Water / Geyser': '🚿',
  'Washing Machine': '🫧',
  'Lift Access': '🛗',
  'Free Parking': '🚗',
}

export function AmenitiesPage() {
  return (
    <div className="mx-auto max-w-5xl px-4 py-16 sm:px-6">
      <Helmet
        title="Amenities — Sri Apartment Homestay Pondicherry"
        description="AC, WiFi, kitchen, washer, parking and more across our accommodations in Moolakulam, Puducherry."
        canonicalPath="/amenities"
      />
      <h1 className="text-center font-serif text-4xl text-brand-green">Amenities</h1>
      <p className="mx-auto mt-2 max-w-xl text-center text-brand-muted">
        Every apartment — A1, A2, B3, B4, C5 and C6 — includes the same standard amenities.
      </p>

      <div className="mt-12 space-y-10">
        {AMENITY_GROUPS.map((group) => (
          <div key={group.title}>
            {/* text-brand-gold-4, not text-brand-gold — WCAG AA contrast fix (same as Apartments card labels) */}
            <h2 className="mb-4 text-xs font-bold tracking-[0.2em] text-brand-gold-4 uppercase">{group.title}</h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {group.items.map((item) => {
                const codes = apartmentsWithAmenity(item)
                return (
                  <div key={item} className="flex items-center justify-between rounded border border-brand-border bg-brand-cream-2 px-4 py-3">
                    <span className="flex items-center gap-3 text-sm text-brand-ink-2">
                      <span aria-hidden="true">{ICONS[item] ?? '•'}</span>
                      {item}
                    </span>
                    <span className="text-xs text-brand-muted">{codes.length === 6 ? 'All apartments' : codes.join(', ')}</span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-12 text-center">
        <Link to="/apartments" className="rounded bg-brand-gold px-6 py-3 text-sm font-bold tracking-wide text-brand-green-3 uppercase">
          View Accommodations
        </Link>
      </div>
    </div>
  )
}
