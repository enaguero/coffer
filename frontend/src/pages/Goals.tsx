import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Plus, Target, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Account, Goal } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Label, PageHeader, ProgressBar, Select } from "../components/ui";
import { fmtMoney } from "../lib/format";
import { useAccountCurrencyMap, useUserCurrency } from "../lib/useCurrency";

// Only asset accounts can fund a goal (mirrors the backend's FUNDABLE_TYPES).
const FUNDABLE_TYPES = new Set(["checking", "savings", "cash", "other"]);

function apiError(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : "That change couldn't be saved — check the values.";
}

export default function Goals() {
  const qc = useQueryClient();
  const currency = useUserCurrency();
  const currencyByAccount = useAccountCurrencyMap();
  const [error, setError] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["goals"],
    queryFn: async () => (await api.get<Goal[]>("/api/v1/goals")).data,
  });
  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });
  const create = useMutation({
    mutationFn: async (payload: Partial<Goal>) => (await api.post("/api/v1/goals", payload)).data,
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: (err) => setError(apiError(err)),
  });
  const update = useMutation({
    mutationFn: async (vars: { id: number } & Partial<Goal>) => {
      const { id, ...body } = vars;
      return (await api.patch(`/api/v1/goals/${id}`, body)).data;
    },
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: (err) => setError(apiError(err)),
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/goals/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goals"] }),
  });

  // Commit a money field on blur only when the value genuinely changed and the
  // input parses ("250" vs server "250.00" is NOT a change; badInput is skipped).
  function commitOnBlur(
    e: React.FocusEvent<HTMLInputElement>,
    current: string | null,
    apply: (value: string | null) => void,
  ) {
    if (e.target.validity && e.target.validity.badInput) return;
    const next = e.target.value.trim();
    const a = next === "" ? null : parseFloat(next);
    const b = current === null || current === "" ? null : parseFloat(current);
    if (a === b) return;
    apply(next === "" ? null : next);
  }

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [accountId, setAccountId] = useState("");
  const [contribution, setContribution] = useState("");

  const accountName = (id: number | null) =>
    accounts.data?.find((a) => a.id === id)?.name ?? null;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      {
        name,
        target_amount: target as unknown as string,
        target_date: targetDate || null,
        account_id: accountId ? Number(accountId) : null,
        monthly_contribution: contribution ? (contribution as unknown as string) : null,
        current_amount: "0" as unknown as string,
      },
      {
        onSuccess: () => {
          setName(""); setTarget(""); setTargetDate(""); setAccountId(""); setContribution("");
          setShowForm(false);
        },
      },
    );
  }

  return (
    <>
      <PageHeader
        title="Goals"
        subtitle="Savings targets with a funding plan — link an account and progress tracks itself."
        right={
          <Button onClick={() => setShowForm((s) => !s)}>
            <Plus className="h-4 w-4" />
            {showForm ? "Cancel" : "New goal"}
          </Button>
        }
      />

      {showForm && (
        <Card className="mb-6 p-6">
          <h2 className="mb-4 text-sm font-semibold text-slate-900">New goal</h2>
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-5">
            <label className="md:col-span-2">
              <Label>Name</Label>
              <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Emergency fund" />
            </label>
            <label>
              <Label>Target amount</Label>
              <Input required value={target} onChange={(e) => setTarget(e.target.value)} placeholder="10000" />
            </label>
            <label>
              <Label>Target date (optional)</Label>
              <Input type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} />
            </label>
            <label>
              <Label>Monthly contribution</Label>
              <Input
                type="number"
                min="0"
                step="0.01"
                value={contribution}
                onChange={(e) => setContribution(e.target.value)}
                placeholder="e.g. 250"
              />
            </label>
            <label className="md:col-span-2">
              <Label>Savings account (progress auto-tracks its balance)</Label>
              <Select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                <option value="">Not linked — update progress by hand</option>
                {accounts.data
                  ?.filter((a) => FUNDABLE_TYPES.has(a.type))
                  .map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
              </Select>
            </label>
            <div className="md:col-span-5">
              <Button type="submit">Save goal</Button>
            </div>
          </form>
        </Card>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      {list.data && list.data.length === 0 ? (
        <EmptyState
          icon={<Target className="h-5 w-5" />}
          title="No goals yet"
          body="Create one above to start tracking your progress."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {list.data?.map((g) => {
            const tone = g.progress >= 1 ? "emerald" : g.progress >= 0.5 ? "brand" : "amber";
            // Auto-tracked amounts are denominated in the linked account's currency.
            const goalCurrency =
              (g.auto_tracked && g.account_id !== null && currencyByAccount.get(g.account_id)) ||
              currency;
            return (
              <Card key={g.id} className="p-5 transition hover:shadow-card-hover">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate font-semibold text-slate-900">{g.name}</h3>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
                      {g.target_date && <span>By {g.target_date}</span>}
                      {g.auto_tracked && (
                        <span className="inline-flex items-center gap-1">
                          <Link2 className="h-3 w-3" />
                          {accountName(g.account_id)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <Badge tone={tone}>{Math.round(g.progress * 100)}%</Badge>
                    {g.on_track !== null && (
                      <Badge tone={g.on_track ? "emerald" : "rose"}>
                        {g.on_track ? "on track" : "behind"}
                      </Badge>
                    )}
                  </div>
                </div>

                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-xl font-bold tracking-tight nums">
                    {fmtMoney(g.current_amount, goalCurrency)}
                  </span>
                  <span className="text-xs text-slate-500 nums">
                    of {fmtMoney(g.target_amount, goalCurrency)}
                  </span>
                </div>

                <ProgressBar value={g.progress} tone={tone} className="mt-3" />

                <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  {g.required_monthly && (
                    <div>
                      <div className="text-slate-500">Needed / month</div>
                      <div className="mt-0.5 font-medium nums">{fmtMoney(g.required_monthly, goalCurrency)}</div>
                    </div>
                  )}
                  {g.monthly_contribution && (
                    <div>
                      <div className="text-slate-500">Committed / month</div>
                      <div className="mt-0.5 font-medium nums">{fmtMoney(g.monthly_contribution, goalCurrency)}</div>
                    </div>
                  )}
                  {g.funded_this_month !== null && (
                    <div>
                      <div className="text-slate-500">Funded this month</div>
                      <div className="mt-0.5 font-medium nums">{fmtMoney(g.funded_this_month, goalCurrency)}</div>
                    </div>
                  )}
                  {g.projected_date && g.progress < 1 && (
                    <div>
                      <div className="text-slate-500">Arrives</div>
                      <div className="mt-0.5 font-medium nums">{g.projected_date}</div>
                    </div>
                  )}
                </div>

                <div className="mt-4 flex items-end gap-2 border-t border-slate-100 pt-3">
                  <label className="flex-1">
                    <Label>Contribution / month</Label>
                    <Input
                      type="number"
                      min="0"
                      step="0.01"
                      defaultValue={g.monthly_contribution ?? ""}
                      onBlur={(e) =>
                        commitOnBlur(e, g.monthly_contribution, (value) =>
                          update.mutate({ id: g.id, monthly_contribution: value }),
                        )
                      }
                      className="!py-1 text-right"
                    />
                  </label>
                  {!g.auto_tracked && (
                    <label className="flex-1">
                      <Label>Update current</Label>
                      <Input
                        type="number"
                        min="0"
                        step="0.01"
                        defaultValue={g.current_amount}
                        onBlur={(e) =>
                          commitOnBlur(e, g.current_amount, (value) => {
                            if (value !== null) update.mutate({ id: g.id, current_amount: value });
                          })
                        }
                        className="!py-1 text-right"
                      />
                    </label>
                  )}
                  <button
                    onClick={() => remove.mutate(g.id)}
                    className="rounded p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                    title="Delete"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
