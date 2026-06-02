import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  CashflowEntryUpsert,
  CashflowGrid,
  CashflowKind,
  CashflowLine,
} from "../api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Label,
  PageHeader,
  Select,
} from "../components/ui";
import { fmtMoney, fmtMoneySigned, MONTH_NAMES, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

type Override = { lineId: number; year: number; month: number; amount: number };
type OverrideMap = Record<string, Override>;

const overrideKey = (lineId: number, year: number, month: number) =>
  `${lineId}:${year}:${month}`;

function monthIndex(year: number, month: number): number {
  return year * 12 + (month - 1);
}

function defaultStart() {
  const now = new Date();
  return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

export default function Cashflow() {
  const qc = useQueryClient();
  const userCurrency = useUserCurrency();

  const [{ year: startYear, month: startMonth }, setStart] = useState(defaultStart);
  const [months, setMonths] = useState(21);
  const [country, setCountry] = useState<string>("");
  const [currencyFilter, setCurrencyFilter] = useState<string>("");
  const [simulator, setSimulator] = useState(false);
  const [overrides, setOverrides] = useState<OverrideMap>({});
  const [showForm, setShowForm] = useState(false);

  const queryKey = [
    "cashflow-grid",
    startYear,
    startMonth,
    months,
    country,
    currencyFilter,
  ] as const;
  const gridQ = useQuery({
    queryKey,
    queryFn: async () => {
      const params: Record<string, number | string> = {
        start_year: startYear,
        start_month: startMonth,
        months,
      };
      if (country) params.country = country;
      if (currencyFilter) params.currency = currencyFilter;
      return (await api.get<CashflowGrid>("/api/v1/cashflow/grid", { params })).data;
    },
  });

  // List all lines (regardless of filter) to derive the country dropdown.
  const linesQ = useQuery({
    queryKey: ["cashflow-lines"],
    queryFn: async () => (await api.get<CashflowLine[]>("/api/v1/cashflow/lines")).data,
  });

  const upsertSingle = useMutation({
    mutationFn: async (vars: CashflowEntryUpsert) =>
      (await api.put("/api/v1/cashflow/entries", vars)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cashflow-grid"] }),
  });

  const bulkSave = useMutation({
    mutationFn: async (entries: CashflowEntryUpsert[]) =>
      (await api.post("/api/v1/cashflow/entries/bulk", { entries })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cashflow-grid"] });
      setOverrides({});
    },
  });

  const removeLine = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/cashflow/lines/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cashflow-grid"] });
      qc.invalidateQueries({ queryKey: ["cashflow-lines"] });
    },
  });

  const grid = gridQ.data;

  // Resolve the effective amount for (line, year, month).
  // Simulator propagation: an override at month M sticks for every month >= M on the
  // same line, until a later override replaces it. Baseline values are used only when
  // no override at-or-before this cell exists for that line.
  function resolveAmount(line: CashflowLine, year: number, month: number): number {
    const target = monthIndex(year, month);
    if (simulator) {
      let stuck: Override | undefined;
      for (const ov of Object.values(overrides)) {
        if (ov.lineId !== line.id) continue;
        if (monthIndex(ov.year, ov.month) > target) continue;
        if (!stuck || monthIndex(stuck.year, stuck.month) < monthIndex(ov.year, ov.month)) {
          stuck = ov;
        }
      }
      if (stuck) return stuck.amount;
    }
    const baseline = line.entries.find((e) => e.year === year && e.month === month);
    return baseline ? toNum(baseline.amount) : 0;
  }

  // Per-currency, per-month totals computed live so the bottom rows update as you type.
  const totals = useMemo(() => {
    if (!grid) return [] as { currency: string; cells: { income: number; expense: number; net: number }[] }[];
    const byCurrency: Record<string, { income: number; expense: number }[]> = {};
    for (const line of grid.lines) {
      const arr = (byCurrency[line.currency] ??= grid.months.map(() => ({ income: 0, expense: 0 })));
      grid.months.forEach((m, i) => {
        const amt = resolveAmount(line, m.year, m.month);
        if (line.kind === "income") arr[i].income += amt;
        else arr[i].expense += amt;
      });
    }
    return Object.keys(byCurrency)
      .sort()
      .map((cur) => ({
        currency: cur,
        cells: byCurrency[cur].map((c) => ({ ...c, net: c.income - c.expense })),
      }));
    // resolveAmount closes over overrides + simulator, so they are the real deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid, overrides, simulator]);

  function onCellChange(line: CashflowLine, year: number, month: number, raw: string) {
    const v = parseFloat(raw);
    if (Number.isNaN(v)) return;
    if (simulator) {
      setOverrides((prev) => ({
        ...prev,
        [overrideKey(line.id, year, month)]: { lineId: line.id, year, month, amount: v },
      }));
    } else {
      upsertSingle.mutate({ line_id: line.id, year, month, amount: v });
    }
  }

  function onSave() {
    const entries = Object.values(overrides).map((o) => ({
      line_id: o.lineId,
      year: o.year,
      month: o.month,
      amount: o.amount,
    }));
    if (entries.length === 0) return;
    bulkSave.mutate(entries);
  }

  function shiftStart(delta: number) {
    let m = startMonth + delta;
    let y = startYear;
    while (m < 1) { m += 12; y -= 1; }
    while (m > 12) { m -= 12; y += 1; }
    setStart({ year: y, month: m });
  }

  const countryOptions = useMemo(() => {
    const set = new Set<string>();
    for (const ln of linesQ.data ?? []) set.add(ln.country);
    return Array.from(set).sort();
  }, [linesQ.data]);

  const currencyOptions = useMemo(() => {
    const set = new Set<string>();
    for (const ln of linesQ.data ?? []) set.add(ln.currency);
    return Array.from(set).sort();
  }, [linesQ.data]);

  // Group lines by currency then by kind for display.
  const grouped = useMemo(() => {
    if (!grid) return [] as { currency: string; income: CashflowLine[]; expense: CashflowLine[] }[];
    const buckets: Record<string, { income: CashflowLine[]; expense: CashflowLine[] }> = {};
    for (const line of grid.lines) {
      const b = (buckets[line.currency] ??= { income: [], expense: [] });
      b[line.kind].push(line);
    }
    return Object.keys(buckets)
      .sort()
      .map((cur) => ({ currency: cur, ...buckets[cur] }));
  }, [grid]);

  const range = grid
    ? `${MONTH_NAMES[grid.months[0].month - 1]} ${grid.months[0].year} → ${
        MONTH_NAMES[grid.months[grid.months.length - 1].month - 1]
      } ${grid.months[grid.months.length - 1].year}`
    : "";

  return (
    <>
      <PageHeader
        title="Cashflow"
        subtitle="Income minus expenses, month by month, across countries and currencies."
        right={
          <Button onClick={() => setShowForm((s) => !s)}>
            <Plus className="h-4 w-4" />
            {showForm ? "Cancel" : "New line"}
          </Button>
        }
      />

      {showForm && (
        <NewLineForm
          defaultCurrency={userCurrency}
          onCreated={() => {
            setShowForm(false);
            qc.invalidateQueries({ queryKey: ["cashflow-grid"] });
            qc.invalidateQueries({ queryKey: ["cashflow-lines"] });
          }}
        />
      )}

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <Label className="!mb-0">Country</Label>
            <Select
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              className="!w-32"
            >
              <option value="">All</option>
              {countryOptions.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </Select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Label className="!mb-0">Currency</Label>
            <Select
              value={currencyFilter}
              onChange={(e) => setCurrencyFilter(e.target.value)}
              className="!w-32"
            >
              <option value="">All</option>
              {currencyOptions.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </Select>
          </label>
          <div className="flex items-center gap-2 text-sm">
            <Label className="!mb-0">Start</Label>
            <Button variant="secondary" onClick={() => shiftStart(-1)} className="!px-2 !py-1">‹</Button>
            <div className="min-w-[7.5rem] text-center text-sm font-medium">
              {MONTH_NAMES[startMonth - 1]} {startYear}
            </div>
            <Button variant="secondary" onClick={() => shiftStart(1)} className="!px-2 !py-1">›</Button>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <Label className="!mb-0">Window</Label>
            <Button
              variant="secondary"
              onClick={() => setMonths((m) => Math.max(1, m - 12))}
              className="!px-2 !py-1"
              disabled={months <= 12}
            >
              −12 mo
            </Button>
            <span className="min-w-[3rem] text-center nums">{months} mo</span>
            <Button
              variant="secondary"
              onClick={() => setMonths((m) => Math.min(36, m + 12))}
              className="!px-2 !py-1"
              disabled={months >= 36}
            >
              +12 mo
            </Button>
          </div>
          <div className="flex flex-1 items-center justify-end gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={simulator}
                onChange={(e) => {
                  setSimulator(e.target.checked);
                  if (!e.target.checked) setOverrides({});
                }}
              />
              <span className="font-medium">Simulator</span>
              {simulator && <Badge tone="amber">{Object.keys(overrides).length} pending</Badge>}
            </label>
            {simulator && (
              <>
                <Button variant="secondary" onClick={() => setOverrides({})}>Reset</Button>
                <Button
                  onClick={onSave}
                  disabled={Object.keys(overrides).length === 0 || bulkSave.isPending}
                >
                  {bulkSave.isPending
                    ? "Saving…"
                    : `Save ${Object.keys(overrides).length} change${Object.keys(overrides).length === 1 ? "" : "s"}`}
                </Button>
              </>
            )}
          </div>
        </div>
        {range && <div className="mt-2 text-xs text-slate-500">{range}</div>}
      </Card>

      {grid && grid.lines.length === 0 ? (
        <EmptyState
          icon={<Plus className="h-5 w-5" />}
          title="No cashflow lines yet"
          body="Add an income or expense line to start planning your monthly cashflow."
        />
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="sticky left-0 z-10 bg-slate-50 px-4 py-3 text-left font-medium">
                  Line
                </th>
                {grid?.months.map((m) => (
                  <th
                    key={`${m.year}-${m.month}`}
                    className="px-2 py-3 text-right font-medium"
                  >
                    {MONTH_NAMES[m.month - 1].slice(0, 3)} {String(m.year).slice(2)}
                  </th>
                ))}
                <th className="w-10" />
              </tr>
            </thead>
            <tbody>
              {grouped.map((group) => (
                <CurrencyGroup
                  key={group.currency}
                  currency={group.currency}
                  incomeLines={group.income}
                  expenseLines={group.expense}
                  months={grid!.months}
                  simulator={simulator}
                  overrides={overrides}
                  resolveAmount={resolveAmount}
                  onCellChange={onCellChange}
                  onDeleteLine={(id) => removeLine.mutate(id)}
                />
              ))}
              {totals.map((t) => (
                <TotalsBlock key={`totals-${t.currency}`} currency={t.currency} cells={t.cells} />
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}

function CurrencyGroup({
  currency,
  incomeLines,
  expenseLines,
  months,
  simulator,
  overrides,
  resolveAmount,
  onCellChange,
  onDeleteLine,
}: {
  currency: string;
  incomeLines: CashflowLine[];
  expenseLines: CashflowLine[];
  months: { year: number; month: number }[];
  simulator: boolean;
  overrides: OverrideMap;
  resolveAmount: (line: CashflowLine, year: number, month: number) => number;
  onCellChange: (line: CashflowLine, year: number, month: number, raw: string) => void;
  onDeleteLine: (id: number) => void;
}) {
  return (
    <>
      <tr className="bg-slate-100/70">
        <td
          colSpan={months.length + 2}
          className="sticky left-0 z-10 bg-slate-100/70 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-600"
        >
          {currency} · Income
        </td>
      </tr>
      {incomeLines.map((line) => (
        <LineRow
          key={line.id}
          line={line}
          months={months}
          simulator={simulator}
          overrides={overrides}
          resolveAmount={resolveAmount}
          onCellChange={onCellChange}
          onDeleteLine={onDeleteLine}
        />
      ))}
      <tr className="bg-slate-100/70">
        <td
          colSpan={months.length + 2}
          className="sticky left-0 z-10 bg-slate-100/70 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-600"
        >
          {currency} · Expenses
        </td>
      </tr>
      {expenseLines.map((line) => (
        <LineRow
          key={line.id}
          line={line}
          months={months}
          simulator={simulator}
          overrides={overrides}
          resolveAmount={resolveAmount}
          onCellChange={onCellChange}
          onDeleteLine={onDeleteLine}
        />
      ))}
    </>
  );
}

function LineRow({
  line,
  months,
  simulator,
  overrides,
  resolveAmount,
  onCellChange,
  onDeleteLine,
}: {
  line: CashflowLine;
  months: { year: number; month: number }[];
  simulator: boolean;
  overrides: OverrideMap;
  resolveAmount: (line: CashflowLine, year: number, month: number) => number;
  onCellChange: (line: CashflowLine, year: number, month: number, raw: string) => void;
  onDeleteLine: (id: number) => void;
}) {
  return (
    <tr className="border-t border-slate-100 hover:bg-slate-50/40">
      <td className="sticky left-0 z-10 bg-white px-4 py-2 font-medium text-slate-900">
        <div className="flex items-center gap-2">
          <span className="truncate">{line.name}</span>
          <Badge tone="slate">{line.country}</Badge>
        </div>
      </td>
      {months.map((m) => {
        const key = overrideKey(line.id, m.year, m.month);
        const isOverridden = simulator && key in overrides;
        const value = resolveAmount(line, m.year, m.month);
        return (
          <td key={key} className="px-1 py-1 text-right">
            <input
              type="number"
              step="0.01"
              key={`${key}:${simulator ? "sim" : "live"}:${value}`}
              defaultValue={value === 0 ? "" : value}
              onBlur={(e) => onCellChange(line, m.year, m.month, e.target.value)}
              className={`w-24 rounded border border-transparent bg-transparent px-2 py-1 text-right text-sm nums focus:border-brand-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/20 ${
                isOverridden ? "border-amber-300 bg-amber-50 italic" : ""
              }`}
            />
          </td>
        );
      })}
      <td className="px-2 py-2 text-center">
        <button
          onClick={() => onDeleteLine(line.id)}
          className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
          title="Delete line"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </td>
    </tr>
  );
}

function TotalsBlock({
  currency,
  cells,
}: {
  currency: string;
  cells: { income: number; expense: number; net: number }[];
}) {
  return (
    <>
      <tr className="border-t-2 border-slate-300 bg-slate-50">
        <td className="sticky left-0 z-10 bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-700">
          {currency} · Income
        </td>
        {cells.map((c, i) => (
          <td key={`inc-${i}`} className="px-2 py-2 text-right nums text-slate-700">
            {fmtMoney(c.income, currency)}
          </td>
        ))}
        <td />
      </tr>
      <tr className="bg-slate-50">
        <td className="sticky left-0 z-10 bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-700">
          {currency} · Expenses
        </td>
        {cells.map((c, i) => (
          <td key={`exp-${i}`} className="px-2 py-2 text-right nums text-slate-700">
            {fmtMoney(c.expense, currency)}
          </td>
        ))}
        <td />
      </tr>
      <tr className="bg-slate-50">
        <td className="sticky left-0 z-10 bg-slate-50 px-4 py-2 text-xs font-bold uppercase tracking-wide text-slate-900">
          {currency} · Net
        </td>
        {cells.map((c, i) => (
          <td
            key={`net-${i}`}
            className={`px-2 py-2 text-right nums font-semibold ${
              c.net < 0 ? "text-rose-600" : "text-emerald-600"
            }`}
          >
            {fmtMoneySigned(c.net, currency)}
          </td>
        ))}
        <td />
      </tr>
    </>
  );
}

function NewLineForm({
  defaultCurrency,
  onCreated,
}: {
  defaultCurrency: string;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CashflowKind>("expense");
  const [country, setCountry] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/api/v1/cashflow/lines", {
          name,
          kind,
          country: country.toUpperCase(),
          currency: currency.toUpperCase(),
        })
      ).data,
    onSuccess: () => {
      setName("");
      setCountry("");
      onCreated();
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      setError(err.response?.data?.detail ?? "Failed to create line");
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (country.length !== 2) {
      setError("Country must be a 2-letter ISO code (e.g. GB, CL).");
      return;
    }
    if (currency.length !== 3) {
      setError("Currency must be a 3-letter ISO code (e.g. GBP, CLP).");
      return;
    }
    create.mutate();
  }

  return (
    <Card className="mb-4 p-5">
      <h2 className="mb-4 text-sm font-semibold text-slate-900">New cashflow line</h2>
      <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-5">
        <label className="md:col-span-2">
          <Label>Name</Label>
          <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Hurdle" />
        </label>
        <label>
          <Label>Kind</Label>
          <Select value={kind} onChange={(e) => setKind(e.target.value as CashflowKind)}>
            <option value="income">Income</option>
            <option value="expense">Expense</option>
          </Select>
        </label>
        <label>
          <Label>Country</Label>
          <Input
            required
            maxLength={2}
            value={country}
            onChange={(e) => setCountry(e.target.value.toUpperCase())}
            placeholder="GB"
          />
        </label>
        <label>
          <Label>Currency</Label>
          <Input
            required
            maxLength={3}
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            placeholder="GBP"
          />
        </label>
        {error && <div className="md:col-span-5 text-sm text-rose-600">{error}</div>}
        <div className="md:col-span-5">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Saving…" : "Save line"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
