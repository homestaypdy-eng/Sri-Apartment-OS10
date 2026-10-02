import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`bg-white rounded-xl border border-slate-200 p-6 ${className}`}>{children}</div>;
}

export function PageHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-6">
      <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
      {action}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-2 mb-4">{message}</div>;
}

export function SuccessBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm rounded-lg px-4 py-2 mb-4">{message}</div>;
}

export function Button({
  children,
  onClick,
  type = "button",
  variant = "primary",
  disabled = false,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: "primary" | "secondary" | "danger" | "warning";
  disabled?: boolean;
  className?: string;
}) {
  const base = "px-4 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-brand-green";
  const variants = {
    primary: "bg-brand-green text-white hover:bg-brand-green-2",
    secondary: "bg-white border border-slate-300 text-slate-700 hover:bg-slate-50",
    danger: "bg-red-600 text-white hover:bg-red-700",
    // Soft, mild warning treatment for a destructive-but-recoverable action
    // (e.g. Cancel Booking) — visually distinct from "danger" (permanent
    // delete) without being a bright, aggressive color.
    warning: "bg-amber-50 border border-amber-300 text-amber-800 hover:bg-amber-100",
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`${base} ${variants[variant]} ${className}`}>
      {children}
    </button>
  );
}

export function Table({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto border border-slate-200 rounded-xl">
      <table className="min-w-full text-sm">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            {headers.map((h) => (
              <th key={h} className="text-left px-4 py-2.5 font-medium text-slate-500">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export type BadgeTone = "slate" | "green" | "red" | "amber" | "blue" | "violet" | "cyan" | "orange" | "pink";

/**
 * "tint" (default) is the standard low-emphasis status/category pill used
 * everywhere (booking status, accommodation type, active/inactive, etc).
 * "solid" is a deliberately higher-contrast variant for the rare case where
 * a badge itself needs to grab attention — e.g. a critical/warning severity
 * marker sitting inside a list rather than a table cell. Prefer "tint"
 * unless the badge needs to visually outrank the text next to it.
 */
export function Badge({ children, tone = "slate", variant = "tint" }: { children: ReactNode; tone?: BadgeTone; variant?: "tint" | "solid" }) {
  const tintTones: Record<BadgeTone, string> = {
    slate: "bg-slate-100 text-slate-700",
    green: "bg-brand-green/10 text-brand-green",
    red: "bg-red-100 text-red-700",
    amber: "bg-amber-100 text-amber-700",
    blue: "bg-blue-100 text-blue-700",
    violet: "bg-violet-100 text-violet-700",
    cyan: "bg-cyan-100 text-cyan-700",
    orange: "bg-orange-100 text-orange-700",
    pink: "bg-pink-100 text-pink-700",
  };
  const solidTones: Record<BadgeTone, string> = {
    slate: "bg-slate-500 text-white",
    green: "bg-brand-green text-white",
    red: "bg-red-600 text-white",
    amber: "bg-amber-500 text-white",
    blue: "bg-blue-600 text-white",
    violet: "bg-violet-600 text-white",
    cyan: "bg-cyan-600 text-white",
    orange: "bg-orange-600 text-white",
    pink: "bg-pink-600 text-white",
  };
  const tones = variant === "solid" ? solidTones : tintTones;
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

/** A single skeleton line — used to build table/card loading placeholders
 * that match the shape of the content about to appear, so the page doesn't
 * visually "jump" once data loads. `aria-hidden` since screen readers
 * should announce the surrounding "Loading" label, not each shimmer bar. */
export function SkeletonLine({ width = "100%" }: { width?: string }) {
  return <div className="h-4 bg-slate-200 rounded animate-pulse" style={{ width }} aria-hidden="true" />;
}

/** A skeleton table body — drop in place of real <tr> rows while a Table's
 * data is loading, matching its column count so the layout doesn't shift. */
export function SkeletonRows({ columns, rows = 4 }: { columns: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r}>
          {Array.from({ length: columns }).map((_, c) => (
            <td key={c} className="px-4 py-2.5">
              <SkeletonLine width={c === 0 ? "70%" : "50%"} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-400 py-6 justify-center" role="status">
      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span>{label}</span>
    </div>
  );
}

/** A consistent "nothing here yet" state — distinguishes "empty because
 * there's genuinely no data" from "still loading" or "something broke",
 * which a blank table body otherwise conflates into the same blank space. */
export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="text-center py-8">
      <p className="text-sm text-slate-400 mb-3">{message}</p>
      {action}
    </div>
  );
}

/**
 * Right-side slide-in panel shell for Add/Edit-style forms — introduced in
 * the Properties & Accommodations redesign, now the standard drawer for any
 * future page that needs one instead of a centered modal. Full-width on
 * screens narrower than max-w-md (448px), capped to 448px above that.
 * Deliberately minimal: header + close button + a single scrollable body.
 * Action buttons (Save/Cancel) belong at the end of the body content itself
 * — there's no separate footer slot, matching how every current usage
 * places its submit/cancel buttons inline at the bottom of its form.
 */
export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-md flex-col bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 className="text-base font-semibold text-slate-900">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="text-xl leading-none text-slate-400 hover:text-slate-600">
            &times;
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </div>
    </div>
  );
}

/**
 * A single row inside an attention/alert list — introduced in the
 * Dashboard "Needs Attention" redesign. Renders a severity Badge (solid
 * variant, so it reads as urgent) next to a title/description, with the
 * title optionally linking somewhere actionable. Purely presentational:
 * callers own what counts as "critical" vs "warning" vs anything else —
 * this component doesn't interpret severity, it just displays whatever
 * tone/label you pass it.
 */
export function AttentionRow({
  severity, severityTone, title, description, to,
}: {
  severity: string;
  severityTone: BadgeTone;
  title: string;
  description?: string;
  to?: string;
}) {
  const titleEl = to ? (
    <Link to={to} className="font-medium text-slate-900 hover:underline">{title}</Link>
  ) : (
    <span className="font-medium text-slate-900">{title}</span>
  );
  return (
    <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-white px-3 py-2.5">
      <Badge tone={severityTone} variant="solid">{severity}</Badge>
      <div className="min-w-0 text-xs">
        {titleEl}
        {description && <p className="text-slate-500">{description}</p>}
      </div>
    </div>
  );
}
