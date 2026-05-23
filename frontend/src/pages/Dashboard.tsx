import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, CreditCard, PiggyBank, Target } from "lucide-react";
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
import type { BudgetMonthView, DebtSummary, Goal } from "../api/types";
import { Card, EmptyState, PageHeader, ProgressBar, StatCard } from "../components/ui";
import { CHART_COLORS, fmtMoney, MONTH_NAMES, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

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
