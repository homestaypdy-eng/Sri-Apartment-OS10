import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { AuthShell } from "../components/AuthShell";
import { customerForgotPassword, extractErrorMessage } from "../lib/api";

export default function CustomerForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [devResetToken, setDevResetToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setDevResetToken(null);
    setLoading(true);
    try {
      const result = await customerForgotPassword(email);
      // Deliberately the same message whether or not this email has an
      // account — the backend never reveals that, and neither does this
      // page (see customer_auth/router.py's forgot_customer_password).
      setMessage(result.message);
      // Only ever present outside production (backend's own safety gate)
      // — never rendered here in a way that could reach a production UI,
      // since the field itself is simply absent there.
      if (result.dev_reset_token) setDevResetToken(result.dev_reset_token);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Forgot Password"
      subtitle="Enter your account email and we'll send you a reset link"
      footer={
        <Link to="/account/login" className="font-medium text-emerald-700 hover:underline">
          Back to Log In
        </Link>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div>
          <label htmlFor="forgot-email" className="mb-1 block text-sm font-medium text-slate-700">
            Email
          </label>
          <input
            id="forgot-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={!!message}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600 disabled:bg-slate-50"
          />
        </div>

        {error && (
          <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        {message && (
          <p role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            {message}
          </p>
        )}

        {devResetToken && (
          <div role="status" className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
            <p>Development mode — reset link token:</p>
            <p className="mt-1 break-all font-mono">{devResetToken}</p>
            <Link
              to={`/account/reset-password?token=${encodeURIComponent(devResetToken)}`}
              className="mt-2 inline-block font-semibold text-emerald-700 hover:underline"
            >
              Continue to reset your password →
            </Link>
          </div>
        )}

        {!message && (
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-emerald-700 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:opacity-50"
          >
            {loading ? "Sending…" : "Request Reset Link"}
          </button>
        )}
      </form>
    </AuthShell>
  );
}
