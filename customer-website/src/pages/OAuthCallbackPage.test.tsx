import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { OAuthCallbackPage } from './OAuthCallbackPage'
import { CustomerAuthProvider } from '../context/CustomerAuthContext'

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = init
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok, status, statusText: ok ? 'OK' : 'Error', json: async () => body }),
  )
}

function renderAt(hash: string) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/oauth/callback', hash }]}>
      <CustomerAuthProvider>
        <Routes>
          <Route path="/oauth/callback" element={<OAuthCallbackPage />} />
          <Route path="/" element={<div>Home page reached</div>} />
          <Route path="/login" element={<div>Login page reached</div>} />
        </Routes>
      </CustomerAuthProvider>
    </MemoryRouter>,
  )
}

describe('OAuthCallbackPage', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('with a token in the URL fragment, restores the profile via /me, authenticates, and lands on the homepage', async () => {
    mockFetchOnce({
      id: 'c1', first_name: 'Grace', last_name: 'Hopper', email: 'grace@example.com', phone: null,
      email_verified_at: null, created_at: '2026-01-01T00:00:00Z',
    })

    renderAt('#token=real.jwt.token')

    expect(await screen.findByText('Home page reached')).toBeInTheDocument()
    expect(localStorage.getItem('customer_access_token')).toBe('real.jwt.token')
  })

  it('with no token in the fragment, redirects to /login with an error, never fabricating a session', async () => {
    renderAt('')
    expect(await screen.findByText('Login page reached')).toBeInTheDocument()
    expect(localStorage.getItem('customer_access_token')).toBeNull()
  })

  it('if /me rejects the token, redirects to /login instead of authenticating', async () => {
    mockFetchOnce({ detail: 'Invalid or expired session' }, { ok: false, status: 401 })
    renderAt('#token=stale.token')
    expect(await screen.findByText('Login page reached')).toBeInTheDocument()
  })
})
