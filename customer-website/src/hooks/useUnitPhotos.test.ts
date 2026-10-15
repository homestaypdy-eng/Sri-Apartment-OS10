import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useUnitPhotos } from './useUnitPhotos'
import type { PublicUnitOut } from '../lib/types'

const UNIT_A1: PublicUnitOut = {
  id: 'unit-a1', name: 'A1', unit_type: 'apartment', base_rate: 3500, max_occupancy: 5,
  property_name: 'Sri Apartment Homestay', property_type: null, description: null, amenities: null,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
}
const UNIT_A2: PublicUnitOut = {
  id: 'unit-a2', name: 'A2', unit_type: 'apartment', base_rate: 3500, max_occupancy: 5,
  property_name: 'Sri Apartment Homestay', property_type: null, description: null, amenities: null,
  bedroom_count: null, bathroom_count: null, house_rules: null, checkin_time: null, checkout_time: null,
}

describe('useUnitPhotos — shared by Home, Apartments, Apartment Detail, Gallery, and Book', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('resolves live OS10 photos for every unit in the list, keyed by unit id', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => ({
        ok: true,
        status: 200,
        // OS10 returns each photo's url as a path relative to the API host
        // (see getUnitPhotos in api.ts) — not a full URL.
        json: async () =>
          url.includes('unit-a1')
            ? [{ id: 'p1', caption: 'A1 living room', url: '/photos/p1' }]
            : [{ id: 'p2', caption: 'A2 living room', url: '/photos/p2' }],
      })),
    )

    const { result } = renderHook(() => useUnitPhotos([UNIT_A1, UNIT_A2]))

    await waitFor(() => {
      expect(result.current['unit-a1']).toEqual([{ src: 'http://localhost:8000/photos/p1', alt: 'A1 living room' }])
      expect(result.current['unit-a2']).toEqual([{ src: 'http://localhost:8000/photos/p2', alt: 'A2 living room' }])
    })
  })

  it('starts empty (no flash of the wrong unit\'s photo) before the fetch resolves', () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise(() => {})))

    const { result } = renderHook(() => useUnitPhotos([UNIT_A1]))

    expect(result.current).toEqual({})
  })
})
