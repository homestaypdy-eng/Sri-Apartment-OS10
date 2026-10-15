import { useEffect } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { Nav } from './Nav'
import { CustomerAuthProvider, useCustomerAuth } from '../context/CustomerAuthContext'
import type { CustomerAccountOut } from '../lib/types'

const CUSTOMER: CustomerAccountOut = {
  id: 'c1',
  first_name: 'Sundaramurthy',
  last_name: 'S',
  email: 'sssmurthy2003@gmail.com',
  phone: '8870237237',
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

/** Simulates the exact call LoginPage makes right after a successful OTP
 * verify — the realistic "just logged in, same tab, no refresh" scenario. */
function LoginTrigger({ customer }: { customer: CustomerAccountOut }) {
  const { customerLogin } = useCustomerAuth()
  useEffect(() => {
    customerLogin('real.jwt.token', customer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return null
}

/** In the real app, CustomerAuthProvider mounts once at the app root and
 * its own one-time GET /me restore effect resolves long before a customer
 * ever logs in on /login — the two never race. In this test harness,
 * though, the Provider is freshly mounted together with LoginTrigger on
 * the same initial render, so LoginTrigger's synchronous customerLogin()
 * call (which writes the token to localStorage) can be observed by the
 * Provider's OWN mount effect, triggering an extra GET /me for the token
 * that was just set. Mocking that call to resolve with the same customer
 * keeps loggedIn:true renders correct without asserting on that
 * incidental extra request. Callers must await the name button appearing
 * before interacting with the menu, since isInitializing only flips to
 * false once this resolves. */
function renderNav({ loggedIn = false }: { loggedIn?: boolean } = {}) {
  if (loggedIn) {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, statusText: 'OK', json: async () => CUSTOMER }),
    )
  }
  return render(
    <MemoryRouter>
      <CustomerAuthProvider>
        {loggedIn && <LoginTrigger customer={CUSTOMER} />}
        <Nav />
      </CustomerAuthProvider>
    </MemoryRouter>,
  )
}

describe('Nav — logged-out state', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('shows LOG IN / SIGN UP / BOOK NOW and no customer name', () => {
    renderNav()
    expect(screen.getAllByText(/log in/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/sign up/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/book now/i).length).toBeGreaterThan(0)
    expect(screen.queryByText('Sundaramurthy')).not.toBeInTheDocument()
  })

  it('links Guest Reviews to the existing homepage section in desktop and mobile navigation', async () => {
    const user = userEvent.setup()
    renderNav()

    const guestReviewsLink = screen.getByRole('link', { name: 'Guest Reviews' })
    expect(guestReviewsLink).toHaveAttribute('href', '/#guest-reviews')
    expect(guestReviewsLink).not.toHaveClass('text-brand-gold')
    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    expect(screen.getAllByRole('link', { name: 'Guest Reviews' })).toHaveLength(2)
    expect(screen.getAllByRole('link', { name: 'Guest Reviews' })[1]).toHaveAttribute('href', '/#guest-reviews')
  })
})

describe('Nav — logged-in state, no hard-coded name', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('hides LOG IN / SIGN UP and shows the authenticated customer\'s own first name, updating immediately without a refresh', async () => {
    renderNav({ loggedIn: true })
    // The name comes from CustomerAuthContext state, not a literal in Nav.
    expect((await screen.findAllByText('Sundaramurthy')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/^log in$/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/sign up/i)).not.toBeInTheDocument()
    expect(screen.getAllByText(/book now/i).length).toBeGreaterThan(0)
  })
})

describe('Nav — account dropdown', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('is closed by default and opens the account menu when the name is clicked', async () => {
    const user = userEvent.setup()
    renderNav({ loggedIn: true })
    const trigger = await screen.findByRole('button', { name: /sundaramurthy/i })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    await user.click(trigger)
    const menu = screen.getByRole('menu', { name: /account menu/i })
    expect(menu).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /my account/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /edit profile/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /change password/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /logout/i })).toBeInTheDocument()
  })

  it('the My Account item links to /account', async () => {
    const user = userEvent.setup()
    renderNav({ loggedIn: true })
    await user.click(await screen.findByRole('button', { name: /sundaramurthy/i }))
    expect(screen.getByRole('menuitem', { name: /my account/i })).toHaveAttribute('href', '/account')
  })

  it('the Edit Profile and Change Password items link to their own routes', async () => {
    const user = userEvent.setup()
    renderNav({ loggedIn: true })
    await user.click(await screen.findByRole('button', { name: /sundaramurthy/i }))
    expect(screen.getByRole('menuitem', { name: /edit profile/i })).toHaveAttribute('href', '/account/edit')
    expect(screen.getByRole('menuitem', { name: /change password/i })).toHaveAttribute('href', '/account/change-password')
  })

  it('closes after selecting an item', async () => {
    const user = userEvent.setup()
    renderNav({ loggedIn: true })
    await user.click(await screen.findByRole('button', { name: /sundaramurthy/i }))
    await user.click(screen.getByRole('menuitem', { name: /my account/i }))
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('closes when clicking outside the menu', async () => {
    const user = userEvent.setup()
    renderNav({ loggedIn: true })
    await user.click(await screen.findByRole('button', { name: /sundaramurthy/i }))
    expect(screen.getByRole('menu')).toBeInTheDocument()

    await user.click(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    renderNav({ loggedIn: true })
    await user.click(await screen.findByRole('button', { name: /sundaramurthy/i }))
    expect(screen.getByRole('menu')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})

describe('Nav — session restoration after a page reload (GET /me)', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('restores the full profile and shows the real customer name, not a generic placeholder', async () => {
    localStorage.setItem('customer_access_token', 'real.jwt.token')
    mockFetchOnce(CUSTOMER)
    renderNav()

    await waitFor(() => expect(screen.getAllByText('Sundaramurthy').length).toBeGreaterThan(0))
    expect(screen.queryByText(/^log in$/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/sign up/i)).not.toBeInTheDocument()
  })

  it('an invalid/expired token (401 from /me) is cleared and the navbar falls back to logged-out', async () => {
    localStorage.setItem('customer_access_token', 'stale.token')
    mockFetchOnce({ detail: 'Invalid or expired session' }, { ok: false, status: 401 })
    renderNav()

    await waitFor(() => expect(screen.getAllByText(/log in/i).length).toBeGreaterThan(0))
    expect(localStorage.getItem('customer_access_token')).toBeNull()
  })

  it('a non-auth failure (e.g. a network hiccup) keeps the customer authenticated with a generic fallback label, instead of signing them out', async () => {
    localStorage.setItem('customer_access_token', 'real.jwt.token')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')))
    renderNav()

    await waitFor(() => expect(screen.queryByText(/^log in$/i)).not.toBeInTheDocument())
    expect(screen.getAllByText(/my account/i).length).toBeGreaterThan(0)
  })
})
