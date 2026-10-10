import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, extractErrorMessage, type UserOut } from "../lib/api";

interface JwtPayload {
  sub: string;
  role: string;
  exp: number;
}

function decodeJwt(token: string): JwtPayload | null {
  try {
    const payload = token.split(".")[1];
    return JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return null;
  }
}

interface AuthContextValue {
  role: string | null;
  isAuthenticated: boolean;
  isInitializing: boolean;
  syncRoleFromStoredSession: () => void;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<string | null>(null);
  // Starts true and flips to false once the localStorage check below has run.
  // Without this, a fresh page load (F5, or opening a bookmarked/direct URL)
  // renders RequireAuth BEFORE the useEffect below has a chance to restore
  // a valid session from localStorage — role is still null on that first
  // render, so RequireAuth redirects to /login even though a perfectly
  // valid token is sitting right there. isInitializing lets RequireAuth
  // wait for this check instead of racing it.
  const [isInitializing, setIsInitializing] = useState(true);

  const syncRoleFromStoredSession = useCallback(() => {
    const current = localStorage.getItem("access_token");
    const payload = current ? decodeJwt(current) : null;
    setRole(payload && payload.exp * 1000 > Date.now() ? payload.role : null);
  }, []);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (token) {
      const payload = decodeJwt(token);
      if (payload && payload.exp * 1000 > Date.now()) {
        setRole(payload.role);
      } else {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
      }
    }
    setIsInitializing(false);
    const updateRole = () => syncRoleFromStoredSession();
    window.addEventListener("os10:auth-updated", updateRole);
    window.addEventListener("storage", updateRole);
    return () => {
      window.removeEventListener("os10:auth-updated", updateRole);
      window.removeEventListener("storage", updateRole);
    };
  }, [syncRoleFromStoredSession]);

  async function login(email: string, password: string) {
    try {
      const { data } = await api.post("/auth/login", { email, password });
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("refresh_token", data.refresh_token);
      const payload = decodeJwt(data.access_token);
      setRole(payload?.role ?? null);
    } catch (err) {
      throw new Error(extractErrorMessage(err));
    }
  }

  const logout = useCallback(() => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    setRole(null);
  }, []);

  return (
    <AuthContext.Provider value={{ role, isAuthenticated: !!role, isInitializing, syncRoleFromStoredSession, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

// Silence unused import warning for UserOut if not referenced elsewhere yet
export type { UserOut };
