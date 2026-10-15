import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { ApiError, customerGetMe, customerLogout as apiCustomerLogout } from '../lib/api'
import type { CustomerAccountOut } from '../lib/types'

const CUSTOMER_TOKEN_KEY = 'customer_access_token'

interface CustomerAuthContextValue {
  isCustomerAuthenticated: boolean
  isInitializing: boolean
  customer: CustomerAccountOut | null
  token: string | null
  customerLogin: (token: string, customer: CustomerAccountOut) => void
  customerLogout: () => Promise<void>
  /** Pushes a freshly-updated profile (e.g. the response of a profile
   * edit) into context immediately, without a second round-trip to /me. */
  updateCustomer: (customer: CustomerAccountOut) => void
}

const CustomerAuthContext = createContext<CustomerAuthContextValue | undefined>(undefined)

export function CustomerAuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isInitializing, setIsInitializing] = useState(true)
  const [customer, setCustomer] = useState<CustomerAccountOut | null>(null)
  const [token, setToken] = useState<string | null>(null)

  // Restores the authenticated customer's full profile from the existing
  // session token on every fresh page load — GET /customer-auth/me derives
  // identity entirely from the token, so this can only ever return the
  // caller's own data. A stored token that turns out to be invalid/expired
  // (401) is treated as logged out; any other failure (e.g. a momentary
  // network hiccup) still honors the stored token as authenticated so a
  // flaky connection doesn't spuriously sign the customer out — the
  // personalized name just isn't available until the next successful call.
  useEffect(() => {
    // Local dev against the read-only production proxy (vite/devApiProxy.ts):
    // customer-auth calls are blocked there, so don't restore a stored
    // session — skip /me and show signed-out. The stored token is left as-is.
    if (import.meta.env.DEV && import.meta.env.VITE_DEV_READ_ONLY_API === 'true') {
      setIsInitializing(false)
      return
    }
    const stored = localStorage.getItem(CUSTOMER_TOKEN_KEY)
    if (!stored) {
      setIsInitializing(false)
      return
    }
    setToken(stored)
    setIsAuthenticated(true)
    customerGetMe(stored)
      .then((profile) => setCustomer(profile))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          localStorage.removeItem(CUSTOMER_TOKEN_KEY)
          setToken(null)
          setIsAuthenticated(false)
        }
      })
      .finally(() => setIsInitializing(false))
  }, [])

  function customerLogin(newToken: string, loggedInCustomer: CustomerAccountOut) {
    localStorage.setItem(CUSTOMER_TOKEN_KEY, newToken)
    setToken(newToken)
    setCustomer(loggedInCustomer)
    setIsAuthenticated(true)
  }

  function updateCustomer(updated: CustomerAccountOut) {
    setCustomer(updated)
  }

  async function customerLogout() {
    // The API call MUST be awaited (and use the token still in state)
    // before clearing localStorage, or the Authorization header would
    // already be gone and the server-side revocation would silently 401.
    // Still best-effort overall — a network failure here must never block
    // the user from being logged out locally, hence the try/finally.
    try {
      if (token) await apiCustomerLogout(token)
    } catch {
      // Best-effort — local logout proceeds regardless (see finally).
    } finally {
      localStorage.removeItem(CUSTOMER_TOKEN_KEY)
      setToken(null)
      setCustomer(null)
      setIsAuthenticated(false)
    }
  }

  return (
    <CustomerAuthContext.Provider
      value={{ isCustomerAuthenticated: isAuthenticated, isInitializing, customer, token, customerLogin, customerLogout, updateCustomer }}
    >
      {children}
    </CustomerAuthContext.Provider>
  )
}

export function useCustomerAuth() {
  const ctx = useContext(CustomerAuthContext)
  if (!ctx) throw new Error('useCustomerAuth must be used within CustomerAuthProvider')
  return ctx
}
