import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api, extractErrorMessage } from "../lib/api";

type Mode = "login" | "forgot" | "reset";

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function handleForgotPassword(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setLoading(true);
    try {
      const { data } = await api.post("/auth/forgot-password", { email });
      setMessage(data.message);
      setMode("reset");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleResetPassword(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setLoading(true);
    try {
      const { data } = await api.post("/auth/reset-password", { token: resetToken, new_password: newPassword });
      setMessage(data.message);
      setMode("login");
      setPassword("");
      setResetToken("");
      setNewPassword("");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <div className="w-full max-w-sm bg-white rounded-xl shadow-sm border border-slate-200 p-8">
        <h1 className="text-xl font-semibold text-slate-900 mb-1">Sri Apartment OS</h1>
        <p className="text-sm text-slate-500 mb-6">
          {mode === "login" && "Sign in to manage bookings"}
          {mode === "forgot" && "Enter your email to receive a reset code"}
          {mode === "reset" && "Enter the code from your email and a new password"}
        </p>

        {mode === "login" && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              />
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-slate-900 text-white rounded-lg py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
            >
              {loading ? "Signing in..." : "Sign in"}
            </button>
            <button
              type="button"
              onClick={() => { setMode("forgot"); setError(null); setMessage(null); }}
              className="w-full text-xs text-slate-500 hover:text-slate-700 hover:underline"
            >
              Forgot password?
            </button>
          </form>
        )}

        {mode === "forgot" && (
          <form onSubmit={handleForgotPassword} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              />
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            {message && <p className="text-sm text-emerald-600">{message}</p>}
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-slate-900 text-white rounded-lg py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
            >
              {loading ? "Sending..." : "Send Reset Code"}
            </button>
            <button
              type="button"
              onClick={() => setMode("reset")}
              className="w-full text-xs text-slate-500 hover:text-slate-700 hover:underline"
            >
              Already have a code? Reset password
            </button>
            <button
              type="button"
              onClick={() => { setMode("login"); setError(null); setMessage(null); }}
              className="w-full text-xs text-slate-400 hover:text-slate-600 hover:underline"
            >
              Back to sign in
            </button>
          </form>
        )}

        {mode === "reset" && (
          <form onSubmit={handleResetPassword} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Reset Code</label>
              <input
                value={resetToken}
                onChange={(e) => setResetToken(e.target.value)}
                required
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              />
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            {message && <p className="text-sm text-emerald-600">{message}</p>}
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-slate-900 text-white rounded-lg py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
            >
              {loading ? "Resetting..." : "Reset Password"}
            </button>
            <button
              type="button"
              onClick={() => { setMode("login"); setError(null); setMessage(null); }}
              className="w-full text-xs text-slate-500 hover:text-slate-700 hover:underline"
            >
              Back to sign in
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
