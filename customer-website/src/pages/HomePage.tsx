import { useEffect, useId, useRef, useState, type JSX } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Helmet } from '../components/Helmet'
import { ResponsiveImage } from '../components/ResponsiveImage'
import { AsyncBoundary } from '../components/AsyncBoundary'
import { useAsync } from '../hooks/useAsync'
import { getPublicUnits } from '../lib/api'
import { getApartmentContent, MAX_GUESTS_DISPLAY } from '../content/apartments'
import { POST_CHECKIN_CANCELLATION_POLICY } from '../content/faq'
import { useUnitPhotos } from '../hooks/useUnitPhotos'
import { useBusinessContact } from '../hooks/useBusinessContact'
import { useReportBookingBarVisibility } from '../hooks/useBookingBarVisibility'
import { accommodationTypeLabel } from '../lib/accommodationTypes'
import { AMENITY_GROUPS } from '../content/amenities'
import { addDaysIso, todayIso, validateDateRange } from '../lib/booking'
import { NEARBY_ATTRACTIONS, PROPERTY_MAP_EMBED_URL, type AttractionIcon } from '../content/location'
import heroImg from '../assets/hero/standalone-1.jpg'

/** Order the property owner wants featured apartments displayed in on the
 * Home page — independent of whatever order OS10's API happens to return. */
const APARTMENT_DISPLAY_ORDER = ['A1', 'A2', 'B3', 'B4', 'C5', 'C6']

/** Minimal inline line icons (no icon library dependency) — a small,
 * consistent stroke-based set used only for the feature badges below. */
function IconBed() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 16v-6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v6M2 16v-2M18 16v-2M2 11V7a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1M11 8h6" />
    </svg>
  )
}
function IconBath() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 10V5.5A1.5 1.5 0 0 1 4.5 4a1.5 1.5 0 0 1 1.5 1.5V6M2 10h16v2a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5v-2ZM5 17l-1 2M15 17l1 2" />
    </svg>
  )
}
function IconUsers() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 17v-1.5a3 3 0 0 0-3-3H5a3 3 0 0 0-3 3V17M7 9.5A2.75 2.75 0 1 0 7 4a2.75 2.75 0 0 0 0 5.5ZM14 5a2.5 2.5 0 0 1 0 5M17 17v-1.5a3 3 0 0 0-2-2.83" />
    </svg>
  )
}
function IconKitchen() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 8.5a5 5 0 0 1 10 0V9H4v-.5Z" />
      <path d="M2.5 9h13M9 3v1.5M6.5 3.6l.6 1.3M11.5 3.6l-.6 1.3M4.5 15h9M5.5 12h7l-.5 3h-6l-.5-3Z" />
    </svg>
  )
}
function IconWifi() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7.5a10 10 0 0 1 14 0M5.5 10.3a6.5 6.5 0 0 1 9 0M8 13a3 3 0 0 1 4 0" />
      <circle cx="10" cy="16" r="1" fill="currentColor" stroke="none" />
    </svg>
  )
}
function IconParking() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2.5" y="2.5" width="15" height="15" rx="2.5" />
      <path d="M7.5 14V6h2.75a2.25 2.25 0 1 1 0 4.5H7.5" />
    </svg>
  )
}
function IconShield() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 2.5 17 5v5c0 4.5-3 7.5-7 8-4-.5-7-3.5-7-8V5l7-2.5Z" />
      <path d="M7 10l2 2 4-4" />
    </svg>
  )
}
function IconChat() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 10a6.5 6.5 0 0 1-9.7 5.66L3 17l1.34-4.3A6.5 6.5 0 1 1 17 10Z" />
      <path d="M6.5 9.5h7M6.5 12h4.5" />
    </svg>
  )
}
function IconCalendarCheck() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2.5" y="3.5" width="15" height="14" rx="2" />
      <path d="M2.5 8h15M6 2v3M14 2v3M7 12.5l2 2 4-4" />
    </svg>
  )
}
function IconCalendarPercent() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2.5" y="3.5" width="15" height="14" rx="2" />
      <path d="M2.5 8h15M6 2v3M14 2v3M7.5 14.5l5-5M8 10h.01M13 14h.01" />
    </svg>
  )
}
function IconClock() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 5.5V10l3 2" />
    </svg>
  )
}
function IconCalendar() {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2.5" y="3.5" width="15" height="14" rx="2" />
      <path d="M2.5 8h15M6 2v3M14 2v3" />
    </svg>
  )
}
function IconWaves() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 8c1.5-1.5 3-1.5 4.5 0s3 1.5 4.5 0 3-1.5 4.5 0 3 1.5 4.5 0" />
      <path d="M2 13c1.5-1.5 3-1.5 4.5 0s3 1.5 4.5 0 3-1.5 4.5 0 3 1.5 4.5 0" />
    </svg>
  )
}
function IconLandmark() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 17h14M4 17V8m3 9V8m3 9V8m3 9V8m3 9V8M2 8l8-5 8 5" />
    </svg>
  )
}
function IconAshram() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 17h12M5 17v-6a5 5 0 0 1 10 0v6M10 6V3M8 3h4" />
    </svg>
  )
}
function IconAuroville() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="4" />
      <path d="M10 2v2M10 16v2M18 10h-2M4 10H2M15.5 4.5l-1.4 1.4M5.9 14.1l-1.4 1.4M15.5 15.5l-1.4-1.4M5.9 5.9 4.5 4.5" />
    </svg>
  )
}
function IconTrain() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="3" width="12" height="11" rx="3" />
      <path d="M4 10h12M7 14l-2 3M13 14l2 3" />
      <circle cx="7.5" cy="7" r="0.5" fill="currentColor" stroke="none" />
      <circle cx="12.5" cy="7" r="0.5" fill="currentColor" stroke="none" />
    </svg>
  )
}
const ATTRACTION_ICONS: Record<AttractionIcon, () => JSX.Element> = {
  beach: IconWaves,
  landmark: IconLandmark,
  ashram: IconAshram,
  auroville: IconAuroville,
  train: IconTrain,
}
/** Solid (filled) star, used for the Guest Reviews five-star visual
 * (GoogleReviewsSection) — a rating *display*, not an interactive control. */
