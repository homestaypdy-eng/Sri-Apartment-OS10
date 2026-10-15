import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AuthShell } from '../components/AuthShell'
import { Helmet } from '../components/Helmet'
import { useCustomerAuth } from '../context/CustomerAuthContext'
import { ApiError, customerChangePassword } from '../lib/api'

export function ChangePasswordPage() {
  const { isCustomerAuthenticated, isInitializing, token, customerLogout } = useCustomerAuth()
  const navigate = useNavigate()

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [status, setStatus] = useState<'idle' | 'saving' | 'done' | 'error'>('idle')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!isInitializing && !isCustomerAuthenticated) navigate('/login', { replace: true })
  }, [isInitializing, isCustomerAuthenticated, navigate])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!token) return
    if (newPassword !== confirmPassword) {
      setStatus('error')
      setMessage('New passwords do not match.')
      return
    }
    setStatus('saving')
    setMessage('')
    try {
      await customerChangePassword(token, { current_password: currentPassword, new_password: newPassword })
      setStatus('done')
    } catch (err) {
      setStatus('error')
      setMessage(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    }
  }

  async function handleContinueToLogin() {
    // The backend revokes every active session (including this one) on a
    // successful password change — mirror that locally now rather than
    // waiting for the next authenticated call to fail with a 401.
    await customerLogout().catch(() => {})
    navigate('/login')
  }

  if (isInitializing || !isCustomerAuthenticated) return null

  if (status === 'done') {
    return (
      <AuthShell title="Password Changed" subtitle="Please log in again with your new password">
        <Helmet title="Change Password — Sri Apartment Homestay" description="Change your Sri Apartment account password." canonicalPath="/account/change-password" noindex />
        <p role="status" className="rounded-md bg-brand-gold/10 px-3 py-2 text-sm text-brand-gold-4">
          Your password has been changed. For your security, you've been signed out everywhere and need to log in again.
        </p>
        <button
          type="button"
          onClick={() => void handleContinueToLogin()}
          className="mt-4 w-full rounded-md bg-brand-gold py-3.5 font-bold tracking-wide text-brand-green-3 uppercase shadow-sm transition hover:bg-brand-gold-2"
        >
          Go to Log In
        </button>
      </AuthShell>
    )
  }

  return (
    <AuthShell
      title="Change Password"
      subtitle="Choose a new password for your account"
      footer={
        <Link to="/account" className="font-semibold text-brand-green underline">
          Back to My Account
        </Link>
      }
    >
      <Helmet title="Change Password — Sri Apartment Homestay" description="Change your Sri Apartment account password." canonicalPath="/account/change-password" noindex />

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <label className="block">
          <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className="w-full rounded-md border border-brand-border bg-brand-cream px-3.5 py-3 text-sm shadow-sm transition focus:border-brand-gold focus:ring-1 focus:ring-brand-gold focus:outline-none"
          />
        </label>

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
          disabled={status === 'saving'}
          className="w-full rounded-md bg-brand-gold py-3.5 font-bold tracking-wide text-brand-green-3 uppercase shadow-sm transition hover:bg-brand-gold-2 disabled:opacity-50"
        >
          {status === 'saving' ? 'Changing…' : 'Change Password'}
        </button>
      </form>
    </AuthShell>
  )
}
