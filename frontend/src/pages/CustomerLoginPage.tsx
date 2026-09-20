import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthShell } from "../components/AuthShell";
import { OAuthButtons } from "../components/OAuthButtons";
import { useCustomerAuth } from "../context/CustomerAuthContext";
import { customerLogin, extractErrorMessage } from "../lib/api";

export default function CustomerLoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [oauthNotice, setOauthNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { customerLogin: setCustomerSession } = useCustomerAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setOauthNotice(null);
    setLoading(true);
    try {
      const result = await customerLogin(email, password);
      setCustomerSession(result.access_token, result.customer);
      navigate("/account");
    } catch (err) {
      // The backend intentionally returns the identical generic message
      // whether the email is unknown or the password is wrong (anti-
      // enumeration) — shown here exactly as received, not reworded.
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  function handleOAuthUnavailable(provider: "google" | "facebook") {
    const label = provider === "google" ? "Google" : "Facebook";
    setOauthNotice(`Continue with ${label} isn't set up yet. Please sign in with your email and password for now.`);
  }

  return (
    <AuthShell
      mode="login"
      title="Log In"
      subtitle="Sign in to your Sri Apartment account"
      footer={
        <>
          <p>
            New to Sri Apartment?{" "}
            <Link to="/account/signup" className="font-medium text-emerald-700 hover:underline">
              Create an account
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
        <div>
          <label htmlFor="customer-login-email" className="mb-1 block text-sm font-medium text-slate-700">
            Email
          </label>
          <input
            id="customer-login-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
          />
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <label htmlFor="customer-login-password" className="block text-sm font-medium text-slate-700">
              Password
            </label>
            <Link to="/account/forgot-password" className="text-xs font-medium text-emerald-700 hover:underline">
              Forgot password?
            </Link>
          </div>
          <input
            id="customer-login-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
          />
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
          {loading ? "Signing in…" : "Log In"}
        </button>

        <div className="relative py-1 text-center text-xs text-slate-400">
          <span className="relative bg-white px-2">or</span>
          <div className="absolute inset-x-0 top-1/2 -z-10 h-px bg-slate-200" aria-hidden="true" />
        </div>

        {oauthNotice && (
          <p role="status" className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {oauthNotice}
          </p>
        )}
        <OAuthButtons providers={["google", "facebook"]} onUnavailable={handleOAuthUnavailable} />
      </form>
    </AuthShell>
  );
}
