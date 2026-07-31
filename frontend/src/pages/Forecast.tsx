import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Radar, ShieldAlert, Wallet } from "lucide-react";
import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";
import type { Forecast as ForecastData, RecurringItem } from "../api/types";
import { Badge, Card, EmptyState, Input, Label, PageHeader, StatCard } from "../components/ui";
import { fmtMoney, fmtMoneySigned, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

const CADENCE_LABEL: Record<string, string> = {
  weekly: "wk",
  fortnightly: "2wk",
  "four-weekly": "4wk",
  monthly: "mo",
  quarterly: "qtr",
  annual: "yr",
};

export default function Forecast() {
  const currency = useUserCurrency();
  const [reserveInput, setReserveInput] = useState("200");
  const [reserve, setReserve] = useState("200");

  const forecast = useQuery({
    queryKey: ["forecast", reserve],
    queryFn: async () =>
      (await api.get<ForecastData>(`/api/v1/insights/forecast?days=60&reserve=${reserve || "0"}`)).data,
  });
  const recurring = useQuery({
    queryKey: ["recurring"],
    queryFn: async () => (await api.get<RecurringItem[]>("/api/v1/insights/recurring")).data,
  });

  const f = forecast.data;
  const chartData = f?.series.map((p) => ({ on: p.on.slice(5), balance: toNum(p.balance) })) ?? [];
  const upcoming = (f?.events ?? []).slice(0, 15);

  return (
    <>
      <PageHeader
        title="Forecast"
        subtitle="The next 60 days, projected from your detected recurring transactions."
        right={
          <label className="flex items-end gap-2">
            <span>
              <Label>Reserve floor</Label>
              <Input
                value={reserveInput}
                onChange={(e) => setReserveInput(e.target.value)}
                onBlur={() => setReserve(reserveInput)}
                onKeyDown={(e) => e.key === "Enter" && setReserve(reserveInput)}
                className="w-28 text-right"
              />
            </span>
          </label>
        }
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Liquid balance today"
          value={fmtMoney(f?.start_balance, currency)}
          icon={<Wallet className="h-5 w-5" />}
          accent="sky"
          hint="checking + savings + cash"
        />
        <StatCard
          label="Projected low point"
          value={fmtMoney(f?.min_balance, currency)}
          icon={<Radar className="h-5 w-5" />}
          accent={f && toNum(f.min_balance) < toNum(f.reserve) ? "rose" : "emerald"}
          hint={f?.min_balance_date ? `on ${f.min_balance_date}` : undefined}
        />
        <StatCard
          label="Safe to commit"
          value={fmtMoney(f?.safe_to_commit, currency)}
          icon={<Wallet className="h-5 w-5" />}
          accent="brand"
          hint="spare after covering bills + reserve"
        />
        <StatCard
          label="Reserve breach"
          value={f?.first_below_reserve ?? "none projected"}
          icon={<ShieldAlert className="h-5 w-5" />}
          accent={f?.first_below_reserve ? "amber" : "emerald"}
          hint={f?.first_below_zero ? `below zero: ${f.first_below_zero}` : undefined}
        />
      </div>

      <Card className="mt-6 p-6">
        <h2 className="text-sm font-semibold text-slate-900">Projected balance</h2>
        {chartData.length > 1 ? (
          <div className="mt-4 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="on" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} width={70} tickFormatter={(v) => fmtMoney(v, currency)} />
                <Tooltip formatter={(v) => fmtMoney(v as number, currency)} />
                <ReferenceLine y={toNum(f?.reserve)} stroke="#f59e0b" strokeDasharray="4 4" />
                <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="balance" stroke="#4f46e5" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            Import a few months of statements so recurring bills can be detected.
          </p>
        )}
      </Card>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-6">
          <h2 className="text-sm font-semibold text-slate-900">Upcoming bills & income</h2>
          {upcoming.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500">Nothing detected yet.</p>
          ) : (
            <ul className="mt-3 divide-y divide-slate-100">
              {upcoming.map((e, i) => (
                <li key={i} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="w-20 shrink-0 text-xs text-slate-500 nums">{e.on}</span>
                    <span className="truncate">{e.description}</span>
                  </div>
                  <span
                    className={`nums font-medium ${e.is_income ? "text-emerald-600" : "text-slate-700"}`}
                  >
                    {fmtMoneySigned(e.amount, currency)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {(f?.due_markers.length ?? 0) > 0 && (
            <div className="mt-4 border-t border-slate-100 pt-3">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Debt due dates
              </div>
              <ul className="mt-2 space-y-1">
                {f?.due_markers.map((m, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm text-slate-600">
                    <CalendarClock className="h-3.5 w-3.5 text-amber-600" />
                    <span className="w-20 text-xs text-slate-500 nums">{m.on}</span>
                    <span>{m.name}</span>
                    {m.minimum_payment && (
                      <span className="ml-auto nums">{fmtMoney(m.minimum_payment, currency)} min</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <Card className="p-6">
          <h2 className="text-sm font-semibold text-slate-900">Detected recurring transactions</h2>
          {(recurring.data?.length ?? 0) === 0 ? (
            <div className="mt-3">
              <EmptyState
                icon={<Radar className="h-5 w-5" />}
                title="No recurring patterns yet"
                body="Detection needs at least 3 occurrences of a merchant — keep importing statements."
              />
            </div>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="py-2 text-left font-medium">Merchant</th>
                    <th className="py-2 text-left font-medium">Cadence</th>
                    <th className="py-2 text-right font-medium">Typical</th>
                    <th className="py-2 text-right font-medium">/month</th>
                    <th className="py-2 text-right font-medium">Next</th>
                  </tr>
                </thead>
                <tbody>
                  {recurring.data?.slice(0, 20).map((r, i) => (
                    <tr key={i} className={`border-t border-slate-100 ${r.active ? "" : "opacity-50"}`}>
                      <td className="max-w-[180px] truncate py-2" title={r.description}>
                        {r.description}
                        {!r.active && <span className="ml-1 text-xs text-slate-400">(lapsed)</span>}
                      </td>
                      <td className="py-2">
                        <Badge tone={r.confidence >= 0.7 ? "emerald" : "slate"}>
                          {CADENCE_LABEL[r.cadence] ?? r.cadence}
                        </Badge>
                      </td>
                      <td className="py-2 text-right nums">{fmtMoneySigned(r.typical_amount, currency)}</td>
                      <td className="py-2 text-right nums text-slate-500">
                        {fmtMoneySigned(r.monthly_equivalent, currency)}
                      </td>
                      <td className="py-2 text-right text-xs text-slate-500 nums">{r.next_expected}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
