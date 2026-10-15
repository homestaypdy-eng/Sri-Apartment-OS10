import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  getAvailability,
  getQuote,
  validatePromo,
  createHold,
  customerRegister,
  customerLoginStep1,
  customerVerifyLoginOtp,
  customerInitializeIdentity,
  customerUploadIdentityDocument,
  customerSubmitIdentity,
  ApiError,
} from './api'

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = init
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      statusText: ok ? 'OK' : 'Error',
      json: async () => body,
    }),
  )
}

describe('OS10 public API client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('getAvailability calls the verified endpoint with query params and returns the parsed body', async () => {
    mockFetchOnce({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03', available: true, nights: 2, estimated_total: 7000 })
    const result = await getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })

    expect(result.available).toBe(true)
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toContain('/api/v1/public/availability?')
    expect(url).toContain('unit_id=u1')
  })

  it('getQuote returns OS10-authoritative pricing, never computed locally', async () => {
    mockFetchOnce({
      unit_id: 'u1', unit_name: 'Flat A1', check_in: '2026-09-01', check_out: '2026-09-03', nights: 2,
      daily_breakdown: [], subtotal_before_discount: 7000, long_stay_discount_percent: 0, long_stay_discount_amount: 0,
      promo_discount_amount: 0, subtotal_after_discount: 7000, gst_rate_percent: 5, cgst: 0, sgst: 0, igst: 350,
      total: 7350, advance_amount: 7350, currency: 'INR', available: true,
    })
    const quote = await getQuote({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })
    expect(quote.total).toBe(7350)
    expect(quote.gst_rate_percent).toBe(5)
  })

  it('validatePromo posts code + subtotal and surfaces an invalid result without throwing', async () => {
    mockFetchOnce({ valid: false, discount_amount: 0, reason: 'Promo code not found' })
    const result = await validatePromo('BADCODE', 7000)
    expect(result.valid).toBe(false)
    expect(result.reason).toMatch(/not found/i)
  })

  it('createHold surfaces a 409 conflict (unit no longer available) as an ApiError, not a silent failure', async () => {
    mockFetchOnce({ detail: 'This unit is not available for the selected dates' }, { ok: false, status: 409 })
    await expect(
      createHold({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03', guest_name: 'Test', guest_phone: '9876543210', guest_email: 'test@example.com', adults: 2 }),
    ).rejects.toMatchObject({ status: 409, message: expect.stringContaining('not available') })
  })

  it('wraps a network failure (fetch throwing) as an ApiError marked isNetworkError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })).rejects.toBeInstanceOf(ApiError)
    try {
      await getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })
    } catch (err) {
      expect((err as ApiError).isNetworkError).toBe(true)
    }
  })
})

describe('customer identity verification API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('initializes the authenticated customer identity endpoint', async () => {
    mockFetchOnce({ status: 'not_required', document_type: null, document_id: null, verified_at: null, message: null })
    const result = await customerInitializeIdentity('customer-token')
    expect(result.status).toBe('not_required')
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toContain('/api/v1/customer-auth/me/identity/profile')
    expect(init.headers.Authorization).toBe('Bearer customer-token')
    expect(init.body).toBeUndefined()
  })

  it('uploads multipart ID content without setting a JSON content type', async () => {
    mockFetchOnce({ id: 'doc-1', document_type: 'passport', filename: 'passport.pdf', uploaded_at: '2026-01-01T00:00:00Z' }, { status: 201 })
    const file = new File(['%PDF-1.4'], 'passport.pdf', { type: 'application/pdf' })
    const result = await customerUploadIdentityDocument('customer-token', 'passport', file)
    expect(result.id).toBe('doc-1')
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toContain('document_type=passport')
    expect(init.body).toBeInstanceOf(FormData)
    expect(init.headers.Authorization).toBe('Bearer customer-token')
    expect(init.headers['Content-Type']).toBeUndefined()
  })

  it('submits a document for review and surfaces API errors', async () => {
    mockFetchOnce({ status: 'submitted', document_type: 'aadhaar', document_id: 'doc-1', verified_at: null, message: null })
    const status = await customerSubmitIdentity('customer-token', 'doc-1')
    expect(status.status).toBe('submitted')
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body)).toEqual({ document_id: 'doc-1' })

    mockFetchOnce({ detail: 'Document not found' }, { ok: false, status: 404 })
    await expect(customerSubmitIdentity('customer-token', 'other-doc')).rejects.toMatchObject({ status: 404, message: 'Document not found' })
  })
})

