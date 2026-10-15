import { Helmet } from '../components/Helmet'
import { PROPERTY_MAP_EMBED_URL } from '../content/location'
import { useBusinessContact } from '../hooks/useBusinessContact'

export function LocationPage() {
  const { address } = useBusinessContact()
  return (
    <div className="mx-auto max-w-5xl px-4 py-16 sm:px-6">
      <Helmet
        title="Location — Sri Apartment Homestay Pondicherry"
        description={`${address}.`}
        canonicalPath="/location"
      />
      <h1 className="text-center font-serif text-4xl text-brand-green">Location</h1>
      <div className="mt-10 grid grid-cols-1 gap-8 md:grid-cols-2">
        <iframe
          title="Sri Apartment Homestay location map"
          src={PROPERTY_MAP_EMBED_URL}
          className="aspect-4/3 w-full rounded border-0"
          loading="lazy"
          referrerPolicy="no-referrer-when-downgrade"
        />
        <div>
          {/* text-brand-gold-4, not text-brand-gold — WCAG AA contrast fix (same as Apartments card labels) */}
          <p className="text-xs font-bold tracking-wide text-brand-gold-4 uppercase">Address</p>
          <p className="mt-2 text-brand-ink-2">{address}, India</p>
          <a
            href="https://www.google.com/maps/place//data=!4m2!3m1!1s0x3a53615790e2d601:0x9b1df761cd2112ca"
            target="_blank"
            rel="noreferrer"
            className="mt-4 inline-block text-sm font-semibold text-brand-green underline"
          >
            View on Google Maps →
          </a>
        </div>
      </div>
    </div>
  )
}
