import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Wallet } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Account, AccountType, UkBank, UkWrapper } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Label, PageHeader, Select } from "../components/ui";
import { ACCOUNT_TYPE_OPTIONS } from "../lib/accountTypes";
import { fmtMoney } from "../lib/format";

const ALL_TYPES: AccountType[] = ACCOUNT_TYPE_OPTIONS.map((o) => o.value);
const TYPE_LABEL = new Map(ACCOUNT_TYPE_OPTIONS.map((o) => [o.value, o.label]));

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
  const banks = useQuery({
    queryKey: ["banks"],
    queryFn: async () => (await api.get<UkBank[]>("/api/v1/banks")).data,
  });
  const create = useMutation({
    mutationFn: async (payload: Partial<Account>) => (await api.post("/api/v1/accounts", payload)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
  const setWrapperMut = useMutation({
    mutationFn: async (vars: { id: number; uk_wrapper: UkWrapper | null }) =>
      (await api.patch(`/api/v1/accounts/${vars.id}`, { uk_wrapper: vars.uk_wrapper })).data,
    onSuccess: () => {
      for (const key of ["accounts", "allowances", "networth"]) {
        qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/accounts/${id}`),
    onSuccess: () => {
      // Deleting an account cascades into goals (unlink), net worth, and coverage.
      for (const key of ["accounts", "goals", "networth", "coverage", "transactions", "allowances"]) {
        qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [bankId, setBankId] = useState("");
  const [type, setType] = useState<AccountType>("checking");
  const [institution, setInstitution] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [opening, setOpening] = useState("0");
  const [wrapper, setWrapper] = useState("");

  const selectedBank = banks.data?.find((b) => b.id === bankId) ?? null;
  const typeOptions = selectedBank ? selectedBank.account_types : ALL_TYPES;

  function onPickBank(id: string) {
    setBankId(id);
    const bank = banks.data?.find((b) => b.id === id);
    if (bank) {
      setInstitution(bank.name);
      setCurrency("GBP");
      if (!bank.account_types.includes(type)) setType(bank.account_types[0]);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      {
        name, type,
        institution: institution || null,
        bank_id: bankId || null,
        uk_wrapper: (wrapper || null) as UkWrapper | null,
        currency,
        opening_balance: opening as unknown as string,
      },
      {
        onSuccess: () => {
          setName(""); setBankId(""); setInstitution(""); setOpening("0"); setWrapper("");
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
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-6">
            <label>
              <Label>Name</Label>
              <Input required value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              <Label>Bank</Label>
              <Select value={bankId} onChange={(e) => onPickBank(e.target.value)}>
                <option value="">Not listed / manual</option>
                {banks.data?.map((b) => (
                  <option key={b.id} value={b.id}>{b.name}</option>
                ))}
              </Select>
            </label>
            <label>
              <Label>Type</Label>
              <Select value={type} onChange={(e) => setType(e.target.value as AccountType)}>
                {typeOptions.map((t) => <option key={t} value={t}>{TYPE_LABEL.get(t) ?? t.replace("_", " ")}</option>)}
              </Select>
            </label>
            {!selectedBank && (
              <label>
                <Label>Institution</Label>
                <Input value={institution} onChange={(e) => setInstitution(e.target.value)} />
              </label>
            )}
            <label>
              <Label>Currency</Label>
              <Input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} maxLength={3} />
            </label>
            <label>
              <Label>Opening balance</Label>
              <Input value={opening} onChange={(e) => setOpening(e.target.value)} />
            </label>
            <label>
              <Label>UK tax wrapper</Label>
              <Select value={wrapper} onChange={(e) => setWrapper(e.target.value)}>
                <option value="">None</option>
                <option value="isa">ISA</option>
                <option value="lisa">Lifetime ISA</option>
                <option value="pension">Pension</option>
              </Select>
            </label>
            <div className="md:col-span-6">
              <Button type="submit">Save account</Button>
              {selectedBank && (
                <span className="ml-3 text-xs text-slate-500">
                  Statement imports for {selectedBank.name} will use its built-in preset
                  {selectedBank.notes ? ` — ${selectedBank.notes}` : ""}
                </span>
              )}
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
                <th className="w-32 px-5 py-3 text-left font-medium">UK wrapper</th>
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
                  <td className="px-5 py-3 text-slate-600">
                    {a.institution ?? "—"}
                    {a.bank_id && (
                      <span className="ml-2 text-xs text-slate-400">preset</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    {a.currency === "GBP" ? (
                      <Select
                        value={a.uk_wrapper ?? ""}
                        onChange={(e) =>
                          setWrapperMut.mutate({
                            id: a.id,
                            uk_wrapper: (e.target.value || null) as UkWrapper | null,
                          })
                        }
                        className="!w-28 !py-1 text-xs"
                      >
                        <option value="">No wrapper</option>
                        <option value="isa">ISA</option>
                        <option value="lisa">LISA</option>
                        <option value="pension">Pension</option>
                      </Select>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </td>
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
