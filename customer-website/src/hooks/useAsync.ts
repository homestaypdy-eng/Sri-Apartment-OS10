import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../lib/api'

export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: ApiError }
  | { status: 'success'; data: T }

/**
 * Wraps an OS10 API call with loading/error/retry state so every page
 * handles "network failure" and "retry" the same way (Step 5 requirement)
 * instead of each component reinventing it.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: React.DependencyList): AsyncState<T> & { retry: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' })
  const fnRef = useRef(fn)
  fnRef.current = fn

  const run = useCallback(() => {
    let cancelled = false
    setState({ status: 'loading' })
    fnRef
      .current()
      .then((data) => {
        if (!cancelled) setState({ status: 'success', data })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const apiErr = err instanceof ApiError ? err : new ApiError('Something went wrong. Please try again.', 0, true)
        setState({ status: 'error', error: apiErr })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  const [retryTick, setRetryTick] = useState(0)
  useEffect(() => run(), [run, retryTick])

  return { ...state, retry: () => setRetryTick((t) => t + 1) }
}
