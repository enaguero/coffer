import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarClock,
  CreditCard,
  Pencil,
  Plus,
  Trash2,
  TrendingDown,
  X,
} from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
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
import type { Debt, DebtPlan, DebtPlanCompare, DebtRepaymentType, DebtSummary } from "../api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Label,
  PageHeader,
  ProgressBar,
  Select,
  WarningBanner,
} from "../components/ui";
import { apiErrorDetail } from "../lib/apiError";
import { fmtMoney, toNum } from "../lib/format";
import { useAccountCurrencyMap, useUserCurrency } from "../lib/useCurrency";

// Everything the UI knows per repayment type, in one place — labels, the
// one-line behavior summary, and the per-type form/card switches. Mirrors the
// backend's mechanics matrix.
const TYPE_CONFIG: Record<
  DebtRepaymentType,
  {
    label: string;
    help: string;
    fixedInstallment: boolean; // installment (not minimum payment) is the contractual payment
    showsApr: boolean;
    showsPromo: boolean;
    requiresPrincipal: boolean;
    requiresBalance: boolean;
    estimated: boolean; // the rate is inferred — figures carry an "estimated" badge
  }
> = {
  revolving: {
    label: "Revolving",
    help: "Interest accrues on the current balance; you pay the minimum plus any extra.",
    fixedInstallment: false,
    showsApr: true,
    showsPromo: true,
    requiresPrincipal: false,
    requiresBalance: false,
    estimated: false,
  },
  amortized: {
    label: "Amortized",
    help: "Fixed installment until the end date; interest accrues on the current balance.",
    fixedInstallment: true,
    showsApr: true,
    showsPromo: false,
    requiresPrincipal: false,
    requiresBalance: false,
    estimated: false,
  },
  flat: {
    label: "Flat interest",
    help: "Interest is fixed on the original principal — installments never shrink, so prepaying saves no interest.",
    fixedInstallment: true,
    showsApr: true,
    showsPromo: false,
    requiresPrincipal: true,
    requiresBalance: false,
    estimated: false,
  },
  statement_only: {
    label: "Statement-only",
    help: "Only the installment, balance, and end date are known — the rate is inferred and every figure is estimated.",
    fixedInstallment: true,
    showsApr: false, // statement-only infers its rate
    showsPromo: false,
    requiresPrincipal: false,
    requiresBalance: true,
    estimated: true,
  },
};

const COMMON_CURRENCIES = ["AUD", "CAD", "CHF", "CLP", "EUR", "GBP", "JPY", "USD"];

type DebtPayload = Partial<Omit<Debt, "id">>;

const SAVE_FALLBACK = "That change couldn't be saved — check the values.";

type Strategy = "optimal" | "avalanche" | "snowball";

interface SnowflakeEntry {
  month: number; // plan month, 1 = next month
  amount: string;
}

