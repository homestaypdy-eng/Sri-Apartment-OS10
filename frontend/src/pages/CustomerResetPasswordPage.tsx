import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AuthShell } from "../components/AuthShell";
import { customerResetPassword, extractErrorMessage } from "../lib/api";

export default function CustomerResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      await customerResetPassword(token, newPassword);
      setSuccess(true);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <AuthShell title="Reset Password" subtitle="This link is missing its reset code">
        <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          This reset link is invalid or incomplete. Please request a new one.
        </p>
        <Link to="/account/forgot-password" className="mt-4 inline-block text-sm font-medium text-emerald-700 hover:underline">
          Request a new reset link
        </Link>
      </AuthShell>
    );
  }

  if (success) {
    return (
      <AuthShell title="Password reset" subtitle="You can now log in with your new password">
        <p role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          Your password has been reset. All previous sessions have been signed out for your security.
        </p>
        <Link to="/account/login" className="mt-4 inline-block text-sm font-medium text-emerald-700 hover:underline">
          Go to Log In
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Reset Password" subtitle="Choose a new password for your account">
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div>
          <label htmlFor="reset-new-password" className="mb-1 block text-sm font-medium text-slate-700">
            New password
          </label>
          <input
            id="reset-new-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
          />
          <p className="mt-1 text-xs text-slate-400">At least 8 characters, with a letter and a number.</p>
        </div>

        <div>
          <label htmlFor="reset-confirm-password" className="mb-1 block text-sm font-medium text-slate-700">
            Confirm new password
          </label>
          <input
            id="reset-confirm-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
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
          {loading ? "Resetting…" : "Reset Password"}
        </button>
      </form>
    </AuthShell>
  );
}
