/**
 * CONTENT LAYER — NOT a business-data source.
 *
 * OS10's public API (verified: GET /api/v1/public/units) returns only
 * {id, name, base_rate, max_occupancy, property_name} — enough to run the
 * booking flow, but no photos/description/amenities. Those are marketing
 * content, and the WEBSITE-00 discovery phase explicitly scoped gallery/
 * amenities content as something the public website owns, not OS10.
 *
 * This module supplies that content, keyed by the unit `name` OS10 returns
 * (confirmed against real production data: "A1".."C6", no "Flat " prefix).
 * It is deliberately NOT used for price, availability, or anything the
 * architecture rule says OS10 must be authoritative for — see
 * ApartmentDetailPage.tsx, which merges this with the live OS10 unit.
 *
 * A real public-safe OS10 wrapper endpoint for this content (unit photos +
 * description + amenities) was proposed but not implemented this phase —
 * see the "NEW PUBLIC API WRAPPERS (proposed, not applied)" section of the
 * final report. If OS10 has no unit matching a given name, components fall
 * back to a generic placeholder rather than inventing a listing.
 *
 * Each apartment's `photos` array lists the first 4 "highlight" photos
 * first (unchanged from the original rollout — cards/detail highlight grid
 * always show photos[0..3]), followed by the rest of that unit's photo set
 * in the photographer's original file order. A handful of photos per unit
 * are shared building/common-area shots (lift, parking, building exterior,
 * rooftop terrace) rather than shots inside that specific flat — those are
 * labelled explicitly as shared amenities in their alt text so the gallery
 * never implies they're part of the apartment's own interior.
 */
import a1_1 from '../assets/apartments/A1/A1-1.jpg'
import a1_2 from '../assets/apartments/A1/A1-2.jpg'
import a1_3 from '../assets/apartments/A1/A1-3.jpg'
import a1_4 from '../assets/apartments/A1/A1-4.jpg'
import a1_5 from '../assets/apartments/A1/A1-5.jpg'
import a1_6 from '../assets/apartments/A1/A1-6.jpg'
import a1_7 from '../assets/apartments/A1/A1-7.jpg'
import a1_8 from '../assets/apartments/A1/A1-8.jpg'
import a1_9 from '../assets/apartments/A1/A1-9.jpg'
import a1_10 from '../assets/apartments/A1/A1-10.jpg'
import a1_11 from '../assets/apartments/A1/A1-11.jpg'
import a1_12 from '../assets/apartments/A1/A1-12.jpg'
import a1_13 from '../assets/apartments/A1/A1-13.jpg'
import a1_14 from '../assets/apartments/A1/A1-14.jpg'
import a1_15 from '../assets/apartments/A1/A1-15.jpg'

import a2_1 from '../assets/apartments/A2/A2-1.jpg'
import a2_2 from '../assets/apartments/A2/A2-2.jpg'
import a2_3 from '../assets/apartments/A2/A2-3.jpg'
import a2_4 from '../assets/apartments/A2/A2-4.jpg'
import a2_5 from '../assets/apartments/A2/A2-5.jpg'
import a2_6 from '../assets/apartments/A2/A2-6.jpg'
import a2_7 from '../assets/apartments/A2/A2-7.jpg'
import a2_8 from '../assets/apartments/A2/A2-8.jpg'
import a2_9 from '../assets/apartments/A2/A2-9.jpg'
import a2_10 from '../assets/apartments/A2/A2-10.jpg'
import a2_11 from '../assets/apartments/A2/A2-11.jpg'
import a2_12 from '../assets/apartments/A2/A2-12.jpg'
import a2_13 from '../assets/apartments/A2/A2-13.jpg'
import a2_14 from '../assets/apartments/A2/A2-14.jpg'
import a2_15 from '../assets/apartments/A2/A2-15.jpg'

