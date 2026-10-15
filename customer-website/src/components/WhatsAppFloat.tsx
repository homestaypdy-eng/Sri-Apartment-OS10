import { useBusinessContact } from '../hooks/useBusinessContact'

export function WhatsAppFloat() {
  const { whatsappNumber } = useBusinessContact()
  if (!whatsappNumber) return null
  return (
    <a
      href={`https://wa.me/${whatsappNumber}`}
      target="_blank"
      rel="noreferrer"
      aria-label="Chat with us on WhatsApp"
      // WCAG AA contrast fix: white text on WhatsApp's lighter action-green
      // (#25D366) measured 1.98:1, far under the 4.5:1 minimum. Swapped to
      // WhatsApp's own official darker brand teal (#075E54, their header/
      // logo color) — still unmistakably WhatsApp branding, now 7.67:1.
      className="fixed right-6 bottom-6 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-[#075E54] text-2xl text-white shadow-lg transition hover:scale-110"
    >
      <span aria-hidden="true">💬</span>
    </a>
  )
}