function IconStarFilled() {
  return (
    <svg viewBox="0 0 20 20" width="22" height="22" fill="currentColor" aria-hidden="true">
      <path d="M10 2.5l2.35 4.76 5.25.76-3.8 3.7.9 5.23L10 14.5l-4.7 2.45.9-5.23-3.8-3.7 5.25-.76L10 2.5Z" />
    </svg>
  )
}

/** Booking-bar and "Why Choose" icons — sized by their container via CSS. */
function IconChevronDown() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m5 7.5 5 5 5-5" />
    </svg>
  )
}
function IconSearch() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="8.75" cy="8.75" r="5.75" />
      <path d="m13 13 4.5 4.5" />
    </svg>
  )
}
function IconHomeSolid() {
  return (
    <svg viewBox="0 0 24 24" width="36" height="36" fill="currentColor" aria-hidden="true">
      <path d="M11.3 2.7a1 1 0 0 1 1.4 0l9 8.5A1 1 0 0 1 21 13h-1.5v7.5a1 1 0 0 1-1 1h-4v-6h-5v6h-4a1 1 0 0 1-1-1V13H3a1 1 0 0 1-.7-1.8l9-8.5Z" />
    </svg>
  )
}
function IconUsersSolid() {
  return (
    <svg viewBox="0 0 24 24" width="36" height="36" fill="currentColor" aria-hidden="true">
      <circle cx="12" cy="7" r="3.25" />
      <circle cx="5.5" cy="8.5" r="2.5" />
      <circle cx="18.5" cy="8.5" r="2.5" />
      <path d="M12 11.5c3.3 0 6 2.2 6 5V19a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-2.5c0-2.8 2.7-5 6-5ZM4.8 12.4a6.6 6.6 0 0 0-.8 3.1V20H1a1 1 0 0 1-1-1v-1.6c0-2.3 2-4.2 4.8-5ZM19.2 12.4c2.8.8 4.8 2.7 4.8 5V19a1 1 0 0 1-1 1h-3v-4.5c0-1.1-.3-2.2-.8-3.1Z" />
    </svg>
  )
}
function IconPinSolid() {
  return (
    <svg viewBox="0 0 24 24" width="36" height="36" fill="currentColor" aria-hidden="true">
      <path d="M12 1.75A7.75 7.75 0 0 0 4.25 9.5c0 5.5 6.6 12.1 6.9 12.4a1.2 1.2 0 0 0 1.7 0c.3-.3 6.9-6.9 6.9-12.4A7.75 7.75 0 0 0 12 1.75Zm0 10.75a3 3 0 1 1 0-6 3 3 0 0 1 0 6Z" />
    </svg>
  )
}

