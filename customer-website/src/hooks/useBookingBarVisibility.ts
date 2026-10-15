import { useEffect, useSyncExternalStore, type RefObject } from 'react'

/**
 * Shares one fact between the homepage booking bar and the global chat
 * launcher (ChatWidget, mounted in Layout): "is the booking bar currently
 * on screen?". The launcher uses it to step aside on mobile so it never
 * covers the bar's fields or submit button.
 */
let bookingBarVisible = false
const listeners = new Set<() => void>()

function setBookingBarVisible(next: boolean) {
  if (next === bookingBarVisible) return
  bookingBarVisible = next
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useBookingBarVisible(): boolean {
  return useSyncExternalStore(subscribe, () => bookingBarVisible, () => false)
}

/** Reports whether `ref`'s element is (even partly) in the viewport. Resets
 * to "not visible" on unmount, so leaving the homepage can never leave the
 * launcher hidden on other pages. */
export function useReportBookingBarVisibility(ref: RefObject<Element | null>) {
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(([entry]) => setBookingBarVisible(entry.isIntersecting))
    observer.observe(el)
    return () => {
      observer.disconnect()
      setBookingBarVisible(false)
    }
  }, [ref])
}
