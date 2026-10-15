import { useEffect } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { EditProfilePage } from './EditProfilePage'
import { CustomerAuthProvider, useCustomerAuth } from '../context/CustomerAuthContext'
import type { CustomerAccountOut } from '../lib/types'

/** Stands in for the real AccountPage — proves the editor actually
 * navigates back AND that the value it displays comes from
 * CustomerAuthContext (updated by EditProfilePage's own updateCustomer
 * call), not a page reload or a second /me round-trip. */
function AccountStub() {
  const { customer } = useCustomerAuth()
  return <div>Account page reached — {customer?.first_name} {customer?.last_name}</div>
}

const CUSTOMER: CustomerAccountOut = {
  id: 'c1',
  first_name: 'Ada',
  last_name: 'Lovelace',
  email: 'ada@example.com',
  phone: '9876543210',
  email_verified_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = init
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok, status, statusText: ok ? 'OK' : 'Error', json: async () => body }),
  )
}

function LoginTrigger() {
  const { customerLogin } = useCustomerAuth()
  useEffect(() => {
    customerLogin('real.jwt.token', CUSTOMER)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return null
}

function renderAuthenticated() {
  // The extra GET /me this fires — see Nav.test.tsx's own explanation of
  // the harness-only mount-order race — resolves with the same customer.
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, statusText: 'OK', json: async () => CUSTOMER }))
  return render(
    <MemoryRouter initialEntries={['/account/edit']}>
      <CustomerAuthProvider>
        <LoginTrigger />
        <Routes>
          <Route path="/account/edit" element={<EditProfilePage />} />
          <Route path="/account" element={<AccountStub />} />
          <Route path="/login" element={<div>Login page reached</div>} />
        </Routes>
      </CustomerAuthProvider>
    </MemoryRouter>,
  )
}

describe('EditProfilePage', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('redirects to /login when not authenticated', async () => {
    render(
      <MemoryRouter initialEntries={['/account/edit']}>
        <CustomerAuthProvider>
          <Routes>
            <Route path="/account/edit" element={<EditProfilePage />} />
            <Route path="/login" element={<div>Login page reached</div>} />
          </Routes>
        </CustomerAuthProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByText('Login page reached')).toBeInTheDocument()
  })

  it('pre-fills the form from the authenticated customer, with email shown but disabled', async () => {
    renderAuthenticated()
    await waitFor(() => expect(screen.getByDisplayValue('Ada')).toBeInTheDocument())
    expect(screen.getByDisplayValue('Lovelace')).toBeInTheDocument()
    // The phone field is now the international PhoneInput — it displays
    // the national number formatted for the (assumed-default) country,
    // not the raw stored digits verbatim.
    expect(screen.getByLabelText(/phone number/i)).toHaveValue('98765 43210')
    const emailInput = screen.getByDisplayValue('ada@example.com') as HTMLInputElement
    expect(emailInput).toBeDisabled()
  })

  it('on save: shows a brief confirmation, updates CustomerAuthContext immediately, then closes the editor by returning to /account — no page refresh', async () => {
    const user = userEvent.setup()
    renderAuthenticated()
    await waitFor(() => expect(screen.getByDisplayValue('Ada')).toBeInTheDocument())

    mockFetchOnce({ ...CUSTOMER, first_name: 'Grace', last_name: 'Hopper', phone: '+919123456780' })

    const firstNameInput = screen.getByDisplayValue('Ada')
    await user.clear(firstNameInput)
    await user.type(firstNameInput, 'Grace')
    const lastNameInput = screen.getByDisplayValue('Lovelace')
    await user.clear(lastNameInput)
    await user.type(lastNameInput, 'Hopper')

    await user.click(screen.getByRole('button', { name: /save changes/i }))

    // Visible confirmation appears immediately on success...
    await waitFor(() => expect(screen.getByText(/profile updated/i)).toBeInTheDocument())
    // ...then the editor closes on its own, landing back on the account
    // view, which reads the already-updated name straight from context
    // (not a fresh /me call and not a page reload).
    expect(await screen.findByText('Account page reached — Grace Hopper', undefined, { timeout: 2000 })).toBeInTheDocument()
  })

  it('rejects an invalid phone number client-side without ever calling the API', async () => {
    const user = userEvent.setup()
    renderAuthenticated()
    await waitFor(() => expect(screen.getByDisplayValue('Ada')).toBeInTheDocument())

    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)

    await user.clear(screen.getByLabelText(/phone number/i))
    await user.type(screen.getByLabelText(/phone number/i), '123')
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(screen.getByText(/valid phone number/i)).toBeInTheDocument())
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(screen.queryByText(/account page reached/i)).not.toBeInTheDocument()
  })

  it('shows a clear error when the phone number is already used by another account, and does NOT close the editor', async () => {
    const user = userEvent.setup()
    renderAuthenticated()
    await waitFor(() => expect(screen.getByDisplayValue('Ada')).toBeInTheDocument())

    mockFetchOnce({ detail: 'An account with this phone number already exists.' }, { ok: false, status: 409 })
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(screen.getByText(/already exists/i)).toBeInTheDocument())
    // Still on the editor — a failed save must never navigate away.
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument()
    expect(screen.queryByText(/account page reached/i)).not.toBeInTheDocument()
  })
})