function PlannerPanel({ currency, hasDebts }: { currency: string; hasDebts: boolean }) {
  const [strategy, setStrategy] = useState<Strategy>("optimal");
  const [extraInput, setExtraInput] = useState("100");
  const [extra, setExtra] = useState("100");
  const [snowflakes, setSnowflakes] = useState<SnowflakeEntry[]>([]);
  const [sfMonth, setSfMonth] = useState("");
  const [sfAmount, setSfAmount] = useState("");
  const [showFullSchedule, setShowFullSchedule] = useState(false);

  const plan = useQuery({
    queryKey: ["debt-plan", extra, snowflakes],
    enabled: hasDebts,
    queryFn: async () =>
      (
        await api.post<DebtPlanCompare>("/api/v1/debts/plan", {
          extra_monthly: extra || "0",
          snowflakes: snowflakes.map((s) => ({ month: s.month, amount: s.amount })),
        })
      ).data,
  });

  const chosen: DebtPlan | undefined = plan.data?.[strategy];
  const compareKey: Strategy = strategy === "avalanche" ? "snowball" : "avalanche";
  const other: DebtPlan | undefined = plan.data?.[compareKey];
  const baseline = plan.data?.minimum;

  // Derived render data can be 600 rows deep — rebuild it only when the
  // chosen plan changes (plan response + strategy), not on every keystroke in
  // the extras form.
  const { chartData, schedule, hasUncommitted, paymentsByMonth } = useMemo(() => {
    const schedule = chosen?.schedule ?? [];
    return {
      chartData:
        chosen?.balance_series.map((p) => ({ on: p.on.slice(0, 7), balance: toNum(p.balance) })) ?? [],
      schedule,
      hasUncommitted: schedule.some((m) => toNum(m.uncommitted) > 0),
      // month → (debt_id → amount) for the schedule table.
      paymentsByMonth: new Map(
        schedule.map((m) => [m.month, new Map(m.payments.map((p) => [p.debt_id, p.amount]))]),
      ),
    };
  }, [chosen]);
  const visibleSchedule = showFullSchedule ? schedule : schedule.slice(0, 24);

  if (!hasDebts) return null;

  function addSnowflake() {
    const month = Number(sfMonth);
    if (!Number.isInteger(month) || month < 1 || month > 600 || !(toNum(sfAmount) > 0)) return;
    // One extra per month — re-adding the same month replaces it (the backend
    // keeps the last entry per month anyway).
    setSnowflakes((prev) =>
      [...prev.filter((s) => s.month !== month), { month, amount: sfAmount }].sort(
        (a, b) => a.month - b.month,
      ),
    );
    setSfMonth("");
    setSfAmount("");
  }

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
            {(["optimal", "avalanche", "snowball"] as const).map((s) => (
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

      <div className="mt-4 flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
        <label>
          <Label>One-off extra — month #</Label>
          <Input
            value={sfMonth}
            onChange={(e) => setSfMonth(e.target.value)}
            placeholder="1 = next month"
            className="w-32 text-right"
          />
        </label>
        <label>
          <Label>Amount</Label>
          <Input
            value={sfAmount}
            onChange={(e) => setSfAmount(e.target.value)}
            className="w-28 text-right"
          />
        </label>
        <Button type="button" variant="secondary" onClick={addSnowflake} className="!py-2">
          <Plus className="h-4 w-4" />
          Add
        </Button>
        {snowflakes.map((s) => (
          <span
            key={s.month}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200"
          >
            Month {s.month}: {fmtMoney(s.amount, currency)}
            <button
              onClick={() => setSnowflakes((prev) => prev.filter((p) => p.month !== s.month))}
              className="text-brand-400 hover:text-brand-700"
              title="Remove"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        {snowflakes.length === 0 && (
          <span className="pb-2 text-xs text-slate-400">
            One-off extra payments (bonus, tax refund) — added on top of the monthly budget.
          </span>
        )}
      </div>

      {(plan.data?.excluded_currencies.length ?? 0) > 0 && (
        <WarningBanner className="mt-4">
          Debts in {plan.data?.excluded_currencies.join(", ")} are{" "}
          <strong>excluded from this plan</strong> — no exchange rate saved. Add rates on the Net
          worth page.
        </WarningBanner>
      )}

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
                vs {compareKey}
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
                    <td className="py-2">
                      {d.name}
                      {d.currency && d.currency !== currency && (
                        <span className="ml-1.5 text-xs text-slate-400">{d.currency}</span>
                      )}
                    </td>
                    <td className="py-2 text-right nums">{d.payoff_date ?? "never at this budget"}</td>
                    <td className="py-2 text-right nums">{fmtMoney(d.interest_paid, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {strategy === "optimal" && schedule.length > 0 && (
            <div className="mt-5">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Monthly payment schedule
                </h3>
                {schedule.length > 24 && (
                  <button
                    onClick={() => setShowFullSchedule((s) => !s)}
                    className="text-xs font-medium text-brand-600 hover:underline"
                  >
                    {showFullSchedule ? "Show first 24 months" : `Show all ${schedule.length} months`}
                  </button>
                )}
              </div>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="py-2 text-left font-medium">Month</th>
                      {chosen.debts.map((d) => (
                        <th key={d.id} className="py-2 text-right font-medium">
                          {d.name}
                          <span className="block text-[10px] font-normal normal-case text-slate-400">
                            {/* Amounts are display-denominated (converted once at
                                plan start) — foreign debts note their own currency. */}
                            {d.currency && d.currency !== currency
                              ? `${d.currency} → ${currency}`
                              : currency}
                          </span>
                        </th>
                      ))}
                      {hasUncommitted && (
                        <th className="py-2 text-right font-medium">
                          Uncommitted
                          <span className="block text-[10px] font-normal normal-case text-slate-400">
                            {currency}
                          </span>
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {visibleSchedule.map((m) => {
                      const byDebt = paymentsByMonth.get(m.month);
                      return (
                        <tr key={m.month} className="border-t border-slate-100">
                          <td className="py-1.5 nums">{m.month.slice(0, 7)}</td>
                          {chosen.debts.map((d) => (
                            <td key={d.id} className="py-1.5 text-right nums">
                              {byDebt?.has(d.id) ? fmtMoney(byDebt.get(d.id), currency) : "—"}
                            </td>
                          ))}
                          {hasUncommitted && (
                            <td className="py-1.5 text-right nums text-slate-500">
                              {toNum(m.uncommitted) > 0 ? fmtMoney(m.uncommitted, currency) : "—"}
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {!showFullSchedule && schedule.length > 24 && (
                  <div className="mt-1 text-xs text-slate-400">
                    Showing the first 24 of {schedule.length} months.
                  </div>
                )}
              </div>
            </div>
          )}

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
  const accountCurrencies = useAccountCurrencyMap();
  const summary = useQuery({
    queryKey: ["debt-summary"],
    queryFn: async () => (await api.get<DebtSummary>("/api/v1/debts/summary")).data,
  });
  const [error, setError] = useState<string | null>(null);
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["debt-summary"] });
    qc.invalidateQueries({ queryKey: ["debt-plan"] });
  };
  const create = useMutation({
    mutationFn: async (payload: DebtPayload) => (await api.post("/api/v1/debts", payload)).data,
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err) => setError(apiErrorDetail(err, SAVE_FALLBACK)),
  });
  const update = useMutation({
    mutationFn: async ({ id, ...body }: { id: number } & DebtPayload) =>
      (await api.patch(`/api/v1/debts/${id}`, body)).data,
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err) => setError(apiErrorDetail(err, SAVE_FALLBACK)),
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/debts/${id}`),
    onSuccess: invalidate,
  });

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [repaymentType, setRepaymentType] = useState<DebtRepaymentType>("revolving");
  const [debtCurrency, setDebtCurrency] = useState("");
  const [original, setOriginal] = useState("0");
  const [current, setCurrent] = useState("0");
  const [apr, setApr] = useState("");
  const [promoApr, setPromoApr] = useState("");
  const [promoEnds, setPromoEnds] = useState("");
  const [minPay, setMinPay] = useState("");
  const [installment, setInstallment] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [dueDay, setDueDay] = useState("");

  // Installment supersedes the minimum payment for the fixed-installment types.
  const typeCfg = TYPE_CONFIG[repaymentType];
  const isFixed = typeCfg.fixedInstallment;
  const showApr = typeCfg.showsApr;
  const showPromo = typeCfg.showsPromo;

  const currencyOptions = Array.from(
    new Set([
      ...Array.from(accountCurrencies.values()),
      ...COMMON_CURRENCIES,
      ...(debtCurrency ? [debtCurrency] : []),
    ]),
  ).sort();

  function resetForm() {
    setEditingId(null);
    setName("");
    setRepaymentType("revolving");
    setDebtCurrency("");
    setOriginal("0");
    setCurrent("0");
    setApr("");
    setPromoApr("");
    setPromoEnds("");
    setMinPay("");
    setInstallment("");
    setEndsOn("");
    setDueDay("");
  }

  function startEdit(d: Debt) {
    setEditingId(d.id);
    setName(d.name);
    setRepaymentType(d.repayment_type);
    // Pre-select the saved currency only when set — "" keeps submitting null.
    setDebtCurrency(d.currency ?? "");
    setOriginal(d.original_principal);
    setCurrent(d.current_balance);
    setApr(d.interest_rate_apr ?? "");
    setPromoApr(d.promo_apr ?? "");
    setPromoEnds(d.promo_ends_on ?? "");
    setMinPay(d.minimum_payment ?? "");
    setInstallment(d.installment_amount ?? "");
    setEndsOn(d.ends_on ?? "");
    setDueDay(d.due_day_of_month != null ? String(d.due_day_of_month) : "");
    setError(null);
    setShowForm(true);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    // Hidden fields submit null — the form is the source of truth for a save.
    const payload: DebtPayload = {
      name,
      repayment_type: repaymentType,
      currency: debtCurrency || null,
      original_principal: original || "0",
      current_balance: current || "0",
      interest_rate_apr: showApr && apr ? apr : null,
      promo_apr: showPromo && promoApr ? promoApr : null,
      promo_ends_on: showPromo && promoEnds ? promoEnds : null,
      minimum_payment: !isFixed && minPay ? minPay : null,
      installment_amount: isFixed && installment ? installment : null,
      ends_on: isFixed && endsOn ? endsOn : null,
      due_day_of_month: dueDay ? Number(dueDay) : null,
    };
    const done = {
      onSuccess: () => {
        resetForm();
        setShowForm(false);
      },
    };
    if (editingId != null) update.mutate({ id: editingId, ...payload }, done);
    else create.mutate(payload, done);
  }

  const items = summary.data?.by_debt ?? [];
  // Pay-down arithmetic only makes sense within one currency — foreign-currency
  // debts are counted in total_owed by the backend (converted) but stay out of
  // the original-vs-current progress math here.
  const displayItems = items.filter((d) => !d.currency || d.currency === currency);
  const foreignCount = items.length - displayItems.length;
  const totalOwed = toNum(summary.data?.total_owed);
  const displayOwed = displayItems.reduce((acc, d) => acc + toNum(d.current_balance), 0);
  const totalOriginal = displayItems.reduce((acc, d) => acc + toNum(d.original_principal), 0);
  const paidDownPct = totalOriginal > 0 ? 1 - displayOwed / totalOriginal : 0;

  return (
    <>
      <PageHeader
        title="Debts"
        subtitle={`${items.length} obligations · ${fmtMoney(totalOwed, currency)} owed`}
        right={
          <Button
            onClick={() => {
              resetForm();
              setShowForm((s) => !s);
            }}
          >
            <Plus className="h-4 w-4" />
            {showForm ? "Cancel" : "Add debt"}
          </Button>
        }
      />

      {error && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      {(summary.data?.excluded_currencies.length ?? 0) > 0 && (
        <WarningBanner className="mb-4">
          Debts in {summary.data?.excluded_currencies.join(", ")} are{" "}
          <strong>excluded from the totals</strong> — no exchange rate saved. Add rates on the Net
          worth page.
        </WarningBanner>
      )}

      <Card className="p-6">
        <div className="flex items-baseline justify-between">
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Pay-down progress
            </div>
            <div className="mt-1 text-2xl font-bold tracking-tight nums">
              {fmtMoney(totalOriginal - displayOwed, currency)}{" "}
              <span className="text-base font-medium text-slate-400">
                of {fmtMoney(totalOriginal, currency)} paid
              </span>
            </div>
          </div>
          <Badge tone="brand">{Math.round(paidDownPct * 100)}%</Badge>
        </div>
        <ProgressBar value={paidDownPct} tone="emerald" className="mt-4" />
        {foreignCount > 0 && (
          <div className="mt-2 text-xs text-slate-400">
            Progress covers {currency}-denominated debts; {foreignCount} in other currencies not
            included.
          </div>
        )}
      </Card>

      {showForm && (
        <Card className="mt-6 p-6">
          <h2 className="mb-4 text-sm font-semibold text-slate-900">
            {editingId != null ? "Edit debt" : "New debt"}
          </h2>
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <label className="md:col-span-2">
              <Label>Name</Label>
              <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Visa card" />
            </label>
            <label className="md:col-span-2">
              <Label>Repayment type</Label>
              <Select
                value={repaymentType}
                onChange={(e) => setRepaymentType(e.target.value as DebtRepaymentType)}
              >
                {(Object.keys(TYPE_CONFIG) as DebtRepaymentType[]).map((t) => (
                  <option key={t} value={t}>
                    {TYPE_CONFIG[t].label}
                  </option>
                ))}
              </Select>
              <span className="mt-1 block text-xs text-slate-400">{typeCfg.help}</span>
            </label>
            <label>
              <Label>Original principal{typeCfg.requiresPrincipal ? " (required)" : ""}</Label>
              <Input required={typeCfg.requiresPrincipal} value={original} onChange={(e) => setOriginal(e.target.value)} />
            </label>
            <label>
              <Label>Current balance{typeCfg.requiresBalance ? " (required)" : ""}</Label>
              <Input value={current} onChange={(e) => setCurrent(e.target.value)} />
            </label>
            <label>
              <Label>Currency</Label>
              <Select value={debtCurrency} onChange={(e) => setDebtCurrency(e.target.value)}>
                <option value="">Same as display currency ({currency})</option>
                {currencyOptions.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </label>
            <label>
              <Label>Due day</Label>
              <Input value={dueDay} onChange={(e) => setDueDay(e.target.value)} placeholder="1–31" />
            </label>
            {showApr && (
              <label>
                <Label>{repaymentType === "flat" ? "APR % (on original principal)" : "APR %"}</Label>
                <Input value={apr} onChange={(e) => setApr(e.target.value)} />
              </label>
            )}
            {showPromo && (
              <>
                <label>
                  <Label>Promo APR % (0% offers)</Label>
                  <Input value={promoApr} onChange={(e) => setPromoApr(e.target.value)} placeholder="e.g. 0" />
                </label>
                <label>
                  <Label>Promo ends</Label>
                  <Input type="date" value={promoEnds} onChange={(e) => setPromoEnds(e.target.value)} />
                </label>
              </>
            )}
            {!isFixed && (
              <label>
                <Label>Min payment</Label>
                <Input value={minPay} onChange={(e) => setMinPay(e.target.value)} />
              </label>
            )}
            {isFixed && (
              <>
                <label>
                  <Label>Installment / month (required)</Label>
                  <Input required value={installment} onChange={(e) => setInstallment(e.target.value)} />
                </label>
                <label>
                  <Label>End date (required)</Label>
                  <Input type="date" required value={endsOn} onChange={(e) => setEndsOn(e.target.value)} />
                </label>
              </>
            )}
            <div className="md:col-span-4">
              <Button type="submit">{editingId != null ? "Save changes" : "Save debt"}</Button>
            </div>
          </form>
        </Card>
      )}

      <PlannerPanel currency={currency} hasDebts={items.length > 0} />

      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        {items.length === 0 && (
          <div className="md:col-span-2">
            <EmptyState
              icon={<CreditCard className="h-5 w-5" />}
              title="No debts tracked"
              body="Add your loans and credit cards to see your pay-down progress."
            />
          </div>
        )}
        {items.map((d) => {
          const cfg = TYPE_CONFIG[d.repayment_type];
          const pay = cfg.fixedInstallment ? d.installment_amount : d.minimum_payment;
          const dCcy = d.currency ?? currency;
          const originalAmt = toNum(d.original_principal);
          const cur = toNum(d.current_balance);
          const paid = Math.max(0, originalAmt - cur);
          const progress = originalAmt > 0 ? paid / originalAmt : 0;
          const promoActive =
            d.promo_apr !== null && d.promo_ends_on !== null && d.promo_ends_on >= new Date().toISOString().slice(0, 10);
          return (
            <Card key={d.id} className="p-5 transition hover:shadow-card-hover">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate font-semibold text-slate-900">{d.name}</h3>
                    <Badge tone="slate">{cfg.label}</Badge>
                    {cfg.estimated && <Badge tone="amber">estimated</Badge>}
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
                    <span className="text-xl font-bold tracking-tight nums">{fmtMoney(cur, dCcy)}</span>
                    <span className="text-xs text-slate-500 nums">of {fmtMoney(originalAmt, dCcy)}</span>
                  </div>
                  {!d.converted && (
                    <div className="mt-1">
                      <Badge tone="amber">no rate — excluded from totals</Badge>
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  <button
                    onClick={() => startEdit(d)}
                    className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                    title="Edit"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => remove.mutate(d.id)}
                    className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                    title="Delete"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>

              <ProgressBar value={progress} tone="emerald" className="mt-4" />

              <div className="mt-4 grid grid-cols-3 gap-3 border-t border-slate-100 pt-3 text-xs">
                <div>
                  <div className="text-slate-500">{cfg.fixedInstallment ? "Installment" : "Min pay"}</div>
                  <div className="mt-0.5 font-medium nums">{pay ? fmtMoney(pay, dCcy) : "—"}</div>
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