/** Feature badges shown on every Home-page apartment card. Bedrooms/AC/WiFi
 * are confirmed for all 6 units directly in content/apartments.ts. Kitchen
 * and Parking are confirmed for A1/A2/B3/B4/C5 (via amenities data and/or
 * photos reviewed directly); C6 is included on the same basis as its sister
 * units in the same building but was not independently photo-confirmed —
 * flagged separately for owner confirmation. */
function ApartmentFeatures() {
  const features = [
    { icon: <IconBed />, label: '2 AC Bedrooms' },
    { icon: <IconBath />, label: '2 En-suite Baths' },
    { icon: <IconUsers />, label: `Up to ${MAX_GUESTS_DISPLAY} guests` },
    { icon: <IconKitchen />, label: 'Full Kitchen' },
    { icon: <IconWifi />, label: 'Free WiFi' },
    { icon: <IconParking />, label: 'Free Parking' },
  ]
  return (
    <ul className="mt-3 grid grid-cols-2 gap-x-2 gap-y-1.5 border-t border-brand-border pt-3 text-xs text-brand-ink-2">
      {features.map((f) => (
        <li key={f.label} className="flex items-center gap-1.5">
          <span className="shrink-0 text-brand-gold-4" aria-hidden="true">
            {f.icon}
          </span>
          {f.label}
        </li>
      ))}
    </ul>
  )
}

export function HomePage() {
  return (
    <div>
      <Helmet
        title="Sri Apartment Homestay Pondicherry — Book Direct"
        description="Comfortable accommodations in Moolakulam, Puducherry, suitable for families and groups. Book direct for instant confirmation and no OTA fees."
        canonicalPath="/"
      />

      <Hero />
      <AvailabilityWidget />
      <WhySriApartment />
      <FeaturedApartments />
      <AmenitiesPreview />
      <GalleryPreview />
      <TrustSection />
      <GoogleReviewsSection />
      <LocationTeaser />
      <FaqTeaser />
      <FinalCta />
    </div>
  )
}

function Hero() {
  return (
    <section className="relative flex min-h-[600px] items-center justify-center overflow-hidden bg-brand-green-3 text-center text-white lg:min-h-[680px]">
      <ResponsiveImage src={heroImg} alt="Sri Apartment living room" width={1600} height={1000} priority className="absolute inset-0 h-full w-full object-cover" />
      {/* Neutral, warm darkening (not a green tint) so the photo keeps its
          natural colour while the white copy stays readable. */}
      <div aria-hidden="true" className="absolute inset-0 bg-gradient-to-b from-black/45 via-black/35 to-black/50" />
      {/* pt/pb: keeps the buttons and copy clear of the floating booking bar
          (AvailabilityWidget), which overlaps this section's bottom edge. */}
      <div className="relative z-10 mx-auto max-w-4xl px-4 pt-16 pb-40 sm:px-6 sm:pb-44">
        <p className="mb-5 text-xs font-medium tracking-[0.35em] text-brand-gold uppercase sm:text-sm">Puducherry · India · Heritage Homestay</p>
        <h1 className="font-serif text-5xl tracking-wide uppercase sm:text-7xl">Sri Apartment Homestay</h1>
        <p className="mt-2 font-serif text-xl text-white/70 italic">Pondicherry</p>
        <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-white/90 sm:text-lg">
          Comfortable accommodations in Puducherry, suitable for families and groups. <br className="hidden sm:inline" />
          Book direct — no OTA fees.
        </p>
        <div className="mt-9 flex flex-col justify-center gap-4 sm:flex-row">
          <Link to="/book" className="rounded-md bg-brand-gold px-11 py-4 text-sm font-bold tracking-[0.12em] text-brand-green-3 uppercase shadow-lg transition hover:bg-brand-gold-2 sm:text-base">
            Book Now
          </Link>
          <Link to="/apartments" className="rounded-md border border-white/80 bg-black/10 px-10 py-4 text-sm font-bold tracking-[0.12em] text-white uppercase transition hover:bg-white/10 sm:text-base">
            View Accommodations
          </Link>
        </div>
      </div>
      {/* Curved ivory base: the page background rises at the edges and dips
          behind the floating booking bar, as in the approved design. */}
      <svg aria-hidden="true" viewBox="0 0 1440 80" preserveAspectRatio="none" className="absolute inset-x-0 -bottom-px h-8 w-full text-brand-cream sm:h-14 lg:h-16">
        <path fill="currentColor" d="M0 0Q720 110 1440 0V80H0Z" />
      </svg>
    </section>
  )
}

