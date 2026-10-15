import { Helmet } from '../components/Helmet'
import { FAQS } from '../content/faq'
import { useBusinessContact } from '../hooks/useBusinessContact'

export function FaqPage() {
  const { whatsappNumber } = useBusinessContact()
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <Helmet
        title="FAQ — Sri Apartment Homestay Pondicherry"
        description="Answers about check-in times, WiFi, kitchen use, parking, payment, GST, and our cancellation policy."
        canonicalPath="/faq"
      />
      <h1 className="text-center font-serif text-4xl text-brand-green">Frequently Asked Questions</h1>
      <dl className="mt-10 space-y-6">
        {FAQS.map((item) => (
          <div key={item.q} className="border-b border-brand-border pb-6">
            <dt className="font-serif text-lg text-brand-green">{item.q}</dt>
            <dd className="mt-2 text-sm leading-relaxed text-brand-ink-2">{item.a}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-10 rounded-lg border border-brand-border bg-brand-cream-2 p-6 text-center">
        <p className="text-sm text-brand-ink-2">
          Have a question about children, ID requirements, smoking, or events that isn’t covered above? We’d rather answer it directly than guess.
        </p>
        {whatsappNumber && (
          // #075E54 not #25D366 — WCAG AA contrast fix, see WhatsAppFloat.tsx
          <a href={`https://wa.me/${whatsappNumber}`} target="_blank" rel="noreferrer" className="mt-3 inline-block rounded bg-[#075E54] px-6 py-2 text-sm font-bold text-white uppercase">
            Ask on WhatsApp
          </a>
        )}
      </div>
    </div>
  )
}
