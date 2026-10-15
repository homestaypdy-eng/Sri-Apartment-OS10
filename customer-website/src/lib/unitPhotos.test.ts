import { describe, it, expect, vi, afterEach } from 'vitest'
import { resolveUnitPhotos } from './unitPhotos'
import type { PublicUnitOut } from './types'

const UNIT_A1: PublicUnitOut = {
  id: 'unit-a1',
  name: 'A1',
  unit_type: 'apartment',
  base_rate: 3500,
  max_occupancy: 5,
  property_name: 'Sri Apartment Homestay',
  property_type: null,
  description: null,
  amenities: null,
  bedroom_count: null,
  bathroom_count: null,
  house_rules: null,
  checkin_time: null,
  checkout_time: null,
}

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

describe('resolveUnitPhotos — the single photo resolver every page shares', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses OS10 admin-uploaded photos when the unit has any', async () => {
    // OS10 returns each photo's url as a path relative to the API host
    // (see getUnitPhotos in api.ts) — not a full URL.
    mockFetchOnce([
      { id: 'p1', caption: 'Living room', url: '/api/v1/public/units/unit-a1/photos/p1/image' },
      { id: 'p2', caption: null, url: '/api/v1/public/units/unit-a1/photos/p2/image' },
    ])

    const photos = await resolveUnitPhotos(UNIT_A1)

    expect(photos).toEqual([
      { src: 'http://localhost:8000/api/v1/public/units/unit-a1/photos/p1/image', alt: 'Living room' },
      { src: 'http://localhost:8000/api/v1/public/units/unit-a1/photos/p2/image', alt: 'A1 photo' },
    ])
  })

  it('falls back to the static content file only when OS10 genuinely has zero photos for the unit', async () => {
    mockFetchOnce([])

    const photos = await resolveUnitPhotos(UNIT_A1)

    // The real A1 fallback content is known to have at least one photo —
    // this proves the fallback path actually engages, not just "didn't throw".
    expect(photos.length).toBeGreaterThan(0)
  })

  it('falls back to static content when the OS10 photos request fails outright', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const photos = await resolveUnitPhotos(UNIT_A1)

    expect(photos.length).toBeGreaterThan(0)
  })

  it('returns an empty list (not an error) for a unit with no OS10 photos and no matching static content', async () => {
    mockFetchOnce([])
    const unknownUnit: PublicUnitOut = { ...UNIT_A1, id: 'unit-z', name: 'Z9' }

    const photos = await resolveUnitPhotos(unknownUnit)

    expect(photos).toEqual([])
  })
})