function AvailabilityWidget() {
  const navigate = useNavigate()
  const [checkIn, setCheckIn] = useState('')
  const [checkOut, setCheckOut] = useState('')
  const [guests, setGuests] = useState(2)
  const [error, setError] = useState<string | null>(null)
  const formRef = useRef<HTMLFormElement>(null)
  // Lets the chat launcher hide itself on mobile while this bar is on screen.
  useReportBookingBarVisibility(formRef)

  // Check-out must be at least one night after check-in — enforced natively
  // via `min`, and re-checked on submit with the same validator BookPage uses.
  const earliestCheckOut = addDaysIso(checkIn || todayIso(), 1)

  const checkInId = useId()
  const checkOutId = useId()
  const guestsId = useId()

  // Native date inputs keep the OS date picker, keyboard entry and the
  // min-date checks. When empty, their locale mask ("dd/mm/yyyy") is hidden
  // behind a friendlier placeholder; the transparent picker indicator is
  // stretched over the whole field so a click anywhere opens the picker.
  const control =
    'peer h-12 w-full appearance-none rounded-lg border border-brand-border bg-white pr-10 pl-12 text-[15px] text-brand-ink shadow-sm transition focus:border-brand-gold'
  const datePicker =
    '[&::-webkit-calendar-picker-indicator]:absolute [&::-webkit-calendar-picker-indicator]:inset-0 [&::-webkit-calendar-picker-indicator]:h-full [&::-webkit-calendar-picker-indicator]:w-full [&::-webkit-calendar-picker-indicator]:cursor-pointer [&::-webkit-calendar-picker-indicator]:opacity-0'
  const openPicker = (e: { currentTarget: HTMLInputElement }) => {
    try {
      e.currentTarget.showPicker?.()
    } catch {
      // Already open, or not supported — the native control still works.
    }
  }

  return (
    // Floats over the hero's curved bottom edge (negative top margin + z-20).
    <section aria-labelledby="availability-bar-heading" className="relative z-20 -mt-20 px-4 sm:-mt-24 sm:px-6 lg:-mt-[104px]">
      <h2 id="availability-bar-heading" className="sr-only">
        Check availability
      </h2>
      <form
        ref={formRef}
        className="mx-auto grid max-w-[1200px] grid-cols-1 gap-4 rounded-[20px] border border-brand-gold/25 bg-brand-cream px-5 py-6 shadow-[0_30px_70px_-25px_rgba(10,42,40,0.5)] sm:px-8 sm:py-7 md:grid-cols-2 md:gap-x-5 lg:grid-cols-[1fr_1fr_1fr_auto] lg:gap-0"
        onSubmit={(e) => {
          e.preventDefault()
          const result = validateDateRange(checkIn, checkOut)
          if (!result.valid) {
            setError(result.message ?? 'Please check your dates.')
            return
          }
          setError(null)
          navigate('/book', { state: { checkIn, checkOut, guests } })
        }}
      >
        <BookingField id={checkInId} label="Check-in" icon={<IconCalendar />} placeholder={checkIn ? null : 'Select check-in date'} className="lg:border-r lg:border-brand-border lg:pr-5">
          <input
            id={checkInId}
            type="date"
            required
            min={todayIso()}
            value={checkIn}
            onClick={openPicker}
            onChange={(e) => {
              const next = e.target.value
              setCheckIn(next)
              setError(null)
              // A previously chosen check-out that no longer leaves at least
              // one night is cleared rather than silently kept.
              if (checkOut && next && checkOut <= next) setCheckOut('')
            }}
            className={`${control} ${datePicker} ${checkIn ? '' : 'text-transparent focus:text-brand-ink'}`}
          />
        </BookingField>
        <BookingField id={checkOutId} label="Check-out" icon={<IconCalendar />} placeholder={checkOut ? null : 'Select check-out date'} className="lg:border-r lg:border-brand-border lg:px-5">
          <input
            id={checkOutId}
            type="date"
            required
            min={earliestCheckOut}
            value={checkOut}
            onClick={openPicker}
            onChange={(e) => {
              setCheckOut(e.target.value)
              setError(null)
            }}
            className={`${control} ${datePicker} ${checkOut ? '' : 'text-transparent focus:text-brand-ink'}`}
          />
        </BookingField>
        <BookingField id={guestsId} label="Guests" icon={<IconUsers />} className="lg:px-5">
          <select id={guestsId} value={guests} onChange={(e) => setGuests(Number(e.target.value))} className={`${control} cursor-pointer`}>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n} Guest{n > 1 ? 's' : ''}
              </option>
            ))}
          </select>
        </BookingField>
        <button
          type="submit"
          className="inline-flex h-14 w-full items-center justify-center gap-3 rounded-xl bg-brand-green px-7 text-sm font-bold tracking-[0.12em] whitespace-nowrap text-brand-gold uppercase shadow-md transition hover:bg-brand-green-3 sm:text-[15px] md:self-end lg:ml-3 lg:w-auto lg:self-center"
        >
          <IconSearch />
          Check Availability
        </button>
        {error && (
          <p role="alert" className="text-sm font-medium text-red-700 md:col-span-2 lg:col-span-4 lg:pt-3">
            {error}
          </p>
        )}
      </form>
    </section>
  )
}

