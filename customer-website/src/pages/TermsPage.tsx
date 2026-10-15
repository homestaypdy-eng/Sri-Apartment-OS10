import { Helmet } from '../components/Helmet'
import { useBusinessContact } from '../hooks/useBusinessContact'
import { POST_CHECKIN_CANCELLATION_POLICY } from '../content/faq'

export function TermsPage() {
  const { address, email } = useBusinessContact()

  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <Helmet
        title="Terms & Conditions — Sri Apartment Homestay"
        description="Booking, payment, and cancellation terms for Sri Apartment Homestay."
        canonicalPath="/terms"
      />
      <h1 className="font-serif text-4xl text-brand-green">Terms & Conditions</h1>
      <p className="mt-2 text-sm text-brand-muted">Last updated: September 2026</p>

      <div className="mt-8 space-y-8 text-sm leading-relaxed text-brand-ink-2">
        <section>
          <h2 className="font-serif text-xl text-brand-green">About these terms</h2>
          <p className="mt-2">
            These terms apply when you create an account or make a booking with Sri Apartment Homestay ({address})
            through this website. By creating an account or completing a booking, you agree to them.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Your account</h2>
          <p className="mt-2">
            You're responsible for keeping your password and account access secure. Provide accurate name, email,
            and phone details — we use them to confirm your identity and your bookings.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Booking and payment</h2>
          <p className="mt-2">
            Full payment (room rate plus GST) is collected online via Razorpay at the time of booking. No balance is
            due at check-in, and partial payments are not accepted. Check-in is from 12:30 PM and check-out is by
            11:00 AM.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Cancellations and refunds</h2>
          <p className="mt-2">
            Cancel more than 48 hours before check-in for a 100% refund of the booking amount. Cancellations within
            48 hours of check-in are eligible for a 50% refund; the remaining 50% is retained as a late-cancellation
            charge. Approved refunds will be initiated promptly to the original payment method. The time taken for
            the refunded amount to appear in your account may vary depending on the payment provider, bank, card
            issuer, or UPI provider. {POST_CHECKIN_CANCELLATION_POLICY}
          </p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Changes to these terms</h2>
          <p className="mt-2">
            We may update these terms from time to time to reflect changes to how we operate. The "last updated"
            date above will always reflect the current version.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Contact us</h2>
          <p className="mt-2">
            {address}
            {email && (
              <>
                {' · '}
                <a href={`mailto:${email}`} className="font-semibold text-brand-green underline">
                  {email}
                </a>
              </>
            )}
          </p>
        </section>
      </div>
    </div>
  )
}
