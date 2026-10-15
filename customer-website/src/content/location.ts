/**
 * Verified location data for Sri Apartment Homestay and nearby Pondicherry
 * attractions.
 *
 * Origin coordinates were resolved by opening the property's own existing
 * Google Maps CID link (already used in Footer.tsx / LocationPage.tsx) in a
 * live browser session and reading the coordinates Google itself resolved
 * it to — not geocoded from the address string, not guessed.
 *
 * Each attraction's distance/drive time was read directly from a real
 * Google Maps driving-directions result FROM that origin TO the
 * attraction's own resolved Google listing (Phase 2A/2B audit) — never a
 * generic "distance from Pondicherry city centre" figure. Do not add
 * attractions or edit distances here without the same verification
 * process: resolve the destination's real coordinates, then read the
 * actual driving route from this exact origin.
 */

export const PROPERTY_COORDS = { lat: 11.9354169, lng: 79.7870981 }

/** Same address already used in Footer.tsx / LocationPage.tsx / HomePage.tsx
 * / index.html's JSON-LD — duplicated there as plain JSX text rather than
 * imported from here (those were built before this file existed). Added
 * here, additively, so new consumers (the chatbot backend) have one place
 * to read it from without introducing a fifth hardcoded copy. */
export const PROPERTY_ADDRESS = '163, 2nd Cross St, MGR Nagar South Extension, Ranga Nagar, Moolakulam, Puducherry – 605010, India'

/** Directions TO the property (destination-only — Google Maps uses the
 * visitor's current location as the origin), as opposed to
 * NEARBY_ATTRACTIONS' directionsUrl fields, which go FROM the property TO
 * an attraction. Same verified coordinates, just the other direction. */
export const PROPERTY_DIRECTIONS_URL = `https://www.google.com/maps/dir/?api=1&destination=${PROPERTY_COORDS.lat},${PROPERTY_COORDS.lng}`

/** Decimal form of the same verified Google Maps place identifier already
 * used as the hex CID (0x9b1df761cd2112ca) in the "Read Reviews on Google"
 * / "View on Google Maps" links in Footer.tsx, LocationPage.tsx, and
 * HomePage.tsx's GoogleReviewsSection — this is that exact real listing,
 * not a new or guessed place. Used for the Home page's embedded map so it
 * centers on and labels the actual "Sri Apartment Homestay" business pin
 * (verified live in-browser) instead of a generic address-text search,
 * which was previously surfacing unrelated nearby business pins instead of
 * the property itself. */
export const PROPERTY_MAP_EMBED_URL = 'https://www.google.com/maps?cid=11177361849628824266&z=17&output=embed'

function directionsUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/dir/?api=1&origin=${PROPERTY_COORDS.lat},${PROPERTY_COORDS.lng}&destination=${lat},${lng}`
}

export type AttractionIcon = 'beach' | 'landmark' | 'ashram' | 'auroville' | 'train'

export interface NearbyAttraction {
  name: string
  distanceKm: number
  driveTimeMin: number
  lat: number
  lng: number
  directionsUrl: string
  icon: AttractionIcon
}

export const NEARBY_ATTRACTIONS: NearbyAttraction[] = [
  {
    name: 'Rock Beach & Promenade',
    distanceKm: 6.8,
    driveTimeMin: 25,
    lat: 11.9339038,
    lng: 79.8361957,
    directionsUrl: directionsUrl(11.9339038, 79.8361957),
    icon: 'beach',
  },
  {
    name: 'White Town (French Quarter)',
    distanceKm: 6.4,
    driveTimeMin: 23,
    lat: 11.9313835,
    lng: 79.8338761,
    directionsUrl: directionsUrl(11.9313835, 79.8338761),
    icon: 'landmark',
  },
  {
    name: 'Sri Aurobindo Ashram',
    distanceKm: 6.7,
    driveTimeMin: 24,
    lat: 11.9361469,
    lng: 79.834131,
    directionsUrl: directionsUrl(11.9361469, 79.834131),
    icon: 'ashram',
  },
  {
    name: 'Paradise Beach (via Chunnambar Boat House)',
    distanceKm: 8.8,
    driveTimeMin: 20,
    lat: 11.8838125,
    lng: 79.8006875,
    directionsUrl: directionsUrl(11.8838125, 79.8006875),
    icon: 'beach',
  },
  {
    name: 'Auroville',
    distanceKm: 12.9,
    driveTimeMin: 28,
    lat: 12.0052483,
    lng: 79.8068922,
    directionsUrl: directionsUrl(12.0052483, 79.8068922),
    icon: 'auroville',
  },
  {
    name: 'Pondicherry Railway Station',
    distanceKm: 5.7,
    driveTimeMin: 18,
    lat: 11.9251138,
    lng: 79.8278739,
    directionsUrl: directionsUrl(11.9251138, 79.8278739),
    icon: 'train',
  },
]
