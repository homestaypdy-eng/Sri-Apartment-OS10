/**
 * The only place in this app that talks to a backend. Every function here
 * calls a real, verified OS10 endpoint (see types.ts header) — nothing in
 * this file computes price, availability, or booking state locally. If
 * OS10 is unreachable, callers get a typed ApiError; they never fall back
 * to a locally-invented answer.
 */
import type {
  CreateHoldRequest,
  CustomerAccountOut,
  CustomerIdentityDocumentOut,
  CustomerIdentityOut,
  CustomerChangePasswordResponse,
  CustomerForgotPasswordResponse,
  CustomerLoginOtpRequestResponse,
  CustomerLoginResponse,
  CustomerOAuthLinkConfirmResponse,
  CustomerOAuthStatus,
  CustomerResetPasswordResponse,
  HoldOut,
  PromoValidateResponse,
  PublicAvailabilityOut,
  PublicBookingConfirmation,
  PublicBusinessInfoOut,
  PublicQuoteOut,
  PublicUnitOut,
  PublicUnitPhotoOut,
  VerifyPaymentRequest,
} from './types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL as string | undefined

if (!BASE_URL && import.meta.env.PROD) {
  // Fail loudly at build/runtime rather than silently hitting a relative
  // path that happens to 404 — a misconfigured deploy should be obvious.
  console.error('VITE_API_BASE_URL is not set. The site cannot reach OS10.')
}

const DEFAULT_TIMEOUT_MS = 12_000

export class ApiError extends Error {
  status: number
  /** true when the request never reached the server (offline, DNS, CORS,
   * timeout) — callers use this to distinguish "OS10 said no" (show the
   * message) from "we couldn't reach OS10" (offer retry). */
  isNetworkError: boolean

  constructor(message: string, status: number, isNetworkError = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.isNetworkError = isNetworkError
  }
}

/** Shape of one entry in FastAPI/Pydantic's 422 `detail` array — see
 * https://fastapi.tiangolo.com/tutorial/handling-errors/#override-request-validation-exceptions */
type FastApiValidationError = { loc?: unknown[]; msg?: string }

/** OS10 error bodies come in two shapes: `detail` is a plain string for
 * hand-raised HTTPExceptions (the common case), or an array of
 * FastAPI/Pydantic validation-error objects for a 422 the framework raised
 * itself. Passing the array straight to `Error.message` stringifies it to
 * "[object Object]" — this normalizes either shape into one readable
 * string, so callers/UI code only ever see plain text. */
