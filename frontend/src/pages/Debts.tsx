import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CreditCard, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Debt, DebtSummary } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Label, PageHeader, ProgressBar } from "../components/ui";
import { fmtMoney, toNum } from "../lib/format";

export default function Debts() {
  const qc = useQueryClient();
  const summary = useQuery({
    queryKey: ["debt-summary"],
    queryFn: async () => (await api.get<DebtSummary>("/api/v1/debts/summary")).data,
  });
  const create = useMutation({
    mutationFn: async (payload: Partial<Debt>) => (await api.post("/api/v1/debts", payload)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["debt-summary"] }),
  });
  const update = useMutation({
    mutationFn: async (vars: { id: number; current_balance: string }) =>
      (await api.patch(`/api/v1/debts/${vars.id}`, { current_balance: vars.current_balance })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["debt-summary"] }),
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/debts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["debt-summary"] }),
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [original, setOriginal] = useState("0");
  const [current, setCurrent] = useState("0");
  const [apr, setApr] = useState("");
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
        minimum_payment: minPay ? (minPay as unknown as string) : null,
        due_day_of_month: dueDay ? Number(dueDay) : null,
      },
      {
        onSuccess: () => {
          setName(""); setOriginal("0"); setCurrent("0"); setApr(""); setMinPay(""); setDueDay("");
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
        subtitle={`${summary.data?.by_debt.length ?? 0} obligations · ${fmtMoney(totalOwed)} owed`}
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
              {fmtMoney(totalOriginal - totalOwed)}{" "}
              <span className="text-base font-medium text-slate-400">
                of {fmtMoney(totalOriginal)} paid
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
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-6">
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
              <Label>Min payment</Label>
              <Input value={minPay} onChange={(e) => setMinPay(e.target.value)} />
            </label>
            <label className="md:col-span-1">
              <Label>Due day</Label>
              <Input value={dueDay} onChange={(e) => setDueDay(e.target.value)} placeholder="1–31" />
            </label>
            <div className="md:col-span-6">
              <Button type="submit">Save debt</Button>
            </div>
          </form>
        </Card>
      )}

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
          return (
            <Card key={d.id} className="p-5 transition hover:shadow-card-hover">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate font-semibold text-slate-900">{d.name}</h3>
                    {d.interest_rate_apr && (
                      <Badge tone={toNum(d.interest_rate_apr) > 20 ? "rose" : "slate"}>
                        {toNum(d.interest_rate_apr).toFixed(1)}% APR
                      </Badge>
                    )}
                  </div>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="text-xl font-bold tracking-tight nums">{fmtMoney(cur)}</span>
                    <span className="text-xs text-slate-500 nums">of {fmtMoney(original)}</span>
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
                    {d.minimum_payment ? fmtMoney(d.minimum_payment) : "—"}
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
