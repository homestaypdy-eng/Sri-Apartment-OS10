import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { customerLogout as apiCustomerLogout, type CustomerAccountOut } from "../lib/api";

const CUSTOMER_TOKEN_KEY = "customer_access_token";

interface CustomerAuthContextValue {
  isCustomerAuthenticated: boolean;
  isInitializing: boolean;
  // Only populated for the lifetime of this browser tab, set at the
  // moment of a successful login/registration — the backend has no
  // "who am I" endpoint for customers yet (customer_auth only exposes
  // register/login/logout/forgot-password/reset-password), so a page
  // reload cannot re-fetch it; it simply reverts to null, and pages
  // relying on it should degrade to a generic (not personalized) greeting.
  customer: CustomerAccountOut | null;
  customerLogin: (token: string, customer: CustomerAccountOut) => void;
  customerLogout: () => Promise<void>;
}

const CustomerAuthContext = createContext<CustomerAuthContextValue | undefined>(undefined);

export function CustomerAuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isInitializing, setIsInitializing] = useState(true);
  const [customer, setCustomer] = useState<CustomerAccountOut | null>(null);

  useEffect(() => {
    setIsAuthenticated(!!localStorage.getItem(CUSTOMER_TOKEN_KEY));
    setIsInitializing(false);
  }, []);

  function customerLogin(token: string, loggedInCustomer: CustomerAccountOut) {
    localStorage.setItem(CUSTOMER_TOKEN_KEY, token);
    setCustomer(loggedInCustomer);
    setIsAuthenticated(true);
  }

  async function customerLogout() {
    // The API call MUST be awaited before clearing localStorage: axios
    // request interceptors run as a microtask, so removing the token
    // first (as a prior version of this function did) meant the
    // Authorization header was already gone by the time the interceptor
    // read localStorage, and the server-side revocation silently 401'd.
    // Still best-effort overall — a network failure here must never
    // block the user from being logged out locally, hence the try/finally.
    try {
      await apiCustomerLogout();
    } catch {
      // Best-effort — local logout proceeds regardless (see finally).
    } finally {
      localStorage.removeItem(CUSTOMER_TOKEN_KEY);
      setCustomer(null);
      setIsAuthenticated(false);
    }
  }

  return (
    <CustomerAuthContext.Provider
      value={{ isCustomerAuthenticated: isAuthenticated, isInitializing, customer, customerLogin, customerLogout }}
    >
      {children}
    </CustomerAuthContext.Provider>
  );
}

export function useCustomerAuth() {
  const ctx = useContext(CustomerAuthContext);
  if (!ctx) throw new Error("useCustomerAuth must be used within CustomerAuthProvider");
  return ctx;
}
