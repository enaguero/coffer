import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, CreditCard, Plus, Trash2, TrendingDown } from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";
import type { Debt, DebtPlan, DebtPlanCompare, DebtSummary } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Label, PageHeader, ProgressBar } from "../components/ui";
import { fmtMoney, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

function PlannerPanel({ currency, hasDebts }: { currency: string; hasDebts: boolean }) {
  const [strategy, setStrategy] = useState<"avalanche" | "snowball">("avalanche");
  const [extraInput, setExtraInput] = useState("100");
  const [extra, setExtra] = useState("100");

  const plan = useQuery({
    queryKey: ["debt-plan", extra],
    enabled: hasDebts,
    queryFn: async () =>
      (
        await api.post<DebtPlanCompare>("/api/v1/debts/plan", {
          extra_monthly: extra || "0",
        })
      ).data,
  });

  if (!hasDebts) return null;
  const chosen: DebtPlan | undefined = plan.data?.[strategy];
  const other: DebtPlan | undefined = plan.data?.[strategy === "avalanche" ? "snowball" : "avalanche"];
  const baseline = plan.data?.minimum;

  const chartData =
    chosen?.balance_series.map((p) => ({ on: p.on.slice(0, 7), balance: toNum(p.balance) })) ?? [];

  return (
    <Card className="mt-6 p-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Payoff planner</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Fixed budget: your minimum payments plus the extra — cleared minimums roll into the pool.
          </p>
        </div>
        <div className="flex items-end gap-3">
          <label>
            <Label>Extra per month</Label>
            <Input
              value={extraInput}
              onChange={(e) => setExtraInput(e.target.value)}
              onBlur={() => setExtra(extraInput)}
              onKeyDown={(e) => e.key === "Enter" && setExtra(extraInput)}
              className="w-28 text-right"
            />
          </label>
          <div className="flex overflow-hidden rounded-lg border border-slate-300">
            {(["avalanche", "snowball"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setStrategy(s)}
                className={`px-3 py-2 text-sm font-medium capitalize transition ${
                  strategy === s ? "bg-brand-600 text-white" : "bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>

      {chosen && baseline && (
        <>
          {chosen.unpayable && (
            <div className="mt-4 flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              Payments don't cover interest — the balance never clears at this budget. Increase the extra.
            </div>
          )}

          <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Debt-free</div>
              <div className="mt-1 text-xl font-bold tracking-tight nums">
                {chosen.debt_free_date ?? "—"}
              </div>
              {chosen.months_saved_vs_minimum != null && chosen.months_saved_vs_minimum > 0 && (
                <div className="text-xs text-emerald-600">
                  {chosen.months_saved_vs_minimum} months sooner than minimums
                </div>
              )}
            </div>
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Total interest</div>
              <div className="mt-1 text-xl font-bold tracking-tight nums">
                {fmtMoney(chosen.total_interest, currency)}
              </div>
              {chosen.interest_saved_vs_minimum && toNum(chosen.interest_saved_vs_minimum) > 0 && (
                <div className="text-xs text-emerald-600">
                  saves {fmtMoney(chosen.interest_saved_vs_minimum, currency)} vs minimums
                </div>
              )}
            </div>
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Monthly budget</div>
              <div className="mt-1 text-xl font-bold tracking-tight nums">
                {fmtMoney(chosen.monthly_budget, currency)}
              </div>
            </div>
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
                vs {strategy === "avalanche" ? "snowball" : "avalanche"}
              </div>
              <div className="mt-1 text-xl font-bold tracking-tight nums">
                {other
                  ? fmtMoney(toNum(other.total_interest) - toNum(chosen.total_interest), currency)
                  : "—"}
              </div>
              <div className="text-xs text-slate-500">interest difference</div>
            </div>
          </div>

          {chosen.promo_cliffs.length > 0 && (
            <div className="mt-4 space-y-2">
              {chosen.promo_cliffs.map((c) => (
                <div
                  key={c.debt_id}
                  className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
                >
                  <CalendarClock className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    <strong>{c.name}</strong>: promo rate ends {c.promo_ends_on} with{" "}
                    {fmtMoney(c.balance_at_expiry, currency)} still owed — reverts to{" "}
                    {toNum(c.reverting_apr).toFixed(1)}% (~{fmtMoney(c.extra_yearly_interest, currency)}
                    /year). Clear it before the cliff if you can.
                  </span>
                </div>
              ))}
            </div>
          )}

          {chartData.length > 1 && !chosen.unpayable && (
            <div className="mt-5 h-44">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                  <defs>
                    <linearGradient id="debtFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#4f46e5" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#4f46e5" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="on" tick={{ fontSize: 11 }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 11 }} width={70} tickFormatter={(v) => fmtMoney(v, currency)} />
                  <Tooltip formatter={(v) => fmtMoney(v as number, currency)} />
                  <Area type="monotone" dataKey="balance" stroke="#4f46e5" fill="url(#debtFill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 text-left font-medium">Debt</th>
                  <th className="py-2 text-right font-medium">Paid off</th>
                  <th className="py-2 text-right font-medium">Interest paid</th>
                </tr>
              </thead>
              <tbody>
                {chosen.debts.map((d) => (
                  <tr key={d.id} className="border-t border-slate-100">
                    <td className="py-2">{d.name}</td>
                    <td className="py-2 text-right nums">{d.payoff_date ?? "never at this budget"}</td>
                    <td className="py-2 text-right nums">{fmtMoney(d.interest_paid, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {chosen.assumptions.length > 0 && (
            <div className="mt-3 text-xs text-slate-400">
              {chosen.assumptions.map((a) => (
                <div key={a}>Assumption: {a}</div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

export default function Debts() {
  const qc = useQueryClient();
  const currency = useUserCurrency();
  const summary = useQuery({
    queryKey: ["debt-summary"],
    queryFn: async () => (await api.get<DebtSummary>("/api/v1/debts/summary")).data,
  });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["debt-summary"] });
    qc.invalidateQueries({ queryKey: ["debt-plan"] });
  };
  const create = useMutation({
    mutationFn: async (payload: Partial<Debt>) => (await api.post("/api/v1/debts", payload)).data,
    onSuccess: invalidate,
  });
  const update = useMutation({
    mutationFn: async (vars: { id: number; current_balance: string }) =>
      (await api.patch(`/api/v1/debts/${vars.id}`, { current_balance: vars.current_balance })).data,
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/debts/${id}`),
    onSuccess: invalidate,
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [original, setOriginal] = useState("0");
  const [current, setCurrent] = useState("0");
  const [apr, setApr] = useState("");
  const [promoApr, setPromoApr] = useState("");
  const [promoEnds, setPromoEnds] = useState("");
  const [minPay, setMinPay] = useState("");
  const [dueDay, setDueDay] = useState("");

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      {
        name,
        original_principal: original as unknown as string,
        current_balance: current as unknown as string,
        interest_rate_apr: apr ? (apr as unknown as string) : null,
        promo_apr: promoApr ? (promoApr as unknown as string) : null,
        promo_ends_on: promoEnds || null,
        minimum_payment: minPay ? (minPay as unknown as string) : null,
        due_day_of_month: dueDay ? Number(dueDay) : null,
      },
      {
        onSuccess: () => {
          setName(""); setOriginal("0"); setCurrent("0"); setApr("");
          setPromoApr(""); setPromoEnds(""); setMinPay(""); setDueDay("");
          setShowForm(false);
        },
      },
    );
  }

  const totalOwed = toNum(summary.data?.total_owed);
  const totalOriginal = (summary.data?.by_debt ?? []).reduce(
    (acc, d) => acc + toNum(d.original_principal),
    0,
  );
  const paidDownPct = totalOriginal > 0 ? 1 - totalOwed / totalOriginal : 0;

  return (
    <>
      <PageHeader
        title="Debts"
        subtitle={`${summary.data?.by_debt.length ?? 0} obligations · ${fmtMoney(totalOwed, currency)} owed`}
        right={
          <Button onClick={() => setShowForm((s) => !s)}>
            <Plus className="h-4 w-4" />
            {showForm ? "Cancel" : "Add debt"}
          </Button>
        }
      />

      <Card className="p-6">
        <div className="flex items-baseline justify-between">
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Pay-down progress
            </div>
            <div className="mt-1 text-2xl font-bold tracking-tight nums">
              {fmtMoney(totalOriginal - totalOwed, currency)}{" "}
              <span className="text-base font-medium text-slate-400">
                of {fmtMoney(totalOriginal, currency)} paid
              </span>
            </div>
          </div>
          <Badge tone="brand">{Math.round(paidDownPct * 100)}%</Badge>
        </div>
        <ProgressBar value={paidDownPct} tone="emerald" className="mt-4" />
      </Card>

      {showForm && (
        <Card className="mt-6 p-6">
          <h2 className="mb-4 text-sm font-semibold text-slate-900">New debt</h2>
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <label className="md:col-span-2">
              <Label>Name</Label>
              <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Visa card" />
            </label>
            <label>
              <Label>Original</Label>
              <Input value={original} onChange={(e) => setOriginal(e.target.value)} />
            </label>
            <label>
              <Label>Current balance</Label>
              <Input value={current} onChange={(e) => setCurrent(e.target.value)} />
            </label>
            <label>
              <Label>APR %</Label>
              <Input value={apr} onChange={(e) => setApr(e.target.value)} />
            </label>
            <label>
              <Label>Promo APR % (0% offers)</Label>
              <Input value={promoApr} onChange={(e) => setPromoApr(e.target.value)} placeholder="e.g. 0" />
            </label>
            <label>
              <Label>Promo ends</Label>
              <Input type="date" value={promoEnds} onChange={(e) => setPromoEnds(e.target.value)} />
            </label>
            <label>
              <Label>Min payment</Label>
              <Input value={minPay} onChange={(e) => setMinPay(e.target.value)} />
            </label>
            <label>
              <Label>Due day</Label>
              <Input value={dueDay} onChange={(e) => setDueDay(e.target.value)} placeholder="1–31" />
            </label>
            <div className="md:col-span-4">
              <Button type="submit">Save debt</Button>
            </div>
          </form>
        </Card>
      )}

      <PlannerPanel currency={currency} hasDebts={(summary.data?.by_debt.length ?? 0) > 0} />

      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        {summary.data?.by_debt.length === 0 && (
          <div className="md:col-span-2">
            <EmptyState
              icon={<CreditCard className="h-5 w-5" />}
              title="No debts tracked"
              body="Add your loans and credit cards to see your pay-down progress."
            />
          </div>
        )}
        {summary.data?.by_debt.map((d) => {
          const original = toNum(d.original_principal);
          const cur = toNum(d.current_balance);
          const paid = Math.max(0, original - cur);
          const progress = original > 0 ? paid / original : 0;
          const promoActive =
            d.promo_apr !== null && d.promo_ends_on !== null && d.promo_ends_on >= new Date().toISOString().slice(0, 10);
          return (
            <Card key={d.id} className="p-5 transition hover:shadow-card-hover">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate font-semibold text-slate-900">{d.name}</h3>
                    {promoActive ? (
                      <Badge tone="amber">
                        <TrendingDown className="h-3 w-3" />
                        {toNum(d.promo_apr).toFixed(1)}% until {d.promo_ends_on}
                      </Badge>
                    ) : (
                      d.interest_rate_apr && (
                        <Badge tone={toNum(d.interest_rate_apr) > 20 ? "rose" : "slate"}>
                          {toNum(d.interest_rate_apr).toFixed(1)}% APR
                        </Badge>
                      )
                    )}
                  </div>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="text-xl font-bold tracking-tight nums">{fmtMoney(cur, currency)}</span>
                    <span className="text-xs text-slate-500 nums">of {fmtMoney(original, currency)}</span>
                  </div>
                </div>
                <button
                  onClick={() => remove.mutate(d.id)}
                  className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                  title="Delete"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>

              <ProgressBar value={progress} tone="emerald" className="mt-4" />

              <div className="mt-4 grid grid-cols-3 gap-3 border-t border-slate-100 pt-3 text-xs">
                <div>
                  <div className="text-slate-500">Min pay</div>
                  <div className="mt-0.5 font-medium nums">
                    {d.minimum_payment ? fmtMoney(d.minimum_payment, currency) : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Due</div>
                  <div className="mt-0.5 font-medium">
                    {d.due_day_of_month ? `Day ${d.due_day_of_month}` : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Update</div>
                  <Input
                    type="number"
                    step="0.01"
                    defaultValue={d.current_balance}
                    onBlur={(e) => {
                      if (e.target.value !== d.current_balance) {
                        update.mutate({ id: d.id, current_balance: e.target.value });
                      }
                    }}
                    className="mt-0.5 !py-1 text-right"
                  />
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
