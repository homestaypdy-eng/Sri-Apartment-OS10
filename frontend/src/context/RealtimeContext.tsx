import axios from "axios";
import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import { useAuth } from "./AuthContext";
import { resolveApiBaseUrl } from "../lib/apiBaseUrl";

type RealtimeContextValue = { connected: boolean };
const RealtimeContext = createContext<RealtimeContextValue>({ connected: false });
const API_BASE = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

function publish(topic: string) {
  window.dispatchEvent(new CustomEvent("os10:invalidate", { detail: { topic } }));
}

async function readEvents(response: Response, signal: AbortSignal, onEvent: (name: string, data: string) => void | Promise<void>) {
  if (!response.body) throw new Error("Realtime stream has no response body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let name = "message";
      const data: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      await onEvent(name, data.join("\n"));
    }
  }
  await reader.cancel();
}

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, logout, syncRoleFromStoredSession } = useAuth();
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!isAuthenticated) return;
    let disposed = false;
    let delay = 1000;
    let refreshPromise: Promise<void> | null = null;
    const refreshSession = () => {
      if (!refreshPromise) {
        refreshPromise = (async () => {
          try {
            const refreshToken = localStorage.getItem("refresh_token");
            if (!refreshToken) throw new Error("Session expired");
            const response = await axios.post(`${API_BASE}/auth/refresh`, { refresh_token: refreshToken });
            localStorage.setItem("access_token", response.data.access_token);
            localStorage.setItem("refresh_token", response.data.refresh_token);
            // Update React auth state synchronously with the refreshed token.
            // The role can change while this tab is idle, so navigation or a
            // later API request must not be needed to refresh guards and UI.
            syncRoleFromStoredSession();
          } catch {
            logout();
            window.location.assign("/login");
            throw new Error("Session could not be refreshed");
          }
        })().finally(() => { refreshPromise = null; });
      }
      return refreshPromise;
    };

    const recover = () => {
      if (document.visibilityState !== "visible") return;
      publish("*");
      controllerRef.current?.abort();
    };
    window.addEventListener("online", recover);
    document.addEventListener("visibilitychange", recover);

    const connect = async () => {
      while (!disposed) {
        if (!navigator.onLine) {
          await new Promise((resolve) => window.setTimeout(resolve, Math.min(delay, 5000)));
          continue;
        }
        const controller = new AbortController();
        controllerRef.current = controller;
        try {
          const token = localStorage.getItem("access_token");
          if (!token) throw new Error("No active staff session");
          const response = await fetch(`${API_BASE}/realtime/events`, {
            headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
            signal: controller.signal,
            cache: "no-store",
          });
          if (response.status === 401) {
            await refreshSession();
            continue;
          }
          if (!response.ok) throw new Error(`Realtime connection failed (${response.status})`);
          delay = 1000;
          await readEvents(response, controller.signal, async (name, data) => {
            if (name === "resync") publish("*");
            if (name === "invalidate") {
              try {
                const event = JSON.parse(data) as { topic?: string };
                if (event.topic) publish(event.topic);
              } catch { /* Ignore malformed invalidation; reconnect/refetch remains authoritative. */ }
            }
            if (name === "auth_expired" || name === "auth_changed") await refreshSession();
            if (name === "auth_revoked") {
              logout();
              window.location.assign("/login");
            }
          });
        } catch (error) {
          if (!controller.signal.aborted && !disposed && error instanceof TypeError) {
            // A transient network/stream error is recovered by the bounded retry below.
          }
        }
        if (disposed) break;
        const jitter = Math.floor(Math.random() * Math.max(1, delay / 4));
        await new Promise((resolve) => window.setTimeout(resolve, delay + jitter));
        delay = Math.min(delay * 2, 30_000);
      }
    };
    void connect();
    return () => {
      disposed = true;
      controllerRef.current?.abort();
      window.removeEventListener("online", recover);
      document.removeEventListener("visibilitychange", recover);
    };
  }, [isAuthenticated, logout, syncRoleFromStoredSession]);

  return <RealtimeContext.Provider value={{ connected: isAuthenticated }}>{children}</RealtimeContext.Provider>;
}

export function useRealtimeStatus() {
  return useContext(RealtimeContext);
}
