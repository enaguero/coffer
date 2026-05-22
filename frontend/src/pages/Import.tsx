import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, FileUp, Upload } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Account, ImportResponse } from "../api/types";
import { Button, Card, Label, PageHeader, Select } from "../components/ui";

export default function Import() {
  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });

  const [accountId, setAccountId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResponse | null>(null);

  const upload = useMutation({
    mutationFn: async (vars: { accountId: string; file: File }) => {
      const fd = new FormData();
      fd.append("account_id", vars.accountId);
      fd.append("file", vars.file);
      const { data } = await api.post<ImportResponse>("/api/v1/imports/upload", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data;
    },
    onSuccess: (data) => setResult(data),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!accountId || !file) return;
    setResult(null);
    upload.mutate({ accountId, file });
  }

  return (
    <>
      <PageHeader
        title="Import statement"
        subtitle="Upload a CSV or PDF — we parse dates, descriptions, and amounts."
      />

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
              <Label>File (CSV or PDF)</Label>
              <div className="mt-1 flex items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 px-6 py-8 hover:border-brand-500 hover:bg-brand-50/30 transition cursor-pointer">
                <input
                  type="file" required accept=".csv,.pdf"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="sr-only"
                  id="statement-file"
                />
                <label htmlFor="statement-file" className="text-center cursor-pointer">
                  <FileUp className="mx-auto h-8 w-8 text-slate-400" />
                  <div className="mt-2 text-sm font-medium text-slate-900">
                    {file ? file.name : "Click to choose a file"}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">CSV or PDF · up to 10MB</div>
                </label>
              </div>
            </label>

            <Button type="submit" disabled={upload.isPending || !file || !accountId}>
              <Upload className="h-4 w-4" />
              {upload.isPending ? "Uploading…" : "Upload"}
            </Button>

            {upload.isError && (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                Upload failed: {(upload.error as Error).message}
              </div>
            )}

            {result && (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm">
                <div className="flex items-center gap-2 font-semibold text-emerald-800">
                  <CheckCircle2 className="h-4 w-4" />
                  Import complete
                </div>
                <ul className="mt-2 space-y-0.5 text-emerald-700 nums">
                  <li>Parsed: <span className="font-semibold">{result.rows_parsed}</span></li>
                  <li>Imported: <span className="font-semibold">{result.rows_imported}</span></li>
                  <li>Skipped duplicates: <span className="font-semibold">{result.skipped_duplicates}</span></li>
                </ul>
              </div>
            )}
          </form>
        </Card>

        <Card className="p-6">
          <h3 className="text-sm font-semibold text-slate-900">Notes</h3>
          <ul className="mt-3 space-y-2 text-sm text-slate-600">
            <li>Most bank CSVs work — we sniff the dialect and look for date / description / amount columns.</li>
            <li>PDFs use table extraction first, falling back to line-by-line parsing.</li>
            <li>Negative amounts are treated as outflows, positive as inflows.</li>
            <li>Re-uploads de-dupe by date + description + amount.</li>
          </ul>
        </Card>
      </div>
    </>
  );
}
