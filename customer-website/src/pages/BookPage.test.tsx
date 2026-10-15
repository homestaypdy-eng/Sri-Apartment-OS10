import { beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { BookPage } from './BookPage'
import { CustomerAuthProvider } from '../context/CustomerAuthContext'
import * as api from '../lib/api'
import { ApiError } from '../lib/api'

vi.mock('../lib/api', async () => ({ ...await vi.importActual('../lib/api'), getPublicUnits: vi.fn(), getAvailability: vi.fn(), getQuote: vi.fn(), customerGetMe: vi.fn(), getPaymentAvailability: vi.fn(), getPublicBusinessInfo: vi.fn(), createHold: vi.fn(), customerLogout: vi.fn(), getUnitPhotos: vi.fn().mockResolvedValue([]) }))
vi.mock('../hooks/useUnitPhotos', () => ({ useUnitPhotos: () => ({}) }))
const unit = { id: 'synthetic-unit', name: 'Synthetic Flat', max_occupancy: 4, base_rate: 2000, unit_type: 'apartment', property_name: 'Synthetic Property' }
const quote = { unit_id: unit.id, unit_name: unit.name, nights: 2, daily_breakdown: [], subtotal_before_discount: 4000, long_stay_discount_amount: 0, promo_discount_amount: 0, cgst: 0, sgst: 0, igst: 200, total: 4200, gst_rate_percent: 5 }
const profile = { id: 'synthetic-customer', first_name: 'Synthetic', last_name: 'Guest', email: 'synthetic@example.com', phone: '+919876543210', email_verified_at: '2026-10-14T12:00:00Z' }
function draft() { sessionStorage.setItem('sri_booking_selection', JSON.stringify({ selectedUnit: unit, checkIn: '2027-01-10', checkOut: '2027-01-12', guests: 3, quote, savedAt: Date.now() })) }
function mount() { return render(<MemoryRouter initialEntries={['/book']}><CustomerAuthProvider><Routes><Route path="/book" element={<BookPage />} /><Route path="/login" element={<h1>Existing Login</h1>} /><Route path="/signup" element={<h1>Existing Signup</h1>} /></Routes></CustomerAuthProvider></MemoryRouter>) }
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.mocked(api.getPaymentAvailability).mockResolvedValue({ available: false }); vi.mocked(api.getPublicBusinessInfo).mockResolvedValue({ whatsapp_business_number: null } as never); vi.mocked(api.getPublicUnits).mockResolvedValue([unit] as never); vi.mocked(api.getAvailability).mockResolvedValue({ available: true, estimated_total: 4200 } as never); vi.mocked(api.getQuote).mockResolvedValue(quote as never) })

it('keeps search anonymous, then gates selection before customer details and preserves the draft', async () => {
  mount()
  fireEvent.change(screen.getByLabelText('Check-in'), { target: { value: '2027-01-10' } })
  fireEvent.change(screen.getByLabelText('Check-out'), { target: { value: '2027-01-12' } })
  fireEvent.change(screen.getByLabelText('Guests'), { target: { value: '3' } })
  fireEvent.click(screen.getByRole('button', { name: /Check Availability/ }))
  await screen.findByText('Synthetic Flat')
  expect(screen.queryByRole('heading', { name: 'Log In / Sign Up' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Synthetic Flat/ }))
  await screen.findByRole('heading', { name: 'Log In / Sign Up' })
  expect(screen.queryByLabelText(/Full name/)).not.toBeInTheDocument()
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('sri_booking_selection')!).guests).toBe(3))
  fireEvent.click(screen.getByRole('link', { name: 'Log In' }))
  await screen.findByRole('heading', { name: 'Existing Login' })
})

it('restores the selected booking after verified login and prefills customer details', async () => {
  draft(); localStorage.setItem('customer_access_token', 'synthetic-token')
  vi.mocked(api.customerGetMe).mockResolvedValue(profile as never)
  mount()
  await screen.findByLabelText(/Full name/)
  expect(screen.getByLabelText(/Full name/)).toHaveValue('Synthetic Guest')
  expect(screen.getByLabelText(/Email/)).toHaveValue(profile.email)
  expect(screen.queryByRole('heading', { name: 'Log In / Sign Up' })).not.toBeInTheDocument()
})

it.each([new ApiError('Expired', 401), new ApiError('Unavailable', 503)])('fails closed when backend session validation fails', async (error) => {
  draft(); localStorage.setItem('customer_access_token', 'synthetic-token'); vi.mocked(api.customerGetMe).mockRejectedValue(error)
  mount()
  await act(async () => {})
  expect(screen.getByRole('heading', { name: 'Log In / Sign Up' })).toBeInTheDocument()
  expect(screen.queryByLabelText(/Full name/)).not.toBeInTheDocument()
  expect(sessionStorage.getItem('sri_booking_selection')).not.toBeNull()
})

it('blocks an unverified account from customer details', async () => {
  draft(); localStorage.setItem('customer_access_token', 'synthetic-token'); vi.mocked(api.customerGetMe).mockResolvedValue({ ...profile, email_verified_at: null } as never)
  mount()
  await screen.findByText(/Please log in and complete contact verification/)
  expect(screen.queryByLabelText(/Full name/)).not.toBeInTheDocument()
})

