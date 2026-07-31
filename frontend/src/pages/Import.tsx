import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileUp, Upload, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Account, Category, ImportProfileConfig, UkBank } from "../api/types";
import { Badge, Button, Card, Label, PageHeader, Select } from "../components/ui";
import { fmtMoneySigned, toNum } from "../lib/format";

interface PreviewRow {
  id: number;
  external_id: string | null;
  posted_on: string;
  description: string;
  amount: string;
  suggested_category_id: number | null;
  is_duplicate: boolean;
}

interface PreviewResponse {
  import_id: number;
  account_id: number;
  filename: string;
  rows: PreviewRow[];
  duplicate_count: number;
  auto_categorized_count: number;
  source: string;
  warnings: string[];
  inferred_config: ImportProfileConfig | null;
  has_profile: boolean;
}

interface RowChoice {
  skip: boolean;
  category_id: number | null;
}

function sourceLabel(source: string, banks: UkBank[] | undefined): string {
  if (source === "ofx") return "Parsed from OFX (exact bank transaction IDs)";
  if (source === "qif") return "Parsed from QIF";
  if (source === "profile") return "Parsed with this account's saved profile";
  if (source === "heuristic") return "Parsed with automatic detection";
  if (source.startsWith("preset:")) {
    const bankId = source.slice("preset:".length);
    const bank = banks?.find((b) => b.id === bankId);
    return `Parsed with the ${bank?.name ?? bankId} preset`;
  }
  if (source.startsWith("adapter:")) return "Parsed with the bank-specific importer";
  return source;
}

