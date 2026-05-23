import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListTree, Plus, Sparkles, Trash2, Wand2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  ApplyRulesResponse,
  Category,
  CategoryKind,
  CategoryRule,
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

const KINDS: CategoryKind[] = ["income", "expense", "debt_payment", "saving"];

const KIND_TONE: Record<CategoryKind, "emerald" | "rose" | "amber" | "sky"> = {
  income: "emerald",
  expense: "rose",
  debt_payment: "amber",
  saving: "sky",
};

export default function Categories() {
  const qc = useQueryClient();
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: async () => (await api.get<Category[]>("/api/v1/categories")).data,
  });
  const rules = useQuery({
    queryKey: ["category-rules"],
    queryFn: async () =>
      (await api.get<CategoryRule[]>("/api/v1/category-rules")).data,
  });

  const createCategory = useMutation({
    mutationFn: async (payload: { name: string; kind: CategoryKind }) =>
      (await api.post("/api/v1/categories", payload)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
  });
  const removeCategory = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/categories/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
  });
  const createRule = useMutation({
    mutationFn: async (payload: Omit<CategoryRule, "id">) =>
      (await api.post("/api/v1/category-rules", payload)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["category-rules"] }),
  });
  const removeRule = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/category-rules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["category-rules"] }),
  });
  const applyRules = useMutation({
    mutationFn: async () =>
      (await api.post<ApplyRulesResponse>("/api/v1/category-rules/apply")).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CategoryKind>("expense");

  const [rulePattern, setRulePattern] = useState("");
  const [rulePriority, setRulePriority] = useState("100");
  const [ruleCategoryId, setRuleCategoryId] = useState<string>("");

  function onSubmitCategory(e: FormEvent) {
    e.preventDefault();
    createCategory.mutate(
      { name, kind },
      { onSuccess: () => { setName(""); setShowForm(false); } },
    );
  }

  function onSubmitRule(e: FormEvent) {
    e.preventDefault();
    if (!rulePattern || !ruleCategoryId) return;
    createRule.mutate(
      {
        pattern: rulePattern,
        category_id: Number(ruleCategoryId),
        priority: Number(rulePriority) || 100,
      },
      {
        onSuccess: () => {
          setRulePattern("");
          setRulePriority("100");
          setRuleCategoryId("");
        },
      },
    );
  }

  const categoryById = new Map((categories.data ?? []).map((c) => [c.id, c]));

  return (
    <>
      <PageHeader
        title="Categories"
        subtitle="Buckets for income, expenses, debt payments, and savings."
        right={
          <Button onClick={() => setShowForm((s) => !s)}>
            <Plus className="h-4 w-4" />
            {showForm ? "Cancel" : "New category"}
          </Button>
        }
      />

      {showForm && (
        <Card className="mb-6 p-6">
          <form onSubmit={onSubmitCategory} className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <label className="md:col-span-2">
              <Label>Name</Label>
              <Input required value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              <Label>Kind</Label>
              <Select value={kind} onChange={(e) => setKind(e.target.value as CategoryKind)}>
                {KINDS.map((k) => <option key={k} value={k}>{k.replace("_", " ")}</option>)}
              </Select>
            </label>
            <div className="flex items-end">
              <Button type="submit" className="w-full">Save</Button>
            </div>
          </form>
        </Card>
      )}

      {categories.data && categories.data.length === 0 ? (
        <EmptyState
          icon={<ListTree className="h-5 w-5" />}
          title="No categories yet"
          body="Create your first category — e.g. House, Groceries, Salary."
        />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-3 text-left font-medium">Name</th>
                <th className="w-40 px-5 py-3 text-left font-medium">Kind</th>
                <th className="w-12 px-5 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {categories.data?.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 hover:bg-slate-50/50">
                  <td className="px-5 py-3 font-medium text-slate-900">{c.name}</td>
                  <td className="px-5 py-3">
                    <Badge tone={KIND_TONE[c.kind]}>{c.kind.replace("_", " ")}</Badge>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button
                      onClick={() => removeCategory.mutate(c.id)}
                      className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                      title="Delete"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <div className="mt-10 mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
            <Wand2 className="h-4 w-4 text-brand-600" /> Auto-categorize rules
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            If a transaction's description contains the pattern (case-insensitive),
            assign that category on import. Lower priority wins ties.
          </p>
        </div>
        <Button
          variant="secondary"
          onClick={() => applyRules.mutate()}
          disabled={applyRules.isPending || (rules.data?.length ?? 0) === 0}
        >
          <Sparkles className="h-4 w-4" />
          {applyRules.isPending ? "Applying…" : "Apply to uncategorized"}
        </Button>
      </div>

      {applyRules.data && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          Updated {applyRules.data.transactions_updated} transactions across{" "}
          {applyRules.data.rules_evaluated} rules.
        </div>
      )}

      <Card className="p-6">
        <form onSubmit={onSubmitRule} className="grid grid-cols-1 gap-3 md:grid-cols-12">
          <label className="md:col-span-5">
            <Label>Pattern (substring)</Label>
            <Input
              required
              value={rulePattern}
              onChange={(e) => setRulePattern(e.target.value)}
              placeholder="e.g. starbucks"
            />
          </label>
          <label className="md:col-span-4">
            <Label>Category</Label>
            <Select
              required
              value={ruleCategoryId}
              onChange={(e) => setRuleCategoryId(e.target.value)}
            >
              <option value="">Choose…</option>
              {categories.data?.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </label>
          <label className="md:col-span-2">
            <Label>Priority</Label>
            <Input
              type="number"
              value={rulePriority}
              onChange={(e) => setRulePriority(e.target.value)}
            />
          </label>
          <div className="flex items-end md:col-span-1">
            <Button type="submit" className="w-full">Add</Button>
          </div>
        </form>

        {rules.data && rules.data.length > 0 && (
          <table className="mt-6 w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-2 text-left font-medium">Pattern</th>
                <th className="px-5 py-2 text-left font-medium">Category</th>
                <th className="w-24 px-5 py-2 text-right font-medium">Priority</th>
                <th className="w-12 px-5 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rules.data.map((r) => (
                <tr key={r.id} className="border-t border-slate-100">
                  <td className="px-5 py-2 font-mono text-slate-900">{r.pattern}</td>
                  <td className="px-5 py-2">
                    {categoryById.get(r.category_id)?.name ?? "—"}
                  </td>
                  <td className="px-5 py-2 text-right nums">{r.priority}</td>
                  <td className="px-5 py-2 text-right">
                    <button
                      onClick={() => removeRule.mutate(r.id)}
                      className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                      title="Delete rule"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {(rules.data?.length ?? 0) === 0 && (
          <div className="mt-6 text-center text-sm text-slate-500">
            No rules yet — add one above.
          </div>
        )}
      </Card>
    </>
  );
}
