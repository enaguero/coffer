import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Wallet } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Account, AccountType } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Label, PageHeader, Select } from "../components/ui";
import { fmtMoney } from "../lib/format";

const TYPES: AccountType[] = ["checking", "savings", "credit_card", "loan", "overdraft", "cash", "other"];

const TYPE_TONE: Record<AccountType, "emerald" | "rose" | "sky" | "amber" | "slate" | "brand"> = {
  checking: "sky",
  savings: "emerald",
  credit_card: "rose",
  loan: "amber",
  overdraft: "rose",
  cash: "slate",
  other: "slate",
};

export default function Accounts() {
  const qc = useQueryClient();
  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });
  const create = useMutation({
    mutationFn: async (payload: Partial<Account>) => (await api.post("/api/v1/accounts", payload)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/accounts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("checking");
  const [institution, setInstitution] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [opening, setOpening] = useState("0");

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      {
        name, type,
        institution: institution || null,
        currency,
        opening_balance: opening as unknown as string,
      },
      {
        onSuccess: () => {
          setName(""); setInstitution(""); setOpening("0");
          setShowForm(false);
        },
      },
    );
  }

  return (
    <>
      <PageHeader
        title="Accounts"
        subtitle="Bank accounts, credit cards, loans, and savings vehicles."
        right={
          <Button onClick={() => setShowForm((s) => !s)}>
            <Plus className="h-4 w-4" />
            {showForm ? "Cancel" : "New account"}
          </Button>
        }
      />

      {showForm && (
        <Card className="mb-6 p-6">
          <h2 className="mb-4 text-sm font-semibold text-slate-900">New account</h2>
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-5">
            <label>
              <Label>Name</Label>
              <Input required value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              <Label>Type</Label>
              <Select value={type} onChange={(e) => setType(e.target.value as AccountType)}>
                {TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
              </Select>
            </label>
            <label>
              <Label>Institution</Label>
              <Input value={institution} onChange={(e) => setInstitution(e.target.value)} />
            </label>
            <label>
              <Label>Currency</Label>
              <Input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} maxLength={3} />
            </label>
            <label>
              <Label>Opening balance</Label>
              <Input value={opening} onChange={(e) => setOpening(e.target.value)} />
            </label>
            <div className="md:col-span-5">
              <Button type="submit">Save account</Button>
            </div>
          </form>
        </Card>
      )}

      {accounts.data && accounts.data.length === 0 ? (
        <EmptyState
          icon={<Wallet className="h-5 w-5" />}
          title="No accounts yet"
          body="Add a checking account or credit card to start tracking."
        />
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-3 text-left font-medium">Name</th>
                <th className="w-32 px-5 py-3 text-left font-medium">Type</th>
                <th className="px-5 py-3 text-left font-medium">Institution</th>
                <th className="w-40 px-5 py-3 text-right font-medium">Opening balance</th>
                <th className="w-12 px-5 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {accounts.data?.map((a) => (
                <tr key={a.id} className="border-t border-slate-100 hover:bg-slate-50/50">
                  <td className="px-5 py-3 font-medium text-slate-900">{a.name}</td>
                  <td className="px-5 py-3">
                    <Badge tone={TYPE_TONE[a.type]}>{a.type.replace("_", " ")}</Badge>
                  </td>
                  <td className="px-5 py-3 text-slate-600">{a.institution ?? "—"}</td>
                  <td className="px-5 py-3 text-right nums">
                    {fmtMoney(a.opening_balance, a.currency)}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button
                      onClick={() => remove.mutate(a.id)}
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
    </>
  );
}
