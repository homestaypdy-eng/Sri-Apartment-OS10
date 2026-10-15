import { useEffect } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ChangePasswordPage } from './ChangePasswordPage'
import { CustomerAuthProvider, useCustomerAuth } from '../context/CustomerAuthContext'
import type { CustomerAccountOut } from '../lib/types'

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
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, statusText: 'OK', json: async () => CUSTOMER }))
  const result = render(
    <MemoryRouter initialEntries={['/account/change-password']}>
      <CustomerAuthProvider>
        <LoginTrigger />
        <Routes>
          <Route path="/account/change-password" element={<ChangePasswordPage />} />
          <Route path="/login" element={<div>Login page reached</div>} />
        </Routes>
      </CustomerAuthProvider>
    </MemoryRouter>,
  )
  // The "New password" <label> also wraps its own helper text ("At least
  // 8 characters…"), so its accessible name isn't just "New password" —
  // query the three password inputs by position instead.
  const passwordInputs = () => result.container.querySelectorAll('input[type="password"]')
  return { ...result, passwordInputs }
}

async function fillForm(
  user: ReturnType<typeof userEvent.setup>,
  inputs: NodeListOf<Element>,
  current: string,
  next: string,
  confirm: string,
) {
  await user.type(inputs[0], current)
  await user.type(inputs[1], next)
  await user.type(inputs[2], confirm)
}

describe('ChangePasswordPage', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('redirects to /login when not authenticated', async () => {
    render(
      <MemoryRouter initialEntries={['/account/change-password']}>
        <CustomerAuthProvider>
          <Routes>
            <Route path="/account/change-password" element={<ChangePasswordPage />} />
            <Route path="/login" element={<div>Login page reached</div>} />
          </Routes>
        </CustomerAuthProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByText('Login page reached')).toBeInTheDocument()
  })

  it('rejects mismatched new/confirm passwords without calling the API', async () => {
    const user = userEvent.setup()
    const { passwordInputs } = renderAuthenticated()
    await waitFor(() => expect(screen.getByLabelText(/current password/i)).toBeInTheDocument())

    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    await fillForm(user, passwordInputs(), 'CorrectHorse123!', 'NewPassword456!', 'DoesNotMatch1!')
    await user.click(screen.getByRole('button', { name: /change password/i }))

    expect(await screen.findByText(/do not match/i)).toBeInTheDocument()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('shows the backend error when the current password is wrong', async () => {
    const user = userEvent.setup()
    const { passwordInputs } = renderAuthenticated()
    await waitFor(() => expect(screen.getByLabelText(/current password/i)).toBeInTheDocument())

    mockFetchOnce({ detail: 'Current password is incorrect.' }, { ok: false, status: 401 })
    await fillForm(user, passwordInputs(), 'WrongPassword1', 'NewPassword456!', 'NewPassword456!')
    await user.click(screen.getByRole('button', { name: /change password/i }))

    expect(await screen.findByText(/current password is incorrect/i)).toBeInTheDocument()
  })

  it('on success, shows the sign-out notice and returns to /login', async () => {
    const user = userEvent.setup()
    const { passwordInputs } = renderAuthenticated()
    await waitFor(() => expect(screen.getByLabelText(/current password/i)).toBeInTheDocument())

    mockFetchOnce({ message: 'Password changed. Please log in again.' })
    await fillForm(user, passwordInputs(), 'CorrectHorse123!', 'NewPassword456!', 'NewPassword456!')
    await user.click(screen.getByRole('button', { name: /change password/i }))

    expect(await screen.findByText(/password has been changed/i)).toBeInTheDocument()

    mockFetchOnce(undefined, { status: 204 })
    await user.click(screen.getByRole('button', { name: /go to log in/i }))
    expect(await screen.findByText('Login page reached')).toBeInTheDocument()
  })
})
