import { Helmet } from '../components/Helmet'
import { useBusinessContact } from '../hooks/useBusinessContact'

/**
 * No submission form here: OS10's only inquiry-creation endpoint
 * (POST /api/v1/inquiries) is staff/admin-authenticated, not public-safe.
 * Wiring a public form to it would mean either exposing an admin
 * credential client-side or silently failing — neither acceptable.
 * A public-safe wrapper is a real candidate for a future phase (see the
 * final report's "NEW PUBLIC API WRAPPERS (proposed, not applied)"
 * section); for now this page offers the direct channels that don't
 * require any backend change.
 */
export function ContactPage() {
  const { whatsappNumber, email } = useBusinessContact()
  return (
    <div className="mx-auto max-w-2xl px-4 py-16 text-center sm:px-6">
      <Helmet
        title="Contact — Sri Apartment Homestay Pondicherry"
        description="Reach Sri Apartment Homestay by WhatsApp, phone, or email."
        canonicalPath="/contact"
      />
      <h1 className="font-serif text-4xl text-brand-green">Contact Us</h1>
      <p className="mt-2 text-brand-muted">We usually reply within minutes on WhatsApp.</p>

      <div className="mt-10 flex flex-col items-center gap-4">
        {whatsappNumber && (
          // #075E54 not #25D366 — WCAG AA contrast fix, see WhatsAppFloat.tsx
          <a href={`https://wa.me/${whatsappNumber}`} target="_blank" rel="noreferrer" className="w-full max-w-xs rounded bg-[#075E54] py-3 font-bold text-white uppercase">
            💬 WhatsApp
          </a>
        )}
        {whatsappNumber && (
          <a href={`tel:+${whatsappNumber}`} className="w-full max-w-xs rounded border border-brand-border py-3 font-semibold text-brand-green uppercase">
            📞 Call
          </a>
        )}
        {email && (
          <a href={`mailto:${email}`} className="w-full max-w-xs rounded border border-brand-border py-3 font-semibold text-brand-green uppercase">
            ✉️ Email
          </a>
        )}
      </div>
    </div>
  )
}
