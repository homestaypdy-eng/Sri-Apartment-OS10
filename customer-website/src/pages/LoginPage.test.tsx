import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { LoginPage } from './LoginPage'
import { CustomerAuthProvider } from '../context/CustomerAuthContext'

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

function renderLoginPage(initialEntry = '/login') {
  const result = render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <CustomerAuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/account" element={<div>Account page reached</div>} />
          <Route path="/" element={<div>Home page reached</div>} />
        </Routes>
      </CustomerAuthProvider>
    </MemoryRouter>,
  )
  // The Password <label> also wraps the "Forgot password?" link, so its
  // accessible name isn't just "Password" — query the input directly.
  const passwordInput = () => result.container.querySelector('input[type="password"]') as HTMLInputElement
  return { ...result, passwordInput }
}

describe('LoginPage — OTP is never exposed to the customer-facing UI', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
    localStorage.clear()
  })

  it('never renders the dev_otp_code returned by step 1, even though the backend includes it outside production', async () => {
    const user = userEvent.setup()
    mockFetchOnce({ challenge_id: 'chal-1', message: "We've sent a verification code to your email.", dev_otp_code: '654321' })
    const { passwordInput } = renderLoginPage()

    await user.type(screen.getByLabelText(/email address/i), 'customer@example.com')
    await user.type(passwordInput(), 'CorrectHorse123!')
    await user.click(screen.getByRole('button', { name: /send verification code/i }))

    await waitFor(() => expect(screen.getByText(/enter your verification code/i)).toBeInTheDocument())

    // The real OTP value must not appear anywhere in the rendered page —
    // not as text, not in an attribute-bearing element's accessible text.
    expect(screen.queryByText('654321')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('654321')
    expect(document.body.textContent?.toLowerCase()).not.toContain('development mode')
    expect(document.body.innerHTML).not.toContain('654321')
  })

  it('shows the required customer-facing copy on the OTP screen', async () => {
    const user = userEvent.setup()
    mockFetchOnce({ challenge_id: 'chal-1', message: "We've sent a verification code to your email.", dev_otp_code: '111111' })
    const { passwordInput } = renderLoginPage()

    await user.type(screen.getByLabelText(/email address/i), 'customer@example.com')
    await user.type(passwordInput(), 'CorrectHorse123!')
    await user.click(screen.getByRole('button', { name: /send verification code/i }))

    await waitFor(() => expect(screen.getByText(/enter your verification code/i)).toBeInTheDocument())
    expect(screen.getByText(/we sent a 6-digit verification code to your email address\./i)).toBeInTheDocument()
  })

  it('redirects to the homepage and authenticates the customer after a correct OTP', async () => {
    const user = userEvent.setup()
    mockFetchOnce({ challenge_id: 'chal-1', message: "We've sent a verification code to your email.", dev_otp_code: '222222' })
    const { passwordInput } = renderLoginPage()

    await user.type(screen.getByLabelText(/email address/i), 'customer@example.com')
    await user.type(passwordInput(), 'CorrectHorse123!')
    await user.click(screen.getByRole('button', { name: /send verification code/i }))
    await waitFor(() => expect(screen.getByText(/enter your verification code/i)).toBeInTheDocument())

    mockFetchOnce({
      access_token: 'real.jwt.token',
      token_type: 'bearer',
      customer: { id: 'c1', first_name: 'Ada', last_name: 'Lovelace', email: 'customer@example.com', phone: null, email_verified_at: null, created_at: '2026-01-01T00:00:00Z' },
    })
    await user.type(screen.getByLabelText(/verification code/i), '222222')
    await user.click(screen.getByRole('button', { name: /verify & continue/i }))

    await waitFor(() => expect(screen.getByText('Home page reached')).toBeInTheDocument())
    expect(localStorage.getItem('customer_access_token')).toBe('real.jwt.token')
  })

  it('preselects the requested contact channel and returns to My Account after verified OTP', async () => {
    const user = userEvent.setup()
    const challengeResponse = { ok: true, status: 200, statusText: 'OK', json: async () => ({ challenge_id: 'phone-challenge', message: 'Code sent.' }) }
    const loginResponse = { ok: true, status: 200, statusText: 'OK', json: async () => ({
        access_token: 'verified.jwt.token', token_type: 'bearer',
        customer: { id: 'c1', first_name: 'Ada', last_name: 'Lovelace', email: 'customer@example.com', phone: '+919876543210', email_verified_at: null, created_at: '2026-01-01T00:00:00Z' },
      }) }
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/customer-auth/login/verify-otp')) return Promise.resolve(loginResponse)
      if (url.includes('/customer-auth/login')) return Promise.resolve(challengeResponse)
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => ({ google: false }) })
    })
    vi.stubGlobal('fetch', fetchMock)
    const { passwordInput } = renderLoginPage('/login?channel=phone&returnTo=%2Faccount')

    expect(screen.getByRole('heading', { name: /sign in with mobile number/i })).toBeInTheDocument()
    await user.type(screen.getByLabelText(/mobile number/i), '9876543210')
    await user.type(passwordInput(), 'CorrectHorse123!')
    await user.click(screen.getByRole('button', { name: /send code/i }))
    await waitFor(() => expect(screen.getByText(/enter your verification code/i)).toBeInTheDocument())
    const loginCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/customer-auth/login'))
    expect(loginCall?.[0]).toContain('/customer-auth/login')
    expect(JSON.parse(String(loginCall?.[1]?.body)).channel).toBe('phone')

    await user.type(screen.getByLabelText(/verification code/i), '123456')
    await user.click(screen.getByRole('button', { name: /verify & continue/i }))
    await waitFor(() => expect(screen.getByText('Account page reached')).toBeInTheDocument())
    expect(localStorage.getItem('customer_access_token')).toBe('verified.jwt.token')
  })
})

