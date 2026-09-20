import { useNavigate } from "react-router-dom";
import { useCustomerAuth } from "../context/CustomerAuthContext";

// Minimal landing page after a successful customer login/signup — just
// enough to prove the login/logout loop end-to-end. A full account/
// booking-history experience for CustomerAccount is separate, later work
// (see the K item in the Phase 3 architecture report: "How a newly
// registered account can later become associated with a Guest record").
export default function CustomerAccountPage() {
  const { customer, customerLogout } = useCustomerAuth();
  const navigate = useNavigate();

  function handleLogout() {
    customerLogout();
    navigate("/account/login");
  }

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
        </div>
      </main>
    </div>
  );
}
