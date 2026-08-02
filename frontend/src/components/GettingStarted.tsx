import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle, Rocket, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Account, BudgetMonthView, CategoryRule, Goal, StatementImportRecord } from "../api/types";
import { Card, ProgressBar } from "./ui";

const DISMISS_KEY = "coffer.getting-started.dismissed";

interface Step {
  label: string;
  detail: string;
  to: string;
  done: boolean;
}

/** Statement-first onboarding: a checklist driven by the account's real state
 * (not a stored wizard step), so it also self-heals — deleting everything
 * brings it back. Hidden once complete or dismissed. */
export default function GettingStarted() {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(DISMISS_KEY) === "1");
  const now = new Date();

  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
    staleTime: 60_000,
  });
  const imports = useQuery({
    queryKey: ["imports"],
    queryFn: async () => (await api.get<StatementImportRecord[]>("/api/v1/imports")).data,
    staleTime: 60_000,
  });
  const rules = useQuery({
    queryKey: ["category-rules"],
    queryFn: async () => (await api.get<CategoryRule[]>("/api/v1/category-rules")).data,
    staleTime: 60_000,
  });
  const monthView = useQuery({
    queryKey: ["budget-month", now.getFullYear(), now.getMonth() + 1],
    queryFn: async () =>
      (await api.get<BudgetMonthView>(`/api/v1/budgets/month/${now.getFullYear()}/${now.getMonth() + 1}`)).data,
  });
  const goals = useQuery({
    queryKey: ["goals"],
    queryFn: async () => (await api.get<Goal[]>("/api/v1/goals")).data,
  });

  if (dismissed) return null;
  // Don't flash the checklist before the data that decides it has arrived.
  if (accounts.isPending || imports.isPending) return null;

  const committed = (imports.data ?? []).filter((i) => i.status === "committed");
  const steps: Step[] = [
    {
      label: "Add your first account",
      detail: "One per bank account you'll import statements for.",
      to: "/import",
      done: (accounts.data?.length ?? 0) > 0,
    },
    {
      label: "Import a bank statement",
      detail: "Download CSV/OFX from your bank, upload it here — that's the whole model.",
      to: "/import",
      done: committed.length > 0,
    },
    {
      label: "Add a categorization rule",
      detail: "Match descriptions once; every future import categorizes itself.",
      to: "/categories",
      done: (rules.data?.length ?? 0) > 0,
    },
    {
      label: "Plan this month's budget",
      detail: "Planned vs actual per category — the monthly heartbeat.",
      to: "/budget",
      done: (monthView.data?.rows ?? []).some((r) => Number(r.planned) > 0),
    },
    {
      label: "Set a goal",
      detail: "Link it to a savings account and funding tracks itself.",
      to: "/goals",
      done: (goals.data?.length ?? 0) > 0,
    },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  if (doneCount === steps.length) return null;

  function dismiss() {
    localStorage.setItem(DISMISS_KEY, "1");
    setDismissed(true);
  }

  return (
    <Card className="mb-6 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Rocket className="h-4 w-4 text-brand-700" />
          <h2 className="text-sm font-semibold text-slate-900">Getting started</h2>
          <span className="text-xs text-slate-500 nums">
            {doneCount} of {steps.length}
          </span>
        </div>
        <button
          onClick={dismiss}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          title="Dismiss — it won't come back"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <ProgressBar value={doneCount / steps.length} tone="emerald" className="mt-3" />
      <ul className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
        {steps.map((s) => (
          <li key={s.label}>
            {s.done ? (
              <div className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm text-slate-400">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                <span className="line-through decoration-slate-300">{s.label}</span>
              </div>
            ) : (
              <Link
                to={s.to}
                className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-brand-50/60"
              >
                <Circle className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" />
                <span>
                  <span className="font-medium text-slate-900">{s.label}</span>
                  <span className="mt-0.5 block text-xs text-slate-500">{s.detail}</span>
                </span>
              </Link>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
