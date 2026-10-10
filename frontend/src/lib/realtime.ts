import { useEffect, useRef } from "react";

export type RealtimeTopic =
  | "bookings.changed" | "availability.changed" | "properties.changed" | "guests.changed"
  | "inquiries.changed" | "payments.changed" | "housekeeping.changed" | "maintenance.changed"
  | "guest_requests.changed" | "inventory.changed" | "finance.changed" | "staff_operations.changed"
  | "notifications.changed" | "messages.changed" | "users.changed" | "audit.changed" | "integrations.changed";

/** Refetch authoritative data for the active screen after server invalidation or reconnect. */
export function useRealtimeRefresh(topics: readonly RealtimeTopic[], refresh: () => void | Promise<void>) {
  const refreshRef = useRef(refresh);
  const topicsRef = useRef(topics);
  refreshRef.current = refresh;
  topicsRef.current = topics;

  useEffect(() => {
    let timeout: number | undefined;
    const run = () => {
      window.clearTimeout(timeout);
      timeout = window.setTimeout(() => { void refreshRef.current(); }, 80);
    };
    const onInvalidate = (event: Event) => {
      const topic = (event as CustomEvent<{ topic?: string }>).detail?.topic;
      if (topic === "*" || (topic && topicsRef.current.includes(topic as RealtimeTopic))) run();
    };
    const onRecover = () => {
      if (document.visibilityState === "visible") run();
    };
    window.addEventListener("os10:invalidate", onInvalidate);
    window.addEventListener("online", onRecover);
    document.addEventListener("visibilitychange", onRecover);
    return () => {
      window.clearTimeout(timeout);
      window.removeEventListener("os10:invalidate", onInvalidate);
      window.removeEventListener("online", onRecover);
      document.removeEventListener("visibilitychange", onRecover);
    };
  }, []);
}
