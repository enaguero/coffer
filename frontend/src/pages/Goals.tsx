import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Target, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Goal } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Label, PageHeader, ProgressBar } from "../components/ui";
import { fmtMoney } from "../lib/format";

export default function Goals() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["goals"],
    queryFn: async () => (await api.get<Goal[]>("/api/v1/goals")).data,
  });
  const create = useMutation({
    mutationFn: async (payload: Partial<Goal>) => (await api.post("/api/v1/goals", payload)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goals"] }),
  });
  const update = useMutation({
    mutationFn: async (vars: { id: number; current_amount: string }) =>
      (await api.patch(`/api/v1/goals/${vars.id}`, { current_amount: vars.current_amount })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goals"] }),
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/goals/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goals"] }),
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [targetDate, setTargetDate] = useState("");

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      {
        name,
        target_amount: target as unknown as string,
        target_date: targetDate || null,
        current_amount: "0" as unknown as string,
      },
      {
        onSuccess: () => {
          setName(""); setTarget(""); setTargetDate("");
          setShowForm(false);
        },
      },
    );
  }

  return (
    <>
      <PageHeader
        title="Goals"
        subtitle="Long-running savings targets — emergency fund, vacation, debt-free dates."
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
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-4">
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
            <div className="md:col-span-4">
              <Button type="submit">Save goal</Button>
            </div>
          </form>
        </Card>
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
            return (
              <Card key={g.id} className="p-5 transition hover:shadow-card-hover">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate font-semibold text-slate-900">{g.name}</h3>
                    {g.target_date && (
                      <div className="mt-0.5 text-xs text-slate-500">By {g.target_date}</div>
                    )}
                  </div>
                  <Badge tone={tone}>{Math.round(g.progress * 100)}%</Badge>
                </div>

                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-xl font-bold tracking-tight nums">
                    {fmtMoney(g.current_amount)}
                  </span>
                  <span className="text-xs text-slate-500 nums">
                    of {fmtMoney(g.target_amount)}
                  </span>
                </div>

                <ProgressBar value={g.progress} tone={tone} className="mt-3" />

                <div className="mt-4 flex items-end gap-2 border-t border-slate-100 pt-3">
                  <label className="flex-1">
                    <Label>Update current</Label>
                    <Input
                      type="number"
                      step="0.01"
                      defaultValue={g.current_amount}
                      onBlur={(e) => {
                        if (e.target.value !== g.current_amount) {
                          update.mutate({ id: g.id, current_amount: e.target.value });
                        }
                      }}
                      className="!py-1 text-right"
                    />
                  </label>
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
