import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Helmet } from '../components/Helmet'
import { useCustomerAuth } from '../context/CustomerAuthContext'
import {
  ApiError,
  customerConfirmOAuthLink,
  customerInitializeIdentity,
  customerSubmitIdentity,
  customerUploadIdentityDocument,
} from '../lib/api'
import type { CustomerIdentityOut } from '../lib/types'

const PENDING_OAUTH_LINK_KEY = 'pending_oauth_link'

interface PendingOAuthLink {
  provider: 'google'
  linkToken: string
}

// Reached deliberately from the customer's name in the navbar — never the
// automatic landing page after login (that's the homepage). The
// customer_auth backend is deliberately isolated from Guest/Booking data
// (no auto-linking), so there is no booking history to show here yet —
// that would be separate, later work with its own explicit linking design.
export function AccountPage() {
  const { isCustomerAuthenticated, isInitializing, customer, token, customerLogout } = useCustomerAuth()
  const navigate = useNavigate()

  const [pendingLink, setPendingLink] = useState<PendingOAuthLink | null>(null)
  const [linkStatus, setLinkStatus] = useState<'idle' | 'linking' | 'linked' | 'error'>('idle')
  const [linkMessage, setLinkMessage] = useState('')
  const [identity, setIdentity] = useState<CustomerIdentityOut | null>(null)
  const [identityLoading, setIdentityLoading] = useState(true)
  const [identityBusy, setIdentityBusy] = useState(false)
  const [identityError, setIdentityError] = useState('')
  const [contactVerificationRequired, setContactVerificationRequired] = useState(false)
  const [identityContactMismatch, setIdentityContactMismatch] = useState(false)
  const [identityNeedsStaffAssistance, setIdentityNeedsStaffAssistance] = useState(false)
  const [contactVerificationBusy, setContactVerificationBusy] = useState(false)
  const [documentType, setDocumentType] = useState('aadhaar')
  const [identityFile, setIdentityFile] = useState<File | null>(null)
  const [identityFilename, setIdentityFilename] = useState('')

  useEffect(() => {
    if (!isInitializing && !isCustomerAuthenticated && !contactVerificationBusy) navigate('/login', { replace: true })
  }, [isInitializing, isCustomerAuthenticated, contactVerificationBusy, navigate])

  useEffect(() => {
    if (!token || !isCustomerAuthenticated) return
    let active = true
    setIdentityLoading(true)
    customerInitializeIdentity(token)
      .then((result) => { if (active) setIdentity(result) })
      .catch((err: unknown) => {
        if (active) {
          const message = err instanceof ApiError ? err.message : 'Identity verification is temporarily unavailable.'
          const contactVerificationError = err instanceof ApiError && err.status === 409 && (
            message === 'Verify your email or phone number before setting up identity verification' ||
            message === 'Please verify your matching contact method or contact Sri Apartment to link your guest profile safely'
          )
          const contactMismatch = message === 'Please verify your matching contact method or contact Sri Apartment to link your guest profile safely'
          const needsStaffAssistance = err instanceof ApiError && err.status === 409 && (
            message.includes('safely match your account to one guest profile') ||
            message.includes('safely link this guest profile') ||
            message.includes('guest profile needs staff assistance')
          )
          setContactVerificationRequired(contactVerificationError)
          setIdentityContactMismatch(contactMismatch)
          setIdentityNeedsStaffAssistance(needsStaffAssistance)
          setIdentityError(contactVerificationError || needsStaffAssistance ? '' : message)
        }
      })
      .finally(() => { if (active) setIdentityLoading(false) })
    return () => { active = false }
  }, [token, isCustomerAuthenticated])

  // Picks up a link set by LoginPage when an OAuth attempt matched this
  // account's (verified) email but wasn't linked yet — see LoginPage's
  // oauth_link_required handling. Reaching this page at all already
  // proves the customer just completed a normal password+OTP login for
  // THIS account, which is the proof-of-ownership step required before
  // the identities are actually linked (see confirm-link on the backend).
  useEffect(() => {
    const raw = sessionStorage.getItem(PENDING_OAUTH_LINK_KEY)
    if (!raw) return
    try {
      setPendingLink(JSON.parse(raw) as PendingOAuthLink)
    } catch {
      sessionStorage.removeItem(PENDING_OAUTH_LINK_KEY)
    }
  }, [])

  async function handleConfirmLink() {
    if (!pendingLink || !token) return
    setLinkStatus('linking')
    setLinkMessage('')
    try {
      await customerConfirmOAuthLink(token, pendingLink.provider, pendingLink.linkToken)
      sessionStorage.removeItem(PENDING_OAUTH_LINK_KEY)
      setPendingLink(null)
      setLinkStatus('linked')
    } catch (err) {
      setLinkStatus('error')
      setLinkMessage(err instanceof ApiError ? err.message : 'Could not link your account. Please try again.')
    }
  }

  function dismissPendingLink() {
    sessionStorage.removeItem(PENDING_OAUTH_LINK_KEY)
    setPendingLink(null)
  }

  function handleIdentityFileChange(file: File | null) {
    setIdentityFile(file)
    setIdentityFilename(file?.name ?? '')
    setIdentityError('')
    // A replacement selection must not leave a previous document ready to submit.
    setIdentity((current) => current ? { ...current, document_id: null, document_type: null } : current)
    if (file && file.size > 10 * 1024 * 1024) {
      setIdentityFile(null)
      setIdentityFilename('')
      setIdentityError('The selected file is larger than 10 MB. Choose a smaller file.')
    } else if (file && !['image/jpeg', 'image/png', 'image/webp', 'application/pdf'].includes(file.type)) {
      setIdentityFile(null)
      setIdentityFilename('')
      setIdentityError('Choose a JPEG, PNG, WebP, or PDF file.')
    }
  }

  async function handleIdentityUpload() {
    if (!token || !identityFile) return
    setIdentityBusy(true)
    setIdentityError('')
    try {
      const uploaded = await customerUploadIdentityDocument(token, documentType, identityFile)
      setIdentity((current) => current ? { ...current, document_id: uploaded.id, document_type: uploaded.document_type } : current)
      setIdentityFile(null)
    } catch (err) {
      setIdentityError(err instanceof ApiError ? err.message : 'We could not upload this document. Please try again.')
    } finally {
      setIdentityBusy(false)
    }
  }

  async function handleIdentitySubmit() {
    if (!token || !identity?.document_id) return
    setIdentityBusy(true)
    setIdentityError('')
    try {
      setIdentity(await customerSubmitIdentity(token, identity.document_id))
    } catch (err) {
      setIdentityError(err instanceof ApiError ? err.message : 'We could not submit your document. Please try again.')
    } finally {
      setIdentityBusy(false)
    }
  }

  async function handleVerifyContact(channel: 'email' | 'phone') {
    setContactVerificationBusy(true)
    try {
      // Reuse the existing password + OTP login flow. Only successful OTP
      // verification marks this channel as verified on the server.
      await customerLogout()
      navigate(`/login?channel=${channel}&returnTo=%2Faccount`, { replace: true })
    } catch {
      setContactVerificationBusy(false)
      setIdentityError('Could not open the contact verification flow. Please try again.')
    }
  }

  async function handleLogout() {
    await customerLogout()
    navigate('/')
  }

  if (isInitializing || !isCustomerAuthenticated) return null

  const providerLabel = 'Google'

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-brand-cream-3 px-4 py-16 sm:px-6">
      <Helmet title="My Account — Sri Apartment Homestay" description="Manage your Sri Apartment account." canonicalPath="/account" noindex />
      <div className="mx-auto max-w-2xl">
        <h1 className="font-serif text-3xl text-brand-green">
          {customer ? `Welcome, ${customer.first_name} ${customer.last_name}` : 'Welcome back'}
        </h1>

        {pendingLink && (
          <div className="mt-4 rounded-md border border-brand-gold/40 bg-brand-gold/10 px-4 py-3 text-sm text-brand-gold-4">
            <p>Link your {providerLabel} account so you can sign in with it next time?</p>
            <div className="mt-2 flex items-center gap-4">
              <button
                type="button"
                onClick={() => void handleConfirmLink()}
                disabled={linkStatus === 'linking'}
                className="rounded-md bg-brand-gold px-3 py-1.5 text-xs font-bold tracking-wide text-brand-green-3 uppercase transition hover:bg-brand-gold-2 disabled:opacity-50"
              >
                {linkStatus === 'linking' ? 'Linking…' : `Link ${providerLabel}`}
              </button>
              <button type="button" onClick={dismissPendingLink} className="text-xs text-brand-muted underline">
                Not now
              </button>
            </div>
            {linkStatus === 'error' && (
              <p role="alert" className="mt-2 text-red-700">
                {linkMessage}
              </p>
            )}
          </div>
        )}
        {linkStatus === 'linked' && (
          <p role="status" className="mt-4 rounded-md bg-brand-gold/10 px-4 py-3 text-sm text-brand-gold-4">
            Your {providerLabel} account has been linked.
          </p>
        )}

        <div className="mt-6 rounded-lg border border-brand-border bg-brand-cream px-6 py-8 shadow-sm sm:px-8">
          <dl className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-bold tracking-wide text-brand-muted uppercase">Email</dt>
              <dd className="mt-1 text-sm text-brand-green">{customer?.email ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs font-bold tracking-wide text-brand-muted uppercase">Phone Number</dt>
              <dd className="mt-1 text-sm text-brand-green">{customer?.phone ?? 'Not set'}</dd>
            </div>
          </dl>

          <div className="mt-6 rounded-md bg-brand-gold/10 px-4 py-3 text-sm text-brand-gold-4">
            Booking-related features for registered accounts will be available in a future update.
          </div>

          <section aria-labelledby="identity-verification-title" className="mt-6 rounded-lg border border-brand-border bg-brand-cream-2 p-5 sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 id="identity-verification-title" className="font-serif text-xl text-brand-green">Identity Verification</h2>
                <p className="mt-1 text-sm text-brand-muted">Securely submit the Main Guest’s government ID for OS10 staff review.</p>
              </div>
              {!identityLoading && identity && (
                <span className={`rounded-full px-3 py-1 text-xs font-bold uppercase tracking-wide ${identity.status === 'verified' ? 'bg-emerald-100 text-emerald-800' : identity.status === 'rejected' ? 'bg-red-100 text-red-800' : 'bg-brand-gold/15 text-brand-gold-4'}`}>
                  {identity.status === 'verified' ? 'Identity Verified' : identity.status === 'rejected' ? 'Rejected' : ['submitted', 'under_review', 'pending'].includes(identity.status) ? 'Pending Staff Verification' : 'Not submitted'}
                </span>
              )}
            </div>

            {identityLoading ? <p role="status" className="mt-4 text-sm text-brand-muted">Loading identity status…</p> : null}
            {!identityLoading && identity?.status === 'verified' && (
              <p className="mt-4 text-sm text-brand-green"><strong>Identity Verified.</strong> Future bookings using this same guest profile do not need this ID uploaded again.</p>
            )}
            {!identityLoading && identity && ['submitted', 'under_review', 'pending'].includes(identity.status) && (
              <p className="mt-4 text-sm text-brand-green"><strong>Pending Staff Verification.</strong> Sri Apartment staff will review your submitted ID; you cannot approve it yourself.</p>
            )}
            {!identityLoading && identity?.status === 'rejected' && (
              <p className="mt-4 text-sm text-brand-green">{identity.message || 'Verification was unsuccessful.'} You can upload a new document and submit it for review.</p>
            )}
            {!identityLoading && identity && ['not_required', 'expired'].includes(identity.status) && (
              <p className="mt-4 text-sm text-brand-green">Main Guest ID verification has not been completed. Upload an accepted government ID to submit it for staff review.</p>
            )}

            {!identityLoading && identity && identity.status !== 'verified' && !['submitted', 'under_review', 'pending'].includes(identity.status) && (
              <div className="mt-4 space-y-3">
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
                  <label className="text-sm font-medium text-brand-green">
                    Document type
                    <select value={documentType} onChange={(event) => setDocumentType(event.target.value)} className="mt-1 block w-full rounded border border-brand-border bg-brand-cream px-3 py-2">
                      <option value="aadhaar">Aadhaar</option>
                      <option value="pan">PAN</option>
                      <option value="passport">Passport</option>
                      <option value="driving_license">Driving Licence</option>
                      <option value="other">Other</option>
                    </select>
                  </label>
                  <label className="text-sm font-medium text-brand-green">
                    Choose / Upload ID Proof
                    <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" capture="environment" onChange={(event) => handleIdentityFileChange(event.target.files?.[0] ?? null)} className="mt-1 block w-full text-sm text-brand-muted file:mr-3 file:rounded file:border-0 file:bg-brand-gold/15 file:px-3 file:py-2 file:font-semibold file:text-brand-green" />
                  </label>
                </div>
                <p className="text-xs text-brand-muted">JPEG, PNG, WebP or PDF; maximum 10 MB. Documents are private and reviewed by authorized staff.</p>
                {identityFile && <p role="status" className="text-sm text-brand-muted">Selected file: {identityFilename}</p>}
                {identity.document_id && (
                  <p role="status" className="text-sm text-brand-green">Document uploaded: {identityFilename || 'ID proof'}. Submit it when you are ready for review.</p>
                )}
                <div className="flex flex-wrap gap-3">
                  <button type="button" onClick={() => void handleIdentityUpload()} disabled={!identityFile || identityBusy} className="rounded-md bg-brand-green px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-green-2 disabled:cursor-not-allowed disabled:opacity-50">
                    {identityBusy ? 'Please wait…' : 'Upload ID Proof'}
                  </button>
                  {identity.document_id && (
                    <button type="button" onClick={() => void handleIdentitySubmit()} disabled={identityBusy} className="rounded-md border border-brand-green px-4 py-2.5 text-sm font-semibold text-brand-green transition hover:bg-brand-green/5 disabled:opacity-50">
                      Submit for Verification
                    </button>
                  )}
                </div>
              </div>
            )}
            {contactVerificationRequired && (
              <div className="mt-4 rounded-md border border-brand-gold/30 bg-brand-gold/10 p-4">
                <p className="text-sm text-brand-green">
                  {identityContactMismatch
                    ? 'A guest profile matches an account contact that is not verified yet. Verify that contact to let us safely connect your account. We will not link profiles using a name alone.'
                    : 'Verify an email or phone number to securely set up your Main Guest identity. Use the contact method that matches your Sri Apartment booking.'}
                  {' '}Only authorized OS10 staff can approve identity documents.
                </p>
                <div className="mt-3 flex flex-wrap gap-3">
                  {!customer?.email_verified_at && (
                    <button type="button" disabled={contactVerificationBusy} onClick={() => void handleVerifyContact('email')} className="rounded-md border border-brand-green px-3 py-2 text-sm font-semibold text-brand-green disabled:opacity-50">
                      Verify Email
                    </button>
                  )}
                  {customer?.phone && !customer.phone_verified_at && (
                    <button type="button" disabled={contactVerificationBusy} onClick={() => void handleVerifyContact('phone')} className="rounded-md border border-brand-green px-3 py-2 text-sm font-semibold text-brand-green disabled:opacity-50">
                      Verify Phone
                    </button>
                  )}
                  {!customer?.phone && <Link to="/account/edit" className="self-center text-sm font-semibold text-brand-green underline">Add a phone number first</Link>}
                  {customer?.email_verified_at && (!customer?.phone || customer.phone_verified_at) && (
                    <Link to="/contact" className="self-center text-sm font-semibold text-brand-green underline">Contact Sri Apartment for help</Link>
                  )}
                </div>
              </div>
            )}
            {identityNeedsStaffAssistance && (
              <div role="status" className="mt-4 rounded-md border border-brand-gold/30 bg-brand-gold/10 p-4 text-sm text-brand-green">
                <p>We could not safely connect your account to one guest profile. Please contact Sri Apartment so staff can review the records. Your identity information will remain protected until the match is resolved.</p>
                <Link to="/contact" className="mt-2 inline-block font-semibold underline">Contact Sri Apartment</Link>
              </div>
            )}
            {identityError && (
              <p role="alert" className="mt-3 text-sm text-red-700">{identityError}{' '}<Link to="/contact" className="font-semibold underline">Contact Sri Apartment</Link></p>
            )}
          </section>

          <div className="mt-8 flex flex-col gap-3 border-t border-brand-border pt-6 sm:flex-row sm:items-center">
            <Link
              to="/account/edit"
              className="rounded-md border border-brand-border bg-brand-cream px-4 py-2.5 text-center text-sm font-semibold text-brand-green transition hover:border-brand-gold hover:bg-brand-gold/10"
            >
              Edit Profile
            </Link>
            <Link
              to="/account/change-password"
              className="rounded-md border border-brand-border bg-brand-cream px-4 py-2.5 text-center text-sm font-semibold text-brand-green transition hover:border-brand-gold hover:bg-brand-gold/10"
            >
              Change Password
            </Link>
            <button
              type="button"
              onClick={() => void handleLogout()}
              className="text-sm text-brand-muted underline sm:ml-auto"
            >
              Logout
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