export default function Import() {
  const qc = useQueryClient();
  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: async () => (await api.get<Category[]>("/api/v1/categories")).data,
  });
  const banks = useQuery({
    queryKey: ["banks"],
    queryFn: async () => (await api.get<UkBank[]>("/api/v1/banks")).data,
  });

  const [accountId, setAccountId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [choices, setChoices] = useState<Record<number, RowChoice>>({});
  const [saveProfile, setSaveProfile] = useState(false);
  const [committedSummary, setCommittedSummary] = useState<{
    rows_imported: number;
    skipped_duplicates: number;
    auto_categorized: number;
  } | null>(null);

  const previewMut = useMutation({
    mutationFn: async (vars: { accountId: string; file: File }) => {
      const fd = new FormData();
      fd.append("account_id", vars.accountId);
      fd.append("file", vars.file);
      const { data } = await api.post<PreviewResponse>("/api/v1/imports/preview", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data;
    },
    onSuccess: (data) => {
      setPreview(data);
      // Seed choices: duplicates start skipped; everything else uses the suggestion.
      const next: Record<number, RowChoice> = {};
      for (const r of data.rows) {
        next[r.id] = {
          skip: r.is_duplicate,
          category_id: r.suggested_category_id,
        };
      }
      setChoices(next);
      // Default to remembering the detected layout when there's nothing saved yet.
      setSaveProfile(Boolean(data.inferred_config) && !data.has_profile);
      setCommittedSummary(null);
    },
  });

  const confirmMut = useMutation({
    mutationFn: async (vars: {
      import_id: number;
      rows: Array<{ id: number; skip: boolean; category_id: number | null }>;
    }) => {
      const { data } = await api.post<{
        rows_imported: number;
        skipped_duplicates: number;
        auto_categorized: number;
      }>(`/api/v1/imports/${vars.import_id}/confirm`, { rows: vars.rows });
      return data;
    },
    onSuccess: async (data) => {
      if (preview && saveProfile && preview.inferred_config) {
        // Best-effort: a failed profile save shouldn't obscure a successful import.
        try {
          await api.put(`/api/v1/accounts/${preview.account_id}/import-profile`, {
            name: `From ${preview.filename}`,
            source: "inferred",
            config: preview.inferred_config,
          });
        } catch {
          // ignore
        }
      }
      setCommittedSummary(data);
      setPreview(null);
      setFile(null);
      qc.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  const discardMut = useMutation({
    mutationFn: async (import_id: number) =>
      api.delete(`/api/v1/imports/${import_id}`),
    onSuccess: () => {
      setPreview(null);
      setFile(null);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!accountId || !file) return;
    setCommittedSummary(null);
    previewMut.mutate({ accountId, file });
  }

  function setRow(id: number, patch: Partial<RowChoice>) {
    setChoices((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  function commit() {
    if (!preview) return;
    const rows = preview.rows.map((r) => ({
      id: r.id,
      skip: choices[r.id]?.skip ?? false,
      category_id: choices[r.id]?.category_id ?? null,
    }));
    confirmMut.mutate({ import_id: preview.import_id, rows });
  }

  const selectedCount = preview
    ? preview.rows.filter((r) => !choices[r.id]?.skip).length
    : 0;

  return (
    <>
      <PageHeader
        title="Import statement"
        subtitle="Upload a statement downloaded from your bank — review the parsed rows, then commit."
      />

      {!preview && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <Card className="p-6 lg:col-span-2">
            <form onSubmit={onSubmit} className="space-y-5">
              <label>
                <Label>Account</Label>
                <Select required value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                  <option value="">Choose an account…</option>
                  {accounts.data?.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </Select>
              </label>

              <label className="block">
                <Label>File (CSV, OFX, QIF, or PDF)</Label>
                <div className="mt-1 flex items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 px-6 py-8 hover:border-brand-500 hover:bg-brand-50/30 transition cursor-pointer">
                  <input
                    type="file" required accept=".csv,.ofx,.qfx,.qif,.pdf"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    className="sr-only"
                    id="statement-file"
                  />
                  <label htmlFor="statement-file" className="text-center cursor-pointer">
                    <FileUp className="mx-auto h-8 w-8 text-slate-400" />
                    <div className="mt-2 text-sm font-medium text-slate-900">
                      {file ? file.name : "Click to choose a file"}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">CSV, OFX, QIF, or PDF</div>
                  </label>
                </div>
              </label>

              <Button type="submit" disabled={previewMut.isPending || !file || !accountId}>
                <Upload className="h-4 w-4" />
                {previewMut.isPending ? "Parsing…" : "Parse for review"}
              </Button>

              {previewMut.isError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  Parse failed: {(previewMut.error as Error).message}
                </div>
              )}

              {committedSummary && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm">
                  <div className="flex items-center gap-2 font-semibold text-emerald-800">
                    <CheckCircle2 className="h-4 w-4" />
                    Import complete
                  </div>
                  <ul className="mt-2 space-y-0.5 text-emerald-700 nums">
                    <li>Imported: <span className="font-semibold">{committedSummary.rows_imported}</span></li>
                    <li>Auto-categorized: <span className="font-semibold">{committedSummary.auto_categorized}</span></li>
                    <li>Skipped duplicates: <span className="font-semibold">{committedSummary.skipped_duplicates}</span></li>
                  </ul>
                </div>
              )}
            </form>
          </Card>

          <Card className="p-6">
            <h3 className="text-sm font-semibold text-slate-900">Notes</h3>
            <ul className="mt-3 space-y-2 text-sm text-slate-600">
              <li>Accounts linked to a UK bank parse with that bank's built-in preset.</li>
              <li>Prefer OFX/QIF downloads when your bank offers them — they carry exact transaction IDs.</li>
              <li>Other CSVs are sniffed automatically, and the detected layout can be saved as the account's profile.</li>
              <li>Negative amounts are outflows, positive are inflows (credit-card presets flip signs for you).</li>
              <li>Re-uploads dedupe within the same account, so overlapping statements are safe.</li>
              <li>Auto-categorization runs from rules defined on the Categories page.</li>
            </ul>
          </Card>
        </div>
      )}

      {preview && (
        <Card className="overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-5 py-3">
            <div className="text-sm">
              <span className="font-semibold text-slate-900">{preview.filename}</span>
              <span className="ml-2 text-slate-500 nums">
                {preview.rows.length} rows
                {preview.duplicate_count > 0 && (
                  <> · <Badge tone="amber">{preview.duplicate_count} duplicates</Badge></>
                )}
                {preview.auto_categorized_count > 0 && (
                  <> · <Badge tone="sky">{preview.auto_categorized_count} auto-categorized</Badge></>
                )}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                onClick={() => discardMut.mutate(preview.import_id)}
                disabled={discardMut.isPending}
              >
                <X className="h-4 w-4" /> Discard
              </Button>
              <Button onClick={commit} disabled={confirmMut.isPending || selectedCount === 0}>
                <CheckCircle2 className="h-4 w-4" />
                {confirmMut.isPending ? "Importing…" : `Import ${selectedCount} rows`}
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-slate-200 bg-white px-5 py-2 text-xs text-slate-500">
            <span>{sourceLabel(preview.source, banks.data)}</span>
            {preview.inferred_config && (
              <label className="flex cursor-pointer items-center gap-1.5 text-slate-600">
                <input
                  type="checkbox"
                  checked={saveProfile}
                  onChange={(e) => setSaveProfile(e.target.checked)}
                />
                {preview.has_profile
                  ? "Update this account's import profile with this layout"
                  : "Remember this layout as the account's import profile"}
              </label>
            )}
          </div>
          {preview.warnings.length > 0 && (
            <div className="border-b border-amber-200 bg-amber-50 px-5 py-2 text-xs text-amber-800">
              {preview.warnings.map((w, i) => (
                <div key={i}>{w}</div>
              ))}
            </div>
          )}

          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-10 px-3 py-3">Keep</th>
                <th className="w-28 px-5 py-3 text-left font-medium">Date</th>
                <th className="px-5 py-3 text-left font-medium">Description</th>
                <th className="w-56 px-5 py-3 text-left font-medium">Category</th>
                <th className="w-32 px-5 py-3 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((r) => {
                const choice = choices[r.id];
                const amt = toNum(r.amount);
                return (
                  <tr
                    key={r.id}
                    className={`border-t border-slate-100 ${
                      choice?.skip ? "bg-slate-50/60 text-slate-400" : ""
                    }`}
                  >
                    <td className="px-3 py-2 text-center">
                      <input
                        type="checkbox"
                        checked={!choice?.skip}
                        onChange={(e) => setRow(r.id, { skip: !e.target.checked })}
                      />
                    </td>
                    <td className="px-5 py-2 nums">{r.posted_on}</td>
                    <td className="px-5 py-2">
                      <div>{r.description}</div>
                      {r.is_duplicate && (
                        <div className="mt-0.5 text-xs text-amber-700">Duplicate of existing transaction</div>
                      )}
                    </td>
                    <td className="px-5 py-2">
                      <Select
                        value={choice?.category_id ?? ""}
                        onChange={(e) =>
                          setRow(r.id, {
                            category_id: e.target.value ? Number(e.target.value) : null,
                          })
                        }
                        className="!py-1"
                        disabled={choice?.skip}
                      >
                        <option value="">Uncategorized</option>
                        {categories.data?.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </Select>
                    </td>
                    <td
                      className={`px-5 py-2 text-right nums font-medium ${
                        choice?.skip
                          ? ""
                          : amt < 0 ? "text-rose-600" : "text-emerald-600"
                      }`}
                    >
                      {fmtMoneySigned(amt)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