/** One labelled booking-bar control: leading icon, optional placeholder
 * (shown only while the control is empty and unfocused) and a chevron. */
function BookingField({
  id,
  label,
  icon,
  placeholder,
  className = '',
  children,
}: {
  id: string
  label: string
  icon: JSX.Element
  placeholder?: string | null
  className?: string
  children: JSX.Element
}) {
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-2 block text-xs font-bold tracking-[0.14em] text-brand-green uppercase">
        {label}
      </label>
      <div className="relative">
        {children}
        <span aria-hidden="true" className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-brand-green [&_svg]:h-5 [&_svg]:w-5">
          {icon}
        </span>
        {placeholder && (
          <span aria-hidden="true" className="pointer-events-none absolute top-1/2 right-10 left-12 -translate-y-1/2 truncate text-[15px] text-brand-ink-2 peer-focus:hidden">
            {placeholder}
          </span>
        )}
        <span aria-hidden="true" className="pointer-events-none absolute top-1/2 right-3.5 -translate-y-1/2 text-brand-ink-2">
          <IconChevronDown />
        </span>
      </div>
    </div>
  )
}

function WhySriApartment() {
  const points = [
    { title: 'Book Direct', text: 'No OTA platform fees — you book straight with us.', icon: <IconHomeSolid /> },
    { title: 'Family-Friendly', text: 'Comfortable accommodations suitable for families and groups.', icon: <IconUsersSolid /> },
    { title: 'Central Location', text: 'Located in Moolakulam, Puducherry.', icon: <IconPinSolid /> },
    { title: 'Instant Confirmation', text: 'Real-time availability and pricing from our own booking system.', icon: <IconStarFilled /> },
  ]
  return (
    <section className="mx-auto max-w-6xl px-4 pt-14 pb-16 sm:px-6 lg:pt-16">
      <p className="flex items-center justify-center gap-4 text-xs font-semibold tracking-[0.3em] text-brand-gold uppercase sm:text-sm">
        <span aria-hidden="true" className="h-px w-8 bg-brand-gold/70 sm:w-12" />
        Why Choose Sri Apartment
        <span aria-hidden="true" className="h-px w-8 bg-brand-gold/70 sm:w-12" />
      </p>
      <h2 className="mt-4 text-center font-serif text-3xl leading-tight font-semibold text-brand-green sm:text-4xl">A Comfortable Stay in the Heart of Puducherry</h2>
      <div className="mt-10 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {points.map((p) => (
          <div key={p.title} className="rounded-xl border border-brand-border/70 bg-white px-6 py-8 text-center shadow-[0_12px_30px_-20px_rgba(10,42,40,0.35)]">
            <span className="inline-flex text-brand-gold [&_svg]:h-9 [&_svg]:w-9">{p.icon}</span>
            <h3 className="mt-4 font-serif text-xl font-semibold text-brand-green">{p.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-brand-muted">{p.text}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

function FeaturedApartments() {
  const state = useAsync(getPublicUnits, [])
  const photosByUnit = useUnitPhotos(state.status === 'success' ? state.data : [])

  return (
    <section className="bg-brand-cream-2 py-16">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <h2 className="text-center font-serif text-3xl text-brand-green">Our Accommodations</h2>
        <p className="mt-2 text-center text-brand-muted">
          {state.status === 'success' && `${state.data.length} accommodation${state.data.length === 1 ? '' : 's'} · `}
          Live availability and pricing, direct from our booking system.
        </p>
        <div className="mt-8">
          <AsyncBoundary state={state} retry={state.status === 'error' ? state.retry : () => {}} loadingLabel="Loading accommodations…">
            {(units) => {
              // Owner-specified display order (A1, A2, B3, B4, C5, C6) rather
              // than whatever order OS10's API happens to return; any unit
              // that doesn't match a known code falls to the end rather than
              // being dropped, so a future new listing still shows up.
              const ordered = [...units].sort((a, b) => {
                const codeA = getApartmentContent(a.name)?.code ?? a.name
                const codeB = getApartmentContent(b.name)?.code ?? b.name
                const ia = APARTMENT_DISPLAY_ORDER.indexOf(codeA)
                const ib = APARTMENT_DISPLAY_ORDER.indexOf(codeB)
                return (ia === -1 ? Infinity : ia) - (ib === -1 ? Infinity : ib)
              })
              return (
                <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 md:grid-cols-3">
                  {ordered.map((unit) => {
                    const content = getApartmentContent(unit.name)
                    const photo = photosByUnit[unit.id]?.[0]
                    return (
                      <Link key={unit.id} to={`/apartments/${content?.code ?? unit.id}`} className="group overflow-hidden rounded-lg border border-brand-border bg-white">
                        {photo && (
                          <ResponsiveImage src={photo.src} alt={photo.alt} width={400} height={280} className="h-48 w-full object-cover transition group-hover:scale-105" />
                        )}
                        <div className="p-4">
                          {/* text-brand-gold-4, not text-brand-gold — same WCAG AA contrast fix as the Apartments/Apartment-Detail card labels (this card also sits on a white background). */}
                          <p className="text-xs font-bold tracking-wide text-brand-gold-4 uppercase">{content?.floor ?? accommodationTypeLabel(unit.unit_type)}</p>
                          <p className="font-serif text-xl text-brand-green">{unit.name}</p>
                          <p className="mt-1 flex items-center gap-1.5 text-sm font-semibold text-brand-gold-4">
                            <IconCalendar />
                            Select your dates to view live pricing
                            <span aria-hidden="true" className="transition group-hover:translate-x-0.5">
                              →
                            </span>
                          </p>
                          <ApartmentFeatures />
                        </div>
                      </Link>
                    )
                  })}
                </div>
              )
            }}
          </AsyncBoundary>
        </div>
        <div className="mt-8 text-center">
          <Link to="/apartments" className="text-sm font-semibold text-brand-green underline">
            View all accommodations →
          </Link>
        </div>
      </div>
    </section>
  )
}

function AmenitiesPreview() {
  const featured = AMENITY_GROUPS.flatMap((g) => g.items).slice(0, 8)
  return (
    <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <h2 className="text-center font-serif text-3xl text-brand-green">Amenities</h2>
      <div className="mt-8 flex flex-wrap justify-center gap-3">
        {featured.map((item) => (
          <span key={item} className="rounded-full border border-brand-border bg-brand-cream-2 px-4 py-2 text-sm text-brand-ink-2">
            {item}
          </span>
        ))}
      </div>
      <div className="mt-6 text-center">
        <Link to="/amenities" className="text-sm font-semibold text-brand-green underline">
          See full amenities list →
        </Link>
      </div>
    </section>
  )
}

function GalleryPreview() {
  const state = useAsync(getPublicUnits, [])
  const units = state.status === 'success' ? state.data : []
  const photosByUnit = useUnitPhotos(units)
  // Same owner-specified ordering as FeaturedApartments, so the preview
  // leads with the same accommodations in the same sequence site-wide.
  const ordered = [...units].sort((a, b) => {
    const codeA = getApartmentContent(a.name)?.code ?? a.name
    const codeB = getApartmentContent(b.name)?.code ?? b.name
    const ia = APARTMENT_DISPLAY_ORDER.indexOf(codeA)
    const ib = APARTMENT_DISPLAY_ORDER.indexOf(codeB)
    return (ia === -1 ? Infinity : ia) - (ib === -1 ? Infinity : ib)
  })
  const photos = ordered.flatMap((u) => photosByUnit[u.id] ?? []).slice(0, 6)
  return (
    <section className="bg-brand-ink py-16">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <h2 className="text-center font-serif text-3xl text-white">Gallery</h2>
        <div className="mt-8 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {photos.map((p) => (
            <ResponsiveImage key={p.src} src={p.src} alt={p.alt} width={400} height={300} className="h-32 w-full rounded object-cover sm:h-40" />
          ))}
        </div>
        <div className="mt-6 text-center">
          <Link to="/gallery" className="text-sm font-semibold text-brand-gold underline">
            View full gallery →
          </Link>
        </div>
      </div>
    </section>
  )
}

