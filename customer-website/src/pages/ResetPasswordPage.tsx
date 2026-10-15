import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AuthShell } from '../components/AuthShell'
import { Helmet } from '../components/Helmet'
import { ApiError, customerResetPassword } from '../lib/api'

export function ResetPasswordPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [status, setStatus] = useState<'idle' | 'submitting' | 'done' | 'error'>('idle')
  const [message, setMessage] = useState('')

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (newPassword !== confirmPassword) {
      setStatus('error')
      setMessage('Passwords do not match.')
      return
    }
    setStatus('submitting')
    setMessage('')
    try {
      const result = await customerResetPassword(token, newPassword)
      setMessage(result.message)
      setStatus('done')
    } catch (err) {
      setStatus('error')
      setMessage(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    }
  }

  if (!token) {
    return (
      <AuthShell title="Reset Password">
        <Helmet title="Reset Password — Sri Apartment Homestay" description="Reset your Sri Apartment account password." canonicalPath="/reset-password" noindex />
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          This reset link is missing its token. Please request a new one.
        </p>
        <Link to="/forgot-password" className="mt-4 inline-block text-sm font-semibold text-brand-green underline">
          Request a new link
        </Link>
      </AuthShell>
    )
  }

  if (status === 'done') {
    return (
      <AuthShell title="Password reset">
        <Helmet title="Reset Password — Sri Apartment Homestay" description="Reset your Sri Apartment account password." canonicalPath="/reset-password" noindex />
        <p role="status" className="rounded-md bg-brand-gold/10 px-3 py-2 text-sm text-brand-gold-4">
          {message}
        </p>
        <Link to="/login" className="mt-4 inline-block text-sm font-semibold text-brand-green underline">
          Go to Log In
        </Link>
      </AuthShell>
    )
  }

  return (
    <AuthShell title="Reset Password" subtitle="Choose a new password for your account">
      <Helmet title="Reset Password — Sri Apartment Homestay" description="Reset your Sri Apartment account password." canonicalPath="/reset-password" noindex />

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <label className="block">
          <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">New password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
          />
          <span className="mt-1.5 block text-xs text-brand-muted">At least 8 characters, with a letter and a number.</span>
        </label>

        <label className="block">
          <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Confirm new password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
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
          disabled={status === 'submitting' || !newPassword || !confirmPassword}
          className="w-full rounded-md bg-brand-gold py-3.5 font-bold tracking-wide text-brand-green-3 uppercase shadow-sm transition hover:bg-brand-gold-2 disabled:opacity-50"
        >
          {status === 'submitting' ? 'Resetting…' : 'Reset Password'}
        </button>
      </form>
    </AuthShell>
  )
}
