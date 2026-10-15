import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useCustomerAuth } from '../context/CustomerAuthContext'

const LINKS = [
  { to: '/apartments', label: 'Accommodation' },
  { to: '/gallery', label: 'Gallery' },
  { to: '/#guest-reviews', label: 'Guest Reviews' },
  { to: '/faq', label: 'FAQ' },
  { to: '/location', label: 'Location' },
  { to: '/contact', label: 'Contact' },
]

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`transition-transform ${open ? 'rotate-180' : ''}`}
      aria-hidden="true"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

/** Desktop account menu: a name button that opens a small dropdown with
 * the account actions. Closes on click-outside, on Escape, and after any
 * item is chosen — standard menu behavior. */
function AccountMenu({ label, onNavigate }: { label: string; onNavigate: () => void }) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const { customerLogout } = useCustomerAuth()

  useEffect(() => {
    if (!open) return
    function onPointerDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  function choose() {
    setOpen(false)
    onNavigate()
  }

  async function handleLogout() {
    setOpen(false)
    onNavigate()
    await customerLogout()
    navigate('/')
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-1.5 rounded px-1 py-1 text-xs font-semibold tracking-wide text-brand-gold uppercase transition hover:text-brand-gold-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-gold"
      >
        {label}
        <ChevronIcon open={open} />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Account menu"
          className="absolute right-0 top-full z-50 mt-2 w-56 overflow-hidden rounded-md border border-brand-gold/25 bg-brand-cream py-1.5 shadow-lg"
        >
          <Link role="menuitem" to="/account" onClick={choose} className="block px-4 py-2.5 text-sm text-brand-green transition hover:bg-brand-gold/10">
            My Account
          </Link>
          <Link
            role="menuitem"
            to="/account/edit"
            onClick={choose}
            className="block px-4 py-2.5 text-sm text-brand-green transition hover:bg-brand-gold/10"
          >
            Edit Profile
          </Link>
          <Link
            role="menuitem"
            to="/account/change-password"
            onClick={choose}
            className="block px-4 py-2.5 text-sm text-brand-green transition hover:bg-brand-gold/10"
          >
            Change Password
          </Link>
          <div className="my-1 border-t border-brand-border" role="none" />
          <button
            role="menuitem"
            type="button"
            onClick={() => void handleLogout()}
            className="block w-full px-4 py-2.5 text-left text-sm text-brand-green transition hover:bg-brand-gold/10"
          >
            Logout
          </button>
        </div>
      )}
    </div>
  )
}

export function Nav() {
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const { isCustomerAuthenticated, isInitializing, customer, customerLogout } = useCustomerAuth()
  const navigate = useNavigate()
  // Falls back to a generic label only in the brief window before GET /me
  // resolves after a page reload (see CustomerAuthContext) — a stored
  // token still means authenticated even before the personalized name
  // arrives.
  const accountLabel = customer?.first_name || 'My Account'

  useEffect(() => {
    if (location.pathname !== '/' || location.hash !== '#guest-reviews') return
    document.getElementById('guest-reviews')?.scrollIntoView({ behavior: 'smooth' })
  }, [location.pathname, location.hash])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  async function handleMobileLogout() {
    setOpen(false)
    await customerLogout()
    navigate('/')
  }

  return (
    <header className="sticky top-0 z-40 border-b border-brand-gold/20 bg-brand-green-3/97 backdrop-blur">
      <nav className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6" aria-label="Main navigation">
        <Link to="/" className="font-serif text-lg tracking-[0.15em] text-white uppercase">
          Sri Apartment
          <span className="block font-sans text-[0.55rem] tracking-[0.3em] text-brand-gold normal-case">Homestay Pondicherry</span>
        </Link>

        <ul className="hidden items-center gap-6 text-xs font-medium tracking-wide text-white/70 uppercase lg:flex">
          {LINKS.map((l) => (
            <li key={l.to}>
              <NavLink
                to={l.to}
                className={({ isActive }) =>
                  isActive && (l.to !== '/#guest-reviews' || location.hash === '#guest-reviews')
                    ? 'text-brand-gold'
                    : 'hover:text-brand-gold'
                }
              >
                {l.label}
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="hidden items-center gap-4 lg:flex">
          {!isInitializing && isCustomerAuthenticated ? (
            <AccountMenu label={accountLabel} onNavigate={() => {}} />
          ) : (
            !isInitializing && (
              <div className="flex items-center gap-3">
                <Link to="/login" className="text-xs font-semibold text-white/80 uppercase hover:text-brand-gold">
                  Log In
                </Link>
                <Link to="/signup" className="rounded border border-brand-gold/60 px-4 py-2 text-xs font-semibold text-brand-gold uppercase hover:bg-brand-gold/10">
                  Sign Up
                </Link>
              </div>
            )
          )}
          <Link to="/book" className="rounded bg-brand-gold px-4 py-2 text-xs font-bold text-brand-green-3 uppercase hover:bg-brand-gold-2">
            Book Now
          </Link>
        </div>

        <button
          type="button"
          className="p-2 text-white lg:hidden"
          aria-expanded={open}
          aria-controls="mobile-menu"
          aria-label={open ? 'Close menu' : 'Open menu'}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '✕' : '☰'}
        </button>
      </nav>

      {open && (
        <div id="mobile-menu" className="border-t border-white/10 bg-brand-green-3 px-4 pb-4 lg:hidden">
          <ul className="flex flex-col gap-3 pt-3 text-sm text-white/80">
            {LINKS.map((l) => (
              <li key={l.to}>
                <NavLink
                  to={l.to}
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    isActive && (l.to !== '/#guest-reviews' || location.hash === '#guest-reviews') ? 'text-brand-gold' : ''
                  }
                >
                  {l.label}
                </NavLink>
              </li>
            ))}
            {!isInitializing && isCustomerAuthenticated ? (
              <>
                <li className="pt-2 text-xs font-bold tracking-wide text-brand-gold uppercase">{accountLabel}</li>
                <li>
                  <Link to="/account" onClick={() => setOpen(false)}>
                    My Account
                  </Link>
                </li>
                <li>
                  <Link to="/account/edit" onClick={() => setOpen(false)}>
                    Edit Profile
                  </Link>
                </li>
                <li>
                  <Link to="/account/change-password" onClick={() => setOpen(false)}>
                    Change Password
                  </Link>
                </li>
                <li>
                  <button type="button" onClick={() => void handleMobileLogout()} className="text-left">
                    Logout
                  </button>
                </li>
              </>
            ) : (
              !isInitializing && (
                <>
                  <li>
                    <Link to="/login" onClick={() => setOpen(false)}>
                      Log In
                    </Link>
                  </li>
                  <li>
                    <Link to="/signup" onClick={() => setOpen(false)}>
                      Sign Up
                    </Link>
                  </li>
                </>
              )
            )}
            <li>
              <Link to="/book" onClick={() => setOpen(false)} className="inline-block rounded bg-brand-gold px-4 py-2 font-bold text-brand-green-3 uppercase">
                Book Now
              </Link>
            </li>
          </ul>
        </div>
      )}
    </header>
  )
}
