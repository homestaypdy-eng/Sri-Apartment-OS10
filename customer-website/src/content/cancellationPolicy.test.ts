import { describe, expect, it } from 'vitest'
import { FAQS, POST_CHECKIN_CANCELLATION_POLICY } from './faq'

describe('approved cancellation policy copy', () => {
  it('retains the approved refund boundaries and adds post-check-in manual review', () => {
    const answer = FAQS.find((item) => item.q === 'What is your cancellation policy?')?.a ?? ''
    expect(answer).toContain('more than 48 hours before check-in for a 100% refund')
    expect(answer).toContain('within 48 hours of check-in are eligible for a 50% refund')
    expect(answer).toContain('remaining 50% is retained as a late-cancellation charge')
    expect(answer).toContain(POST_CHECKIN_CANCELLATION_POLICY)
    expect(POST_CHECKIN_CANCELLATION_POLICY).toContain('12:30 PM Asia/Kolkata')
    expect(POST_CHECKIN_CANCELLATION_POLICY).toContain('no automatic policy refund')
    expect(POST_CHECKIN_CANCELLATION_POLICY).toContain('manual review')
  })

  it('does not promise a fixed refund posting timeline', () => {
    const answer = FAQS.find((item) => item.q === 'What is your cancellation policy?')?.a ?? ''
    expect(answer).toMatch(/posting time may vary|time .* may vary/i)
    expect(answer).not.toMatch(/5[-–]7 business days/i)
  })
})
