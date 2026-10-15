import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { AuthShell } from '../components/AuthShell'
import { Helmet } from '../components/Helmet'
import { ApiError, customerForgotPassword } from '../lib/api'

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<'idle' | 'submitting' | 'sent' | 'error'>('idle')
  const [message, setMessage] = useState('')
  // Dev/testability-only — see CustomerForgotPasswordResponse.dev_reset_token.
  // Always absent/null in production; never rendered there.
  const [devResetToken, setDevResetToken] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setStatus('submitting')
    setMessage('')
    try {
      const result = await customerForgotPassword(email)
      setMessage(result.message)
      setDevResetToken(result.dev_reset_token ?? null)
      setStatus('sent')
    } catch (err) {
      setStatus('error')
      setMessage(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    }
  }

  return (
    <AuthShell
      title="Forgot Password"
      subtitle="We'll send a reset link to your email"
      footer={
        <p>
          Remembered it?{' '}
          <Link to="/login" className="font-semibold text-brand-green underline">
            Log In
          </Link>
        </p>
      }
    >
      <Helmet title="Forgot Password — Sri Apartment Homestay" description="Reset your Sri Apartment account password." canonicalPath="/forgot-password" noindex />

      {status === 'sent' ? (
        <div className="space-y-4">
          <p role="status" className="rounded-md bg-brand-gold/10 px-3 py-2 text-sm text-brand-gold-4">
            {message}
          </p>
          {devResetToken && (
            <div className="rounded-md border border-brand-border bg-brand-cream-2 px-3 py-2 text-xs text-brand-muted">
              <p className="font-bold tracking-wide uppercase">Development mode</p>
              <Link to={`/reset-password?token=${devResetToken}`} className="mt-1 block break-all text-brand-green underline">
                Continue to reset password
              </Link>
            </div>
          )}
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <label className="block">
            <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Email address</span>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
            />
          </label>

          {status === 'error' && (
            <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {message}
            </p>
          )}

          <button
            type="submit"
            disabled={status === 'submitting' || !email}
            className="w-full rounded-md bg-brand-gold py-3.5 font-bold tracking-wide text-brand-green-3 uppercase shadow-sm transition hover:bg-brand-gold-2 disabled:opacity-50"
          >
            {status === 'submitting' ? 'Sending…' : 'Send Reset Link'}
          </button>
        </form>
      )}
    </AuthShell>
  )
}
