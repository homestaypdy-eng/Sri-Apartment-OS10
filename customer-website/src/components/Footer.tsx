import { Link } from 'react-router-dom'
import { useBusinessContact } from '../hooks/useBusinessContact'

export function Footer() {
  const { address, email, whatsappNumber } = useBusinessContact()
  return (
    <footer className="bg-brand-green-3 py-14 text-white/70">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-10 px-4 sm:grid-cols-2 sm:px-6 md:grid-cols-4">
        <div>
          <p className="font-serif text-xl text-white">Sri Apartment</p>
          <p className="text-[0.6rem] tracking-[0.25em] text-brand-gold uppercase">Homestay Pondicherry</p>
          <p className="mt-3 max-w-xs text-sm text-white/50">
            Comfortable accommodations in Moolakulam, Puducherry. Book direct for instant confirmation and no OTA fees.
          </p>
        </div>

        <div>
          <p className="mb-3 text-[0.6rem] font-bold tracking-[0.2em] text-brand-gold uppercase">Quick Links</p>
          <ul className="space-y-2 text-sm">
            <li><Link to="/apartments">Accommodations</Link></li>
            <li><Link to="/amenities">Amenities</Link></li>
            <li><Link to="/gallery">Gallery</Link></li>
            <li><Link to="/faq">FAQ</Link></li>
            <li><Link to="/location">Location</Link></li>
          </ul>
        </div>

        <div>
          <p className="mb-3 text-[0.6rem] font-bold tracking-[0.2em] text-brand-gold uppercase">Account</p>
          <ul className="space-y-2 text-sm">
            <li><Link to="/book">Book Now</Link></li>
            <li><Link to="/login">Log In</Link></li>
            <li><Link to="/signup">Sign Up</Link></li>
            <li><Link to="/contact">Contact</Link></li>
          </ul>
        </div>

        <div>
          <p className="mb-3 text-[0.6rem] font-bold tracking-[0.2em] text-brand-gold uppercase">Contact</p>
          <ul className="space-y-2 text-sm">
            {whatsappNumber && (
              <li>
                <a href={`https://wa.me/${whatsappNumber}`} target="_blank" rel="noreferrer">
                  WhatsApp us
                </a>
              </li>
            )}
            {email && <li><a href={`mailto:${email}`}>{email}</a></li>}
            <li>
              <a
                href="https://www.google.com/maps/place//data=!4m2!3m1!1s0x3a53615790e2d601:0x9b1df761cd2112ca"
                target="_blank"
                rel="noreferrer"
              >
                {address}
              </a>
            </li>
          </ul>
        </div>
      </div>

      {/* text-white/55, not /30: Lighthouse measured the original at 2.65:1
          against this footer's dark green background, well under WCAG AA's
          4.5:1 — /55 clears it at 5.57:1 while staying visually the most
          de-emphasized line in the footer. */}
      <p className="mt-10 text-center text-xs text-white/55">
        © {new Date().getFullYear()} Sri Apartment Homestay, Puducherry. <Link to="/privacy-policy">Privacy Policy</Link> · <Link to="/terms">Terms &amp; Conditions</Link>
      </p>
    </footer>
  )
}
