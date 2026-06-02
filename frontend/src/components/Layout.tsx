import {
  ArrowUpDown,
  Banknote,
  CreditCard,
  LayoutDashboard,
  LineChart,
  ListTree,
  LogOut,
  PiggyBank,
  Target,
  Upload,
  Wallet,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

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
  { to: "/banks", label: "Banks", icon: Banknote },
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
  const initials = (user?.full_name ?? user?.email ?? "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase())
    .join("");

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
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

      <main className="flex-1 overflow-auto bg-slate-50">
        <div className="mx-auto max-w-7xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
