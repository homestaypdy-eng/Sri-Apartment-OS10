import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isInitializing } = useAuth();
  // Render nothing (briefly) while the localStorage session check is still
  // running, rather than redirecting on the first render before that check
  // has had a chance to restore a valid session.
  if (isInitializing) return null;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { role, isInitializing } = useAuth();
  if (isInitializing) return null;
  if (role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