import b3_1 from '../assets/apartments/B3/B3-1.jpg'
import b3_2 from '../assets/apartments/B3/B3-2.jpg'
import b3_3 from '../assets/apartments/B3/B3-3.jpg'
import b3_4 from '../assets/apartments/B3/B3-4.jpg'
import b3_5 from '../assets/apartments/B3/B3-5.jpg'
import b3_6 from '../assets/apartments/B3/B3-6.jpg'
import b3_7 from '../assets/apartments/B3/B3-7.jpg'
import b3_8 from '../assets/apartments/B3/B3-8.jpg'
import b3_9 from '../assets/apartments/B3/B3-9.jpg'
import b3_10 from '../assets/apartments/B3/B3-10.jpg'
import b3_11 from '../assets/apartments/B3/B3-11.jpg'
import b3_12 from '../assets/apartments/B3/B3-12.jpg'
import b3_13 from '../assets/apartments/B3/B3-13.jpg'
import b3_14 from '../assets/apartments/B3/B3-14.jpg'
import b3_15 from '../assets/apartments/B3/B3-15.jpg'
import b3_16 from '../assets/apartments/B3/B3-16.jpg'
import b3_17 from '../assets/apartments/B3/B3-17.jpg'

import b4_1 from '../assets/apartments/B4/B4-1.jpg'
import b4_2 from '../assets/apartments/B4/B4-2.jpg'
import b4_3 from '../assets/apartments/B4/B4-3.jpg'
import b4_4 from '../assets/apartments/B4/B4-4.jpg'
import b4_5 from '../assets/apartments/B4/B4-5.jpg'
import b4_6 from '../assets/apartments/B4/B4-6.jpg'
import b4_7 from '../assets/apartments/B4/B4-7.jpg'
import b4_8 from '../assets/apartments/B4/B4-8.jpg'
import b4_9 from '../assets/apartments/B4/B4-9.jpg'
import b4_10 from '../assets/apartments/B4/B4-10.jpg'
import b4_11 from '../assets/apartments/B4/B4-11.jpg'
import b4_12 from '../assets/apartments/B4/B4-12.jpg'
import b4_13 from '../assets/apartments/B4/B4-13.jpg'
import b4_14 from '../assets/apartments/B4/B4-14.jpg'
import b4_15 from '../assets/apartments/B4/B4-15.jpg'
import b4_16 from '../assets/apartments/B4/B4-16.jpg'
import b4_17 from '../assets/apartments/B4/B4-17.jpg'
import b4_18 from '../assets/apartments/B4/B4-18.jpg'

import c5_1 from '../assets/apartments/C5/C5-1.jpg'
import c5_2 from '../assets/apartments/C5/C5-2.jpg'
import c5_3 from '../assets/apartments/C5/C5-3.jpg'
import c5_4 from '../assets/apartments/C5/C5-4.jpg'
import c5_5 from '../assets/apartments/C5/C5-5.jpg'
import c5_6 from '../assets/apartments/C5/C5-6.jpg'
import c5_7 from '../assets/apartments/C5/C5-7.jpg'
import c5_8 from '../assets/apartments/C5/C5-8.jpg'
import c5_9 from '../assets/apartments/C5/C5-9.jpg'
import c5_10 from '../assets/apartments/C5/C5-10.jpg'
import c5_11 from '../assets/apartments/C5/C5-11.jpg'
import c5_12 from '../assets/apartments/C5/C5-12.jpg'
import c5_13 from '../assets/apartments/C5/C5-13.jpg'
import c5_14 from '../assets/apartments/C5/C5-14.jpg'
import c5_15 from '../assets/apartments/C5/C5-15.jpg'
import c5_16 from '../assets/apartments/C5/C5-16.jpg'
import c5_17 from '../assets/apartments/C5/C5-17.jpg'

import c6_1 from '../assets/apartments/C6/C6-1.jpg'
import c6_2 from '../assets/apartments/C6/C6-2.jpg'
import c6_3 from '../assets/apartments/C6/C6-3.jpg'
import c6_4 from '../assets/apartments/C6/C6-4.jpg'
import c6_5 from '../assets/apartments/C6/C6-5.jpg'
import c6_6 from '../assets/apartments/C6/C6-6.jpg'

