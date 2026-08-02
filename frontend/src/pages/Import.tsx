import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileUp, Inbox, Trash2, Upload, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Account, Category, ImportProfileConfig, UkBank } from "../api/types";
import { Badge, Button, Card, Input, Label, PageHeader, Select } from "../components/ui";
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

/** A file waiting in the backfill queue. Files advance pending → reviewing →
 * done/skipped/failed; the next pending file auto-previews after each. */
interface QueuedFile {
  file: File;
  status: "pending" | "reviewing" | "done" | "failed" | "skipped";
  note?: string;
}

const QUEUE_TONE: Record<QueuedFile["status"], "slate" | "sky" | "emerald" | "rose" | "amber"> = {
  pending: "slate",
  reviewing: "sky",
  done: "emerald",
  failed: "rose",
  skipped: "amber",
};

interface InboxFile {
  filename: string;
  size: number;
  modified_at: string;
}

const SHARE_CACHE = "coffer-shared-files";

let drainInFlight = false;

/** Files parked by the service worker's share-target handler → inbox API. */
async function drainSharedFiles(): Promise<number> {
  if (!("caches" in window) || drainInFlight) return 0;
  drainInFlight = true;
  let uploaded = 0;
  try {
    const cache = await caches.open(SHARE_CACHE);
    const keys = await cache.keys();
    for (const key of keys) {
      const resp = await cache.match(key);
      if (!resp) continue;
      const blob = await resp.blob();
      const raw = resp.headers.get("X-Filename");
      const name = raw ? decodeURIComponent(raw) : "shared-statement.csv";
      const fd = new FormData();
      fd.append("file", new File([blob], name));
      try {
        await api.post("/api/v1/imports/inbox", fd, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        await cache.delete(key);
        uploaded += 1;
      } catch (err) {
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status && status >= 400 && status < 500) {
          // Deterministic rejection (bad type, too large) — retrying forever
          // would make an invisible zombie. Drop it.
          await cache.delete(key);
        }
        // Transient/network failures stay parked for the next visit.
      }
    }
  } finally {
    drainInFlight = false;
  }
  return uploaded;
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
  const inbox = useQuery({
    queryKey: ["inbox"],
    queryFn: async () => (await api.get<InboxFile[]>("/api/v1/imports/inbox")).data,
  });

  // Pick up files shared via the OS share sheet (parked by the service worker).
  useEffect(() => {
    drainSharedFiles().then((n) => {
      if (n > 0) qc.invalidateQueries({ queryKey: ["inbox"] });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const inboxPreviewMut = useMutation({
    mutationFn: async (vars: { filename: string; accountId: string }) => {
      const { data } = await api.post<PreviewResponse>(
        `/api/v1/imports/inbox/${encodeURIComponent(vars.filename)}/preview`,
        { account_id: Number(vars.accountId) },
      );
      return data;
    },
    onSuccess: (data) => {
      setPreview(data);
      const next: Record<number, RowChoice> = {};
      for (const r of data.rows) {
        next[r.id] = { skip: r.is_duplicate, category_id: r.suggested_category_id };
      }
      setChoices(next);
      setSaveProfile(Boolean(data.inferred_config) && !data.has_profile);
      setCommittedSummary(null);
      qc.invalidateQueries({ queryKey: ["inbox"] });
    },
  });
  const inboxDiscardMut = useMutation({
    mutationFn: async (filename: string) =>
      api.delete(`/api/v1/imports/inbox/${encodeURIComponent(filename)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
  });

  const [accountId, setAccountId] = useState("");
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [reviewingIndex, setReviewingIndex] = useState<number | null>(null);
  const [showNewAccount, setShowNewAccount] = useState(false);
  const [newAcct, setNewAcct] = useState({ name: "", type: "checking", currency: "GBP", bank_id: "" });
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [choices, setChoices] = useState<Record<number, RowChoice>>({});
  const [saveProfile, setSaveProfile] = useState(false);
  const [committedSummary, setCommittedSummary] = useState<{
    rows_imported: number;
    skipped_duplicates: number;
    auto_categorized: number;
  } | null>(null);

  // First-run: the fastest path to a first import is creating the account
  // right here instead of bouncing to the Accounts page.
  useEffect(() => {
    if (accounts.data && accounts.data.length === 0) setShowNewAccount(true);
  }, [accounts.data]);

  const createAccountMut = useMutation({
    mutationFn: async () =>
      (
        await api.post<Account>("/api/v1/accounts", {
          name: newAcct.name,
          type: newAcct.type,
          currency: newAcct.currency,
          bank_id: newAcct.bank_id || null,
          opening_balance: "0",
        })
      ).data,
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      setAccountId(String(a.id));
      setShowNewAccount(false);
      setNewAcct({ name: "", type: "checking", currency: "GBP", bank_id: "" });
    },
  });

  function startFile(index: number, files: QueuedFile[]) {
    setReviewingIndex(index);
    setQueue(files.map((f, i) => (i === index ? { ...f, status: "reviewing" } : f)));
    previewMut.mutate({ accountId, file: files[index].file });
  }

  /** Close out the file under review and auto-advance to the next pending one. */
  function finishCurrent(status: "done" | "failed" | "skipped", note?: string) {
    if (reviewingIndex === null) return;
    const next = queue.map((f, i) => (i === reviewingIndex ? { ...f, status, note } : f));
    setQueue(next);
    setReviewingIndex(null);
    const nextIdx = next.findIndex((f) => f.status === "pending");
    if (nextIdx !== -1) startFile(nextIdx, next);
  }

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
    },
    onError: (err) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "parse failed";
      finishCurrent("failed", detail);
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
      setCommittedSummary((prev) => ({
        rows_imported: (prev?.rows_imported ?? 0) + data.rows_imported,
        skipped_duplicates: (prev?.skipped_duplicates ?? 0) + data.skipped_duplicates,
        auto_categorized: (prev?.auto_categorized ?? 0) + data.auto_categorized,
      }));
      setPreview(null);
      finishCurrent("done", `${data.rows_imported} rows imported`);
      // An import changes balances everywhere derived data is cached.
      for (const key of ["transactions", "goals", "networth", "surplus", "coverage", "recurring", "forecast", "allowances"]) {
        qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });

  const discardMut = useMutation({
    mutationFn: async (import_id: number) =>
      api.delete(`/api/v1/imports/${import_id}`),
    onSuccess: () => {
      setPreview(null);
      finishCurrent("skipped", "discarded without importing");
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!accountId) return;
    const idx = queue.findIndex((f) => f.status === "pending");
    if (idx === -1) return;
    setCommittedSummary(null);
    startFile(idx, queue);
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

      {!preview && (inbox.data?.length ?? 0) > 0 && (
        <Card className="mb-6 p-5">
          <div className="flex items-center gap-2">
            <Inbox className="h-4 w-4 text-brand-700" />
            <h2 className="text-sm font-semibold text-slate-900">
              Statement inbox — {inbox.data?.length} file{(inbox.data?.length ?? 0) > 1 ? "s" : ""} waiting
            </h2>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Shared from your phone or dropped into the inbox folder. Pick the account, then review.
          </p>
          <ul className="mt-3 divide-y divide-slate-100">
            {inbox.data?.map((f) => (
              <li key={f.filename} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate font-medium">{f.filename}</span>
                <span className="text-xs text-slate-400 nums">{(f.size / 1024).toFixed(0)} KiB</span>
                <Button
                  className="!py-1.5"
                  disabled={!accountId || inboxPreviewMut.isPending}
                  title={accountId ? "Review this file" : "Choose an account below first"}
                  onClick={() => inboxPreviewMut.mutate({ filename: f.filename, accountId })}
                >
                  Review
                </Button>
                <button
                  onClick={() => inboxDiscardMut.mutate(f.filename)}
                  className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                  title="Discard"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
          {!accountId && (
            <p className="mt-2 text-xs text-amber-700">Select an account in the form below to enable review.</p>
          )}
          {inboxPreviewMut.isError && (
            <p className="mt-2 rounded border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700">
              Couldn't parse that file:{" "}
              {((inboxPreviewMut.error as { response?: { data?: { detail?: string } } })?.response
                ?.data?.detail) ?? "check it's a valid statement."}
            </p>
          )}
        </Card>
      )}

      {!preview && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <Card className="p-6 lg:col-span-2">
            <form onSubmit={onSubmit} className="space-y-5">
              <label>
                <Label>Account</Label>
                <Select
                  required
                  value={showNewAccount ? "__new" : accountId}
                  onChange={(e) => {
                    if (e.target.value === "__new") {
                      setShowNewAccount(true);
                    } else {
                      setShowNewAccount(false);
                      setAccountId(e.target.value);
                    }
                  }}
                >
                  <option value="">Choose an account…</option>
                  {accounts.data?.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                  <option value="__new">＋ New account…</option>
                </Select>
              </label>

              {showNewAccount && (
                <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-4">
                  <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    New account
                  </div>
                  <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
                    <label>
                      <Label>Name</Label>
                      <Input
                        value={newAcct.name}
                        onChange={(e) => setNewAcct((s) => ({ ...s, name: e.target.value }))}
                        placeholder="e.g. Monzo Current"
                      />
                    </label>
                    <label>
                      <Label>Bank</Label>
                      <Select
                        value={newAcct.bank_id}
                        onChange={(e) => setNewAcct((s) => ({ ...s, bank_id: e.target.value }))}
                      >
                        <option value="">Not listed / manual</option>
                        {banks.data?.map((b) => (
                          <option key={b.id} value={b.id}>{b.name}</option>
                        ))}
                      </Select>
                    </label>
                    <label>
                      <Label>Type</Label>
                      <Select
                        value={newAcct.type}
                        onChange={(e) => setNewAcct((s) => ({ ...s, type: e.target.value }))}
                      >
                        <option value="checking">Checking / current</option>
                        <option value="savings">Savings</option>
                        <option value="credit_card">Credit card</option>
                        <option value="loan">Loan</option>
                        <option value="overdraft">Overdraft</option>
                        <option value="cash">Cash</option>
                        <option value="other">Other (pension, property…)</option>
                      </Select>
                    </label>
                    <label>
                      <Label>Currency</Label>
                      <Input
                        value={newAcct.currency}
                        maxLength={3}
                        onChange={(e) => setNewAcct((s) => ({ ...s, currency: e.target.value.toUpperCase() }))}
                        className="uppercase"
                      />
                    </label>
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <Button
                      type="button"
                      className="!py-1.5"
                      disabled={!newAcct.name.trim() || !/^[A-Z]{3}$/.test(newAcct.currency) || createAccountMut.isPending}
                      onClick={() => createAccountMut.mutate()}
                    >
                      {createAccountMut.isPending ? "Creating…" : "Create account"}
                    </Button>
                    {(accounts.data?.length ?? 0) > 0 && (
                      <button
                        type="button"
                        onClick={() => setShowNewAccount(false)}
                        className="text-xs text-slate-500 hover:text-slate-700"
                      >
                        Cancel
                      </button>
                    )}
                    {createAccountMut.isError && (
                      <span className="text-xs text-rose-600">Couldn't create the account — check the fields.</span>
                    )}
                  </div>
                </div>
              )}

              <label className="block">
                <Label>Files (CSV, OFX, QIF, or PDF — select several to backfill months at once)</Label>
                <div className="mt-1 flex items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 px-6 py-8 hover:border-brand-500 hover:bg-brand-50/30 transition cursor-pointer">
                  <input
                    type="file" required multiple accept=".csv,.ofx,.qfx,.qif,.pdf"
                    onChange={(e) =>
                      setQueue(Array.from(e.target.files ?? []).map((file) => ({ file, status: "pending" as const })))
                    }
                    className="sr-only"
                    id="statement-file"
                  />
                  <label htmlFor="statement-file" className="text-center cursor-pointer">
                    <FileUp className="mx-auto h-8 w-8 text-slate-400" />
                    <div className="mt-2 text-sm font-medium text-slate-900">
                      {queue.length === 0
                        ? "Click to choose files"
                        : queue.length === 1
                          ? queue[0].file.name
                          : `${queue.length} files queued`}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">CSV, OFX, QIF, or PDF</div>
                  </label>
                </div>
              </label>

              {queue.length > 1 && (
                <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                  {queue.map((q, i) => (
                    <li key={i} className="flex items-center gap-3 px-3 py-1.5 text-sm">
                      <span className="min-w-0 flex-1 truncate">{q.file.name}</span>
                      {q.note && <span className="max-w-[200px] truncate text-xs text-slate-400">{q.note}</span>}
                      <Badge tone={QUEUE_TONE[q.status]}>{q.status}</Badge>
                    </li>
                  ))}
                </ul>
              )}

              <Button
                type="submit"
                disabled={previewMut.isPending || !accountId || !queue.some((f) => f.status === "pending")}
              >
                <Upload className="h-4 w-4" />
                {previewMut.isPending
                  ? "Parsing…"
                  : queue.length > 1
                    ? "Start review queue"
                    : "Parse for review"}
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
