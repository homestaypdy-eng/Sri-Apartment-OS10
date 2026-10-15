import { Helmet } from '../components/Helmet'
import { useBusinessContact } from '../hooks/useBusinessContact'

export function PrivacyPolicyPage() {
  const { address, email } = useBusinessContact()

  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <Helmet
        title="Privacy Policy — Sri Apartment Homestay"
        description="How Sri Apartment Homestay collects, uses, and protects your personal information."
        canonicalPath="/privacy-policy"
      />
      <h1 className="font-serif text-4xl text-brand-green">Privacy Policy</h1>
      <p className="mt-2 text-sm text-brand-muted">Last updated: September 2026</p>

      <div className="mt-8 space-y-8 text-sm leading-relaxed text-brand-ink-2">
        <section>
          <h2 className="font-serif text-xl text-brand-green">Who we are</h2>
          <p className="mt-2">
            Sri Apartment Homestay ({address}) operates this website and the booking system behind it. This
            policy explains what personal information we collect when you create an account, log in, or make a
            booking, and how we use it.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Information we collect</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>Name, email address, and phone number when you create an account or make a booking.</li>
            <li>Your password, stored only as a one-way cryptographic hash — we never store or can see your actual password.</li>
            <li>
              If you sign in with Google, the name, email address, and profile information Google shares with us for
              that purpose. We never see or store your Google password.
            </li>
            <li>Booking details: stay dates, guest count, the accommodation booked, and special requests you provide.</li>
            <li>
              Messages you send us directly, for example via WhatsApp or email, when you contact us with a question or
              request.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Information we do not collect or store</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>
              Your card, UPI, or bank details. Payments are processed directly by Razorpay, our payment gateway
              partner — we never receive or store your full payment credentials.
            </li>
            <li>We do not use advertising cookies, ad trackers, or sell your information to third parties.</li>
          </ul>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">How we use your information</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>To create and secure your account, including sending one-time verification codes at login.</li>
            <li>To process and confirm your bookings, and to send booking confirmations and invoices by email.</li>
            <li>To respond to questions or requests you send us, including over WhatsApp.</li>
            <li>To meet our legal and tax obligations, including GST-compliant invoicing.</li>
          </ul>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Who we share information with</h2>
          <p className="mt-2">
            We share only what's necessary, only with the following service providers, and only for the purpose of
            operating this booking system:
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>Razorpay, to process payments.</li>
            <li>Google, if you choose to sign in with your Google account.</li>
            <li>Our email delivery provider, to send account, verification, and booking emails.</li>
            <li>Our property and booking management system, to manage availability and reservations.</li>
          </ul>
          <p className="mt-2">We do not sell your personal information to anyone.</p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">How we store your session</h2>
          <p className="mt-2">
            After you log in, a session token is stored in your browser's local storage to keep you signed in. It is
            removed when you log out. We do not use tracking cookies.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-xl text-brand-green">Your rights</h2>
          <p className="mt-2">
            You can review and update your name and phone number at any time from your Account page. To request a
            copy of your data, ask us to correct or delete it, or ask any other question about this policy, contact
            us using the details below.
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
