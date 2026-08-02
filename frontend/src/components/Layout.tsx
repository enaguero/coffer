import {
  ArrowUpDown,
  CreditCard,
  LayoutDashboard,
  LineChart,
  ListTree,
  LogOut,
  Menu,
  PiggyBank,
  Radar,
  Scale,
  Target,
  Upload,
  Wallet,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useEffect } from "react";

import { useAuth } from "../contexts/useAuth";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/budget", label: "Budget", icon: PiggyBank },
  { to: "/cashflow", label: "Cashflow", icon: LineChart },
  { to: "/transactions", label: "Transactions", icon: ArrowUpDown },
  { to: "/accounts", label: "Accounts", icon: Wallet },
  { to: "/categories", label: "Categories", icon: ListTree },
  { to: "/debts", label: "Debts", icon: CreditCard },
  { to: "/goals", label: "Goals", icon: Target },
  { to: "/forecast", label: "Forecast", icon: Radar },
  { to: "/networth", label: "Net worth", icon: Scale },
  { to: "/import", label: "Import", icon: Upload },
];

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
    isActive
      ? "bg-brand-600 text-white shadow-card"
      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
  }`;

export default function Layout() {
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);
  const initials = (user?.full_name ?? user?.email ?? "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase())
    .join("");

  return (
    <div className="flex h-full">
      {/* Mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-40 flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 md:hidden">
        <button onClick={() => setMenuOpen((o) => !o)} aria-label="Menu" className="rounded p-1 text-slate-600">
          {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white">
          <Wallet className="h-4 w-4" />
        </div>
        <div className="text-base font-bold tracking-tight">Coffer</div>
      </div>

      <aside
        className={`${menuOpen ? "translate-x-0" : "-translate-x-full"} fixed inset-y-0 left-0 z-30 flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white pt-14 transition-transform md:static md:translate-x-0 md:pt-0`}
      >
        <div className="flex items-center gap-2 px-5 py-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Wallet className="h-4 w-4" />
          </div>
          <div className="text-lg font-bold tracking-tight">Coffer</div>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={linkClass}>
              <Icon className="h-4 w-4" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-slate-200 p-3">
          <div className="flex items-center gap-3 rounded-lg px-2 py-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700">
              {initials || "?"}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-slate-900">
                {user?.full_name ?? user?.email}
              </div>
              {user?.full_name && (
                <div className="truncate text-xs text-slate-500">{user.email}</div>
              )}
            </div>
          </div>
          <button
            onClick={logout}
            className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900"
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-auto bg-slate-50 pt-12 md:pt-0">
        <div className="mx-auto max-w-7xl px-4 py-6 md:px-8 md:py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
