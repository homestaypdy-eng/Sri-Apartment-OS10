import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { guestApi, extractErrorMessage } from "../lib/api";
import { useGuestAuth } from "../context/GuestAuthContext";

export default function GuestLoginPage() {
  const [phone, setPhone] = useState("");
  const [step, setStep] = useState<"request" | "verify">("request");
  const [devToken, setDevToken] = useState<string | null>(null);
  const [manualToken, setManualToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { guestLogin } = useGuestAuth();
  const navigate = useNavigate();

  async function handleRequestLogin(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      const { data } = await guestApi.post("/portal/request-login", { phone });
      if (data.sent) {
        setInfo(`A login link has been emailed to you (${data.channel}). Check your inbox.`);
      } else {
        setInfo("If this phone number is on file, a login link would be sent. Enter your token below to continue.");
      }
      setDevToken(data.dev_magic_token);
      setStep("verify");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tokenToUse = manualToken || devToken;
      if (!tokenToUse) {
        setError("Enter the token from your login link.");
        setLoading(false);
        return;
      }
      const { data } = await guestApi.post("/portal/verify", { token: tokenToUse });
      guestLogin(data.access_token);
      navigate("/guest");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm bg-white rounded-xl shadow-sm border border-slate-200 p-8">
        <h1 className="text-xl font-semibold text-slate-900 mb-1">Guest Portal</h1>
        <p className="text-sm text-slate-500 mb-6">Sri Apartment Homestay</p>

        {step === "request" ? (
          <form onSubmit={handleRequestLogin} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Phone Number</label>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                required
                placeholder="The phone number on your booking"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              />
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-slate-900 text-white rounded-lg py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
            >
              {loading ? "Sending..." : "Send Login Link"}
            </button>
          </form>
        ) : (
          <form onSubmit={handleVerify} className="space-y-4">
            {info && <p className="text-sm text-emerald-700 bg-emerald-50 rounded-lg px-3 py-2">{info}</p>}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Login Token</label>
              <input
                value={manualToken}
                onChange={(e) => setManualToken(e.target.value)}
                placeholder={devToken ? "Pre-filled below for this demo environment" : "Paste the token from your link"}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono text-xs focus:outline-none focus:ring-2 focus:ring-slate-900"
              />
              {devToken && (
                <p className="text-xs text-amber-600 mt-1">
                  Dev/testing note: a real deployment would only deliver this via email, never show it here.
                </p>
              )}
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-slate-900 text-white rounded-lg py-2 text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
            >
              {loading ? "Verifying..." : "Log In"}
            </button>
            <button
              type="button"
              onClick={() => setStep("request")}
              className="w-full text-xs text-slate-400 hover:text-slate-600"
            >
              Use a different phone number
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
