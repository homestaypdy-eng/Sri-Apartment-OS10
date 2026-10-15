import { describe, it, expect, vi, afterEach } from 'vitest'
import { fetchBusinessContact, FALLBACK_BUSINESS_CONTACT } from './businessContact'

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = init
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      statusText: ok ? 'OK' : 'Error',
      json: async () => body,
    }),
  )
}

describe('fetchBusinessContact — OS10 Business Settings is the single source of truth', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('maps a live OS10 response to the site-wide contact shape', async () => {
    mockFetchOnce({
      company_name: 'SRI APARTMENT HOMESTAY',
      address: '163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010',
      contact_phone: '+91 8870237237',
      contact_email: 'homestaypdy@gmail.com',
      whatsapp_business_number: '918870237237',
      house_rules: 'No smoking indoors.',
    })

    const contact = await fetchBusinessContact()

    expect(contact).toEqual({
      companyName: 'SRI APARTMENT HOMESTAY',
      address: '163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010',
      email: 'homestaypdy@gmail.com',
      whatsappNumber: '918870237237',
      houseRules: 'No smoking indoors.',
    })
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toContain('/api/v1/public/business-info')
  })

  it('reflects an admin-edited value immediately on the next fetch — no code change involved', async () => {
    mockFetchOnce({
      company_name: 'SRI APARTMENT HOMESTAY',
      address: 'A brand-new address the admin just typed into Settings',
      contact_phone: '+91 8870237237',
      contact_email: 'homestaypdy@gmail.com',
      whatsapp_business_number: '918870237237',
    })

    const contact = await fetchBusinessContact()

    expect(contact.address).toBe('A brand-new address the admin just typed into Settings')
  })

  it('falls back to the last-known-good contact info only when OS10 is genuinely unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const contact = await fetchBusinessContact()

    expect(contact).toEqual(FALLBACK_BUSINESS_CONTACT)
  })

  it('falls back on a non-2xx response too, rather than showing a broken/empty value', async () => {
    mockFetchOnce({ detail: 'Internal Server Error' }, { ok: false, status: 500 })

    const contact = await fetchBusinessContact()

    expect(contact).toEqual(FALLBACK_BUSINESS_CONTACT)
  })
})