export interface ApartmentContent {
  /** Matches OS10's Unit.name exactly — the join key. */
  unitName: string
  code: string
  floor: string
  bedrooms: number
  photos: { src: string; alt: string }[]
  description: string
  amenities: string[]
}

/** Confirmed business rule: every apartment sleeps up to 5 guests. This is
 * a customer-facing display value only — it does NOT feed the booking
 * flow's availability search (src/hooks/useBookingFlow.ts filters on
 * OS10's live `max_occupancy`, which stays authoritative for eligibility;
 * this constant only controls what copy is shown on the Home page cards
 * and apartment detail pages). */
export const MAX_GUESTS_DISPLAY = 5

/** Identical for all six apartments by business rule (approved content
 * standardization) — a single shared constant rather than six duplicated
 * strings, same pattern as MAX_GUESTS_DISPLAY above and
 * DEFAULT_CHECKIN_TIME/DEFAULT_CHECKOUT_TIME in ApartmentDetailPage.tsx.
 * Kept here (not a new BusinessSettings field) specifically to avoid an
 * unnecessary database schema change for content that has no per-unit
 * variation to represent. */
export const GUEST_ACCESS_TEXT =
  'Guests have exclusive access to the entire apartment with no shared spaces. Free parking and lift access are available for a smooth and comfortable stay.'

/** Approved content standardization (identical across all six apartments) —
 * matches the live OS10 Unit.description/amenities for A1-C6 exactly (each
 * apartment's per-unit `description`/`amenities` below is only the fallback
 * shown when OS10 hasn't set that content directly; kept in sync here so a
 * unit falling back to this file still shows the correct, approved copy). */
const APPROVED_DESCRIPTION =
  'Enjoy a peaceful stay at SRI APARTMENT HOMESTAY PONDICHERRY, a clean and spacious private 2BHK family apartment near White Town, beaches, JIPMER, East Coast Hospitals, cafés, and tourist attractions. Ideal for families, groups, medical visits, business trips, and long stays.'

const APPROVED_AMENITIES = [
  '2 AC Bedrooms',
  '2 Attached Bathrooms',
  'Kitchen',
  'Living/Dining',
  'High-speed WiFi',
  'Smart TV',
  'Washing Machine',
  'Refrigerator',
  'RO Water Purifier',
  'Hot Water / Geyser',
  'LPG Kitchen',
  'Lift Access',
  'Free Parking',
  'Family-Friendly Environment',
]

const SHARED_LIFT = 'Sri Apartment building — lift & staircase lobby (shared area, not part of this apartment)'
const SHARED_PARKING = 'Sri Apartment building — covered parking & entrance porch (shared area, not part of this apartment)'
const SHARED_FACADE_1 = 'Sri Apartment building — exterior view (shared property)'
const SHARED_FACADE_2 = 'Sri Apartment building — exterior view, street angle (shared property)'
const SHARED_ROOFTOP = 'Sri Apartment building — rooftop terrace lounge (shared amenity)'