it('rechecks availability when going back from a restored booking instead of inventing an empty result', async () => {
  draft(); localStorage.setItem('customer_access_token', 'synthetic-token'); vi.mocked(api.customerGetMe).mockResolvedValue(profile as never)
  mount()
  await screen.findByLabelText(/Full name/)
  fireEvent.click(screen.getByRole('button', { name: '← Back' }))
  await screen.findByRole('button', { name: /Synthetic Flat/ })
  expect(api.getAvailability).toHaveBeenCalled()
})

it('fetches an unfinished quotation when returning from a quick authentication redirect', async () => {
  draft()
  const saved = JSON.parse(sessionStorage.getItem('sri_booking_selection')!)
  sessionStorage.setItem('sri_booking_selection', JSON.stringify({ ...saved, quote: null }))
  localStorage.setItem('customer_access_token', 'synthetic-token'); vi.mocked(api.customerGetMe).mockResolvedValue(profile as never)
  mount()
  await screen.findByLabelText(/Full name/)
  expect(api.getQuote).toHaveBeenCalledWith(expect.objectContaining({ unit_id: unit.id, check_in: saved.checkIn, check_out: saved.checkOut }))
})


async function openReview() {
  draft(); localStorage.setItem('customer_access_token', 'synthetic-token')
  vi.mocked(api.customerGetMe).mockResolvedValue(profile as never)
  mount()
  await screen.findByLabelText(/Full name/)
  fireEvent.click(screen.getByRole('button', { name: 'Review Booking →' }))
  await screen.findByRole('heading', { name: 'Booking Review' })
}

it('replaces payment with a configured WhatsApp action when the provider is unavailable', async () => {
  vi.mocked(api.getPublicBusinessInfo).mockResolvedValue({ whatsapp_business_number: '+918870237237' } as never)
  await openReview()
  const contact = await screen.findByRole('link', { name: 'Contact on WhatsApp' })
  expect(contact).toHaveAttribute('href', 'https://wa.me/918870237237')
  expect(screen.queryByRole('button', { name: /Pay Securely/ })).not.toBeInTheDocument()
  expect(screen.getByText(/Your booking has not been confirmed/)).toBeInTheDocument()
  expect(api.createHold).not.toHaveBeenCalled()
})

it('never invents a contact action when WhatsApp is not configured', async () => {
  await openReview()
  await screen.findByRole('heading', { name: 'Online payment is currently unavailable' })
  expect(screen.queryByRole('link', { name: 'Contact on WhatsApp' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Pay Securely/ })).not.toBeInTheDocument()
})

it('requires terms and an affirmative server availability result before enabling payment', async () => {
  vi.mocked(api.getPaymentAvailability).mockResolvedValue({ available: true })
  await openReview()
  const pay = await screen.findByRole('button', { name: /Pay Securely/ })
  expect(pay).toBeDisabled()
  fireEvent.click(pay)
  expect(api.createHold).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept the booking terms/ }))
  expect(pay).toBeEnabled()
})

it('fails closed when payment availability cannot be checked', async () => {
  vi.mocked(api.getPaymentAvailability).mockRejectedValue(new ApiError('Offline', 503))
  await openReview()
  await screen.findByText(/We could not check online payment availability/)
  expect(screen.queryByRole('button', { name: /Pay Securely/ })).not.toBeInTheDocument()
  expect(api.createHold).not.toHaveBeenCalled()
})

it('requires reauthentication if the payment availability check finds an expired session', async () => {
  vi.mocked(api.getPaymentAvailability).mockRejectedValue(new ApiError('Expired', 401))
  await openReview()
  await screen.findByRole('heading', { name: 'Log In / Sign Up' })
  expect(screen.queryByRole('button', { name: /Pay Securely/ })).not.toBeInTheDocument()
  expect(api.createHold).not.toHaveBeenCalled()
})

it('ignores forged restored payment and details-completed state', async () => {
  draft()
  const saved=JSON.parse(sessionStorage.getItem('sri_booking_selection')!)
  sessionStorage.setItem('sri_booking_selection',JSON.stringify({ ...saved, step: 'payment', detailsReviewed: true, guestDetails: { name: 'Forged', phone: '9876543210', email: 'forged@example.com' } }))
  localStorage.setItem('customer_access_token','synthetic-token'); vi.mocked(api.customerGetMe).mockResolvedValue(profile as never)
  mount()
  await screen.findByLabelText(/Full name/)
  expect(screen.queryByRole('heading', { name: 'Booking Review' })).not.toBeInTheDocument()
  expect(api.createHold).not.toHaveBeenCalled()
})

it('removes payment after an order endpoint reports the provider became unavailable', async () => {
  vi.mocked(api.getPaymentAvailability).mockResolvedValue({ available: true })
  vi.mocked(api.createHold).mockRejectedValue(new ApiError('Gateway disabled',501))
  await openReview()
  fireEvent.click(screen.getByRole('checkbox', { name: /I accept the booking terms/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Pay Securely/ }))
  await screen.findByRole('heading', { name: 'Online payment is currently unavailable' })
  expect(screen.queryByRole('button', { name: /Pay Securely/ })).not.toBeInTheDocument()
  expect(api.createHold).toHaveBeenCalledOnce()
})
