import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { AuthShell } from '../components/AuthShell'
import { Helmet } from '../components/Helmet'
import { OAuthButtons } from '../components/OAuthButtons'
import { PhoneInput } from '../components/PhoneInput'
import { ApiError, customerOAuthStatus, customerRegister } from '../lib/api'
import { isValidPhoneNumber } from '../lib/phone'
import type { CustomerOAuthStatus } from '../lib/types'

function friendlyError(err: unknown): string {
  if (err instanceof ApiError) return err.message
  return 'Something went wrong. Please try again.'
}

export function SignUpPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const loginPath = searchParams.get("returnTo") === "/book" ? "/login?returnTo=/book" : "/login"
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [termsAccepted, setTermsAccepted] = useState(false)
  const [privacyAccepted, setPrivacyAccepted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [loading, setLoading] = useState(false)
  const [oauthNotice, setOauthNotice] = useState<string | null>(null)
  const [oauthStatus, setOauthStatus] = useState<CustomerOAuthStatus>({ google: false })

  useEffect(() => {
    customerOAuthStatus()
      .then(setOauthStatus)
      .catch(() => setOauthStatus({ google: false }))
  }, [])

  function handleOAuthUnavailable() {
    setOauthNotice("Continue with Google isn't set up yet. Please create your account with the form below for now.")
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    if (!termsAccepted) {
      setError('You must accept the Terms & Conditions to create an account.')
      return
    }
    if (!privacyAccepted) {
      setError('You must accept the Privacy Policy to create an account.')
      return
    }
    if (!isValidPhoneNumber(phone)) {
      setError('Please enter a valid phone number.')
      return
    }

    setLoading(true)
    try {
      await customerRegister({
        first_name: firstName,
        last_name: lastName,
        email,
        phone,
        password,
        terms_accepted: termsAccepted,
        privacy_accepted: privacyAccepted,
      })
      // Deliberate: signup does not log the customer in automatically —
      // send them to Log In with their new credentials.
      setSuccess(true)
      setTimeout(() => navigate(loginPath), 1800)
    } catch (err) {
      setError(friendlyError(err))
    } finally {
      setLoading(false)
    }
  }

  if (success) {
    return (
      <AuthShell title="Account created" subtitle="Redirecting you to Log In…">
        <Helmet title="Sign Up — Sri Apartment Homestay" description="Create your Sri Apartment account." canonicalPath="/signup" noindex />
        <p role="status" className="rounded-md bg-brand-gold/10 px-3 py-2 text-sm text-brand-gold-4">
          Your Sri Apartment account has been created. Please log in with your new password.
        </p>
        <Link to={loginPath} className="mt-4 inline-block text-sm font-semibold text-brand-green underline">
          Go to Log In now
        </Link>
      </AuthShell>
    )
  }

  return (
    <AuthShell
      mode="signup"
      title="Sign Up"
      subtitle="Create your Sri Apartment account"
      footer={
        <p>
          Already have an account?{' '}
          <Link to={loginPath} className="font-semibold text-brand-green underline">
            Log In
          </Link>
        </p>
      }
    >
      <Helmet title="Sign Up — Sri Apartment Homestay" description="Create your Sri Apartment account." canonicalPath="/signup" noindex />

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">First name</span>
            <input
              type="text"
              autoComplete="given-name"
              required
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Last name</span>
            <input
              type="text"
              autoComplete="family-name"
              required
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
            />
          </label>
        </div>

        <label className="block">
          <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
          />
        </label>

        <PhoneInput label="Phone number" value={phone} onChange={setPhone} required autoComplete="tel" />

        <label className="block">
          <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
          />
          <span className="mt-1.5 block text-xs text-brand-muted">At least 8 characters, with a letter and a number.</span>
        </label>

        <div className="space-y-2">
          <label className="flex items-start gap-2 text-sm text-brand-green">
            <input
              type="checkbox"
              checked={termsAccepted}
              onChange={(e) => setTermsAccepted(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-brand-border text-brand-gold-4 focus:ring-brand-gold"
            />
            <span>
              I agree to the{' '}
              <Link to="/terms" target="_blank" rel="noreferrer" className="font-semibold underline">
                Terms &amp; Conditions
              </Link>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm text-brand-green">
            <input
              type="checkbox"
              checked={privacyAccepted}
              onChange={(e) => setPrivacyAccepted(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-brand-border text-brand-gold-4 focus:ring-brand-gold"
            />
            <span>
              I agree to the{' '}
              <Link to="/privacy-policy" target="_blank" rel="noreferrer" className="font-semibold underline">
                Privacy Policy
              </Link>
            </span>
          </label>
        </div>

        {error && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-md bg-brand-gold py-3.5 font-bold tracking-wide text-brand-green-3 uppercase shadow-sm transition hover:bg-brand-gold-2 disabled:opacity-50"
        >
          {loading ? 'Creating account…' : 'Create Account'}
        </button>

        <div className="relative py-1 text-center text-xs text-brand-muted">
          <span className="relative bg-brand-cream px-2">or</span>
          <div className="absolute inset-x-0 top-1/2 -z-10 h-px bg-brand-border" aria-hidden="true" />
        </div>

        {oauthNotice && (
          <p role="status" className="rounded-md bg-brand-gold/10 px-3 py-2 text-xs text-brand-gold-4">
            {oauthNotice}
          </p>
        )}
        <OAuthButtons configured={oauthStatus.google} onUnavailable={handleOAuthUnavailable} />
      </form>
    </AuthShell>
  )
}
