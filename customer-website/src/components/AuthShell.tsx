import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

type AuthMode = 'login' | 'signup'

// Exactly two entry points, always shown together — see the site-wide
// requirement that ONLY "Log In" and "Sign Up" ever appear as auth
// options anywhere on this site (no Guest Login tab/button/option).
const MODE_TABS: { key: AuthMode; label: string; to: string }[] = [
  { key: 'login', label: 'Log In', to: '/login' },
  { key: 'signup', label: 'Sign Up', to: '/signup' },
]

function AuthModeSwitcher({ active }: { active: AuthMode }) {
  const [searchParams] = useSearchParams()
  const bookingReturn = searchParams.get("returnTo") === "/book"
  return (
    <div role="tablist" aria-label="Log in or sign up" className="mb-6 grid grid-cols-2 gap-1 rounded-md border border-brand-border bg-brand-cream-2 p-1">
      {MODE_TABS.map((tab) => (
        <Link
          key={tab.key}
          to={bookingReturn ? `${tab.to}?returnTo=/book` : tab.to}
          role="tab"
          aria-selected={active === tab.key}
          className={`rounded py-2 text-center text-xs font-bold tracking-wide uppercase transition ${
            active === tab.key ? 'bg-brand-gold text-brand-green-3 shadow-sm' : 'text-brand-muted hover:text-brand-green'
          }`}
        >
          {tab.label}
        </Link>
      ))}
    </div>
  )
}

export function AuthShell({
  mode,
  title,
  subtitle,
  children,
  footer,
}: {
  /** Omit on sub-flows (Forgot/Reset Password, OTP verification) that
   * branch off Log In rather than being one of the two primary entry
   * points themselves. */
  mode?: AuthMode
  title: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center bg-brand-cream-3 px-4 py-12 sm:px-6 sm:py-16">
      <div className="w-full max-w-md">
        <div className="text-center">
          <Link to="/" className="inline-block font-serif text-4xl tracking-[0.15em] text-brand-green uppercase">
            Sri Apartment
            <span className="mt-1.5 block font-sans text-xs tracking-[0.3em] text-brand-gold-4 normal-case">Homestay Pondicherry</span>
          </Link>
          <span className="mx-auto mt-5 block h-px w-12 bg-brand-gold" aria-hidden="true" />
        </div>

        <div className="mt-6 rounded-xl border border-brand-border bg-brand-cream px-6 py-9 shadow-md sm:px-10 sm:py-10">
          {mode && <AuthModeSwitcher active={mode} />}

          <h1 className="text-center font-serif text-2xl leading-snug text-brand-green sm:text-[1.75rem]">{title}</h1>
          {subtitle && <p className="mt-2.5 text-center text-sm leading-relaxed text-brand-muted">{subtitle}</p>}

          <div className="mt-7">{children}</div>
        </div>

        {footer && <div className="mt-6 text-center text-sm text-brand-muted">{footer}</div>}
      </div>
    </div>
  )
}
