import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCustomerAuth } from "../context/CustomerAuthContext";
import { extractErrorMessage, initializeCustomerIdentityProfile, type CustomerIdentityStatusOut } from "../lib/api";

// Customer account landing page with the server-resolved Main Guest
// identity setup. Booking history remains a separate future feature.
export default function CustomerAccountPage() {
  const { customer, customerLogout } = useCustomerAuth();
  const navigate = useNavigate();
  const [identity, setIdentity] = useState<CustomerIdentityStatusOut | null>(null);
  const [identityError, setIdentityError] = useState<string | null>(null);
  const [identityLoading, setIdentityLoading] = useState(false);

  function handleLogout() {
    customerLogout();
    navigate("/account/login");
  }

  async function handleInitializeIdentity() {
    setIdentityLoading(true);
    setIdentityError(null);
    try {
      // The API intentionally receives no guest_id. The authenticated
      // customer identity and verified contacts are resolved server-side.
      setIdentity(await initializeCustomerIdentityProfile());
    } catch (error) {
      setIdentityError(extractErrorMessage(error));
    } finally {
      setIdentityLoading(false);
    }
  }

  const identityTone = identity?.status === "verified"
    ? "bg-emerald-50 text-emerald-800"
    : identity?.status === "rejected"
      ? "bg-red-50 text-red-700"
      : identity?.status === "pending" || identity?.status === "submitted"
        ? "bg-amber-50 text-amber-800"
        : "bg-slate-100 text-slate-700";

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-3xl items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-700 text-xs font-bold text-white">SA</span>
            <span className="font-semibold text-slate-900">Sri Apartment</span>
          </div>
          <button onClick={handleLogout} className="text-sm font-medium text-slate-500 hover:text-emerald-700">
            Log out
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          <h1 className="text-xl font-semibold text-slate-900">{customer ? `Welcome, ${customer.first_name}` : "Welcome back"}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {customer?.email ?? "You're signed in to your Sri Apartment account."}
          </p>

          <div className="mt-6 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            Your customer account is now active. Booking-related features for registered accounts (linking a stay,
            viewing history) will be available in a future update.
          </div>

          <section aria-labelledby="customer-identity-heading" className="mt-6 rounded-xl border border-slate-200 p-5">
            <h2 id="customer-identity-heading" className="text-base font-semibold text-slate-900">
              Identity Verification
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Set up your Main Guest identity profile using a verified email or phone on your account.
            </p>

            {identity ? (
              <div className="mt-4">
                <span className={`inline-flex rounded-full px-3 py-1 text-sm font-medium capitalize ${identityTone}`}>
                  {identity.status.replaceAll("_", " ")}
                </span>
                {identity.message && <p className="mt-2 text-sm text-slate-600">{identity.message}</p>}
              </div>
            ) : (
              <button
                type="button"
                onClick={handleInitializeIdentity}
                disabled={identityLoading}
                className="mt-4 rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:opacity-50"
              >
                {identityLoading ? "Setting up…" : "Set up identity verification"}
              </button>
            )}

            {identityError && (
              <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                {identityError}
              </p>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
