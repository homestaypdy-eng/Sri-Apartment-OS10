import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { CustomerAuthProvider, useCustomerAuth } from './CustomerAuthContext'
import type { CustomerAccountOut } from '../lib/types'

const CUSTOMER_TOKEN_KEY = 'customer_access_token'
const STORED_TOKEN = 'stale.local.token'

const CUSTOMER: CustomerAccountOut = {
  id: 'c1',
  first_name: 'Test',
  last_name: 'Customer',
  email: 'test@example.com',
  phone: '9000000000',
  email_verified_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

function AuthState() {
  const { isCustomerAuthenticated, isInitializing, customer } = useCustomerAuth()
  return (
    <p>
      {isInitializing ? 'initializing' : isCustomerAuthenticated ? `signed-in:${customer?.first_name ?? ''}` : 'signed-out'}
    </p>
  )
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('CustomerAuthProvider session restore', () => {
  it('skips /me and shows signed-out in read-only dev mode, keeping the stored token', async () => {
    vi.stubEnv('VITE_DEV_READ_ONLY_API', 'true')
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem(CUSTOMER_TOKEN_KEY, STORED_TOKEN)

    render(<CustomerAuthProvider><AuthState /></CustomerAuthProvider>)

    expect(await screen.findByText('signed-out')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(localStorage.getItem(CUSTOMER_TOKEN_KEY)).toBe(STORED_TOKEN)
  })

  it('restores the session via /me when the flag is not set', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, statusText: 'OK', json: async () => CUSTOMER })
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem(CUSTOMER_TOKEN_KEY, STORED_TOKEN)

    render(<CustomerAuthProvider><AuthState /></CustomerAuthProvider>)

    expect(await screen.findByText('signed-in:Test')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v1/customer-auth/me')
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${STORED_TOKEN}`)
  })
})
