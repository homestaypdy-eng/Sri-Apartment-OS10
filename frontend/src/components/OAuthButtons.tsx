// Google/Facebook sign-in buttons for the customer-facing auth screens.
//
// IMPORTANT: no backend Google/Facebook OAuth integration exists yet (no
// endpoint, no client ID/secret, no provider configuration — confirmed by
// inspection of app/modules/customer_auth and app/core/config.py before
// building this). These buttons are real, clickable UI — not disabled,
// not decorative — but clicking one never fakes or simulates a successful
// sign-in. It reports the honest current state via onUnavailable, which
// the calling page turns into a plain, non-alarming inline notice next to
// the button — the same pattern this backend already uses for "channel
// not configured" states (e.g. OTP delivery's own 501 handling), applied
// here on the frontend since there is no endpoint to even call yet.
type Provider = "google" | "facebook";

const LABELS: Record<Provider, string> = { google: "Google", facebook: "Facebook" };

function ProviderMark({ provider }: { provider: Provider }) {
  if (provider === "google") {
    return (
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
        <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.9c1.7-1.57 2.7-3.88 2.7-6.62Z" />
        <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.26c-.8.54-1.84.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.95v2.33A9 9 0 0 0 9 18Z" />
        <path fill="#FBBC05" d="M3.95 10.7A5.4 5.4 0 0 1 3.67 9c0-.59.1-1.17.28-1.7V4.97H.95A9 9 0 0 0 0 9c0 1.45.35 2.83.95 4.03l3-2.33Z" />
        <path fill="#EA4335" d="M9 3.58c1.32 0 2.51.46 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .95 4.97l3 2.33C4.66 5.17 6.65 3.58 9 3.58Z" />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <circle cx="9" cy="9" r="9" fill="#1877F2" />
      <path
        fill="#fff"
        d="M12.1 9.35h-2.02V15h-2.4V9.35H6.2V7.25h1.48V5.98c0-1.47.7-2.75 2.95-2.75.66 0 1.4.1 1.4.1v2h-1.04c-.6 0-.8.34-.8.86v1.06h1.94l-.03 2.1Z"
      />
    </svg>
  );
}

export function OAuthButtons({
  providers,
  onUnavailable,
}: {
  providers: Provider[];
  onUnavailable: (provider: Provider) => void;
}) {
  return (
    <div className="space-y-2">
      {providers.map((provider) => (
        <button
          key={provider}
          type="button"
          onClick={() => onUnavailable(provider)}
          className="flex w-full items-center justify-center gap-2.5 rounded-lg border border-slate-300 bg-white py-2.5 text-sm font-medium text-slate-700 transition hover:border-emerald-600 hover:bg-emerald-50/50"
        >
          <ProviderMark provider={provider} />
          Continue with {LABELS[provider]}
        </button>
      ))}
    </div>
  );
}
