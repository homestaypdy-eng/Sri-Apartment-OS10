import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useBusinessContact } from './useBusinessContact'
import { FALLBACK_BUSINESS_CONTACT } from '../lib/businessContact'

describe('useBusinessContact — Footer, Location, Contact pages, and every WhatsApp link share this', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('starts with the fallback so links/text are never blank, then swaps to the live OS10 value', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          company_name: 'SRI APARTMENT HOMESTAY',
          address: 'Live address from Settings',
          contact_phone: '+91 8870237237',
          contact_email: 'homestaypdy@gmail.com',
          whatsapp_business_number: '918870237237',
        }),
      }),
    )

    const { result } = renderHook(() => useBusinessContact())

    // Initial render, before the effect resolves.
    expect(result.current).toEqual(FALLBACK_BUSINESS_CONTACT)

    await waitFor(() => {
      expect(result.current.address).toBe('Live address from Settings')
    })
  })

  it('stays on the fallback (not blank, not stuck loading) when OS10 is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const { result } = renderHook(() => useBusinessContact())

    await waitFor(() => {
      expect(result.current).toEqual(FALLBACK_BUSINESS_CONTACT)
    })
  })
})
