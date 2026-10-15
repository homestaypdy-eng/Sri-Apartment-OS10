/**
 * Deterministic intent matching for the "Ask Sri Apartment" concierge.
 *
 * No external AI, no paid API — plain keyword/regex rules over verified
 * content already in this codebase (content/faq.ts, content/apartments.ts,
 * content/location.ts). If a message doesn't confidently match one of
 * these rules, the caller must show the fixed "I don't have confirmed
 * information" fallback rather than guess — this module never returns a
 * low-confidence guess, only a match or 'unknown'.
 */
import { NEARBY_ATTRACTIONS, type NearbyAttraction } from '../content/location'
import { FAQS } from '../content/faq'
import { MAX_GUESTS_DISPLAY } from '../content/apartments'

export type IntentId =
  | 'checkin_time'
  | 'checkout_time'
  | 'early_checkin'
  | 'late_checkout'
  | 'cancellation'
  | 'max_guests'
  | 'amenity'
  | 'attraction'
  | 'property_location'
  | 'availability'
  | 'price'
  | 'how_to_book'
  | 'whatsapp'
  | 'unknown'

function normalize(text: string): string {
  return text.trim().toLowerCase()
}

// Order matters: more specific rules (early/late) are tested before the
// generic check-in/check-out time rules they'd otherwise also match.
const RULES: Array<{ id: IntentId; test: (t: string) => boolean }> = [
  {
    id: 'early_checkin',
    test: (t) => /early.*check[\s-]?in|check[\s-]?in.*early|before\s*12:?30|arrive\s+(before|earlier)|check[\s-]?in.*(before|earlier)/.test(t),
  },
  {
    id: 'late_checkout',
    test: (t) => /late.*check[\s-]?out|check[\s-]?out.*late|check[\s-]?out.*(after|later)|leave\s+later|stay\s+(past|after|longer)/.test(t),
  },
  {
    id: 'checkin_time',
    test: (t) => /check[\s-]?in.*(time|when)|what time.*check[\s-]?in|when.*check[\s-]?in/.test(t),
  },
  {
    id: 'checkout_time',
    test: (t) => /check[\s-]?out.*(time|when)|what time.*check[\s-]?out|when.*(check[\s-]?out|leave)/.test(t),
  },
  { id: 'cancellation', test: (t) => /cancel/.test(t) },
  {
    id: 'max_guests',
    test: (t) => /how many (people|guests)|max(imum)?\s*(guests?|occupancy)|guests?.*stay|stay.*guests?/.test(t),
  },
  {
    id: 'amenity',
    test: (t) => /amenit|facilit|kitchen|wi-?fi|internet|parking|pool|swimming|gym|fitness|air.?condition|\bac\b|breakfast/.test(t),
  },
  {
    id: 'how_to_book',
    test: (t) => /how (do|can) i book|how to book|book direct/.test(t),
  },
  {
    id: 'whatsapp',
    test: (t) => /whatsapp|talk to (a )?(human|person|someone|staff)|real person|speak to/.test(t),
  },
  {
    id: 'price',
    test: (t) => /price|cost|how much|\brate\b|charge/.test(t),
  },
  {
    id: 'availability',
    test: (t) => /avail|vacan|do you have (a |any )?room|which apartment|any apartment/.test(t),
  },
  {
    // Directions TO the property itself — checked before the generic
    // 'attraction' rule (which is FROM the property TO somewhere else) so
    // "how do I get there?" resolves correctly instead of falling through
    // to 'unknown'.
    id: 'property_location',
    test: (t) =>
      /how (do|can) (i|we) (get|reach) (there|here|to (you|sri apartment|the property))|where are you located|your location|directions to (sri apartment|you|your (place|property))|how to reach( you)?\b/.test(
        t,
      ),
  },
  {
    id: 'attraction',
    test: (t) => /how far|how long|distance|direction|get to|reach\b/.test(t),
  },
]

/** Alias map so "promenade", "ashram", "chunnambar" etc. all resolve to
 * the correct verified attraction without requiring the exact card title. */
const ATTRACTION_ALIASES: Array<{ pattern: RegExp; name: string }> = [
  { pattern: /rock beach|promenade/, name: 'Rock Beach & Promenade' },
  { pattern: /white town|french quarter/, name: 'White Town (French Quarter)' },
  { pattern: /aurobindo|ashram/, name: 'Sri Aurobindo Ashram' },
  { pattern: /auroville/, name: 'Auroville' },
  { pattern: /paradise beach|chunnambar|boat house/, name: 'Paradise Beach (via Chunnambar Boat House)' },
  { pattern: /railway|train station|\bstation\b/, name: 'Pondicherry Railway Station' },
]

export function matchAttraction(text: string): NearbyAttraction | null {
  const t = normalize(text)
  const alias = ATTRACTION_ALIASES.find((a) => a.pattern.test(t))
  if (!alias) return null
  return NEARBY_ATTRACTIONS.find((a) => a.name === alias.name) ?? null
}

export function matchIntent(text: string): IntentId {
  const t = normalize(text)
  if (!t) return 'unknown'
  const rule = RULES.find((r) => r.test(t))
  if (!rule) return 'unknown'
  // 'attraction' only counts as a real match if we can actually identify a
  // verified attraction — "how far is some random hotel" must fall through
  // to 'unknown' rather than a half-matched, empty answer.
  if (rule.id === 'attraction' && !matchAttraction(t)) return 'unknown'
  return rule.id
}

function faqAnswer(matchQ: RegExp): string | null {
  return FAQS.find((f) => matchQ.test(f.q))?.a ?? null
}

/** Verified amenity facts — matches exactly what's already displayed on
 * the Home page's apartment cards (Phase 1), not the raw per-unit
 * amenities arrays in content/apartments.ts, which vary slightly per unit
 * and would contradict what's already live. Anything not in this map is
 * genuinely unconfirmed, not silently assumed absent. */
const VERIFIED_AMENITIES: Record<string, boolean> = {
  kitchen: true,
  wifi: true,
  internet: true,
  parking: true,
  'air condition': true,
  ac: true,
}

export function answerAmenityQuestion(text: string): { confirmed: boolean; text: string } {
  const t = normalize(text)
  const known = Object.keys(VERIFIED_AMENITIES).find((k) => t.includes(k))
  if (known && VERIFIED_AMENITIES[known]) {
    return { confirmed: true, text: 'Yes — every Sri Apartment unit has 2 AC bedrooms, a full kitchen, free WiFi, and free parking.' }
  }
  return { confirmed: false, text: "I don't have confirmed information for that." }
}

export const STATIC_ANSWERS: Partial<Record<IntentId, string>> = {
  checkin_time: 'Our standard check-in time is 12:30 PM.',
  checkout_time: 'Our standard check-out time is 11:00 AM.',
  early_checkin: 'Early check-in may be possible depending on availability. Please contact us on WhatsApp to confirm.',
  late_checkout: 'Late check-out may be possible depending on availability. Please contact us on WhatsApp to confirm.',
  cancellation: faqAnswer(/cancellation/) ?? undefined,
  max_guests: `Up to ${MAX_GUESTS_DISPLAY} guests can stay in any Sri Apartment unit.`,
  how_to_book: 'Book directly on this website — choose your dates and guests, review live pricing from our booking system, and pay securely online. No OTA fees.',
}

export const FALLBACK_TEXT = "I don't have confirmed information for that. Please contact us on WhatsApp and our team will help you."
