import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { HomePage } from './HomePage'
import { addDaysIso, todayIso } from '../lib/booking'

/** Stands in for BookPage: shows the prefill state the homepage hands over. */
function BookProbe() {
  const location = useLocation()
  return <pre data-testid="book-state">{JSON.stringify(location.state)}</pre>
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/book" element={<BookProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

const inDays = (n: number) => addDaysIso(todayIso(), n)

beforeEach(() => {
  // HomePage also loads units/photos/business info; none of that is under
  // test here, so every API call simply comes back empty.
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string) =>
      String(url).includes('/public/units') ? { ok: true, status: 200, json: async () => [] } : { ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) },
    ),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HomePage floating booking bar', () => {
  it('renders exactly one booking form, without the old standalone section', () => {
    renderHome()

    expect(screen.getAllByLabelText('Check-in')).toHaveLength(1)
    expect(screen.getAllByLabelText('Check-out')).toHaveLength(1)
    expect(screen.getAllByLabelText('Guests')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Check Availability' })).toHaveLength(1)
    expect(screen.queryByText(/See Real Pricing/i)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Check availability' })).toBeInTheDocument()
  })

  it('keeps the hero heading and both hero buttons', () => {
    renderHome()

    expect(screen.getByRole('heading', { level: 1, name: 'Sri Apartment Homestay' })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Book Now' })[0]).toHaveAttribute('href', '/book')
    expect(screen.getAllByRole('link', { name: 'View Accommodations' })[0]).toHaveAttribute('href', '/apartments')
  })

  it('shows date placeholders only until a date is chosen', () => {
    renderHome()
    expect(screen.getByText('Select check-in date')).toBeInTheDocument()
    expect(screen.getByText('Select check-out date')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Check-in'), { target: { value: inDays(10) } })
    expect(screen.queryByText('Select check-in date')).not.toBeInTheDocument()
    expect(screen.getByText('Select check-out date')).toBeInTheDocument()
  })

  it('shows the "Why Choose" heading and four feature cards below the bar', () => {
    renderHome()
    expect(screen.getByText('Why Choose Sri Apartment')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'A Comfortable Stay in the Heart of Puducherry' })).toBeInTheDocument()
    for (const title of ['Book Direct', 'Family-Friendly', 'Central Location', 'Instant Confirmation']) {
      expect(screen.getByRole('heading', { level: 3, name: title })).toBeInTheDocument()
    }
  })

  it('requires check-out to be at least one day after check-in', () => {
    renderHome()
    const checkIn = screen.getByLabelText('Check-in')
    const checkOut = screen.getByLabelText('Check-out')

    expect(checkIn).toHaveAttribute('min', todayIso())
    expect(checkOut).toHaveAttribute('min', inDays(1))

    fireEvent.change(checkIn, { target: { value: inDays(10) } })
    expect(checkOut).toHaveAttribute('min', inDays(11))
  })

  it('clears a check-out that no longer leaves at least one night', () => {
    renderHome()
    const checkIn = screen.getByLabelText('Check-in')
    const checkOut = screen.getByLabelText('Check-out')

    fireEvent.change(checkIn, { target: { value: inDays(10) } })
    fireEvent.change(checkOut, { target: { value: inDays(12) } })
    fireEvent.change(checkIn, { target: { value: inDays(12) } })

    expect(checkOut).toHaveValue('')
  })

  it('blocks a same-day stay on submit and stays on the homepage', () => {
    renderHome()
    fireEvent.change(screen.getByLabelText('Check-in'), { target: { value: inDays(10) } })
    fireEvent.change(screen.getByLabelText('Check-out'), { target: { value: inDays(10) } })
    // fireEvent.submit skips the browser's native min check, exercising the
    // JS validation that backs it up.
    fireEvent.submit(screen.getByRole('button', { name: 'Check Availability' }).closest('form')!)

    expect(screen.getByRole('alert')).toHaveTextContent(/check-out must be after check-in/i)
    expect(screen.queryByTestId('book-state')).not.toBeInTheDocument()
  })

  it('navigates to /book with the chosen dates and guests', () => {
    renderHome()
    fireEvent.change(screen.getByLabelText('Check-in'), { target: { value: inDays(10) } })
    fireEvent.change(screen.getByLabelText('Check-out'), { target: { value: inDays(13) } })
    fireEvent.change(screen.getByLabelText('Guests'), { target: { value: '4' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check Availability' }))

    expect(JSON.parse(screen.getByTestId('book-state').textContent!)).toEqual({ checkIn: inDays(10), checkOut: inDays(13), guests: 4 })
  })
})
