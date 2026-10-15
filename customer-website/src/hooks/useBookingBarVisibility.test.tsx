import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ChatWidget } from '../components/ChatWidget'
import { useReportBookingBarVisibility } from './useBookingBarVisibility'

/** Minimal IntersectionObserver double: tests drive visibility by hand. */
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = []
  observed: Element[] = []
  disconnected = false
  callback: IntersectionObserverCallback
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeIntersectionObserver.instances.push(this)
  }
  observe(el: Element) {
    this.observed.push(el)
  }
  disconnect() {
    this.disconnected = true
  }
  unobserve() {}
  takeRecords() {
    return []
  }
  fire(isIntersecting: boolean) {
    act(() => this.callback([{ isIntersecting, target: this.observed[0] } as IntersectionObserverEntry], this as unknown as IntersectionObserver))
  }
}

function BookingBar() {
  const ref = useRef<HTMLFormElement>(null)
  useReportBookingBarVisibility(ref)
  return <form ref={ref} aria-label="booking bar" />
}

function renderPage({ withBar }: { withBar: boolean }) {
  return render(
    <MemoryRouter>
      {withBar && <BookingBar />}
      <ChatWidget />
    </MemoryRouter>,
  )
}

const launcher = () => screen.getByRole('button', { name: /Ask Sri Apartment chat/i, hidden: true })
const io = () => FakeIntersectionObserver.instances.at(-1)!

beforeEach(() => {
  FakeIntersectionObserver.instances = []
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({}) }))
  // jsdom has no Element.scrollTo; the open chat panel calls it.
  if (!Element.prototype.scrollTo) Element.prototype.scrollTo = vi.fn()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('chat launcher vs. homepage booking bar', () => {
  it('hides the launcher on mobile only while the booking bar is visible', () => {
    renderPage({ withBar: true })
    expect(io().observed).toHaveLength(1)
    expect(launcher().className).not.toMatch(/max-sm:invisible/)

    io().fire(true)
    expect(launcher().className).toMatch(/max-sm:invisible/)
    expect(launcher().className).toMatch(/max-sm:opacity-0/)
    // Mobile-only: no unprefixed hiding class, so desktop is unaffected.
    expect(launcher().className.split(/\s+/)).not.toContain('invisible')

    io().fire(false)
    expect(launcher().className).not.toMatch(/max-sm:invisible/)
  })

  it('never hides the launcher while the chat panel is open', () => {
    renderPage({ withBar: true })
    fireEvent.click(launcher())
    expect(launcher()).toHaveAttribute('aria-expanded', 'true')

    io().fire(true)
    expect(launcher().className).not.toMatch(/max-sm:invisible/)
  })

  it('disconnects and shows the launcher again when the booking bar unmounts', () => {
    const { rerender } = renderPage({ withBar: true })
    const observer = io()
    observer.fire(true)
    expect(launcher().className).toMatch(/max-sm:invisible/)

    rerender(
      <MemoryRouter>
        <ChatWidget />
      </MemoryRouter>,
    )
    expect(observer.disconnected).toBe(true)
    expect(launcher().className).not.toMatch(/max-sm:invisible/)
  })

  it('leaves the launcher alone on pages without a booking bar', () => {
    renderPage({ withBar: false })
    expect(FakeIntersectionObserver.instances).toHaveLength(0)
    expect(launcher().className).not.toMatch(/max-sm:invisible/)
  })
})
