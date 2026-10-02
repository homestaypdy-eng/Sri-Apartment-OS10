import { useEffect, useRef, useState } from "react";
import { api, type NotificationLogOut } from "../lib/api";

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationLogOut[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);

  async function load() {
    try {
      const { data } = await api.get("/notifications/inbox");
      setItems(data);
    } catch {
      // In-app inbox is a convenience layer, not critical path — fail quietly rather than surface an error banner for it
    }
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function markRead(id: string) {
    try {
      await api.post(`/notifications/inbox/${id}/mark-read`);
      setItems((prev) => prev.map((i) => (i.id === id ? { ...i, read_at: new Date().toISOString() } : i)));
    } catch {
      // best-effort
    }
  }

  const unreadCount = items.filter((i) => !i.read_at).length;

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative text-white/70 hover:text-white p-1.5 rounded-lg hover:bg-white/10"
        aria-label="Notifications"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M13.73 21a2 2 0 01-3.46 0" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 bg-red-500 text-white text-[10px] font-semibold rounded-full min-w-[16px] h-4 px-1 flex items-center justify-center">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 bg-white border border-slate-200 rounded-xl shadow-lg z-50 max-h-96 overflow-y-auto">
          <div className="px-4 py-2.5 border-b border-slate-100 text-xs font-semibold text-slate-700">Notifications</div>
          {items.length === 0 ? (
            <p className="px-4 py-6 text-xs text-slate-400 text-center">Nothing here yet.</p>
          ) : (
            items.map((n) => (
              <button
                key={n.id}
                onClick={() => !n.read_at && markRead(n.id)}
                className={`w-full text-left px-4 py-2.5 border-b border-slate-50 text-xs hover:bg-slate-50 ${!n.read_at ? "bg-slate-50" : ""}`}
              >
                <p className={`font-medium ${!n.read_at ? "text-slate-900" : "text-slate-500"}`}>{n.event_type.replace(/_/g, " ")}</p>
                <p className="text-slate-400 mt-0.5">{new Date(n.created_at).toLocaleString()}</p>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
