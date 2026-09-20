import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthShell } from "../components/AuthShell";
import { customerRegister, extractErrorMessage } from "../lib/api";

export default function CustomerSignUpPage() {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (!termsAccepted) {
      setError("You must accept the Terms & Conditions to create an account.");
      return;
    }
    if (!privacyAccepted) {
      setError("You must accept the Privacy Policy to create an account.");
      return;
    }

    setLoading(true);
    try {
      await customerRegister({
        first_name: firstName,
        last_name: lastName,
        email,
        password,
        terms_accepted: termsAccepted,
        privacy_accepted: privacyAccepted,
      });
      // Phase 3B's deliberate design: signup does not log the customer in
      // automatically — send them to Log In with their new credentials.
      setSuccess(true);
      setTimeout(() => navigate("/account/login"), 1800);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  if (success) {
    return (
      <AuthShell mode="signup" title="Account created" subtitle="Redirecting you to Log In…">
        <p role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          Your Sri Apartment account has been created. Please log in with your new password.
        </p>
        <Link to="/account/login" className="mt-4 inline-block text-sm font-medium text-emerald-700 hover:underline">
          Go to Log In now
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      mode="signup"
      title="Sign Up"
      subtitle="Create your Sri Apartment account"
      footer={
        <>
          <p>
            Already have an account?{" "}
            <Link to="/account/login" className="font-medium text-emerald-700 hover:underline">
              Log In
            </Link>
          </p>
          <p className="mt-2">
            Have a booking with us?{" "}
            <Link to="/guest/login" className="font-medium text-emerald-700 hover:underline">
              Guest Login
            </Link>
          </p>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="signup-first-name" className="mb-1 block text-sm font-medium text-slate-700">
              First name
            </label>
            <input
              id="signup-first-name"
              type="text"
              autoComplete="given-name"
              required
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
            />
          </div>
          <div>
            <label htmlFor="signup-last-name" className="mb-1 block text-sm font-medium text-slate-700">
              Last name
            </label>
            <input
              id="signup-last-name"
              type="text"
              autoComplete="family-name"
              required
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
            />
          </div>
        </div>

        <div>
          <label htmlFor="signup-email" className="mb-1 block text-sm font-medium text-slate-700">
            Email
          </label>
          <input
            id="signup-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
          />
        </div>

        <div>
          <label htmlFor="signup-password" className="mb-1 block text-sm font-medium text-slate-700">
            Password
          </label>
          <input
            id="signup-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
          />
          <p className="mt-1 text-xs text-slate-400">At least 8 characters, with a letter and a number.</p>
        </div>

        <div className="space-y-2">
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={termsAccepted}
              onChange={(e) => setTermsAccepted(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-slate-300 text-emerald-700 focus:ring-emerald-600"
            />
            <span>
              I agree to the <span className="font-medium text-slate-900">Terms &amp; Conditions</span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={privacyAccepted}
              onChange={(e) => setPrivacyAccepted(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-slate-300 text-emerald-700 focus:ring-emerald-600"
            />
            <span>
              I agree to the <span className="font-medium text-slate-900">Privacy Policy</span>
            </span>
          </label>
        </div>

        {error && (
          <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-emerald-700 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:opacity-50"
        >
          {loading ? "Creating account…" : "Create Account"}
        </button>
      </form>
    </AuthShell>
  );
}
