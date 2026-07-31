import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Landmark, PiggyBank, Scale } from "lucide-react";
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
import type { Account, Allowances, NetWorth as NetWorthData } from "../api/types";
import { Badge, Button, Card, Input, Label, PageHeader, ProgressBar, Select, StatCard } from "../components/ui";
import { fmtMoney, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

const WRAPPER_LABEL: Record<string, string> = {
  isa: "ISA allowance",
  lisa: "Lifetime ISA allowance",
  pension: "Pension annual allowance",
};

const SOURCE_TONE: Record<string, "emerald" | "sky" | "slate"> = {
  statement: "emerald",
  manual: "sky",
  derived: "slate",
  opening: "slate",
};

export default function NetWorth() {
  const qc = useQueryClient();
  const currency = useUserCurrency();
  const networth = useQuery({
    queryKey: ["networth"],
    queryFn: async () => (await api.get<NetWorthData>("/api/v1/insights/networth")).data,
  });
  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });
  const allowances = useQuery({
    queryKey: ["allowances"],
    queryFn: async () => (await api.get<Allowances>("/api/v1/insights/allowances")).data,
    // Only ask once the accounts list shows a wrapped account — saves a wasted
    // request for users with no UK wrappers.
    enabled: accounts.data?.some((a) => a.uk_wrapper != null) ?? false,
  });

  const [valAccount, setValAccount] = useState("");
  const [valDate, setValDate] = useState(new Date().toISOString().slice(0, 10));
  const [valAmount, setValAmount] = useState("");

  const addValuation = useMutation({
    mutationFn: async () =>
      api.post(`/api/v1/accounts/${valAccount}/snapshots`, { as_of: valDate, balance: valAmount }),
    onSuccess: () => {
      setValAmount("");
      qc.invalidateQueries({ queryKey: ["networth"] });
    },
  });

  function onAddValuation(e: FormEvent) {
    e.preventDefault();
    if (!valAccount || !valAmount) return;
    addValuation.mutate();
  }

  const nw = networth.data;
  const chartData =
    nw?.series.map((p) => ({
      on: p.on.slice(0, 7),
      net: toNum(p.net),
      assets: toNum(p.assets),
      liabilities: toNum(p.liabilities),
    })) ?? [];
  const drifting = (nw?.accounts ?? []).filter((a) => a.drift !== null);

  return (
    <>
      <PageHeader
        title="Net worth"
        subtitle="Everything you own minus everything you owe — built from statement balances and valuations."
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatCard
          label="Net worth"
          value={fmtMoney(nw?.net, currency)}
          icon={<Scale className="h-5 w-5" />}
          accent={nw && toNum(nw.net) >= 0 ? "brand" : "rose"}
        />
        <StatCard
          label="Assets"
          value={fmtMoney(nw?.assets, currency)}
          icon={<PiggyBank className="h-5 w-5" />}
          accent="emerald"
        />
        <StatCard
          label="Liabilities"
          value={fmtMoney(nw?.liabilities, currency)}
          icon={<Landmark className="h-5 w-5" />}
          accent="rose"
        />
      </div>

      {drifting.length > 0 && (
        <div className="mt-4 space-y-2">
          {drifting.map((a) => (
            <div
              key={a.id}
              className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
            >
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>
                <strong>{a.name}</strong>: the bank's stated balance differs from your imported
                transactions by {fmtMoney(a.drift, a.currency)} — a statement may be missing.
              </span>
            </div>
          ))}
        </div>
      )}

      <Card className="mt-6 p-6">
        <h2 className="text-sm font-semibold text-slate-900">Trend</h2>
        {chartData.length > 1 ? (
          <div className="mt-4 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <defs>
                  <linearGradient id="nwFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="on" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} width={80} tickFormatter={(v) => fmtMoney(v, currency)} />
                <Tooltip formatter={(v) => fmtMoney(v as number, currency)} />
                <Area type="monotone" dataKey="net" stroke="#10b981" fill="url(#nwFill)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            The trend builds as statements and valuations accumulate — worthless at month one,
            priceless at month twenty-four.
          </p>
        )}
      </Card>

      {(allowances.data?.meters.length ?? 0) > 0 && (
        <Card className="mt-6 p-6">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-semibold text-slate-900">UK tax-year allowances</h2>
            <span className="text-xs text-slate-500 nums">
              {allowances.data?.tax_year_start} → {allowances.data?.tax_year_end} ·{" "}
              <strong>{allowances.data?.days_left} days left</strong> — unused allowance doesn't
              roll over
            </span>
          </div>
          <div className="mt-4 grid grid-cols-1 gap-5 md:grid-cols-3">
            {allowances.data?.meters.map((m) => {
              const usedPct = toNum(m.used) / Math.max(toNum(m.allowance), 1);
              return (
                <div key={m.wrapper}>
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="font-medium text-slate-900">
                      {WRAPPER_LABEL[m.wrapper] ?? m.wrapper}
                    </span>
                    <span className="text-xs text-slate-500 nums">
                      {fmtMoney(m.used, "GBP")} / {fmtMoney(m.allowance, "GBP")}
                    </span>
                  </div>
                  <ProgressBar
                    value={usedPct}
                    tone={usedPct >= 1 ? "rose" : usedPct >= 0.75 ? "amber" : "emerald"}
                    className="mt-2"
                  />
                  <div className="mt-1 text-xs text-slate-500 nums">
                    {fmtMoney(m.remaining, "GBP")} remaining
                    {m.wrapper === "isa" && toNum(m.lisa_portion) > 0 && (
                      <> (includes {fmtMoney(m.lisa_portion, "GBP")} via LISA)</>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Counted from positive transactions into wrapper-tagged GBP accounts this tax year
            (rows described as interest are excluded). Transfers between your own ISAs may be
            miscounted, and pension figures omit employer contributions and tax relief — treat
            the pension meter as a floor. Tag accounts on the Accounts page.
          </p>
        </Card>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="p-6 lg:col-span-2">
          <h2 className="text-sm font-semibold text-slate-900">Accounts</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 text-left font-medium">Account</th>
                  <th className="py-2 text-left font-medium">As of</th>
                  <th className="py-2 text-right font-medium">Balance</th>
                </tr>
              </thead>
              <tbody>
                {nw?.accounts.map((a) => (
                  <tr key={a.id} className="border-t border-slate-100">
                    <td className="py-2">
                      {a.name}{" "}
                      <Badge tone={SOURCE_TONE[a.source] ?? "slate"}>{a.source}</Badge>
                    </td>
                    <td className="py-2 text-xs text-slate-500 nums">{a.as_of ?? "—"}</td>
                    <td
                      className={`py-2 text-right nums font-medium ${
                        toNum(a.balance) < 0 ? "text-rose-600" : ""
                      }`}
                    >
                      {fmtMoney(a.balance, a.currency)}
                    </td>
                  </tr>
                ))}
                {nw?.register_debts.map((d) => (
                  <tr key={`debt-${d.id}`} className="border-t border-slate-100">
                    <td className="py-2">
                      {d.name} <Badge tone="rose">debt</Badge>
                    </td>
                    <td className="py-2 text-xs text-slate-500">register</td>
                    <td className="py-2 text-right nums font-medium text-rose-600">
                      −{fmtMoney(d.balance, currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="p-6">
          <h2 className="text-sm font-semibold text-slate-900">Record a valuation</h2>
          <p className="mt-1 text-xs text-slate-500">
            For assets without statements — pension, property, ISA. Create an account of type
            "other" for each, then value it here every so often.
          </p>
          <form onSubmit={onAddValuation} className="mt-4 space-y-3">
            <label className="block">
              <Label>Account</Label>
              <Select value={valAccount} onChange={(e) => setValAccount(e.target.value)} required>
                <option value="">Choose…</option>
                {accounts.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </label>
            <label className="block">
              <Label>Date</Label>
              <Input type="date" value={valDate} onChange={(e) => setValDate(e.target.value)} required />
            </label>
            <label className="block">
              <Label>Value</Label>
              <Input
                value={valAmount}
                onChange={(e) => setValAmount(e.target.value)}
                placeholder="e.g. 42000"
                required
              />
            </label>
            <Button type="submit" disabled={addValuation.isPending}>
              Save valuation
            </Button>
          </form>
        </Card>
      </div>
    </>
  );
}
