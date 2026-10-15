import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { OAuthButtons } from './OAuthButtons'

describe('OAuthButtons', () => {
  let hrefSetter: (value: string) => void

  beforeEach(() => {
    // jsdom's real Location object refuses to have `href` redefined
    // directly (it would actually try to navigate). Replace the whole
    // `window.location` binding instead — Object.defineProperty (rather
    // than a plain assignment) sidesteps TS's strict Location typing for
    // this test-only stand-in.
    hrefSetter = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { get href() { return '' }, set href(v: string) { hrefSetter(v) } },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('falls back to the honest onUnavailable notice when Google is not configured', async () => {
    const user = userEvent.setup()
    const onUnavailable = vi.fn()
    render(<OAuthButtons configured={false} onUnavailable={onUnavailable} />)

    await user.click(screen.getByRole('button', { name: /continue with google/i }))

    expect(onUnavailable).toHaveBeenCalled()
    expect(hrefSetter).not.toHaveBeenCalled() // no navigation attempted
  })

  it('treats a missing configured prop the same as not configured', async () => {
    const user = userEvent.setup()
    const onUnavailable = vi.fn()
    render(<OAuthButtons onUnavailable={onUnavailable} />)

    await user.click(screen.getByRole('button', { name: /continue with google/i }))

    expect(onUnavailable).toHaveBeenCalled()
  })

  it('does a real full-page navigation to the backend Google start endpoint when configured, never calling onUnavailable', async () => {
    const user = userEvent.setup()
    const onUnavailable = vi.fn()
    render(<OAuthButtons configured={true} onUnavailable={onUnavailable} />)

    await user.click(screen.getByRole('button', { name: /continue with google/i }))

    expect(onUnavailable).not.toHaveBeenCalled()
    expect(hrefSetter).toHaveBeenCalledWith(expect.stringContaining('/api/v1/customer-auth/oauth/google/start'))
  })

  it('renders exactly one button, with no Facebook option anywhere', () => {
    render(<OAuthButtons configured={true} onUnavailable={vi.fn()} />)
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.queryByText(/facebook/i)).not.toBeInTheDocument()
  })
})
