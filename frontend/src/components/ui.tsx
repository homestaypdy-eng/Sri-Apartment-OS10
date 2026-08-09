import type { ReactNode } from "react";

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
}: {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
}) {
  const base = "px-4 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50";
  const variants = {
    primary: "bg-slate-900 text-white hover:bg-slate-800",
    secondary: "bg-white border border-slate-300 text-slate-700 hover:bg-slate-50",
    danger: "bg-red-600 text-white hover:bg-red-700",
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`${base} ${variants[variant]}`}>
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

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "green" | "red" | "amber" }) {
  const tones = {
    slate: "bg-slate-100 text-slate-700",
    green: "bg-emerald-100 text-emerald-700",
    red: "bg-red-100 text-red-700",
    amber: "bg-amber-100 text-amber-700",
  };
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