describe('customer account auth (two-step password + OTP login, verified against current OS10 source)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('customerRegister posts the new required phone field alongside the existing fields', async () => {
    mockFetchOnce({ id: 'c1', first_name: 'Ada', last_name: 'Lovelace', email: 'ada@example.com', phone: '9876543210', email_verified_at: null, created_at: '2026-01-01T00:00:00Z' })
    const result = await customerRegister({
      first_name: 'Ada', last_name: 'Lovelace', email: 'ada@example.com', phone: '9876543210',
      password: 'CorrectHorse123!', terms_accepted: true, privacy_accepted: true,
    })
    expect(result.phone).toBe('9876543210')
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body as string).phone).toBe('9876543210')
  })

  it('customerLoginStep1 never returns a session — only a challenge_id proving an OTP was dispatched', async () => {
    mockFetchOnce({ challenge_id: 'chal-1', message: "We've sent a verification code to your email.", dev_otp_code: '123456' })
    const result = await customerLoginStep1('email', 'ada@example.com', 'CorrectHorse123!')
    expect(result.challenge_id).toBe('chal-1')
    expect(result).not.toHaveProperty('access_token')
  })

  it('customerLoginStep1 surfaces a wrong password as the same generic 401 the backend returns (anti-enumeration)', async () => {
    mockFetchOnce({ detail: 'Incorrect email/phone or password' }, { ok: false, status: 401 })
    await expect(customerLoginStep1('email', 'unknown@example.com', 'wrong')).rejects.toMatchObject({ status: 401 })
  })

  it('customerVerifyLoginOtp exchanges a correct code for a real session token', async () => {
    mockFetchOnce({
      access_token: 'real.jwt.token', token_type: 'bearer',
      customer: { id: 'c1', first_name: 'Ada', last_name: 'Lovelace', email: 'ada@example.com', phone: '9876543210', email_verified_at: null, created_at: '2026-01-01T00:00:00Z' },
    })
    const session = await customerVerifyLoginOtp('chal-1', '123456')
    expect(session.access_token).toBe('real.jwt.token')
  })

  it('customerVerifyLoginOtp surfaces a wrong/expired code as an ApiError', async () => {
    mockFetchOnce({ detail: 'That code is incorrect or has expired.' }, { ok: false, status: 401 })
    await expect(customerVerifyLoginOtp('chal-1', '000000')).rejects.toMatchObject({ status: 401 })
  })
})

describe('ApiError', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('falls back to statusText when the error body is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: 'Internal Server Error', json: async () => { throw new Error('not json') } }),
    )
    await expect(getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })).rejects.toMatchObject({
      status: 500,
      message: 'Internal Server Error',
    })
  })

  it('uses a plain string detail as-is (hand-raised HTTPException — the common case)', async () => {
    mockFetchOnce({ detail: 'Incorrect email/phone or password' }, { ok: false, status: 401 })
    await expect(getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })).rejects.toMatchObject({
      status: 401,
      message: 'Incorrect email/phone or password',
    })
  })

  it('turns a FastAPI/Pydantic 422 validation-error array into a readable message instead of "[object Object]"', async () => {
    mockFetchOnce(
      { detail: [{ type: 'value_error', loc: ['body', 'email'], msg: 'value is not a valid email address: An email address must have an @-sign.', input: 'not-a-valid-email' }] },
      { ok: false, status: 422 },
    )
    await expect(getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })).rejects.toMatchObject({
      status: 422,
      message: 'email: value is not a valid email address: An email address must have an @-sign.',
    })
  })

  it('joins multiple validation errors and omits the field prefix when loc is missing', async () => {
    mockFetchOnce(
      { detail: [{ msg: 'field required', loc: ['body', 'password'] }, { msg: 'Something went wrong' }] },
      { ok: false, status: 422 },
    )
    await expect(getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })).rejects.toMatchObject({
      status: 422,
      message: 'password: field required Something went wrong',
    })
  })

  it('falls back to statusText when detail is an empty array or otherwise unusable', async () => {
    mockFetchOnce({ detail: [] }, { ok: false, status: 422 })
    await expect(getAvailability({ unit_id: 'u1', check_in: '2026-09-01', check_out: '2026-09-03' })).rejects.toMatchObject({
      status: 422,
      message: 'Error',
    })
  })
})
