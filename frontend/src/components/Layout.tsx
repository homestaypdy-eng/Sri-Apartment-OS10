import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import NotificationBell from "./NotificationBell";

const navItems = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/properties", label: "Properties & Units" },
  { to: "/guests", label: "Guests" },
  { to: "/bookings", label: "Bookings" },
  { to: "/inquiries", label: "Inquiries" },
  { to: "/housekeeping", label: "Housekeeping" },
  { to: "/maintenance", label: "Maintenance" },
  { to: "/inventory", label: "Inventory" },
  { to: "/staff", label: "Staff" },
  { to: "/guest-requests", label: "Guest Requests" },
  { to: "/billing", label: "Billing" },
  { to: "/finance", label: "Finance" },
  { to: "/reports", label: "Reports Center" },
  { to: "/profit-simulator", label: "Profit Simulator" },
  { to: "/notifications", label: "Notifications" },
  { to: "/settings", label: "Settings" },
  { to: "/audit", label: "Audit", adminOnly: true },
  { to: "/users", label: "Users", adminOnly: true },
];

export default function Layout() {
  const { role, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  function handleLogout() {
    logout();
    navigate("/login");
  }

  const visibleItems = navItems.filter((item) => !item.adminOnly || role === "admin");

  const sidebarContent = (
    <>
      <div className="px-5 py-5 border-b border-slate-100 flex items-center justify-between">
        <div>
          <h1 className="font-semibold text-slate-900">Sri Apartment OS</h1>
          <p className="text-xs text-slate-400 mt-0.5">Signed in as {role}</p>
        </div>
        <div className="flex items-center gap-1">
          <NotificationBell />
          <button
            onClick={() => setMenuOpen(false)}
            className="md:hidden text-slate-400 hover:text-slate-600 text-xl leading-none px-2"
            aria-label="Close menu"
          >
            &times;
          </button>
        </div>
      </div>
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        {visibleItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={() => setMenuOpen(false)}
            className={({ isActive }) =>
              `block px-3 py-2 rounded-lg text-sm font-medium transition ${
                isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="p-3 border-t border-slate-100">
        <button
          onClick={handleLogout}
          className="w-full text-left px-3 py-2 rounded-lg text-sm text-slate-500 hover:bg-slate-100"
        >
          Log out
        </button>
      </div>
    </>
  );

  return (
    <div className="min-h-screen flex bg-slate-50">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-60 bg-white border-r border-slate-200 flex-col">
        {sidebarContent}
      </aside>

      {/* Mobile drawer + overlay */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMenuOpen(false)} />
          <aside className="absolute left-0 top-0 bottom-0 w-72 bg-white flex flex-col shadow-xl">
            {sidebarContent}
          </aside>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile top bar */}
        <div className="md:hidden flex items-center justify-between bg-white border-b border-slate-200 px-4 py-3">
          <button
            onClick={() => setMenuOpen(true)}
            className="text-slate-600 hover:text-slate-900"
            aria-label="Open menu"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 6h18M3 12h18M3 18h18" strokeLinecap="round" />
            </svg>
          </button>
          <span className="font-semibold text-slate-900 text-sm">Sri Apartment OS</span>
          <NotificationBell />
        </div>

        <main className="flex-1 p-4 sm:p-6 md:p-8 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
