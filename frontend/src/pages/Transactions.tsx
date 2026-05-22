import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Receipt } from "lucide-react";
import { useState } from "react";

import { api } from "../api/client";
import type { Account, Category, Transaction } from "../api/types";
import { Card, EmptyState, PageHeader, Select } from "../components/ui";
import { fmtMoneySigned, toNum } from "../lib/format";

export default function Transactions() {
  const qc = useQueryClient();
  const [accountId, setAccountId] = useState<string>("");

  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: async () => (await api.get<Category[]>("/api/v1/categories")).data,
  });
  const txns = useQuery({
    queryKey: ["transactions", accountId],
    queryFn: async () =>
      (await api.get<Transaction[]>("/api/v1/transactions", {
        params: accountId ? { account_id: Number(accountId) } : undefined,
      })).data,
  });

  const updateCategory = useMutation({
    mutationFn: async (vars: { id: number; category_id: number | null }) => {
      await api.patch(`/api/v1/transactions/${vars.id}`, { category_id: vars.category_id });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
  });

  return (
    <>
      <PageHeader
        title="Transactions"
        subtitle="Every line item from your statements — categorize as you go."
        right={
          <Select
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            className="!w-48"
          >
            <option value="">All accounts</option>
            {accounts.data?.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </Select>
        }
      />

      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-28 px-5 py-3 text-left font-medium">Date</th>
              <th className="px-5 py-3 text-left font-medium">Description</th>
              <th className="w-56 px-5 py-3 text-left font-medium">Category</th>
              <th className="w-32 px-5 py-3 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            {txns.data?.map((t) => {
              const amt = toNum(t.amount);
              return (
                <tr key={t.id} className="border-t border-slate-100 hover:bg-slate-50/50">
                  <td className="px-5 py-2.5 text-slate-500 nums">{t.posted_on}</td>
                  <td className="px-5 py-2.5 text-slate-900">{t.description}</td>
                  <td className="px-5 py-2.5">
                    <Select
                      value={t.category_id ?? ""}
                      onChange={(e) =>
                        updateCategory.mutate({
                          id: t.id,
                          category_id: e.target.value ? Number(e.target.value) : null,
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
                    {fmtMoneySigned(amt)}
                  </td>
                </tr>
              );
            })}
            {txns.data && txns.data.length === 0 && (
              <tr>
                <td colSpan={4} className="p-0">
                  <EmptyState
                    icon={<Receipt className="h-5 w-5" />}
                    title="No transactions yet"
                    body="Import a CSV or PDF bank statement to populate this view."
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