export const APARTMENT_CONTENT: ApartmentContent[] = [
  {
    unitName: 'A1',
    code: 'A1',
    floor: '1st Floor',
    bedrooms: 2,
    photos: [
      { src: a1_1, alt: 'Flat A1 living room with teak furniture' },
      { src: a1_2, alt: 'Flat A1 master bedroom' },
      { src: a1_3, alt: 'Flat A1 twin bedroom' },
      { src: a1_4, alt: 'Flat A1 modular kitchen' },
      { src: a1_5, alt: 'Flat A1 kitchen and dining area' },
      { src: a1_6, alt: 'Flat A1 kitchen, wide view' },
      { src: a1_7, alt: 'Flat A1 kitchen appliances' },
      { src: a1_8, alt: 'Flat A1 bathroom' },
      { src: a1_9, alt: 'Flat A1 bathroom, alternate view' },
      { src: a1_10, alt: 'Flat A1 balcony with washing machine' },
      { src: a1_11, alt: SHARED_LIFT },
      { src: a1_12, alt: SHARED_PARKING },
      { src: a1_13, alt: SHARED_FACADE_1 },
      { src: a1_14, alt: SHARED_FACADE_2 },
      { src: a1_15, alt: SHARED_ROOFTOP },
    ],
    description: APPROVED_DESCRIPTION,
    amenities: APPROVED_AMENITIES,
  },
  {
    unitName: 'A2',
    code: 'A2',
    floor: '1st Floor',
    bedrooms: 2,
    photos: [
      { src: a2_1, alt: 'Flat A2 living room' },
      { src: a2_2, alt: 'Flat A2 master bedroom' },
      { src: a2_3, alt: 'Flat A2 twin bedroom' },
      { src: a2_4, alt: 'Flat A2 master bedroom with en-suite bathroom' },
      { src: a2_5, alt: 'Flat A2 kitchen, wide view' },
      { src: a2_6, alt: 'Flat A2 kitchen appliances' },
      { src: a2_7, alt: 'Flat A2 balcony with washing machine' },
      { src: a2_8, alt: 'Flat A2 bathroom' },
      { src: a2_9, alt: 'Flat A2 bathroom, alternate view' },
      { src: a2_10, alt: 'Flat A2 balcony washing machine, alternate view' },
      { src: a2_11, alt: SHARED_LIFT },
      { src: a2_12, alt: SHARED_PARKING },
      { src: a2_13, alt: SHARED_FACADE_1 },
      { src: a2_14, alt: SHARED_FACADE_2 },
      { src: a2_15, alt: SHARED_ROOFTOP },
    ],
    description: APPROVED_DESCRIPTION,
    amenities: APPROVED_AMENITIES,
  },
  {
    unitName: 'B3',
    code: 'B3',
    floor: '2nd Floor',
    bedrooms: 2,
    photos: [
      { src: b3_1, alt: 'Flat B3 open-plan living room' },
      { src: b3_2, alt: 'Flat B3 master bedroom' },
      { src: b3_3, alt: 'Flat B3 kitchen' },
      { src: b3_4, alt: 'Flat B3 rooftop access' },
      { src: b3_5, alt: 'Flat B3 second bedroom' },
      { src: b3_6, alt: 'Flat B3 second bedroom, alternate view' },
      { src: b3_7, alt: 'Flat B3 kitchen, wide view' },
      { src: b3_8, alt: 'Flat B3 kitchen, close view' },
      { src: b3_9, alt: 'Flat B3 kitchen appliances' },
      { src: b3_10, alt: 'Flat B3 kitchen drawer with plates' },
      { src: b3_11, alt: 'Flat B3 bathroom' },
      { src: b3_12, alt: 'Flat B3 bathroom, alternate view' },
      { src: b3_13, alt: SHARED_LIFT },
      { src: b3_14, alt: SHARED_PARKING },
      { src: b3_15, alt: SHARED_FACADE_1 },
      { src: b3_16, alt: SHARED_FACADE_2 },
      { src: b3_17, alt: SHARED_ROOFTOP },
    ],
    description: APPROVED_DESCRIPTION,
    amenities: APPROVED_AMENITIES,
  },
  {
    unitName: 'B4',
    code: 'B4',
    floor: '2nd Floor',
    bedrooms: 2,
    photos: [
      { src: b4_1, alt: 'Flat B4 living room' },
      { src: b4_2, alt: 'Flat B4 master bedroom' },
      { src: b4_3, alt: 'Flat B4 second bedroom' },
      { src: b4_4, alt: 'Flat B4 kitchen' },
      { src: b4_5, alt: 'Flat B4 living/dining area' },
      { src: b4_6, alt: 'Flat B4 living/dining area, alternate view' },
      { src: b4_7, alt: 'Flat B4 living/dining area with chandelier' },
      { src: b4_8, alt: 'Flat B4 bathroom' },
      { src: b4_9, alt: 'Flat B4 bathroom, alternate view' },
      { src: b4_10, alt: 'Flat B4 kitchen, wide view' },
      { src: b4_11, alt: 'Flat B4 kitchen appliances' },
      { src: b4_12, alt: 'Flat B4 kitchen, alternate view' },
      { src: b4_13, alt: 'Flat B4 kitchen drawer with plates' },
      { src: b4_14, alt: 'Flat B4 kitchen drawer with cutlery' },
      { src: b4_15, alt: SHARED_FACADE_1 },
      { src: b4_16, alt: SHARED_LIFT },
      { src: b4_17, alt: SHARED_PARKING },
      { src: b4_18, alt: 'Sri Apartment building — rooftop terrace (shared amenity)' },
    ],
    description: APPROVED_DESCRIPTION,
    amenities: APPROVED_AMENITIES,
  },
  {
    unitName: 'C5',
    code: 'C5',
    floor: '3rd Floor',
    bedrooms: 2,
    photos: [
      { src: c5_1, alt: 'Flat C5 living room' },
      { src: c5_2, alt: 'Flat C5 living/dining area, second view' },
      { src: c5_3, alt: 'Flat C5 master bedroom' },
      { src: c5_4, alt: 'Flat C5 twin bedroom' },
      { src: c5_5, alt: 'Flat C5 kitchen, wide view' },
      { src: c5_6, alt: 'Flat C5 kitchen appliances' },
      { src: c5_7, alt: 'Flat C5 kitchen drawer with plates' },
      { src: c5_8, alt: 'Flat C5 kitchen drawer with cutlery' },
      { src: c5_9, alt: 'Flat C5 bathroom' },
      { src: c5_10, alt: 'Flat C5 bathroom, alternate view' },
      { src: c5_11, alt: 'Flat C5 rooftop dual washing machines with city view' },
      { src: c5_12, alt: 'Flat C5 rooftop water tank & wash area' },
      { src: c5_13, alt: SHARED_LIFT },
      { src: c5_14, alt: SHARED_PARKING },
      { src: c5_15, alt: SHARED_FACADE_1 },
      { src: c5_16, alt: SHARED_FACADE_2 },
      { src: c5_17, alt: SHARED_ROOFTOP },
    ],
    description: APPROVED_DESCRIPTION,
    amenities: APPROVED_AMENITIES,
  },
  {
    unitName: 'C6',
    code: 'C6',
    floor: '3rd Floor',
    bedrooms: 2,
    photos: [
      { src: c6_1, alt: 'Flat C6 green-themed living room' },
      { src: c6_2, alt: 'Flat C6 master bedroom' },
      { src: c6_3, alt: 'Flat C6 twin bedroom' },
      { src: c6_4, alt: 'Flat C6 city-view balcony' },
      { src: c6_5, alt: 'Flat C6 bedroom and wardrobe area' },
      { src: c6_6, alt: 'Flat C6 balcony with rooftop access, city view' },
    ],
    description: APPROVED_DESCRIPTION,
    amenities: APPROVED_AMENITIES,
  },
]

/** Normalizes a unit name for matching: lowercase, trims whitespace, and
 * strips a leading "flat "/"apartment " label if present. OS10's real
 * production names are bare ("A1"), but this keeps the join resilient to
 * naming drift on either side instead of relying on an exact string match. */
export function normalizeUnitName(name: string): string {
  return name.trim().toLowerCase().replace(/^(flat|apartment)\s+/, '')
}

export function getApartmentContent(unitName: string): ApartmentContent | undefined {
  const normalized = normalizeUnitName(unitName)
  return APARTMENT_CONTENT.find((a) => normalizeUnitName(a.unitName) === normalized)
}
