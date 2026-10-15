import { useEffect } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { AccountPage } from './AccountPage'
import { CustomerAuthProvider, useCustomerAuth } from '../context/CustomerAuthContext'
import type { CustomerAccountOut } from '../lib/types'

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = init
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok, status, statusText: ok ? 'OK' : 'Error', json: async () => body }),
  )
}

const CUSTOMER: CustomerAccountOut = {
  id: 'c1',
  first_name: 'Sundaramurthy',
  last_name: 'Alexandre',
  email: 'sssmurthy2003@gmail.com',
  phone: '8870237237',
  email_verified_at: null,
  phone_verified_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

function LoginTrigger({ account = CUSTOMER }: { account?: CustomerAccountOut }) {
  const { customerLogin } = useCustomerAuth()
  useEffect(() => {
    customerLogin('real.jwt.token', account)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return null
}

function LoginLocation() {
  const location = useLocation()
  return <div data-testid="login-location">Login page reached{location.search}</div>
}

function renderAuthenticated(fetchMock?: ReturnType<typeof vi.fn>, account = CUSTOMER) {
  vi.stubGlobal('fetch', fetchMock ?? vi.fn().mockImplementation((url: string) => {
    const body = String(url).includes('/identity/profile')
      ? { status: 'not_required', document_type: null, document_id: null, verified_at: null, message: null }
      : CUSTOMER
    return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => body })
  }))
  return render(
    <MemoryRouter initialEntries={['/account']}>
      <CustomerAuthProvider>
        <LoginTrigger account={account} />
        <Routes>
          <Route path="/account" element={<AccountPage />} />
          <Route path="/login" element={<LoginLocation />} />
        </Routes>
      </CustomerAuthProvider>
    </MemoryRouter>,
  )
}

describe('AccountPage', () => {
  afterEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    vi.unstubAllGlobals()
  })

  it('redirects to /login when not authenticated', async () => {
    render(
      <MemoryRouter initialEntries={['/account']}>
        <CustomerAuthProvider>
          <Routes>
            <Route path="/account" element={<AccountPage />} />
            <Route path="/login" element={<div>Login page reached</div>} />
          </Routes>
        </CustomerAuthProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByText('Login page reached')).toBeInTheDocument()
  })

  it('shows the FULL name (first + last), not just the first name shown in the navbar', async () => {
    renderAuthenticated()
    await waitFor(() => expect(screen.getByText('Welcome, Sundaramurthy Alexandre')).toBeInTheDocument())
  })

  it('shows the email and phone number', async () => {
    renderAuthenticated()
    await waitFor(() => expect(screen.getByText('sssmurthy2003@gmail.com')).toBeInTheDocument())
    expect(screen.getByText('8870237237')).toBeInTheDocument()
  })

  it('offers Edit Profile, Change Password, and Logout actions', async () => {
    renderAuthenticated()
    await waitFor(() => expect(screen.getByRole('link', { name: /edit profile/i })).toBeInTheDocument())
    expect(screen.getByRole('link', { name: /edit profile/i })).toHaveAttribute('href', '/account/edit')
    expect(screen.getByRole('link', { name: /change password/i })).toHaveAttribute('href', '/account/change-password')
    expect(screen.getByRole('button', { name: /logout/i })).toBeInTheDocument()
  })

  it('offers to link a pending Google identity set by LoginPage, and confirms it via the authenticated API', async () => {
    const user = userEvent.setup()
    sessionStorage.setItem('pending_oauth_link', JSON.stringify({ provider: 'google', linkToken: 'link-token-abc' }))
    renderAuthenticated()

    await waitFor(() => expect(screen.getByText(/link your google account/i)).toBeInTheDocument())

    mockFetchOnce({ message: 'Your Google account has been linked.' })
    await user.click(screen.getByRole('button', { name: /^link google$/i }))

    await waitFor(() => expect(screen.getByText(/has been linked/i)).toBeInTheDocument())
    expect(sessionStorage.getItem('pending_oauth_link')).toBeNull()
  })

  it('dismissing the pending-link banner clears it without calling the API', async () => {
    const user = userEvent.setup()
    sessionStorage.setItem('pending_oauth_link', JSON.stringify({ provider: 'google', linkToken: 'link-token-xyz' }))
    renderAuthenticated()

    await waitFor(() => expect(screen.getByText(/link your google account/i)).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /not now/i }))

    expect(screen.queryByText(/link your google account/i)).not.toBeInTheDocument()
    expect(sessionStorage.getItem('pending_oauth_link')).toBeNull()
  })

  it('shows no pending-link banner when there is nothing to link', async () => {
    renderAuthenticated()
    await waitFor(() => expect(screen.getByRole('link', { name: /edit profile/i })).toBeInTheDocument())
    expect(screen.queryByText(/link your/i)).not.toBeInTheDocument()
  })

  it('never mentions Facebook anywhere on the page', async () => {
    sessionStorage.setItem('pending_oauth_link', JSON.stringify({ provider: 'google', linkToken: 'link-token-abc' }))
    renderAuthenticated()
    await waitFor(() => expect(screen.getByRole('link', { name: /edit profile/i })).toBeInTheDocument())
    expect(screen.queryByText(/facebook/i)).not.toBeInTheDocument()
  })

  it('shows Not Submitted with upload action and no self-verification controls', async () => {
    renderAuthenticated()
    expect(await screen.findByText(/Main Guest ID verification has not been completed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload id proof/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/choose \/ upload id proof/i)).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp,application/pdf')
    expect(screen.getByLabelText(/choose \/ upload id proof/i)).toHaveAttribute('capture', 'environment')
    expect(screen.queryByRole('button', { name: /verify|approve/i })).not.toBeInTheDocument()
  })

  it.each(['pending', 'submitted', 'under_review'])('shows review-pending state for %s without approval controls', async (status) => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: true, status: 200, statusText: 'OK', json: async () => String(url).includes('/identity/profile')
        ? { status, document_type: 'pan', document_id: null, verified_at: null, message: null }
        : CUSTOMER,
    }))
    renderAuthenticated(fetchMock)
    expect((await screen.findAllByText(/Pending Staff Verification/i)).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /verify|approve/i })).not.toBeInTheDocument()
  })

  it('shows Verified and explains reuse for future bookings', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: true, status: 200, statusText: 'OK', json: async () => String(url).includes('/identity/profile')
        ? { status: 'verified', document_type: 'aadhaar', document_id: 'd1', verified_at: '2026-01-01T00:00:00Z', message: null }
        : CUSTOMER,
    }))
    renderAuthenticated(fetchMock)
    expect(await screen.findByText(/Future bookings using this same guest profile do not need this ID uploaded again/i)).toBeInTheDocument()
    expect(screen.getByText('Identity Verified')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /upload id proof/i })).not.toBeInTheDocument()
  })

  it('shows safe rejection messaging and permits resubmission', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: true, status: 200, statusText: 'OK', json: async () => String(url).includes('/identity/profile')
        ? { status: 'rejected', document_type: 'pan', document_id: null, verified_at: null, message: 'Your identity verification could not be completed. Please submit a valid identity document.' }
        : CUSTOMER,
    }))
    renderAuthenticated(fetchMock)
    expect(await screen.findByText(/verification could not be completed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload id proof/i })).toBeInTheDocument()
    expect(screen.queryByText(/staff notes|identity_rejection_reason/i)).not.toBeInTheDocument()
  })

  it('uploads the selected file and then offers submit for review', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes('/identity/profile')) {
        return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => ({ status: 'not_required', document_type: null, document_id: null, verified_at: null, message: null }) })
      }
      if (String(url).includes('/identity/documents')) {
        expect(init?.body).toBeInstanceOf(FormData)
        return Promise.resolve({ ok: true, status: 201, statusText: 'OK', json: async () => ({ id: 'doc-new', document_type: 'aadhaar', filename: 'id.png', uploaded_at: '2026-01-01T00:00:00Z' }) })
      }
      if (String(url).endsWith('/identity/submit')) {
        return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => ({ status: 'submitted', document_type: 'aadhaar', document_id: 'doc-new', verified_at: null, message: null }) })
      }
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => CUSTOMER })
    })
    renderAuthenticated(fetchMock)
    await screen.findByRole('heading', { name: /identity verification/i })
    const file = new File(['\x89PNG\r\n\x1a\n'], 'id.png', { type: 'image/png' })
    await user.upload(screen.getByLabelText(/choose \/ upload id proof/i), file)
    expect(await screen.findByText('Selected file: id.png')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /upload id proof/i }))
    expect(await screen.findByText(/Document uploaded: id\.png/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /submit for verification/i }))
    expect((await screen.findAllByText(/Pending Staff Verification/i)).length).toBeGreaterThan(0)
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/identity/submit'))).toBe(true)
  })

  it('rejects unsupported and over-10-MB selections before upload', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: true, status: 200, statusText: 'OK', json: async () => String(url).includes('/identity/profile')
        ? { status: 'not_required', document_type: null, document_id: null, verified_at: null, message: null }
        : CUSTOMER,
    }))
    renderAuthenticated(fetchMock)
    const input = await screen.findByLabelText(/choose \/ upload id proof/i)
    await user.upload(input, new File(['not an image'], 'bad.txt', { type: 'text/plain' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Choose a JPEG, PNG, WebP, or PDF/i)
    const tooLarge = new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'large.pdf', { type: 'application/pdf' })
    await user.upload(input, tooLarge)
    expect(await screen.findByRole('alert')).toHaveTextContent(/larger than 10 MB/i)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/identity/documents'))).toBe(false)
  })

  it('clears the prior uploaded document when a replacement file is selected', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: true, status: 200, statusText: 'OK', json: async () => {
        if (String(url).includes('/identity/profile')) return { status: 'not_required', document_type: null, document_id: null, verified_at: null, message: null }
        if (String(url).includes('/identity/documents')) return { id: 'doc-old', document_type: 'aadhaar', filename: 'old.png', uploaded_at: '2026-01-01T00:00:00Z' }
        return CUSTOMER
      },
    }))
    renderAuthenticated(fetchMock)
    const input = await screen.findByLabelText(/choose \/ upload id proof/i)
    await user.upload(input, new File(['png'], 'old.png', { type: 'image/png' }))
    await user.click(screen.getByRole('button', { name: /upload id proof/i }))
    expect(await screen.findByRole('button', { name: /submit for verification/i })).toBeInTheDocument()
    await user.upload(input, new File(['pdf'], 'replacement.pdf', { type: 'application/pdf' }))
    expect(screen.queryByRole('button', { name: /submit for verification/i })).not.toBeInTheDocument()
    expect(await screen.findByText('Selected file: replacement.pdf')).toBeInTheDocument()
  })

  it('shows a safe error if customer identity initialization fails', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: !String(url).includes('/identity/profile'), status: String(url).includes('/identity/profile') ? 409 : 200,
      statusText: 'Conflict', json: async () => String(url).includes('/identity/profile') ? { detail: 'We could not safely match this account. Please contact Sri Apartment.' } : CUSTOMER,
    }))
    renderAuthenticated(fetchMock)
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/identity/profile'))).toBe(true))
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not safely match/i)
  })

  it('offers OTP for the unverified matching contact on the safe-link conflict', async () => {
    const user = userEvent.setup()
    const verifiedEmailCustomer = { ...CUSTOMER, email_verified_at: '2026-01-01T00:00:00Z', phone_verified_at: null }
    const message = 'Please verify your matching contact method or contact Sri Apartment to link your guest profile safely'
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: !String(url).includes('/identity/profile'), status: String(url).includes('/identity/profile') ? 409 : 200,
      statusText: 'Conflict', json: async () => String(url).includes('/identity/profile') ? { detail: message } : verifiedEmailCustomer,
    }))
    renderAuthenticated(fetchMock, verifiedEmailCustomer)
    expect(await screen.findByText(/A guest profile matches an account contact that is not verified yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Verify Email' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Verify Phone' }))
    expect(await screen.findByTestId('login-location')).toHaveTextContent('?channel=phone&returnTo=%2Faccount')
  })

  it('shows staff assistance for ambiguous Guest candidates without offering a guessed link', async () => {
    const message = 'We could not safely match your account to one guest profile. Please contact Sri Apartment for assistance'
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: !String(url).includes('/identity/profile'), status: String(url).includes('/identity/profile') ? 409 : 200,
      statusText: 'Conflict', json: async () => String(url).includes('/identity/profile') ? { detail: message } : CUSTOMER,
    }))
    renderAuthenticated(fetchMock)
    expect(await screen.findByText(/could not safely connect your account to one guest profile/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /contact sri apartment/i })).toHaveAttribute('href', '/contact')
    expect(screen.queryByRole('button', { name: /verify email|verify phone/i })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/choose \/ upload id proof/i)).not.toBeInTheDocument()
  })

  it.each([
    ['email', 'Verify Email', '?channel=email&returnTo=%2Faccount'],
    ['phone', 'Verify Phone', '?channel=phone&returnTo=%2Faccount'],
  ] as const)('routes the customer through existing %s OTP login when contact verification is required', async (_channel, action, expectedSearch) => {
    const user = userEvent.setup()
    const message = 'Verify your email or phone number before setting up identity verification'
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: !String(url).includes('/identity/profile'),
      status: String(url).includes('/identity/profile') ? 409 : 200,
      statusText: 'Response',
      json: async () => String(url).includes('/identity/profile') ? { detail: message } : CUSTOMER,
    }))
    renderAuthenticated(fetchMock)
    expect(await screen.findByText(/Verify an email or phone number to securely set up your Main Guest identity/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: action }))
    expect(await screen.findByTestId('login-location')).toHaveTextContent(expectedSearch)
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/customer-auth/logout'))).toBe(true)
    expect(screen.queryByRole('button', { name: /verify|approve/i })).not.toBeInTheDocument()
  })

  it('keeps account edit, password, and logout actions available with identity section', async () => {
    renderAuthenticated()
    expect(await screen.findByRole('heading', { name: /identity verification/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /edit profile/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /change password/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /logout/i })).toBeInTheDocument()
  })
})
