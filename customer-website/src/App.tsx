import { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'

const HomePage = lazy(() => import('./pages/HomePage').then((m) => ({ default: m.HomePage })))
const ApartmentsPage = lazy(() => import('./pages/ApartmentsPage').then((m) => ({ default: m.ApartmentsPage })))
const ApartmentDetailPage = lazy(() => import('./pages/ApartmentDetailPage').then((m) => ({ default: m.ApartmentDetailPage })))
const GalleryPage = lazy(() => import('./pages/GalleryPage').then((m) => ({ default: m.GalleryPage })))
const AmenitiesPage = lazy(() => import('./pages/AmenitiesPage').then((m) => ({ default: m.AmenitiesPage })))
const FaqPage = lazy(() => import('./pages/FaqPage').then((m) => ({ default: m.FaqPage })))
const LocationPage = lazy(() => import('./pages/LocationPage').then((m) => ({ default: m.LocationPage })))
const ContactPage = lazy(() => import('./pages/ContactPage').then((m) => ({ default: m.ContactPage })))
const BookPage = lazy(() => import('./pages/BookPage').then((m) => ({ default: m.BookPage })))
const BookingConfirmationPage = lazy(() => import('./pages/BookingConfirmationPage').then((m) => ({ default: m.BookingConfirmationPage })))
const LoginPage = lazy(() => import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })))
const SignUpPage = lazy(() => import('./pages/SignUpPage').then((m) => ({ default: m.SignUpPage })))
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage').then((m) => ({ default: m.ForgotPasswordPage })))
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage').then((m) => ({ default: m.ResetPasswordPage })))
const AccountPage = lazy(() => import('./pages/AccountPage').then((m) => ({ default: m.AccountPage })))
const EditProfilePage = lazy(() => import('./pages/EditProfilePage').then((m) => ({ default: m.EditProfilePage })))
const ChangePasswordPage = lazy(() => import('./pages/ChangePasswordPage').then((m) => ({ default: m.ChangePasswordPage })))
const OAuthCallbackPage = lazy(() => import('./pages/OAuthCallbackPage').then((m) => ({ default: m.OAuthCallbackPage })))
const PrivacyPolicyPage = lazy(() => import('./pages/PrivacyPolicyPage').then((m) => ({ default: m.PrivacyPolicyPage })))
const TermsPage = lazy(() => import('./pages/TermsPage').then((m) => ({ default: m.TermsPage })))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })))

function RouteFallback() {
  return (
    <div role="status" aria-live="polite" className="flex justify-center py-24">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-brand-gold border-t-transparent" aria-hidden="true" />
    </div>
  )
}

export function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<HomePage />} />
          <Route path="apartments" element={<ApartmentsPage />} />
          <Route path="apartments/:id" element={<ApartmentDetailPage />} />
          <Route path="gallery" element={<GalleryPage />} />
          <Route path="amenities" element={<AmenitiesPage />} />
          <Route path="faq" element={<FaqPage />} />
          <Route path="location" element={<LocationPage />} />
          <Route path="contact" element={<ContactPage />} />
          <Route path="book" element={<BookPage />} />
          <Route path="booking/confirmation" element={<BookingConfirmationPage />} />
          <Route path="login" element={<LoginPage />} />
          <Route path="signup" element={<SignUpPage />} />
          <Route path="forgot-password" element={<ForgotPasswordPage />} />
          <Route path="reset-password" element={<ResetPasswordPage />} />
          <Route path="account" element={<AccountPage />} />
          <Route path="account/edit" element={<EditProfilePage />} />
          <Route path="account/change-password" element={<ChangePasswordPage />} />
          <Route path="oauth/callback" element={<OAuthCallbackPage />} />
          <Route path="privacy-policy" element={<PrivacyPolicyPage />} />
          <Route path="terms" element={<TermsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
