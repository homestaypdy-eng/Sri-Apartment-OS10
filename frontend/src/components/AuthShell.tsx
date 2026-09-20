import type { ReactNode } from "react";
import { Link } from "react-router-dom";

type AuthMode = "login" | "signup" | "guest";

const MODE_TABS: { key: AuthMode; label: string; to: string }[] = [
  { key: "login", label: "Log In", to: "/account/login" },
  { key: "signup", label: "Sign Up", to: "/account/signup" },
  { key: "guest", label: "Guest Login", to: "/guest/login" },
];

/** The shared three-way switcher: Log In / Sign Up / Guest Login are one
 * coherent entry point, always visually present together so a customer
 * never has to guess which of the three exists or where to find it. */
function AuthModeSwitcher({ active }: { active: AuthMode }) {
  return (
    <div role="tablist" aria-label="Choose how to continue" className="mb-6 grid grid-cols-3 gap-1 rounded-lg border border-slate-200 bg-slate-100 p-1">
      {MODE_TABS.map((tab) => (
        <Link
          key={tab.key}
          to={tab.to}
          role="tab"
          aria-selected={active === tab.key}
          className={`rounded-md py-2 text-center text-[11px] font-semibold tracking-wide uppercase transition sm:text-xs ${
            active === tab.key ? "bg-emerald-700 text-white shadow-sm" : "text-slate-600 hover:text-emerald-700"
          }`}
        >
          {tab.label}
        </Link>
      ))}
    </div>
  );
}

export function AuthShell({
  mode,
  title,
  subtitle,
  children,
  footer,
}: {
  /** Omit on sub-flows (Forgot/Reset Password) that branch off Log In
   * rather than being one of the three primary entry points themselves. */
  mode?: AuthMode;
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <Link to="/" className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-700 text-sm font-bold text-white">SA</span>
            <span className="text-lg font-semibold tracking-tight text-slate-900">Sri Apartment</span>
          </Link>
        </div>

        {mode && <AuthModeSwitcher active={mode} />}

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
          <div className="mt-6">{children}</div>
        </div>

        {footer && <div className="mt-6 text-center text-sm text-slate-500">{footer}</div>}
      </div>
    </div>
  );
}
