import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { AuthShell } from "../components/AuthShell";
import { OAuthButtons } from "../components/OAuthButtons";
import { useGuestAuth } from "../context/GuestAuthContext";
import { extractErrorMessage, requestEmailOtp, requestPhoneOtp, verifyEmailOtp, verifyPhoneOtp } from "../lib/api";

// Guest Login — for a customer who already has a Sri Apartment booking or
// stay, authenticated via phone/email OTP (app/modules/portal/auth_router.py
// / auth_email_router.py). This REPLACES the page's previous magic-link-
// only form with the modern OTP flow, but deliberately keeps the exact
// same route (/guest/login) and downstream wiring (useGuestAuth,
// GuestDashboardPage at /guest) — an upgrade in place, not a second,
// duplicate guest-login surface living alongside the old one.
type Method = "phone" | "email";

const RESEND_COOLDOWN_SECONDS = 60;

export default function GuestLoginPage() {
  const [method, setMethod] = useState<Method>("phone");
  const [identifier, setIdentifier] = useState("");
  const [step, setStep] = useState<"request" | "verify">("request");
  const [challengeId, setChallengeId] = useState("");
  const [code, setCode] = useState("");
  const [devOtpCode, setDevOtpCode] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "sending" | "verifying" | "error">("idle");
  const [message, setMessage] = useState("");
  const [oauthNotice, setOauthNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const { guestLogin } = useGuestAuth();
  const navigate = useNavigate();

  const busy = status === "sending" || status === "verifying";

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  function switchMethod(next: Method) {
    if (next === method) return;
    setMethod(next);
    resetToRequestStep();
  }

  function resetToRequestStep() {
    setStep("request");
    setIdentifier("");
    setChallengeId("");
    setCode("");
    setDevOtpCode(null);
    setMessage("");
    setStatus("idle");
    setCooldown(0);
  }

  async function sendOtp(e?: FormEvent) {
    e?.preventDefault();
    setStatus("sending");
    setMessage("");
    setOauthNotice(null);
    try {
      const result = method === "phone" ? await requestPhoneOtp(identifier) : await requestEmailOtp(identifier);
      setChallengeId(result.challenge_id);
      setCode("");
      setDevOtpCode(result.dev_otp_code ?? null);
      setStep("verify");
      setStatus("idle");
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setStatus("error");
      setMessage(extractErrorMessage(err));
    }
  }

  async function verifyOtp(e: FormEvent) {
    e.preventDefault();
    setStatus("verifying");
    setMessage("");
    try {
      const result = method === "phone" ? await verifyPhoneOtp(challengeId, code) : await verifyEmailOtp(challengeId, code);
      guestLogin(result.access_token);
      navigate("/guest");
    } catch (err) {
      setStatus("error");
      setMessage(extractErrorMessage(err));
    }
  }

  function handleOAuthUnavailable() {
    setOauthNotice("Continue with Google isn't set up yet. Please use phone or email OTP for now.");
  }

  return (
    <AuthShell
      mode="guest"
      title="Guest Login"
      subtitle="For customers with a Sri Apartment booking or stay"
      footer={
        <p>
          Don't have a booking yet? Use <span className="font-medium text-emerald-700">Log In</span> or{" "}
          <span className="font-medium text-emerald-700">Sign Up</span> above to create an account instead.
        </p>
      }
    >
      <p className="mb-5 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
        Guest Login is for verifying an existing Sri Apartment booking or stay — no password needed. If you'd like a
        full account instead, use Sign Up above.
      </p>

      {step === "request" && (
        <div role="tablist" aria-label="OTP method" className="mb-5 grid grid-cols-2 gap-1 rounded-lg border border-slate-200 bg-slate-100 p-1">
          <button
            type="button"
            role="tab"
            aria-selected={method === "phone"}
            onClick={() => switchMethod("phone")}
            className={`rounded-md py-2 text-xs font-semibold tracking-wide uppercase transition ${
              method === "phone" ? "bg-white text-emerald-700 shadow-sm" : "text-slate-500 hover:text-emerald-700"
            }`}
          >
            Phone
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={method === "email"}
            onClick={() => switchMethod("email")}
            className={`rounded-md py-2 text-xs font-semibold tracking-wide uppercase transition ${
              method === "email" ? "bg-white text-emerald-700 shadow-sm" : "text-slate-500 hover:text-emerald-700"
            }`}
          >
            Email
          </button>
        </div>
      )}

      {step === "request" ? (
        <form onSubmit={sendOtp} className="space-y-4" noValidate>
          <div>
            <label htmlFor="guest-identifier" className="mb-1 block text-sm font-medium text-slate-700">
              {method === "phone" ? "Mobile number" : "Email address"}
            </label>
            <input
              id="guest-identifier"
              type={method === "phone" ? "tel" : "email"}
              autoComplete={method === "phone" ? "tel" : "email"}
              required
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder={method === "phone" ? "The number you booked with" : "The email you booked with"}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
            />
          </div>

          {message && (
            <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {message}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !identifier}
            className="w-full rounded-lg bg-emerald-700 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:opacity-50"
          >
            {status === "sending" ? "Sending…" : "Send OTP"}
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
          <OAuthButtons providers={["google"]} onUnavailable={handleOAuthUnavailable} />
        </form>
      ) : (
        <form onSubmit={verifyOtp} className="space-y-4" noValidate>
          <p className="text-sm text-slate-500">
            Enter the code sent to <span className="font-medium text-slate-900">{identifier}</span>
          </p>

          {devOtpCode && (
            <p role="status" className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Development mode — your verification code is: <span className="font-mono font-semibold">{devOtpCode}</span>
            </p>
          )}

          <div>
            <label htmlFor="guest-otp-code" className="mb-1 block text-sm font-medium text-slate-700">
              Verification code
            </label>
            <input
              id="guest-otp-code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              autoComplete="one-time-code"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              placeholder="6-digit code"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-center text-lg tracking-[0.4em] focus:border-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-600"
            />
          </div>

          {message && (
            <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {message}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || code.length !== 6}
            className="w-full rounded-lg bg-emerald-700 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:opacity-50"
          >
            {status === "verifying" ? "Verifying…" : "Verify OTP"}
          </button>

          <div className="flex items-center justify-between text-xs">
            <button type="button" onClick={resetToRequestStep} className="text-slate-500 underline hover:text-emerald-700">
              {method === "phone" ? "Change number" : "Change email"}
            </button>
            <button
              type="button"
              onClick={() => void sendOtp()}
              disabled={cooldown > 0 || busy}
              className="text-slate-500 underline hover:text-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {cooldown > 0 ? `Resend OTP (${cooldown}s)` : "Resend OTP"}
            </button>
          </div>
        </form>
      )}
    </AuthShell>
  );
}
