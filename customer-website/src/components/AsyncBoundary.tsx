import type { AsyncState } from '../hooks/useAsync'

/**
 * Renders the loading / error(+retry) / success states for an AsyncState.
 * Centralizing this means every page's "no availability", "network
 * failure", "retry" behavior looks and works the same way.
 */
export function AsyncBoundary<T>({
  state,
  retry,
  children,
  loadingLabel = 'Loading…',
}: {
  state: AsyncState<T>
  retry: () => void
  children: (data: T) => React.ReactNode
  loadingLabel?: string
}) {
  if (state.status === 'loading') {
    return (
      <div role="status" aria-live="polite" className="flex flex-col items-center gap-3 py-16 text-brand-muted">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-brand-gold border-t-transparent" aria-hidden="true" />
        <span>{loadingLabel}</span>
      </div>
    )
  }

  if (state.status === 'error') {
    const message = state.error.isNetworkError
      ? state.error.message
      : state.error.message || 'Something went wrong. Please try again.'
    return (
      <div role="alert" className="flex flex-col items-center gap-4 rounded-lg border border-red-200 bg-red-50 px-6 py-10 text-center">
        <p className="text-brand-ink-2">{message}</p>
        <button
          type="button"
          onClick={retry}
          className="rounded bg-brand-green px-5 py-2 text-sm font-semibold tracking-wide text-brand-gold uppercase hover:bg-brand-green-2"
        >
          Try again
        </button>
      </div>
    )
  }

  return <>{children(state.data)}</>
}
