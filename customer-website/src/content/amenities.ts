/**
 * Grouped view of the approved, standardized amenity list (identical across
 * all six apartments — see APPROVED_AMENITIES in content/apartments.ts).
 * Derived from APARTMENT_CONTENT rather than duplicating the 14 strings a
 * second time, so the two files can never drift out of sync again.
 */
import { APARTMENT_CONTENT } from './apartments'

export interface AmenityGroup {
  title: string
  items: string[]
}

const ALL_AMENITIES = Array.from(new Set(APARTMENT_CONTENT.flatMap((a) => a.amenities)))

function pick(...names: string[]): string[] {
  return names.filter((n) => ALL_AMENITIES.includes(n))
}

export const AMENITY_GROUPS: AmenityGroup[] = [
  { title: 'Bedrooms & Bathrooms', items: pick('2 AC Bedrooms', '2 Attached Bathrooms', 'Family-Friendly Environment') },
  { title: 'Connectivity & Entertainment', items: pick('High-speed WiFi', 'Smart TV') },
  { title: 'Kitchen & Dining', items: pick('Kitchen', 'Living/Dining', 'LPG Kitchen', 'Refrigerator', 'RO Water Purifier', 'Hot Water / Geyser') },
  { title: 'Utility', items: pick('Washing Machine', 'Lift Access') },
  { title: 'Parking', items: pick('Free Parking') },
]
  // Drop any group left empty (e.g. if a future edit removes the last item in a category)
  .filter((g) => g.items.length > 0)

/** Every apartment currently carries the identical approved amenity list
 * (business rule — see content/apartments.ts), so this always returns all
 * six codes today; kept as a function (not a constant) so the amenities
 * page still displays correctly if a future apartment's list ever diverges. */
export function apartmentsWithAmenity(amenity: string): string[] {
  return APARTMENT_CONTENT.filter((a) => a.amenities.includes(amenity)).map((a) => a.code)
}