describe('LoginPage — LOCAL email OTP delivery status (otp_delivery_status)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
    localStorage.clear()
  })

  async function submitAndReachOtpStep() {
    const user = userEvent.setup()
    const { passwordInput } = renderLoginPage()
    await user.type(screen.getByLabelText(/email address/i), 'customer@example.com')
    await user.type(passwordInput(), 'CorrectHorse123!')
    await user.click(screen.getByRole('button', { name: /send verification code/i }))
    await waitFor(() => expect(screen.getByText(/enter your verification code/i)).toBeInTheDocument())
  }

  it('dev_code_only: clearly says local email delivery is disabled and shows the development code', async () => {
    mockFetchOnce({
      challenge_id: 'chal-1',
      message: "We've sent a verification code to your email.",
      dev_otp_code: '111222',
      otp_delivery_status: 'dev_code_only',
    })
    await submitAndReachOtpStep()

    expect(screen.getByText(/development mode/i)).toBeInTheDocument()
    expect(screen.getByText(/email delivery is disabled locally/i)).toBeInTheDocument()
    expect(document.body.textContent).toContain('111222')
  })

  it('dev_real_email_sent: says to check the email, and never prints the development code', async () => {
    mockFetchOnce({
      challenge_id: 'chal-1',
      message: "We've sent a verification code to your email.",
      dev_otp_code: '333444',
      otp_delivery_status: 'dev_real_email_sent',
    })
    await submitAndReachOtpStep()

    expect(screen.getByText(/a real email was sent.*check your inbox/i)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('333444')
  })

  it('dev_real_email_failed: clearly says the local email test failed and shows the development code', async () => {
    mockFetchOnce({
      challenge_id: 'chal-1',
      message: "We've sent a verification code to your email.",
      dev_otp_code: '555666',
      otp_delivery_status: 'dev_real_email_failed',
    })
    await submitAndReachOtpStep()

    expect(screen.getByText(/local email test failed/i)).toBeInTheDocument()
    expect(document.body.textContent).toContain('555666')
  })

  it('dev_real_email_not_allowlisted: clearly indicates the address is not enabled for local testing, and shows the development code', async () => {
    mockFetchOnce({
      challenge_id: 'chal-1',
      message: "We've sent a verification code to your email.",
      dev_otp_code: '777888',
      otp_delivery_status: 'dev_real_email_not_allowlisted',
    })
    await submitAndReachOtpStep()

    expect(screen.getByText(/isn't enabled for local email testing/i)).toBeInTheDocument()
    expect(document.body.textContent).toContain('777888')
  })

  it('production-shaped response (otp_delivery_status: "sent") shows no local-dev notice at all', async () => {
    mockFetchOnce({
      challenge_id: 'chal-1',
      message: "We've sent a verification code to your email.",
      dev_otp_code: null,
      otp_delivery_status: 'sent',
    })
    await submitAndReachOtpStep()

    expect(screen.queryByText(/development mode/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/local email test/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/isn't enabled for local email testing/i)).not.toBeInTheDocument()
  })

  it('production build never exposes a development OTP even when the API returns a dev_* status', async () => {
    vi.stubEnv('DEV', false)
    mockFetchOnce({
      challenge_id: 'chal-1',
      message: "We've sent a verification code to your email.",
      dev_otp_code: '999888',
      otp_delivery_status: 'dev_code_only',
    })

    await submitAndReachOtpStep()

    expect(document.body.textContent).not.toContain('999888')
    expect(document.body.innerHTML).not.toContain('999888')
    expect(screen.queryByText(/development mode/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/email delivery is disabled locally/i)).not.toBeInTheDocument()
  })
})
