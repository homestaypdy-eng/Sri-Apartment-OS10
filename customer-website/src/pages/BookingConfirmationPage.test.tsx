import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { BookingConfirmationPage } from './BookingConfirmationPage'
import type { PublicBookingConfirmation } from '../lib/types'

const CONFIRMATION: PublicBookingConfirmation = {
  booking_id: 'bk-123',
  guest_id: 'g-1',
  check_in: '2099-01-01',
  check_out: '2099-01-03',
  nights: 2,
  subtotal: 7000,
  discount_amount: 0,
  total: 7350,
  invoice_id: 'inv-1',
  invoice_no: 'INV/2099/001',
  confirmation_emailed: true,
  confirmation_sms_sent: true,
  confirmation_whatsapp_sent: false,
}

function renderAt(path: string, state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <Routes>
        <Route path="/booking/confirmation" element={<BookingConfirmationPage />} />
        <Route path="/book" element={<div>Redirected to /book</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BookingConfirmationPage — only ever shows an OS10-issued confirmation', () => {
  it('renders the authoritative booking reference and total when reached with real confirmation state', () => {
    renderAt('/booking/confirmation', { confirmation: CONFIRMATION, unitName: 'Flat A1' })
    expect(screen.getByText('bk-123')).toBeInTheDocument()
    expect(screen.getByText('Flat A1')).toBeInTheDocument()
    expect(screen.getByText(/₹7,350/)).toBeInTheDocument()
  })

  it('redirects to /book instead of fabricating a confirmation when reached directly (no router state)', () => {
    renderAt('/booking/confirmation', undefined)
    expect(screen.getByText('Redirected to /book')).toBeInTheDocument()
  })

  it('claims both channels only when the backend actually confirmed both sent', () => {
    renderAt('/booking/confirmation', { confirmation: CONFIRMATION })
    expect(screen.getByText('A confirmation has been sent to your phone (SMS) and email.')).toBeInTheDocument()
  })

  it('never claims a channel succeeded when the backend reports it did not', () => {
    renderAt('/booking/confirmation', {
      confirmation: { ...CONFIRMATION, confirmation_emailed: false, confirmation_sms_sent: false },
    })
    expect(screen.queryByText(/sent to your/)).not.toBeInTheDocument()
    expect(screen.getByText('Your booking details are confirmed below.')).toBeInTheDocument()
  })

  it('claims only the channel that actually succeeded, not both', () => {
    renderAt('/booking/confirmation', {
      confirmation: { ...CONFIRMATION, confirmation_emailed: false, confirmation_sms_sent: true },
    })
    expect(screen.getByText('A confirmation has been sent to your phone (SMS).')).toBeInTheDocument()
  })

  it('treats an unknown (null) outcome the same as not-sent, never assuming success', () => {
    renderAt('/booking/confirmation', {
      confirmation: { ...CONFIRMATION, confirmation_emailed: null, confirmation_sms_sent: null },
    })
    expect(screen.queryByText(/sent to your/)).not.toBeInTheDocument()
    expect(screen.getByText('Your booking details are confirmed below.')).toBeInTheDocument()
  })

  it('claims WhatsApp only when the backend actually confirmed it sent', () => {
    renderAt('/booking/confirmation', {
      confirmation: { ...CONFIRMATION, confirmation_emailed: false, confirmation_sms_sent: false, confirmation_whatsapp_sent: true },
    })
    expect(screen.getByText('A confirmation has been sent to your WhatsApp.')).toBeInTheDocument()
  })

  it('never claims WhatsApp was sent while no provider is configured (false outcome)', () => {
    // Note: the page's static footer line always mentions "WhatsApp" (a
    // generic "message us" invite, unrelated to send status) — so this
    // asserts the specific channel-status line's exact text, not merely
    // the absence of the word "WhatsApp" anywhere on the page.
    renderAt('/booking/confirmation', {
      confirmation: { ...CONFIRMATION, confirmation_sms_sent: false, confirmation_whatsapp_sent: false },
    })
    expect(screen.getByText('A confirmation has been sent to your email.')).toBeInTheDocument()
  })

  it('lists all three channels together when all three succeeded', () => {
    renderAt('/booking/confirmation', {
      confirmation: { ...CONFIRMATION, confirmation_whatsapp_sent: true },
    })
    expect(screen.getByText('A confirmation has been sent to your phone (SMS) and WhatsApp and email.')).toBeInTheDocument()
  })
})
