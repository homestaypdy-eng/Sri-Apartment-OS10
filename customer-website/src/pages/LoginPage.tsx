import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { AuthShell } from '../components/AuthShell'
import { Helmet } from '../components/Helmet'
import { OAuthButtons } from '../components/OAuthButtons'
import { PhoneInput } from '../components/PhoneInput'
import { useCustomerAuth } from '../context/CustomerAuthContext'
import { useBusinessContact } from '../hooks/useBusinessContact'
import { ApiError, customerLoginStep1, customerOAuthStatus, customerVerifyLoginOtp } from '../lib/api'
import type { CustomerOAuthStatus, OtpDeliveryStatus } from '../lib/types'

// The key AccountPage looks for to offer "confirm linking your Google
// account" once the customer has logged in normally — see the
// oauth_link_required handling below and AccountPage's own pickup of it.
const PENDING_OAUTH_LINK_KEY = 'pending_oauth_link'

// Client-side pacing only, to discourage rapid re-taps of "Resend code" —
// the backend's own rate limiting (5 attempts / 5 minutes per identifier)
// is what actually enforces a limit; this is just UX.
const RESEND_COOLDOWN_SECONDS = 60

type Channel = 'email' | 'phone'
type Step = 'credentials' | 'otp'

function maskIdentifier(channel: Channel, raw: string): string {
  if (channel === 'phone') {
    const digits = raw.replace(/\D/g, '')
    return digits.length < 4 ? '••••••' : `••••••${digits.slice(-4)}`
  }
  const [local, domain] = raw.split('@')
  if (!domain) return '••••••'
  const visible = local.slice(0, 2)
  return `${visible}${'•'.repeat(Math.max(local.length - visible.length, 3))}@${domain}`
}

function friendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 501) return "We're unable to send verification codes right now. Please WhatsApp us and we'll help you sign in."
    return err.message
  }
  return 'Something went wrong. Please try again.'
}

// otp_delivery_status ('sent' in production, one of the LOCAL-only 'dev_*'
// values otherwise — see CustomerLoginOtpRequestResponse) is what makes it
// possible to never say "sent" unless a real send actually happened: 'sent'
// itself renders nothing extra here (the existing AuthShell subtitle above
// the form already says a code was sent), every other value is this local-
// dev-only notice instead — never shown, and never reachable, in production.
type OtpNotice = { tone: 'info' | 'success' | 'warning'; text: string } | null

function otpDeliveryNotice(status: OtpDeliveryStatus, devCode: string | null | undefined, channel: Channel): OtpNotice {
  // The server status is not a security boundary: a misconfigured backend
  // must never make a production bundle reveal a development OTP.
  if (import.meta.env.DEV !== true) return null

  const channelWord = channel === 'email' ? 'email' : 'SMS'
  switch (status) {
    case 'dev_code_only':
      return {
        tone: 'info',
        text: `Development mode: ${channelWord} delivery is disabled locally — no message was actually sent. Use this development verification code: ${devCode ?? '(unavailable)'}`,
      }
    case 'dev_real_email_not_allowlisted':
      return {
        tone: 'info',
        text: `This address isn't enabled for local email testing. Use this development verification code instead: ${devCode ?? '(unavailable)'}`,
      }
    case 'dev_real_email_sent':
      return { tone: 'success', text: 'Local test: a real email was sent — check your inbox.' }
    case 'dev_real_email_failed':
      return {
        tone: 'warning',
        text: `Local email test failed — the message could not be delivered. Use this development verification code instead: ${devCode ?? '(unavailable)'}`,
      }
    case 'sent':
    default:
      return null
  }
}

