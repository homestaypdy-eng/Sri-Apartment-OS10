import { Outlet } from 'react-router-dom'
import { Nav } from './Nav'
import { Footer } from './Footer'
import { ChatWidget } from './ChatWidget'

// WhatsAppFloat is intentionally not mounted here — Ask Sri Apartment is the
// single primary floating action (Phase 4A). WhatsApp itself is unaffected:
// it's still reachable via the concierge's "Talk to Us on WhatsApp" action
// and via Footer / ContactPage / HomePage's FinalCta, all untouched.
export function Layout() {
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-brand-gold focus:px-4 focus:py-2">
        Skip to content
      </a>
      <Nav />
      <main id="main-content" className="flex-1">
        <Outlet />
      </main>
      <Footer />
      <ChatWidget />
    </div>
  )
}
