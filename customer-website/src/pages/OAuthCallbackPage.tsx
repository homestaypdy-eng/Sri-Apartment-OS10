import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Helmet } from '../components/Helmet'
import { useCustomerAuth } from '../context/CustomerAuthContext'
import { customerGetMe } from '../lib/api'

/** Landing point after OS10's own Google OAuth callback
 * completes. The session token travels here in the URL FRAGMENT (never
 * a query string) — see oauth_router.py's _callback_redirect_url for why
 * — so it's read from window.location.hash, never sent to any server,
 * logged, or left in a shareable link. Restores the customer's profile
 * via the existing GET /me (never trusting a frontend-supplied name),
 * signs in through the SAME CustomerAuthContext every other login path
 * uses, then lands on the normal homepage, exactly like an email/mobile
 * OTP login. */
export function OAuthCallbackPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { customerLogin } = useCustomerAuth()
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return
    ran.current = true

    const hash = location.hash.startsWith('#') ? location.hash.slice(1) : location.hash
    const token = new URLSearchParams(hash).get('token')

    if (!token) {
      navigate('/login?oauth_error=missing_token', { replace: true })
      return
    }

    customerGetMe(token)
      .then((profile) => {
        customerLogin(token, profile)
        // Clears the token out of the URL/history before landing — the
        // fragment must not linger in the browser's back-history entry.
        navigate(sessionStorage.getItem('sri_booking_selection') ? '/book' : '/', { replace: true })
      })
      .catch(() => {
        navigate('/login?oauth_error=session', { replace: true })
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center bg-brand-cream-3 px-4">
      <Helmet title="Signing In — Sri Apartment Homestay" description="Completing your sign-in." canonicalPath="/oauth/callback" noindex />
      <p role="status" className="text-brand-muted">
        Signing you in…
      </p>
    </div>
  )
}
