import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AuthShell } from '../components/AuthShell'
import { Helmet } from '../components/Helmet'
import { PhoneInput } from '../components/PhoneInput'
import { useCustomerAuth } from '../context/CustomerAuthContext'
import { ApiError, customerUpdateProfile } from '../lib/api'
import { isValidPhoneNumber } from '../lib/phone'

export function EditProfilePage() {
  const { isCustomerAuthenticated, isInitializing, customer, token, updateCustomer } = useCustomerAuth()
  const navigate = useNavigate()

  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [phone, setPhone] = useState('')
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!isInitializing && !isCustomerAuthenticated) navigate('/login', { replace: true })
  }, [isInitializing, isCustomerAuthenticated, navigate])

  useEffect(() => {
    if (!customer) return
    setFirstName(customer.first_name)
    setLastName(customer.last_name)
    setPhone(customer.phone ?? '')
  }, [customer])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!token) return
    if (!isValidPhoneNumber(phone)) {
      setStatus('error')
      setMessage('Please enter a valid phone number.')
      return
    }
    setStatus('saving')
    setMessage('')
    try {
      const updated = await customerUpdateProfile(token, { first_name: firstName, last_name: lastName, phone })
      // Pushes the saved profile into CustomerAuthContext immediately — the
      // navbar and Account page both re-render with the new values with no
      // page refresh and no second round-trip to /me.
      updateCustomer(updated)
      setStatus('saved')
      // Brief, visible confirmation, then return to the account view the
      // customer came from — an editor that never closes on success reads
      // as broken, not saved. Only a successful save reaches this branch;
      // validation/API failures fall to the catch block below and leave
      // the form open with the entered values intact.
      window.setTimeout(() => navigate('/account', { replace: true }), 700)
    } catch (err) {
      setStatus('error')
      setMessage(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    }
  }

  if (isInitializing || !isCustomerAuthenticated) return null

  return (
    <AuthShell
      title="Edit Profile"
      subtitle="Update your personal information"
      footer={
        <Link to="/account" className="font-semibold text-brand-green underline">
          Back to My Account
        </Link>
      }
    >
      <Helmet title="Edit Profile — Sri Apartment Homestay" description="Update your Sri Apartment account details." canonicalPath="/account/edit" noindex />

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

        <PhoneInput label="Phone number" value={phone} onChange={setPhone} required autoComplete="tel" />

        <label className="block">
          <span className="mb-1.5 block text-xs font-bold tracking-wide text-brand-muted uppercase">Email</span>
          <input
            type="email"
            value={customer?.email ?? ''}
            disabled
            className="w-full cursor-not-allowed rounded-md border border-brand-border bg-brand-cream-2 px-3.5 py-3 text-sm text-brand-muted"
          />
          <span className="mt-1.5 block text-xs text-brand-muted">Email can't be changed here. WhatsApp us if you need it updated.</span>
        </label>

        {status === 'saved' && (
          <p role="status" className="rounded-md bg-brand-gold/10 px-3 py-2 text-sm text-brand-gold-4">
            Profile updated.
          </p>
        )}
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
          {status === 'saving' ? 'Saving…' : 'Save Changes'}
        </button>
      </form>
    </AuthShell>
  )
}
