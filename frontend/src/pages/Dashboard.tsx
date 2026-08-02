import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownRight,
  ArrowUpRight,
  Coins,
  CreditCard,
  Database,
  PiggyBank,
  Target,
  TrendingUp,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";
import GettingStarted from "../components/GettingStarted";
import type { AccountCoverage, BudgetMonthView, DebtSummary, Goal, Surplus } from "../api/types";
import { Badge, Card, EmptyState, PageHeader, ProgressBar, StatCard } from "../components/ui";
import { CHART_COLORS, fmtMoney, MONTH_NAMES, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

const STALE_AFTER_DAYS = 35;

function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

export default function Dashboard() {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1;
  const currency = useUserCurrency();

  const monthView = useQuery({
    queryKey: ["budget-month", year, month],
    queryFn: async () => (await api.get<BudgetMonthView>(`/api/v1/budgets/month/${year}/${month}`)).data,
  });
  const debts = useQuery({
    queryKey: ["debt-summary"],
    queryFn: async () => (await api.get<DebtSummary>("/api/v1/debts/summary")).data,
  });
  const goals = useQuery({
    queryKey: ["goals"],
    queryFn: async () => (await api.get<Goal[]>("/api/v1/goals")).data,
  });
  const surplus = useQuery({
    queryKey: ["surplus"],
    queryFn: async () => (await api.get<Surplus>("/api/v1/insights/surplus")).data,
  });
  const coverage = useQuery({
    queryKey: ["coverage"],
    queryFn: async () => (await api.get<AccountCoverage[]>("/api/v1/accounts/coverage")).data,
  });

  const staleAccounts = (coverage.data ?? []).filter((c) => {
    const days = daysSince(c.last_txn_on);
    return days === null || days > STALE_AFTER_DAYS;
  });

  const netCashflow =
    toNum(monthView.data?.income_actual) - toNum(monthView.data?.expenses_actual);

  const debtSlices = (debts.data?.by_debt ?? [])
    .map((d) => ({ name: d.name, value: toNum(d.current_balance) }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value);

  const budgetBars = (monthView.data?.rows ?? [])
    .map((r) => ({
      name: r.category_name,
      Planned: toNum(r.planned),
      Actual: toNum(r.actual),
    }))
    .filter((r) => r.Planned > 0 || r.Actual > 0)
    .slice(0, 8);

  return (
    <>
      <PageHeader
        title={`${MONTH_NAMES[month - 1]} ${year}`}
        subtitle="Your monthly snapshot — income, spending, debt, and goals."
      />

      <GettingStarted />

      {(surplus.data?.raises_detected.length ?? 0) > 0 && (
        <div className="mb-4 space-y-2">
          {surplus.data?.raises_detected.map((r, i) => (
            <div
              key={i}
              className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
            >
              <TrendingUp className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                <strong>Pay rise detected:</strong> "{r.description}" went from{" "}
                {fmtMoney(r.previous_amount, currency)} to {fmtMoney(r.new_amount, currency)} (~
                {fmtMoney(r.monthly_delta, currency)}/month more). Commit part of it to a debt or
                goal before it becomes lifestyle — even half is{" "}
                {fmtMoney(toNum(r.monthly_delta) / 2, currency)}/month. (April tax-code changes can
                also look like small raises.)
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Income"
          value={fmtMoney(monthView.data?.income_actual, currency)}
          accent="emerald"
          icon={<ArrowDownRight className="h-5 w-5" />}
          hint={`Planned ${fmtMoney(monthView.data?.income_planned, currency)}`}
        />
        <StatCard
          label="Expenses"
          value={fmtMoney(monthView.data?.expenses_actual, currency)}
          accent="rose"
          icon={<ArrowUpRight className="h-5 w-5" />}
          hint={`Planned ${fmtMoney(monthView.data?.expenses_planned, currency)}`}
        />
        <StatCard
          label="Saving"
          value={fmtMoney(monthView.data?.saving_actual, currency)}
          accent="sky"
          icon={<PiggyBank className="h-5 w-5" />}
          hint={`Planned ${fmtMoney(monthView.data?.saving_planned, currency)}`}
        />
        <StatCard
          label="Total debt"
          value={fmtMoney(debts.data?.total_owed, currency)}
          accent="amber"
          icon={<CreditCard className="h-5 w-5" />}
          hint={`${debts.data?.by_debt.length ?? 0} obligations`}
        />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Card className="p-6 lg:col-span-3">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-sm font-semibold text-slate-900">Budget vs. actual</h2>
            <span className="text-xs text-slate-500 nums">
              Net cashflow:{" "}
              <span className={netCashflow >= 0 ? "text-emerald-600" : "text-rose-600"}>
                {fmtMoney(netCashflow, currency)}
              </span>
            </span>
          </div>
          {budgetBars.length === 0 ? (
            <div className="flex h-64 items-center justify-center text-sm text-slate-500">
              No budget data yet.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={budgetBars} margin={{ top: 5, right: 8, bottom: 5, left: 8 }}>
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  interval={0}
                  angle={-12}
                  textAnchor="end"
                  height={48}
                />
                <YAxis tick={{ fontSize: 11, fill: "#64748b" }} width={48} />
                <Tooltip
                  cursor={{ fill: "rgba(99, 102, 241, 0.08)" }}
                  contentStyle={{
                    borderRadius: 8,
                    border: "1px solid #e2e8f0",
                    fontSize: 12,
                  }}
                  formatter={(v: number) => fmtMoney(v, currency)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="Planned" fill="#cbd5e1" radius={[4, 4, 0, 0]} />
                <Bar dataKey="Actual" fill="#4f46e5" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-6 lg:col-span-2">
          <h2 className="mb-4 text-sm font-semibold text-slate-900">Debt breakdown</h2>
          {debtSlices.length === 0 ? (
            <div className="flex h-64 items-center justify-center text-sm text-slate-500">
              No debts tracked.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={debtSlices}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={55}
                  outerRadius={90}
                  paddingAngle={2}
                >
                  {debtSlices.map((_, i) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    borderRadius: 8,
                    border: "1px solid #e2e8f0",
                    fontSize: 12,
                  }}
                  formatter={(v: number) => fmtMoney(v, currency)}
                />
                <Legend
                  wrapperStyle={{ fontSize: 11 }}
                  iconType="circle"
                  iconSize={8}
                  layout="vertical"
                  verticalAlign="middle"
                  align="right"
                />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Card className="p-6 lg:col-span-3">
          <div className="flex items-center gap-2">
            <Coins className="h-4 w-4 text-slate-400" />
            <h2 className="text-sm font-semibold text-slate-900">
              {surplus.data
                ? `${MONTH_NAMES[surplus.data.month - 1]} surplus — where should it go?`
                : "Monthly surplus"}
            </h2>
          </div>
          {surplus.data ? (
            <>
              <div className="mt-3 flex items-baseline gap-3">
                <span
                  className={`text-2xl font-bold tracking-tight nums ${
                    toNum(surplus.data.surplus) >= 0 ? "text-emerald-600" : "text-rose-600"
                  }`}
                >
                  {fmtMoney(surplus.data.surplus, currency)}
                </span>
                <span className="text-xs text-slate-500 nums">
                  {fmtMoney(surplus.data.income, currency)} in −{" "}
                  {fmtMoney(surplus.data.outflows, currency)} out
                </span>
              </div>
              {surplus.data.uncategorized_count > 0 && (
                <div className="mt-1 text-xs text-amber-700">
                  {surplus.data.uncategorized_count} uncategorized transactions (
                  {fmtMoney(surplus.data.uncategorized_amount, currency)}) —{" "}
                  <Link to="/transactions" className="underline">
                    review them
                  </Link>{" "}
                  to keep this number honest.
                </div>
              )}
              {surplus.data.options.length > 0 ? (
                <ul className="mt-4 space-y-2">
                  {surplus.data.options.slice(0, 4).map((o, i) => (
                    <li
                      key={i}
                      className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2 text-sm"
                    >
                      <div>
                        <span className="font-medium text-slate-900">{o.name}</span>
                        <span className="ml-2 text-xs text-slate-500">{o.note}</span>
                      </div>
                      <Badge tone={o.kind === "debt" ? "rose" : o.kind === "goal" ? "sky" : "emerald"}>
                        {o.kind}
                      </Badge>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-sm text-slate-500">
                  No positive surplus last month — the Forecast page shows where the money went.
                </p>
              )}
            </>
          ) : (
            <p className="mt-3 text-sm text-slate-500">Import statements to compute a surplus.</p>
          )}
        </Card>

        <Card className="p-6 lg:col-span-2">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-slate-400" />
            <h2 className="text-sm font-semibold text-slate-900">Data freshness</h2>
          </div>
          {coverage.data && coverage.data.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {coverage.data.map((c) => {
                const days = daysSince(c.last_txn_on);
                const stale = days === null || days > STALE_AFTER_DAYS;
                return (
                  <li key={c.account_id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate">{c.name}</span>
                    <Badge tone={stale ? "amber" : "emerald"}>
                      {c.last_txn_on ? `through ${c.last_txn_on}` : "no data yet"}
                    </Badge>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-slate-500">No accounts yet.</p>
          )}
          {staleAccounts.length > 0 && (
            <div className="mt-3 border-t border-slate-100 pt-2 text-xs text-amber-700">
              {staleAccounts.length} account{staleAccounts.length > 1 ? "s" : ""} need a fresh
              statement —{" "}
              <Link to="/import" className="underline">
                import now
              </Link>
              . Every number above is only as current as its data.
            </div>
          )}
        </Card>
      </div>

      <section className="mt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">Goals</h2>
          <Target className="h-4 w-4 text-slate-400" />
        </div>
        {goals.isLoading ? (
          <div className="text-sm text-slate-500">Loading…</div>
        ) : goals.data && goals.data.length === 0 ? (
          <EmptyState
            icon={<Target className="h-5 w-5" />}
            title="No goals yet"
            body="Set a savings target on the Goals page to track progress here."
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {goals.data?.map((g) => (
              <Card key={g.id} className="p-5">
                <div className="flex items-baseline justify-between gap-3">
                  <div className="truncate font-medium text-slate-900">{g.name}</div>
                  <div className="shrink-0 text-xs font-semibold text-brand-700 nums">
                    {Math.round(g.progress * 100)}%
                  </div>
                </div>
                <div className="mt-1 text-xs text-slate-500 nums">
                  {fmtMoney(g.current_amount, currency)} of {fmtMoney(g.target_amount, currency)}
                </div>
                <ProgressBar value={g.progress} tone="emerald" className="mt-3" />
              </Card>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
