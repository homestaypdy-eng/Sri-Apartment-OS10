import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ApartmentDetailPage } from './ApartmentDetailPage'
import type { PublicUnitOut } from '../lib/types'

const BASE_UNIT: PublicUnitOut = {
  id: 'test-unit-1',
  name: 'Test Unit',
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

function mockFetch(unit: typeof BASE_UNIT, businessHouseRules: string | null = null) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('/api/v1/public/units') && url.includes('/photos')) {
        return { ok: true, status: 200, json: async () => [] }
      }
      if (url.includes('/api/v1/public/units')) {
        return { ok: true, status: 200, json: async () => [unit] }
      }
      if (url.includes('/api/v1/public/business-info')) {
        return {
          ok: true, status: 200,
          json: async () => ({
            company_name: 'Sri Apartment Homestay', address: 'Test Address', contact_phone: '+91 8870237237',
            contact_email: 'homestaypdy@gmail.com', whatsapp_business_number: '918870237237', house_rules: businessHouseRules,
          }),
        }
      }
      return { ok: true, status: 200, json: async () => [] }
    }),
  )
}

function renderPage(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/apartments/${id}`]}>
      <Routes>
        <Route path="/apartments/:id" element={<ApartmentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ApartmentDetailPage — structured unit details (Admin-control phase 2)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('displays bedroom and bathroom counts when OS10 has set them', async () => {
    mockFetch({ ...BASE_UNIT, bedroom_count: 2, bathroom_count: 1 })
    renderPage('test-unit-1')

    await waitFor(() => expect(screen.getByText(/2 bedrooms/i)).toBeInTheDocument())
    expect(screen.getByText(/1 bathroom\b/i)).toBeInTheDocument()
  })

  it('omits bedroom/bathroom counts entirely when unset, rather than showing "null" or a placeholder', async () => {
    mockFetch(BASE_UNIT)
    renderPage('test-unit-1')

    await waitFor(() => expect(screen.getByText('Test Unit')).toBeInTheDocument())
    expect(screen.queryByText(/bedroom/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/bathroom/i)).not.toBeInTheDocument()
  })

  it("uses the unit's own check-in/check-out time when set", async () => {
    mockFetch({ ...BASE_UNIT, checkin_time: '2:00 PM', checkout_time: '10:00 AM' })
    renderPage('test-unit-1')

    await waitFor(() => expect(screen.getByText(/2:00 PM/)).toBeInTheDocument())
    expect(screen.getByText(/10:00 AM/)).toBeInTheDocument()
  })

  it('falls back to the site default check-in/check-out time when the unit has not set its own', async () => {
    mockFetch(BASE_UNIT)
    renderPage('test-unit-1')

    await waitFor(() => expect(screen.getByText(/12:30 PM/)).toBeInTheDocument())
    expect(screen.getByText(/11:00 AM/)).toBeInTheDocument()
  })

  it("prefers the unit's own house rules over the business-wide default", async () => {
    mockFetch({ ...BASE_UNIT, house_rules: 'No pets allowed in this specific unit.' }, 'Business-wide: no smoking.')
    renderPage('test-unit-1')

    await waitFor(() => expect(screen.getByText('No pets allowed in this specific unit.')).toBeInTheDocument())
    expect(screen.queryByText('Business-wide: no smoking.')).not.toBeInTheDocument()
  })

  it('falls back to the business-wide house rules when the unit has not set its own', async () => {
    mockFetch(BASE_UNIT, 'Business-wide: no smoking.')
    renderPage('test-unit-1')

    await waitFor(() => expect(screen.getByText('Business-wide: no smoking.')).toBeInTheDocument())
  })

  it('shows no house rules section at all when neither the unit nor the business has any set', async () => {
    mockFetch(BASE_UNIT, null)
    renderPage('test-unit-1')

    await waitFor(() => expect(screen.getByText('Test Unit')).toBeInTheDocument())
    expect(screen.queryByText('House Rules')).not.toBeInTheDocument()
  })
})
