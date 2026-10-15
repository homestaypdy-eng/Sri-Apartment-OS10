import { MAX_GUESTS_DISPLAY } from './apartments'

/**
 * Content sourced only from verified business facts (OS10's GST config,
 * the confirmed check-in/check-out times, the confirmed cancellation
 * policy, and each apartment's own confirmed amenity list). Nothing
 * invented, per Step 9's "if uncertain, flag it instead of guessing" —
 * see FaqPage.tsx's "not yet covered" note for what's genuinely unknown
 * rather than silently answered.
 *
 * Extracted from FaqPage.tsx (Phase 3C) so the FAQ page and the AI
 * concierge's get_booking_information tool share one source instead of
 * two copies that could drift apart. No wording was changed in the move.
 */
export const POST_CHECKIN_CANCELLATION_POLICY = 'At or after scheduled check-in (12:30 PM Asia/Kolkata), no automatic policy refund applies; cancellation and refund require manual review.'

export interface FaqItem {
  q: string
  a: string
}

export const FAQS: FaqItem[] = [
  {
    q: 'What time is check-in and check-out?',
    a: 'Check-in is from 12:30 PM and check-out is by 11:00 AM.',
  },
  {
    q: 'How many guests can stay?',
    a: `Up to ${MAX_GUESTS_DISPLAY} guests can stay in any Sri Apartment unit.`,
  },
  {
    q: 'Is WiFi available?',
    a: 'Yes, all our apartments include WiFi.',
  },
  {
    q: 'Can I use the kitchen?',
    a: 'Yes — apartments with a kitchen amenity have one equipped for guest use. Check the specific apartment’s amenities for details (some have an open-plan kitchen).',
  },
  {
    q: 'Is parking available?',
    a: 'Parking is listed as an amenity on the apartments that have it — check the specific apartment’s page, as it is not confirmed at every unit.',
  },
  {
    q: 'How do I pay, and when?',
    a: 'Full payment (room rate + GST) is collected online via Razorpay at the time of booking — no balance is due at check-in, and partial payments are not accepted.',
  },
  {
    q: 'Is GST included in the price?',
    a: 'Yes. GST is calculated automatically per current homestay rules and shown before you pay — you will never be asked to pay an amount you have not seen broken down first.',
  },
  {
    q: 'What is your cancellation policy?',
    a: `Cancel more than 48 hours before check-in for a 100% refund of the booking amount. Cancellations within 48 hours of check-in are eligible for a 50% refund; the remaining 50% is retained as a late-cancellation charge. Approved refunds will be initiated promptly to the original payment method. The time taken for the refunded amount to appear may vary depending on the payment provider, bank, card issuer, or UPI provider. ${POST_CHECKIN_CANCELLATION_POLICY}`,
  },
  {
    q: 'Can I book directly instead of through an OTA?',
    a: 'Yes — this website books directly with us, with no OTA platform fees.',
  },
]
