/**
 * Local development routing: remote public reads, or an explicit isolated loopback sandbox.
 *
 * Remote mode: production CORS does not allow http://localhost:5175, so in `vite serve`
 * the browser calls relative /api/... and Vite forwards it. Only verified
 * public, read-only endpoints are forwarded; everything else under /api is
 * answered locally with a 403 before it reaches the proxy, so the dev site
 * can never create holds, verify payments or touch customer accounts on
 * production. Credentials are stripped in both directions. Isolated mode
 * instead forwards only customer-booking routes to a validated loopback target
 * and preserves the customer Authorization header for backend verification.
 */
import type { Plugin, ProxyOptions } from 'vite'

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

/** Public read-only OS10 endpoints (backend app/modules/public_booking/router.py
 * and customer_auth's oauth status), each verified as unauthenticated GET. */
const ALLOWED_PATHS: RegExp[] = [
  /^\/api\/v1\/public\/units$/,
  new RegExp(`^/api/v1/public/units/${UUID}/photos$`),
  new RegExp(`^/api/v1/public/units/${UUID}/photos/${UUID}/image$`),
  /^\/api\/v1\/public\/business-info$/,
  /^\/api\/v1\/public\/availability$/,
  /^\/api\/v1\/public\/quote$/,
  /^\/api\/v1\/customer-auth\/oauth\/status$/,
]
const ALLOWED_METHODS = new Set(['GET', 'HEAD'])
const STRIPPED_REQUEST_HEADERS = ['cookie', 'authorization', 'origin', 'referer']

// Explicitly limited to the customer booking journey; never admin/staff routes.
const LOCAL_CUSTOMER_ROUTES: [string, RegExp][] = [
  ['GET', /^\/api\/v1\/public\/payment-availability$/],
  ['POST', /^\/api\/v1\/customer-auth\/register$/],
  ['POST', /^\/api\/v1\/customer-auth\/login$/],
  ['POST', /^\/api\/v1\/customer-auth\/login\/verify-otp$/],
  ['POST', /^\/api\/v1\/customer-auth\/logout$/],
  ['GET', /^\/api\/v1\/customer-auth\/me$/],
  ['PATCH', /^\/api\/v1\/customer-auth\/me$/],
  ['POST', /^\/api\/v1\/public\/promo\/validate$/],
  ['POST', /^\/api\/v1\/public\/holds$/],
  ['POST', new RegExp(`^/api/v1/public/holds/${UUID}/verify-payment$`)],
]

export function assertIsolatedLocalTarget(target: string): void {
  const url = new URL(target)
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Isolated authentication proxy requires http://127.0.0.1:<port> with no path, credentials, query or fragment.')
  }
}

export function isAllowedIsolatedCustomerRequest(method: string | undefined, rawUrl: string | undefined): boolean {
  if (isAllowedDevApiRequest(method, rawUrl)) return true
  if (!method || !rawUrl) return false
  const path = rawUrl.split('?')[0]
  if (/%|\.\.|\/\/|\\/.test(path)) return false
  return LOCAL_CUSTOMER_ROUTES.some(([allowedMethod, pattern]) => method.toUpperCase() === allowedMethod && pattern.test(path))
}

export function isAllowedDevApiRequest(method: string | undefined, rawUrl: string | undefined): boolean {
  if (!method || !rawUrl || !ALLOWED_METHODS.has(method.toUpperCase())) return false
  const queryStart = rawUrl.indexOf('?')
  const path = queryStart === -1 ? rawUrl : rawUrl.slice(0, queryStart)
  // No encoded chars, dot-segments, double slashes or backslashes, so the
  // matched path is exactly what gets forwarded.
  if (/%|\.\.|\/\/|\\/.test(path)) return false
  return ALLOWED_PATHS.some((re) => re.test(path))
}

/** Registered directly in configureServer, so it runs before Vite's proxy
 * middleware — a blocked request is never forwarded. */
export function devApiGuard(isolatedTarget?: string): Plugin {
  if (isolatedTarget) assertIsolatedLocalTarget(isolatedTarget)
  return {
    name: 'dev-api-guard',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api')) return next()
        const allowed = isolatedTarget ? isAllowedIsolatedCustomerRequest(req.method, req.url) : isAllowedDevApiRequest(req.method, req.url)
        if (allowed) return next()
        res.statusCode = 403
        res.setHeader('Content-Type', 'application/json')
        res.end(JSON.stringify({ detail: isolatedTarget ? 'Blocked by isolated local dev proxy: this customer endpoint is not allowed.' : 'Blocked by local dev proxy: only public read-only endpoints are forwarded.' }))
      })
    },
  }
}

export function devApiProxy(target: string, isolated = false): Record<string, ProxyOptions> {
  if (isolated) assertIsolatedLocalTarget(target)
  return {
    '/api/': {
      target,
      changeOrigin: true,
      secure: true,
      followRedirects: false,
      configure(proxy) {
        proxy.on('proxyReq', (proxyReq) => {
          for (const header of isolated ? ['cookie'] : STRIPPED_REQUEST_HEADERS) proxyReq.removeHeader(header)
        })
        proxy.on('proxyRes', (proxyRes, req) => {
          if (isolated) console.info(`[isolated local API] ${req.method} ${req.url?.split('?')[0]} → ${proxyRes.statusCode}`)
          delete proxyRes.headers['set-cookie']
        })
      },
    },
  }
}