function detailToMessage(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail || undefined

  const errors = Array.isArray(detail) ? detail : detail ? [detail] : []
  const messages = errors
    .filter((e): e is FastApiValidationError => typeof e === 'object' && e !== null && typeof (e as FastApiValidationError).msg === 'string')
    .map((e) => {
      const field = Array.isArray(e.loc) ? e.loc.filter((p) => p !== 'body').join('.') : ''
      return field ? `${field}: ${e.msg}` : e.msg!
    })

  return messages.length > 0 ? messages.join(' ') : undefined
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  { timeoutMs = DEFAULT_TIMEOUT_MS, authToken }: { timeoutMs?: number; authToken?: string } = {},
): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(`${BASE_URL ?? ''}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        ...(init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        ...init.headers,
      },
    })

    if (!res.ok) {
      let detail = res.statusText || `Request failed (${res.status})`
      try {
        const body = (await res.json()) as { detail?: unknown }
        const message = detailToMessage(body.detail)
        if (message) detail = message
      } catch {
        // Non-JSON error body — keep the statusText fallback above.
      }
      throw new ApiError(detail, res.status)
    }

    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  } catch (err) {
    if (err instanceof ApiError) throw err
    const isAbort = err instanceof DOMException && err.name === 'AbortError'
    throw new ApiError(
      isAbort ? 'The request timed out. Please check your connection and try again.' : 'Could not reach the server. Please check your connection and try again.',
      0,
      true,
    )
  } finally {
    clearTimeout(timer)
  }
}

function qs(params: Record<string, string | number | undefined>): string {
  const usable = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string | number][]
  return new URLSearchParams(usable.map(([k, v]) => [k, String(v)])).toString()
}

// ---------------------------------------------------------------------------
// Public booking
// ---------------------------------------------------------------------------

export function getPublicUnits(): Promise<PublicUnitOut[]> {
  return request('/api/v1/public/units')
}

/** Single source of truth for business contact info shown on the website
 * (footer, location, contact pages) — see getBusinessContact in
 * businessContact.ts for the fallback behavior when OS10 is unreachable. */
export function getPublicBusinessInfo(): Promise<PublicBusinessInfoOut> {
  return request('/api/v1/public/business-info')
}

/** OS10 returns each photo's `url` as a path relative to the API host —
 * prefix it with BASE_URL here so callers get a ready-to-use <img src>. */
export async function getUnitPhotos(unitId: string): Promise<PublicUnitPhotoOut[]> {
  const photos = await request<PublicUnitPhotoOut[]>(`/api/v1/public/units/${unitId}/photos`)
  return photos.map((p) => ({ ...p, url: `${BASE_URL ?? ''}${p.url}` }))
}

export function getAvailability(params: { unit_id: string; check_in: string; check_out: string }): Promise<PublicAvailabilityOut> {
  return request(`/api/v1/public/availability?${qs(params)}`)
}

export function getQuote(params: { unit_id: string; check_in: string; check_out: string; promo_code?: string; guest_state?: string }): Promise<PublicQuoteOut> {
  return request(`/api/v1/public/quote?${qs(params)}`)
}

export function validatePromo(code: string, subtotal: number): Promise<PromoValidateResponse> {
  return request('/api/v1/public/promo/validate', {
    method: 'POST',
    body: JSON.stringify({ code, subtotal }),
  })
}

/** Read-only server check; order creation still rechecks the real gateway. */
export function getPaymentAvailability(authToken: string): Promise<{ available: boolean }> {
  return request('/api/v1/public/payment-availability', {}, { authToken })
}

export function createHold(payload: CreateHoldRequest, authToken?: string): Promise<HoldOut> {
  return request('/api/v1/public/holds', {
    method: 'POST',
    body: JSON.stringify(payload),
  }, { authToken })
}

export function verifyPayment(holdId: string, payload: VerifyPaymentRequest, authToken?: string): Promise<PublicBookingConfirmation> {
  return request(`/api/v1/public/holds/${holdId}/verify-payment`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }, { authToken })
}

// ---------------------------------------------------------------------------
// Customer account auth (OS10 customer_auth module) — the site's ONLY
// public sign-in surface: register / two-step (password + OTP) login /
// logout / forgot+reset password. Deliberately separate from the OS10
// guest-booking auth system (no Guest Login is exposed anywhere on this
// site) and from staff auth — see backend app/modules/customer_auth's own
// module docstrings for the security boundary this mirrors.
// ---------------------------------------------------------------------------

export function customerRegister(payload: {
  first_name: string
  last_name: string
  email: string
  phone: string
  password: string
  terms_accepted: boolean
  privacy_accepted: boolean
}): Promise<CustomerAccountOut> {
  return request('/api/v1/customer-auth/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** Step 1: password is checked here; a correct password never returns a
 * session, only proof a one-time code was dispatched to the SAME channel
 * (email→email, phone→SMS). See customerVerifyLoginOtp for step 2. */
export function customerLoginStep1(channel: 'email' | 'phone', identifier: string, password: string): Promise<CustomerLoginOtpRequestResponse> {
  return request('/api/v1/customer-auth/login', {
    method: 'POST',
    body: JSON.stringify({ channel, identifier, password }),
  })
}

/** Step 2: the only call that can ever return a real customer session. */
export function customerVerifyLoginOtp(challengeId: string, code: string): Promise<CustomerLoginResponse> {
  return request('/api/v1/customer-auth/login/verify-otp', {
    method: 'POST',
    body: JSON.stringify({ challenge_id: challengeId, code }),
  })
}

export function customerLogout(authToken: string): Promise<void> {
  return request('/api/v1/customer-auth/logout', { method: 'POST' }, { authToken })
}

/** Restores the authenticated customer's profile from the session token
 * alone — used on page load so a refresh doesn't lose the personalized
 * navbar/account view, without ever needing to keep the customer object
 * in localStorage itself. */
export function customerGetMe(authToken: string): Promise<CustomerAccountOut> {
  return request('/api/v1/customer-auth/me', {}, { authToken })
}

export function customerInitializeIdentity(authToken: string): Promise<CustomerIdentityOut> {
  return request('/api/v1/customer-auth/me/identity/profile', { method: 'POST' }, { authToken })
}

export function customerGetIdentity(authToken: string): Promise<CustomerIdentityOut> {
  return request('/api/v1/customer-auth/me/identity', {}, { authToken })
}

export function customerUploadIdentityDocument(
  authToken: string,
  documentType: string,
  file: File,
): Promise<CustomerIdentityDocumentOut> {
  const form = new FormData()
  form.append('file', file)
  return request(`/api/v1/customer-auth/me/identity/documents?${qs({ document_type: documentType })}`, {
    method: 'POST', body: form,
  }, { authToken })
}

export function customerSubmitIdentity(authToken: string, documentId: string): Promise<CustomerIdentityOut> {
  return request('/api/v1/customer-auth/me/identity/submit', {
    method: 'POST', body: JSON.stringify({ document_id: documentId }),
  }, { authToken })
}

/** Deliberately excludes email — see the backend's CustomerUpdateProfileRequest
 * docstring for why an email change needs its own re-verification flow
 * this doesn't build. */
export function customerUpdateProfile(
  authToken: string,
  payload: { first_name: string; last_name: string; phone: string },
): Promise<CustomerAccountOut> {
  return request('/api/v1/customer-auth/me', { method: 'PATCH', body: JSON.stringify(payload) }, { authToken })
}

export function customerChangePassword(
  authToken: string,
  payload: { current_password: string; new_password: string },
): Promise<CustomerChangePasswordResponse> {
  return request(
    '/api/v1/customer-auth/change-password',
    { method: 'POST', body: JSON.stringify({ current_password: payload.current_password, new_password: payload.new_password }) },
    { authToken },
  )
}

export function customerForgotPassword(email: string): Promise<CustomerForgotPasswordResponse> {
  return request('/api/v1/customer-auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export function customerResetPassword(token: string, newPassword: string): Promise<CustomerResetPasswordResponse> {
  return request('/api/v1/customer-auth/reset-password', {
    method: 'POST',
    body: JSON.stringify({ token, new_password: newPassword }),
  })
}

// ---------------------------------------------------------------------------
// Customer social login (Google — the only provider this project
// supports) — see OS10's customer_auth/oauth_router.py for the full
// security model. The frontend never touches a client secret or provider
// token: a click either does a full-page navigation to OS10's own /start
// endpoint (which redirects to the real provider) or, if unconfigured,
// shows the existing honest notice — see OAuthButtons.tsx and
// customerOAuthStatus below.
// ---------------------------------------------------------------------------

export function customerOAuthStatus(): Promise<CustomerOAuthStatus> {
  return request('/api/v1/customer-auth/oauth/status')
}

/** Full backend URL a "Continue with Google" click navigates the whole
 * page to — never fetched via JS, since the response is itself a
 * redirect to Google's real consent screen. */
export function oauthStartUrl(provider: 'google'): string {
  return `${BASE_URL ?? ''}/api/v1/customer-auth/oauth/${provider}/start`
}

export function customerConfirmOAuthLink(
  authToken: string,
  provider: 'google',
  linkToken: string,
): Promise<CustomerOAuthLinkConfirmResponse> {
  return request(
    `/api/v1/customer-auth/oauth/${provider}/confirm-link`,
    { method: 'POST', body: JSON.stringify({ link_token: linkToken }) },
    { authToken },
  )
}