function TrustSection() {
  // Every item here is a verified, existing business rule — no fabricated
  // reviews/ratings (see WEBSITE-00's finding on Enterprise v5's unverified
  // "4.8 · 324 reviews" claim) and no invented perks. Cancellation terms
  // match FaqPage.tsx exactly; payment/WhatsApp are live features elsewhere
  // on this site; early check-in/late check-out are confirmed
  // owner-provided policy, always framed as "subject to availability"
  // rather than a guarantee.
  const items = [
    { icon: <IconShield />, title: 'Secure Direct Booking', text: 'Payments processed securely through Razorpay, direct with us — no OTA middleman.' },
    { icon: <IconChat />, title: 'Instant WhatsApp Assistance', text: 'Reach us on WhatsApp before, during, and after your stay.' },
    { icon: <IconCalendarCheck />, title: 'Free Cancellation', text: 'Cancellations more than 48 hours before check-in are eligible for a 100% refund of the booking amount.' },
    { icon: <IconCalendarPercent />, title: '50% Refund Within 48 Hours', text: `Cancellations within 48 hours of check-in are eligible for a 50% refund; the remaining 50% is retained as a late-cancellation charge. ${POST_CHECKIN_CANCELLATION_POLICY}` },
    { icon: <IconClock />, title: 'Early Check-in', text: 'Available on request, subject to availability.' },
    { icon: <IconClock />, title: 'Late Check-out', text: 'Available on request, subject to availability.' },
  ]
  return (
    <section className="bg-brand-green py-16 text-white">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <h2 className="text-center font-serif text-3xl">Direct Booking Benefits</h2>
        <div className="mt-8 grid grid-cols-1 gap-8 sm:grid-cols-2 md:grid-cols-3">
          {items.map((item) => (
            <div key={item.title} className="text-center">
              <span className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-white/10 text-brand-gold" aria-hidden="true">
                {item.icon}
              </span>
              <p className="mt-3 font-serif text-lg text-brand-gold">{item.title}</p>
              <p className="mt-2 text-sm text-white/60">{item.text}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

/** Deliberately no rating NUMBER, review count, or review text, since none
 * is verified — the five stars below are a fixed, visual-only "5-star
 * experience" mark, not a real aggregate rating and not an input: they
 * carry no onClick/button semantics and cannot be used to submit a rating
 * from this site. Links to the real, existing Google Maps listing (same
 * place ID already used in Footer.tsx / LocationPage.tsx) so the section is
 * functional rather than a static promise of reviews to come. */
function GoogleReviewsSection() {
  useEffect(() => {
    if (window.location.hash !== '#guest-reviews') return
    document.getElementById('guest-reviews')?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  return (
    <section id="guest-reviews" className="mx-auto max-w-6xl px-4 py-16 text-center sm:px-6">
      <div className="flex justify-center gap-1 text-brand-gold-4" aria-label="Five star rating (visual only)" role="img">
        <IconStarFilled />
        <IconStarFilled />
        <IconStarFilled />
        <IconStarFilled />
        <IconStarFilled />
      </div>
      <h2 className="mt-4 font-serif text-3xl text-brand-green">Guest Reviews</h2>
      <p className="mx-auto mt-2 max-w-md text-brand-muted">See what our guests are saying on Google.</p>
      <a
        href="https://www.google.com/maps/place//data=!4m2!3m1!1s0x3a53615790e2d601:0x9b1df761cd2112ca"
        target="_blank"
        rel="noreferrer"
        className="mt-6 inline-block rounded border border-brand-border px-6 py-3 text-sm font-semibold tracking-wide text-brand-green uppercase hover:border-brand-gold hover:text-brand-gold-4"
      >
        Read Reviews on Google →
      </a>
    </section>
  )
}

function LocationTeaser() {
  return (
    <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <div className="text-center">
        <h2 className="font-serif text-3xl text-brand-green">Explore Pondicherry from Sri Apartment</h2>
        <p className="mx-auto mt-2 max-w-2xl text-brand-muted">
          Beaches, heritage streets, cultural landmarks and Auroville are all a short drive away — Sri Apartment puts you close to everything Pondicherry has to offer.
        </p>
      </div>

      <iframe
        title="Sri Apartment Homestay location map"
        src={PROPERTY_MAP_EMBED_URL}
        className="mt-8 aspect-[21/9] w-full rounded-lg border-0"
        loading="lazy"
        referrerPolicy="no-referrer-when-downgrade"
      />

      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {NEARBY_ATTRACTIONS.map((a) => {
          const Icon = ATTRACTION_ICONS[a.icon]
          return (
            <div key={a.name} className="rounded-lg border border-brand-border bg-brand-cream-2 p-5">
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-white text-brand-gold-4" aria-hidden="true">
                <Icon />
              </span>
              <p className="mt-3 font-serif text-lg text-brand-green">{a.name}</p>
              <p className="mt-1 text-sm text-brand-muted">~{a.driveTimeMin} min drive</p>
              <a
                href={a.directionsUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-block text-xs font-bold tracking-wide text-brand-gold-4 uppercase hover:underline"
              >
                Get Directions →
              </a>
            </div>
          )
        })}
      </div>

      <p className="mt-6 text-center text-xs text-brand-muted">Drive times are approximate and may vary with traffic.</p>

      <div className="mt-4 text-center">
        <Link to="/location" className="text-sm font-semibold text-brand-green underline">
          View full location & directions →
        </Link>
      </div>
    </section>
  )
}

function FaqTeaser() {
  return (
    <section className="bg-brand-cream-2 py-16 text-center">
      <h2 className="font-serif text-3xl text-brand-green">Questions?</h2>
      <p className="mt-2 text-brand-muted">Check-in, payment, cancellation and more.</p>
      <Link to="/faq" className="mt-4 inline-block text-sm font-semibold text-brand-green underline">
        Read our FAQ →
      </Link>
    </section>
  )
}

function FinalCta() {
  const { whatsappNumber } = useBusinessContact()
  return (
    <section className="bg-brand-green-3 py-16 text-center text-white">
      <h2 className="font-serif text-3xl">Ready to book?</h2>
      <p className="mt-2 text-white/60">Instant confirmation, no OTA fees.</p>
      <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
        <Link to="/book" className="rounded bg-brand-gold px-8 py-3 text-sm font-bold tracking-widest text-brand-green-3 uppercase">
          Book Now
        </Link>
        {whatsappNumber && (
          <a href={`https://wa.me/${whatsappNumber}`} target="_blank" rel="noreferrer" className="rounded border border-white/40 px-8 py-3 text-sm font-semibold tracking-widest text-white uppercase">
            WhatsApp Us
          </a>
        )}
      </div>
    </section>
  )
}
