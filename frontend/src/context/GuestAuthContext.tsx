import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

interface GuestAuthContextValue {
  isGuestAuthenticated: boolean;
  guestLogin: (token: string) => void;
  guestLogout: () => void;
}

const GuestAuthContext = createContext<GuestAuthContextValue | undefined>(undefined);

const GUEST_TOKEN_KEY = "guest_access_token";

export function GuestAuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  useEffect(() => {
    setIsAuthenticated(!!localStorage.getItem(GUEST_TOKEN_KEY));
  }, []);

  function guestLogin(token: string) {
    localStorage.setItem(GUEST_TOKEN_KEY, token);
    setIsAuthenticated(true);
  }

  function guestLogout() {
    localStorage.removeItem(GUEST_TOKEN_KEY);
    setIsAuthenticated(false);
  }

  return (
    <GuestAuthContext.Provider value={{ isGuestAuthenticated: isAuthenticated, guestLogin, guestLogout }}>
      {children}
    </GuestAuthContext.Provider>
  );
}

export function useGuestAuth() {
  const ctx = useContext(GuestAuthContext);
  if (!ctx) throw new Error("useGuestAuth must be used within GuestAuthProvider");
  return ctx;
}
