/** Loads Razorpay's Checkout.js on demand (payment step only) rather than
 * on every page load. Returns the global `Razorpay` constructor. */

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayOptions) => RazorpayInstance
  }
}

export interface RazorpayOptions {
  key: string
  amount: number
  currency: string
  name: string
  description?: string
  order_id: string
  prefill?: { name?: string; contact?: string; email?: string }
  theme?: { color?: string }
  modal?: { ondismiss?: () => void }
  handler: (response: { razorpay_payment_id: string; razorpay_order_id: string; razorpay_signature: string }) => void
}

export interface RazorpayInstance {
  open: () => void
  on: (event: 'payment.failed', handler: (response: { error?: { description?: string } }) => void) => void
}

let loadPromise: Promise<void> | null = null

const LOAD_TIMEOUT_MS = 10_000

export function loadRazorpayScript(): Promise<void> {
  if (window.Razorpay) return Promise.resolve()
  if (loadPromise) return loadPromise

  loadPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://checkout.razorpay.com/v1/checkout.js'
    script.async = true

    let settled = false
    // A blocked/hanging connection may never fire onerror at all — without
    // this, the "Waiting for payment…" button could spin forever instead
    // of surfacing a visible, actionable error (Step 11's core rule).
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      reject(new Error('The payment provider is taking too long to load. Please check your connection and try again.'))
    }, LOAD_TIMEOUT_MS)

    script.onload = () => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve()
    }
    script.onerror = () => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      script.remove()
      reject(new Error('Could not load the payment provider. Please check your connection and try again.'))
    }

    document.body.appendChild(script)
  }).catch((err: unknown) => {
    // Don't cache a failed attempt — a retry should actually retry, not
    // instantly replay the same rejection.
    loadPromise = null
    throw err
  })
  return loadPromise
}
