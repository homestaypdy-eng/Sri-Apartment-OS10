import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { role, isAuthenticated, isInitializing } = useAuth();
  // Render nothing (briefly) while the localStorage session check is still
  // running, rather than redirecting on the first render before that check
  // has had a chance to restore a valid session.
  if (isInitializing) return null;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (role !== "admin" && role !== "manager" && role !== "staff") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function RequireRoles({ children, allowed }: { children: ReactNode; allowed: readonly string[] }) {
  const { role, isInitializing } = useAuth();
  if (isInitializing) return null;
  if (!role || !allowed.includes(role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { role, isInitializing } = useAuth();
  if (isInitializing) return null;
  if (role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

// RBAC Phase B: for the small set of pages that stay Admin+Staff (as they
// always were — neither route was touched in Phase B Part 1's backend
// work) but must NOT be reachable by Manager: /staff (the HR/payroll
// module, distinct from /operations' staff_ops) and /settings (whose
// underlying GET /settings and GET /settings/bank were never granted to
// Manager). Same redirect-to-dashboard behavior as RequireAdmin — this
// mirrors the existing app convention rather than introducing a new
// "forbidden" page/pattern.
export function RequireNotManager({ children }: { children: ReactNode }) {
  return <RequireRoles allowed={["admin", "staff"]}>{children}</RequireRoles>;
}