export function LoginPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { customerLogin, isCustomerAuthenticated } = useCustomerAuth()
  const { whatsappNumber } = useBusinessContact()

  const postLoginPath = searchParams.get('returnTo') === '/book' ? '/book' : searchParams.get('returnTo') === '/account' ? '/account' : '/'
  const [channel, setChannel] = useState<Channel>(() => searchParams.get('channel') === 'phone' ? 'phone' : 'email')
  const [step, setStep] = useState<Step>('credentials')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [challengeId, setChallengeId] = useState('')
  const [code, setCode] = useState('')
  // LOCAL-DEV-ONLY: always 'sent' in production (see otpDeliveryNotice) —
  // devOtpCode is never read/shown unless otpDeliveryStatus says a real
  // send did NOT happen, which the backend can only ever say outside
  // production.
  const [otpDeliveryStatus, setOtpDeliveryStatus] = useState<OtpDeliveryStatus>('sent')
  const [devOtpCode, setDevOtpCode] = useState<string | null>(null)
  const [status, setStatus] = useState<'idle' | 'submitting' | 'verifying' | 'error'>('idle')
  const [message, setMessage] = useState('')
  const [oauthNotice, setOauthNotice] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(0)
  const [oauthStatus, setOauthStatus] = useState<CustomerOAuthStatus>({ google: false })

  const busy = status === 'submitting' || status === 'verifying'

  // Checked once on mount so a click either does a real provider redirect
  // or shows the honest not-configured notice — never a button that
  // looks real but silently does nothing. A failed status check is
  // treated the same as "not configured" (the safe default).
  useEffect(() => {
    customerOAuthStatus()
      .then(setOauthStatus)
      .catch(() => setOauthStatus({ google: false }))
  }, [])

  // Handles the two outcomes only a redirect back from OS10's own OAuth
  // callback can produce (see oauth_router.py's _login_redirect_url):
  // either the provider/verification step failed, or a verified OAuth
  // email matched an existing account that hasn't linked this provider
  // yet. Neither ever logs the customer in here — a "link required"
  // customer still has to prove ownership via a normal password+OTP
  // login below, exactly like anyone else.
  useEffect(() => {
    const oauthError = searchParams.get('oauth_error')
    const linkRequired = searchParams.get('oauth_link_required')
    const linkToken = searchParams.get('oauth_link_token')

    if (oauthError) {
      setMessage("We couldn't complete that sign-in. Please try again, or log in with your email and password.")
      setStatus('error')
    } else if (linkRequired && linkToken) {
      sessionStorage.setItem(PENDING_OAUTH_LINK_KEY, JSON.stringify({ provider: linkRequired, linkToken }))
      setMessage(
        'An account with this email already exists. Log in below with your password, then confirm linking your Google account from My Account.',
      )
    }

    if (oauthError || linkRequired) {
      setSearchParams({}, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (cooldown <= 0) return
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000)
    return () => clearTimeout(timer)
  }, [cooldown])

  // Safety net for the redirect after a successful login: driven by the
  // auth context's own state rather than only an imperative navigate()
  // call at the point of success, so the redirect still happens even if
  // this component's state updates and the navigation ever land in
  // different ticks (e.g. under React's automatic batching timing).
  // Lands on the normal public homepage, not /account — the account view
  // is reached deliberately, from the customer's name in the navbar.
  useEffect(() => {
    if (isCustomerAuthenticated) navigate(postLoginPath, { replace: true })
  }, [isCustomerAuthenticated, navigate, postLoginPath])

  function switchChannel(next: Channel) {
    if (next === channel) return
    setChannel(next)
    setIdentifier('')
    setMessage('')
    setStatus('idle')
  }

  async function submitCredentials(e: FormEvent) {
    e.preventDefault()
    setOauthNotice(null)
    setStatus('submitting')
    setMessage('')
    try {
      // dev_otp_code/otp_delivery_status are only ever meaningfully
      // populated outside production (see CustomerLoginOtpRequestResponse)
      // — otpDeliveryNotice is what decides whether/how to surface them;
      // in production otp_delivery_status is always 'sent', which renders
      // nothing, so this UI is unchanged there.
      const result = await customerLoginStep1(channel, identifier, password)
      setChallengeId(result.challenge_id)
      setCode('')
      setOtpDeliveryStatus(import.meta.env.DEV === true ? result.otp_delivery_status : 'sent')
      setDevOtpCode(import.meta.env.DEV === true ? (result.dev_otp_code ?? null) : null)
      setStep('otp')
      setStatus('idle')
      setCooldown(RESEND_COOLDOWN_SECONDS)
    } catch (err) {
      setStatus('error')
      // The backend intentionally returns the identical generic message
      // whether the identifier is unknown or the password is wrong (anti-
      // enumeration) — shown here exactly as received, not reworded.
      setMessage(friendlyError(err))
    }
  }

  async function resendCode() {
    setStatus('submitting')
    setMessage('')
    try {
      const result = await customerLoginStep1(channel, identifier, password)
      setChallengeId(result.challenge_id)
      setCode('')
      setOtpDeliveryStatus(import.meta.env.DEV === true ? result.otp_delivery_status : 'sent')
      setDevOtpCode(import.meta.env.DEV === true ? (result.dev_otp_code ?? null) : null)
      setStatus('idle')
      setCooldown(RESEND_COOLDOWN_SECONDS)
    } catch (err) {
      setStatus('error')
      setMessage(friendlyError(err))
    }
  }

  async function verifyOtp(e: FormEvent) {
    e.preventDefault()
    setStatus('verifying')
    setMessage('')
    try {
      const session = await customerVerifyLoginOtp(challengeId, code)
      // Clear all OTP-step state immediately — nothing from this flow
      // (challenge id, entered code) should linger once authenticated.
      setChallengeId('')
      setCode('')
      setPassword('')
      setStatus('idle')
      customerLogin(session.access_token, session.customer)
      // The only supported return destination is the same-site account
      // page; arbitrary redirect URLs are ignored by postLoginPath.
      navigate(postLoginPath, { replace: true })
    } catch (err) {
      setStatus('error')
      setMessage(friendlyError(err))
    }
  }

  function changeCredentials() {
    setStep('credentials')
    setPassword('')
    setChallengeId('')
    setCode('')
    setMessage('')
    setStatus('idle')
    setCooldown(0)
    setOtpDeliveryStatus('sent')
    setDevOtpCode(null)
  }

  function handleOAuthUnavailable() {
    setOauthNotice(`Continue with Google isn't set up yet. Please log in with your ${channel === 'email' ? 'email' : 'mobile number'} and password for now.`)
  }

  return (
    <AuthShell
      mode="login"
      title={step === 'credentials' ? 'Welcome to Sri Apartment Homestay' : 'Enter your verification code'}
      subtitle={
        step === 'credentials'
          ? 'Sign in securely to manage your bookings and stay details.'
          : `We sent a 6-digit verification code to your ${channel === 'email' ? 'email address' : 'mobile number'}.`
      }
      footer={
        <>
          <p>
            New to Sri Apartment Homestay?{' '}
            <Link to={postLoginPath === "/book" ? "/signup?returnTo=/book" : "/signup"} className="font-semibold text-brand-green underline">
              Create an account
            </Link>
          </p>
          {whatsappNumber && (
            <p className="mt-2">
              Need help?{' '}
              <a href={`https://wa.me/${whatsappNumber}`} target="_blank" rel="noreferrer" className="font-semibold text-brand-green underline">
                WhatsApp us
              </a>
            </p>
          )}
        </>
      }
    >
      <Helmet title="Log In — Sri Apartment Homestay" description="Sign in to your Sri Apartment account." canonicalPath="/login" noindex />

      {step === 'credentials' ? (
        <>
          <h2 className="mb-4 text-center text-xs font-bold tracking-wide text-brand-muted uppercase">
            {channel === 'email' ? 'Sign in with Email' : 'Sign in with Mobile Number'}
          </h2>

          <form onSubmit={submitCredentials} className="space-y-4" noValidate>
            {channel === 'email' ? (
              <label className="block">
                <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Enter your email address</span>
                <input
                  type="email"
                  autoComplete="email"
                  inputMode="email"
                  required
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
                />
              </label>
            ) : (
              <PhoneInput label="Mobile number" value={identifier} onChange={setIdentifier} required autoComplete="tel" />
            )}

            <label className="block">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="block text-xs font-bold tracking-wide text-brand-muted uppercase">Password</span>
                <Link to="/forgot-password" className="text-xs font-semibold text-brand-green underline">
                  Forgot your password?
                </Link>
              </div>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
              />
            </label>

            {message && (
              <p role={status === 'error' ? 'alert' : 'status'} className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
                {message}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || !identifier || !password}
              className="w-full rounded-md bg-brand-gold py-3.5 font-bold tracking-wide text-brand-green-3 uppercase shadow-sm transition hover:bg-brand-gold-2 disabled:opacity-50"
            >
              {status === 'submitting' ? 'Sending code…' : channel === 'email' ? 'Send Verification Code' : 'Send Code'}
            </button>

            <div className="text-center">
              {channel === 'email' ? (
                <button type="button" onClick={() => switchChannel('phone')} className="text-xs font-semibold text-brand-muted underline hover:text-brand-green">
                  Or continue with mobile
                </button>
              ) : (
                <button type="button" onClick={() => switchChannel('email')} className="text-xs font-semibold text-brand-muted underline hover:text-brand-green">
                  Use email instead
                </button>
              )}
            </div>

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
        </>
      ) : (
        <form className="space-y-5" onSubmit={verifyOtp} noValidate>
          <p className="text-center text-sm text-brand-muted">
            Sent to <span className="font-semibold text-brand-green">{maskIdentifier(channel, identifier)}</span>
          </p>

          {(() => {
            const notice = otpDeliveryNotice(otpDeliveryStatus, devOtpCode, channel)
            if (!notice) return null
            const toneClass =
              notice.tone === 'success'
                ? 'bg-green-50 text-green-800'
                : notice.tone === 'warning'
                  ? 'bg-amber-50 text-amber-800'
                  : 'bg-blue-50 text-blue-800'
            return (
              <p role="status" className={`rounded-md px-3 py-2 text-sm ${toneClass}`}>
                {notice.text}
              </p>
            )
          })()}

          <label className="block">
            <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Verification code</span>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              autoComplete="one-time-code"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              placeholder="6-digit code"
              className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-center text-lg tracking-[0.4em] shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
            />
          </label>

          {message && (
            <p role={status === 'error' ? 'alert' : 'status'} className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {message}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || code.length !== 6}
            className="w-full rounded-md bg-brand-gold py-3.5 font-bold tracking-wide text-brand-green-3 uppercase shadow-sm transition hover:bg-brand-gold-2 disabled:opacity-50"
          >
            {status === 'verifying' ? 'Verifying…' : 'Verify & Continue'}
          </button>

          <div className="flex items-center justify-between text-xs">
            <button type="button" onClick={changeCredentials} className="text-brand-muted underline">
              Use different credentials
            </button>
            <span className="text-brand-muted">
              Didn't receive the code?{' '}
              <button
                type="button"
                onClick={() => void resendCode()}
                disabled={cooldown > 0 || busy}
                className="font-semibold text-brand-green underline disabled:cursor-not-allowed disabled:text-brand-muted disabled:no-underline disabled:opacity-50"
              >
                {cooldown > 0 ? `Resend code (${cooldown}s)` : 'Resend code'}
              </button>
            </span>
          </div>
        </form>
      )}
    </AuthShell>
  )
}
