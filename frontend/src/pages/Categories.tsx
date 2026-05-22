import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListTree, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Category, CategoryKind } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Label, PageHeader, Select } from "../components/ui";

const KINDS: CategoryKind[] = ["income", "expense", "debt_payment", "saving"];

const KIND_TONE: Record<CategoryKind, "emerald" | "rose" | "amber" | "sky"> = {
  income: "emerald",
  expense: "rose",
  debt_payment: "amber",
  saving: "sky",
};

export default function Categories() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["categories"],
    queryFn: async () => (await api.get<Category[]>("/api/v1/categories")).data,
  });
  const create = useMutation({
    mutationFn: async (payload: { name: string; kind: CategoryKind }) =>
      (await api.post("/api/v1/categories", payload)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
  });
  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/categories/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["categories"] }),
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CategoryKind>("expense");

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { name, kind },
      { onSuccess: () => { setName(""); setShowForm(false); } },
    );
  }

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
          <form onSubmit={onSubmit} className="grid grid-cols-1 gap-3 md:grid-cols-4">
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

      {list.data && list.data.length === 0 ? (
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
              {list.data?.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 hover:bg-slate-50/50">
                  <td className="px-5 py-3 font-medium text-slate-900">{c.name}</td>
                  <td className="px-5 py-3">
                    <Badge tone={KIND_TONE[c.kind]}>{c.kind.replace("_", " ")}</Badge>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button
                      onClick={() => remove.mutate(c.id)}
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
