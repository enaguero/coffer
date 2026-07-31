import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Filter, Receipt, Wand2, X } from "lucide-react";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import type { Account, Category, Transaction } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Select } from "../components/ui";
import { fmtMoneySigned, toNum } from "../lib/format";
import { useAccountCurrencyMap } from "../lib/useCurrency";

type Filter = "all" | "uncategorized";

// Mirror of the backend's merchant normalization: the stable token of a
// description, suitable as a substring rule pattern.
function suggestPattern(description: string): string {
  return description
    .toUpperCase()
    .replace(/[0-9]+|[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .slice(0, 3)
    .join(" ")
    .slice(0, 32);
}

interface RuleHint {
  pattern: string;
  categoryId: number;
  categoryName: string;
}

export default function Transactions() {
  const qc = useQueryClient();
  const [accountId, setAccountId] = useState<string>("");
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkCategoryId, setBulkCategoryId] = useState<string>("");
  const currencyByAccount = useAccountCurrencyMap();

  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: async () => (await api.get<Category[]>("/api/v1/categories")).data,
  });
  const txns = useQuery({
    queryKey: ["transactions", accountId, filter],
    queryFn: async () => {
      const params: Record<string, string | number | boolean> = {};
      if (accountId) params.account_id = Number(accountId);
      if (filter === "uncategorized") params.uncategorized = true;
      return (await api.get<Transaction[]>("/api/v1/transactions", { params })).data;
    },
  });

  const [ruleHint, setRuleHint] = useState<RuleHint | null>(null);

  const updateCategory = useMutation({
    mutationFn: async (vars: {
      id: number;
      category_id: number | null;
      description: string;
      was_uncategorized: boolean;
    }) => api.patch(`/api/v1/transactions/${vars.id}`, { category_id: vars.category_id }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      // Offer to make the correction permanent as an auto-categorization rule.
      if (vars.category_id !== null) {
        const cat = categories.data?.find((c) => c.id === vars.category_id);
        const pattern = suggestPattern(vars.description);
        if (cat && pattern) {
          setRuleHint({ pattern, categoryId: cat.id, categoryName: cat.name });
        }
      }
    },
  });

  const saveRule = useMutation({
    mutationFn: async (vars: { pattern: string; category_id: number }) => {
      await api.post("/api/v1/category-rules", vars);
      // Retroactively categorize everything the new rule matches.
      return (await api.post<{ transactions_updated: number }>("/api/v1/category-rules/apply")).data;
    },
    onSuccess: () => {
      setRuleHint(null);
      qc.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  const bulkAssign = useMutation({
    mutationFn: async (vars: { ids: number[]; category_id: number | null }) =>
      (await api.post<{ updated: number }>("/api/v1/transactions/bulk-assign", {
        transaction_ids: vars.ids,
        category_id: vars.category_id,
      })).data,
    onSuccess: () => {
      setSelectedIds(new Set());
      qc.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  const uncategorizedCount = useMemo(
    () => (txns.data ?? []).filter((t) => t.category_id === null).length,
    [txns.data],
  );

  function toggleRow(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    const ids = (txns.data ?? []).map((t) => t.id);
    if (selectedIds.size === ids.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(ids));
    }
  }

  return (
    <>
      <PageHeader
        title="Transactions"
        subtitle="Every line item from your statements — categorize as you go."
        right={
          <div className="flex items-center gap-2">
            <Select
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              className="!w-44"
            >
              <option value="">All accounts</option>
              {accounts.data?.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
            <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1">
              <button
                onClick={() => setFilter("all")}
                className={`rounded px-2.5 py-1 text-xs font-medium transition ${
                  filter === "all" ? "bg-brand-600 text-white" : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                All
              </button>
              <button
                onClick={() => setFilter("uncategorized")}
                className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition ${
                  filter === "uncategorized"
                    ? "bg-brand-600 text-white"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                <Filter className="h-3 w-3" />
                Uncategorized
                {filter !== "uncategorized" && uncategorizedCount > 0 && (
                  <Badge tone="amber">{uncategorizedCount}</Badge>
                )}
              </button>
            </div>
          </div>
        }
      />

      {ruleHint && (
        <Card className="mb-4 flex flex-wrap items-center gap-3 border-brand-200 bg-brand-50/40 p-3">
          <Wand2 className="h-4 w-4 shrink-0 text-brand-700" />
          <span className="text-sm text-slate-700">
            Always file descriptions containing
          </span>
          <Input
            value={ruleHint.pattern}
            onChange={(e) => setRuleHint({ ...ruleHint, pattern: e.target.value })}
            className="!w-56 !py-1 font-mono text-xs"
          />
          <span className="text-sm text-slate-700">
            as <strong>{ruleHint.categoryName}</strong>?
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button
              className="!py-1.5"
              disabled={!ruleHint.pattern.trim() || saveRule.isPending}
              onClick={() =>
                saveRule.mutate({
                  pattern: ruleHint.pattern.trim(),
                  category_id: ruleHint.categoryId,
                })
              }
            >
              {saveRule.isPending ? "Saving…" : "Save rule + apply"}
            </Button>
            <Button variant="ghost" className="!py-1.5" onClick={() => setRuleHint(null)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </Card>
      )}

      {selectedIds.size > 0 && (
        <Card className="mb-4 flex items-center justify-between gap-3 p-3">
          <div className="text-sm font-medium text-slate-700">
            {selectedIds.size} selected
          </div>
          <div className="flex items-center gap-2">
            <Select
              value={bulkCategoryId}
              onChange={(e) => setBulkCategoryId(e.target.value)}
              className="!w-56 !py-1"
            >
              <option value="">Choose category…</option>
              <option value="__null__">— Clear category —</option>
              {categories.data?.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
            <Button
              disabled={!bulkCategoryId || bulkAssign.isPending}
              onClick={() =>
                bulkAssign.mutate({
                  ids: Array.from(selectedIds),
                  category_id: bulkCategoryId === "__null__" ? null : Number(bulkCategoryId),
                })
              }
            >
              {bulkAssign.isPending ? "Assigning…" : `Assign ${selectedIds.size}`}
            </Button>
            <Button variant="ghost" onClick={() => setSelectedIds(new Set())}>
              Clear
            </Button>
          </div>
        </Card>
      )}

      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-10 px-3 py-3">
                <input
                  type="checkbox"
                  checked={
                    (txns.data?.length ?? 0) > 0 &&
                    selectedIds.size === (txns.data?.length ?? 0)
                  }
                  onChange={toggleAll}
                />
              </th>
              <th className="w-28 px-5 py-3 text-left font-medium">Date</th>
              <th className="px-5 py-3 text-left font-medium">Description</th>
              <th className="w-56 px-5 py-3 text-left font-medium">Category</th>
              <th className="w-32 px-5 py-3 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            {txns.data?.map((t) => {
              const amt = toNum(t.amount);
              const selected = selectedIds.has(t.id);
              return (
                <tr
                  key={t.id}
                  className={`border-t border-slate-100 ${
                    selected ? "bg-brand-50/40" : "hover:bg-slate-50/50"
                  }`}
                >
                  <td className="px-3 py-2.5">
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggleRow(t.id)}
                    />
                  </td>
                  <td className="px-5 py-2.5 text-slate-500 nums">{t.posted_on}</td>
                  <td className="px-5 py-2.5 text-slate-900">{t.description}</td>
                  <td className="px-5 py-2.5">
                    <Select
                      value={t.category_id ?? ""}
                      onChange={(e) =>
                        updateCategory.mutate({
                          id: t.id,
                          category_id: e.target.value ? Number(e.target.value) : null,
                          description: t.description,
                          was_uncategorized: t.category_id === null,
                        })
                      }
                      className="!py-1"
                    >
                      <option value="">Uncategorized</option>
                      {categories.data?.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </Select>
                  </td>
                  <td
                    className={`px-5 py-2.5 text-right nums font-medium ${
                      amt < 0 ? "text-rose-600" : "text-emerald-600"
                    }`}
                  >
                    {fmtMoneySigned(amt, currencyByAccount.get(t.account_id))}
                  </td>
                </tr>
              );
            })}
            {txns.data && txns.data.length === 0 && (
              <tr>
                <td colSpan={5} className="p-0">
                  <EmptyState
                    icon={<Receipt className="h-5 w-5" />}
                    title={
                      filter === "uncategorized"
                        ? "Nothing uncategorized"
                        : "No transactions yet"
                    }
                    body={
                      filter === "uncategorized"
                        ? "Every transaction has a category. Nice."
                        : "Import a CSV or PDF bank statement to populate this view."
                    }
                  />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </>
  );
}
