import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";

import { api } from "../api/client";
import type { BudgetMonthView } from "../api/types";
import { Card, Input, PageHeader, StatCard } from "../components/ui";
import { fmtMoney, MONTH_NAMES, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

export default function Budget() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const qc = useQueryClient();
  const currency = useUserCurrency();

  const view = useQuery({
    queryKey: ["budget-month", year, month],
    queryFn: async () => (await api.get<BudgetMonthView>(`/api/v1/budgets/month/${year}/${month}`)).data,
  });

  const upsert = useMutation({
    mutationFn: async (vars: { category_id: number; planned: number }) => {
      await api.post("/api/v1/budgets", {
        category_id: vars.category_id, year, month,
        planned_amount: vars.planned,
      }).catch(async (err) => {
        if (err.response?.status === 409) {
          const list = await api.get("/api/v1/budgets", { params: { year, month } });
          const existing = list.data.find(
            (e: { category_id: number; id: number }) => e.category_id === vars.category_id,
          );
          if (existing) {
            await api.patch(`/api/v1/budgets/${existing.id}`, { planned_amount: vars.planned });
          }
        } else {
          throw err;
        }
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["budget-month", year, month] }),
  });

  function shift(delta: number) {
    let m = month + delta;
    let y = year;
    while (m < 1) { m += 12; y -= 1; }
    while (m > 12) { m -= 12; y += 1; }
    setMonth(m); setYear(y);
  }

  return (
    <>
      <PageHeader
        title="Budget"
        subtitle="Plan each category, see how actuals track."
        right={
          <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-card">
            <button
              onClick={() => shift(-1)}
              className="rounded p-1.5 text-slate-600 hover:bg-slate-100"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <div className="min-w-[8.5rem] text-center text-sm font-medium">
              {MONTH_NAMES[month - 1]} {year}
            </div>
            <button
              onClick={() => shift(1)}
              className="rounded p-1.5 text-slate-600 hover:bg-slate-100"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard
          label="Income"
          value={fmtMoney(view.data?.income_actual, currency)}
          accent="emerald"
          hint={`Planned ${fmtMoney(view.data?.income_planned, currency)}`}
        />
        <StatCard
          label="Expenses"
          value={fmtMoney(view.data?.expenses_actual, currency)}
          accent="rose"
          hint={`Planned ${fmtMoney(view.data?.expenses_planned, currency)}`}
        />
        <StatCard
          label="Saving"
          value={fmtMoney(view.data?.saving_actual, currency)}
          accent="sky"
          hint={`Planned ${fmtMoney(view.data?.saving_planned, currency)}`}
        />
      </div>

      <Card className="mt-6 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-5 py-3 text-left font-medium">Category</th>
              <th className="w-40 px-5 py-3 text-right font-medium">Planned</th>
              <th className="w-40 px-5 py-3 text-right font-medium">Actual</th>
              <th className="w-40 px-5 py-3 text-right font-medium">Difference</th>
            </tr>
          </thead>
          <tbody>
            {view.data?.rows.map((row) => {
              const planned = toNum(row.planned);
              const actual = toNum(row.actual);
              const diff = planned - actual;
              const overBudget = diff < 0;
              return (
                <tr key={row.category_id} className="border-t border-slate-100 hover:bg-slate-50/50">
                  <td className="px-5 py-3 font-medium text-slate-900">{row.category_name}</td>
                  <td className="px-5 py-3 text-right">
                    <Input
                      type="number"
                      step="0.01"
                      defaultValue={planned}
                      onBlur={(e) => {
                        const v = parseFloat(e.target.value);
                        if (!Number.isNaN(v) && v !== planned) {
                          upsert.mutate({ category_id: row.category_id, planned: v });
                        }
                      }}
                      className="ml-auto w-32 !py-1 text-right"
                    />
                  </td>
                  <td className="px-5 py-3 text-right nums text-slate-700">{fmtMoney(actual, currency)}</td>
                  <td
                    className={`px-5 py-3 text-right nums font-medium ${
                      overBudget ? "text-rose-600" : "text-emerald-600"
                    }`}
                  >
                    {fmtMoney(diff, currency)}
                  </td>
                </tr>
              );
            })}
            {view.data && view.data.rows.length === 0 && (
              <tr>
                <td colSpan={4} className="px-5 py-10 text-center text-sm text-slate-500">
                  No categories yet. Create them on the Categories page.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </>
  );
}
