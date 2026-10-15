import { Link } from 'react-router-dom'
import { Helmet } from '../components/Helmet'

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-md px-4 py-24 text-center">
      <Helmet
        title="Page Not Found — Sri Apartment Homestay"
        description="The page you're looking for doesn't exist."
        noindex
      />
      <h1 className="font-serif text-3xl text-brand-green">Page not found</h1>
      <Link to="/" className="mt-6 inline-block text-sm text-brand-gold-4 underline">
        Back to home
      </Link>
    </div>
  )
}
