import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import NotificationBell from "./NotificationBell";
import logo from "../assets/logo.png";

// RBAC Phase B: adminOnly items are also correctly hidden from Manager
// (the filter below checks role === "admin", same as it always did — a
// third role doesn't change that). hiddenFromManager is the new, opposite
// case: routes Staff can still see but Manager's backend access doesn't
// cover yet (the whole Staff/HR module, and Settings — see
// RequireNotManager in RequireAuth.tsx for why: neither GET /staff/...
// nor GET /settings was granted to Manager in Phase B Part 1).
type NavItem = { to: string; label: string; end?: boolean; adminOnly?: boolean; hiddenFromManager?: boolean; managerOrAdminOnly?: boolean };
type NavGroup = { label: string; items: NavItem[] };

const navGroups: NavGroup[] = [
  {
    label: "Operations",
    items: [
      { to: "/", label: "Dashboard", end: true },
      { to: "/messages", label: "Messages" },
      { to: "/availability", label: "Availability Calendar" },
      { to: "/properties", label: "Properties & Units" },
      { to: "/guests", label: "Guests" },
      { to: "/bookings", label: "Bookings" },
      { to: "/housekeeping", label: "Housekeeping" },
      { to: "/maintenance", label: "Maintenance" },
      { to: "/operations", label: "Staff Operations" },
      { to: "/inquiries", label: "Inquiries" },
      { to: "/inventory", label: "Inventory" },
      { to: "/guest-requests", label: "Guest Requests" },
    ],
  },
  {
    label: "Finance",
    items: [
      { to: "/billing", label: "Billing" },
      { to: "/finance", label: "Finance" },
      { to: "/payment-reconciliation", label: "Payment Reconciliation", managerOrAdminOnly: true },
      { to: "/reports", label: "Reports Center" },
      { to: "/revenue-dashboard", label: "Revenue Dashboard" },
      { to: "/promo-codes", label: "Promo Codes" },
      { to: "/profit-simulator", label: "Profit Simulator" },
    ],
  },
  {
    label: "Management",
    items: [
      { to: "/staff", label: "Staff", hiddenFromManager: true },
      { to: "/users", label: "Users", adminOnly: true },
      { to: "/audit", label: "Audit", adminOnly: true },
      { to: "/notifications", label: "Notifications" },
      { to: "/zotel", label: "Zotel Integration", adminOnly: true },
      { to: "/settings", label: "Settings", hiddenFromManager: true },
      { to: "/ai-strategist", label: "AI Strategist", adminOnly: true },
    ],
  },
];

export default function Layout() {
  const { role, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  function handleLogout() {
    logout();
    navigate("/login");
  }

  const visibleGroups = navGroups.map((group) => ({
    ...group,
    items: group.items.filter(
      (item) => (!item.adminOnly || role === "admin") && (!item.hiddenFromManager || role !== "manager") && (!item.managerOrAdminOnly || role === "admin" || role === "manager")
    ),
  })).filter((group) => group.items.length > 0);

  const sidebarContent = (
    <>
      <div className="px-5 py-5 border-b border-white/10 flex items-center justify-between">
        <div className="flex items-center gap-2.5 min-w-0">
          <img src={logo} alt="" className="h-9 w-9 rounded-full object-cover shrink-0" />
          <div className="min-w-0">
            <h1 className="font-semibold text-white truncate">Sri Apartment OS</h1>
            <p className="text-xs text-white/60 mt-0.5">Signed in as {role}</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <NotificationBell />
          <button
            onClick={() => setMenuOpen(false)}
            className="md:hidden text-white/60 hover:text-white text-xl leading-none px-2"
            aria-label="Close menu"
          >
            &times;
          </button>
        </div>
      </div>
      <nav aria-label="Main navigation" className="flex-1 overflow-y-auto px-3 py-4">
        {visibleGroups.map((group) => (
          <section key={group.label} className="mb-5 last:mb-0">
            <h2 className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/45">{group.label}</h2>
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    `block rounded-[10px] px-3 py-2 text-[13px] font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white ${
                      isActive ? "bg-brand-green-4 text-white shadow-sm" : "text-white/70 hover:bg-white/[0.08] hover:text-white"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          </section>
        ))}
      </nav>
      <div className="p-3 border-t border-white/10">
        <button
          onClick={handleLogout}
          className="w-full text-left px-3 py-2 rounded-lg text-sm text-white/60 hover:bg-white/10 hover:text-white"
        >
          Log out
        </button>
      </div>
    </>
  );

  return (
    <div className="min-h-screen flex bg-slate-50">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-60 bg-brand-green-3 flex-col">
        {sidebarContent}
      </aside>

      {/* Mobile drawer + overlay */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMenuOpen(false)} />
          <aside className="absolute left-0 top-0 bottom-0 w-72 bg-brand-green-3 flex flex-col shadow-xl">
            {sidebarContent}
          </aside>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile top bar */}
        <div className="md:hidden flex items-center justify-between bg-brand-green-3 px-4 py-3">
          <button
            onClick={() => setMenuOpen(true)}
            className="text-white/70 hover:text-white"
            aria-label="Open menu"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 6h18M3 12h18M3 18h18" strokeLinecap="round" />
            </svg>
          </button>
          <span className="flex items-center gap-2 font-semibold text-white text-sm">
            <img src={logo} alt="" className="h-6 w-6 rounded-full object-cover" />
            Sri Apartment OS
          </span>
          <NotificationBell />
        </div>

        <main className="flex-1 p-4 sm:p-6 md:p-8 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
