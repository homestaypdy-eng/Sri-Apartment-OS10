import { describe, expect, it, vi } from 'vitest'
import { assertIsolatedLocalTarget, devApiGuard, devApiProxy, isAllowedDevApiRequest, isAllowedIsolatedCustomerRequest } from './devApiProxy.ts'

const LOCAL = 'http://127.0.0.1:8025'
const ID = '231fb3ff-8e54-4c75-99ba-434c745d96a9'

describe('isolated local customer API routing', () => {
  it.each([
    ['GET', '/api/v1/public/payment-availability'],
    ['POST', '/api/v1/customer-auth/register'],
    ['POST', '/api/v1/customer-auth/login'],
    ['POST', '/api/v1/customer-auth/login/verify-otp'],
    ['POST', '/api/v1/customer-auth/logout'],
    ['GET', '/api/v1/customer-auth/me'],
    ['PATCH', '/api/v1/customer-auth/me'],
    ['POST', '/api/v1/public/holds'],
    ['POST', `/api/v1/public/holds/${ID}/verify-payment`],
    ['POST', '/api/v1/public/promo/validate'],
  ])('allows %s %s only in isolated mode', (method, path) => {
    expect(isAllowedIsolatedCustomerRequest(method, path)).toBe(true)
    expect(isAllowedDevApiRequest(method, path)).toBe(false)
  })

  it.each([
    ['POST', '/api/v1/auth/login'], ['GET', '/api/v1/users'],
    ['DELETE', '/api/v1/customer-auth/me'], ['POST', '/api/v1/customer-auth/login/extra'],
    ['POST', '/api/v1/customer-auth/%6cogin'], ['POST', '/api/v1/customer-auth/../auth/login'],
    ['POST', '/api/v1/customer-auth//login'], ['GET', '/api/v1/customer-auth/oauth/google/start'],
    ['POST', '/api/v1/payment-gateway/orders'], ['POST', '/api/v1/public/holds/not-a-uuid/verify-payment'],
  ])('keeps unrelated or disguised routes blocked: %s %s', (method, path) => {
    expect(isAllowedIsolatedCustomerRequest(method, path)).toBe(false)
  })

  it.each([
    'https://api.sriapartment.in', 'https://127.0.0.1:8025', 'http://staging.example.com:8025',
    'http://127.0.0.1.evil.example:8025', 'http://127.0.0.1:8025/api',
    'http://user:secret@127.0.0.1:8025', 'http://127.0.0.1:8025?target=production',
    'http://127.0.0.1:8025#target', 'http://127.0.0.1',
  ])('rejects unsafe isolated targets: %s', (target) => {
    expect(() => assertIsolatedLocalTarget(target)).toThrow()
    expect(() => devApiProxy(target, true)).toThrow()
    expect(() => devApiGuard(target)).toThrow()
  })

  it('uses one fixed loopback target and never follows redirects', () => {
    const options = devApiProxy(LOCAL, true)['/api/']
    expect(options.target).toBe(LOCAL)
    expect(options.followRedirects).toBe(false)
    expect(() => assertIsolatedLocalTarget(LOCAL)).not.toThrow()
  })

  it.each([false, true])('retains customer Authorization only for isolated routing (%s)', (isolated) => {
    const options = devApiProxy(isolated ? LOCAL : 'https://api.sriapartment.in', isolated)['/api/']
    const listeners = new Map<string, (...args: never[]) => void>()
    const proxy = { on: (event: string, handler: (...args: never[]) => void) => listeners.set(event, handler) }
    options.configure?.(proxy as unknown as Parameters<NonNullable<typeof options.configure>>[0], options)
    const removeHeader = vi.fn()
    const handler = listeners.get('proxyReq') as unknown as (request: { removeHeader: typeof removeHeader }) => void
    handler({ removeHeader })
    expect(removeHeader).toHaveBeenCalledWith('cookie')
    if (isolated) expect(removeHeader).not.toHaveBeenCalledWith('authorization')
    else expect(removeHeader).toHaveBeenCalledWith('authorization')
  })

  it('returns the original 403 for remote login, but forwards isolated login to backend checks', () => {
    type Middleware = (req: { method: string; url: string }, res: { statusCode: number; setHeader: ReturnType<typeof vi.fn>; end: ReturnType<typeof vi.fn> }, next: ReturnType<typeof vi.fn>) => void
    for (const isolated of [false, true]) {
      let middleware!: Middleware
      const plugin = devApiGuard(isolated ? LOCAL : undefined)
      const configure = plugin.configureServer as unknown as (server: { middlewares: { use: (fn: Middleware) => void } }) => void
      configure({ middlewares: { use: (fn) => { middleware = fn } } })
      const response = { statusCode: 200, setHeader: vi.fn(), end: vi.fn() }
      const next = vi.fn()
      middleware({ method: 'POST', url: '/api/v1/customer-auth/login' }, response, next)
      if (isolated) {
        expect(next).toHaveBeenCalledOnce()
        expect(response.end).not.toHaveBeenCalled()
      } else {
        expect(response.statusCode).toBe(403)
        expect(next).not.toHaveBeenCalled()
        expect(JSON.parse(response.end.mock.calls[0][0]).detail).toBe('Blocked by local dev proxy: only public read-only endpoints are forwarded.')
      }
    }
  })
})
