import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, Link2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  BankConnection,
  InstitutionRef,
  LinkStartResponse,
  SyncJob,
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

// EU/UK countries supported by GoCardless Bank Account Data. The picker lists
// these; users can free-type a different ISO-2 if needed.
const COMMON_COUNTRIES = [
  ["GB", "United Kingdom"],
  ["IE", "Ireland"],
  ["FR", "France"],
  ["DE", "Germany"],
  ["ES", "Spain"],
  ["IT", "Italy"],
  ["NL", "Netherlands"],
  ["BE", "Belgium"],
  ["PT", "Portugal"],
  ["AT", "Austria"],
  ["FI", "Finland"],
  ["SE", "Sweden"],
  ["DK", "Denmark"],
  ["PL", "Poland"],
] as const;

function statusTone(s: BankConnection["status"]): "emerald" | "amber" | "rose" | "slate" {
  switch (s) {
    case "linked":
      return "emerald";
    case "pending":
      return "amber";
    case "expired":
      return "amber";
    case "revoked":
      return "rose";
    default:
      return "slate";
  }
}

function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  return Math.floor(ms / 86_400_000);
}

export default function BankConnections() {
  const qc = useQueryClient();
  const connections = useQuery({
    queryKey: ["bank-connections"],
    queryFn: async () =>
      (await api.get<BankConnection[]>("/api/v1/bank-connections")).data,
  });
  const syncJobs = useQuery({
    queryKey: ["sync-jobs"],
    queryFn: async () =>
      (await api.get<SyncJob[]>("/api/v1/bank-connections/sync-jobs?limit=50")).data,
    refetchInterval: 5_000,
  });

  const [showPicker, setShowPicker] = useState(false);
  const [country, setCountry] = useState<string>("GB");
  const [filter, setFilter] = useState("");

  const institutions = useQuery({
    queryKey: ["institutions", country],
    queryFn: async () =>
      (
        await api.get<InstitutionRef[]>(
          `/api/v1/bank-connections/institutions?country=${country}`,
        )
      ).data,
    enabled: showPicker,
  });

  const linkStart = useMutation({
    mutationFn: async (institutionId: string) => {
      const { data } = await api.post<LinkStartResponse>(
        "/api/v1/bank-connections/link/start",
        { institution_id: institutionId, country },
      );
      return data;
    },
    onSuccess: (data) => {
      // Hand off to the bank's auth UI. They'll send the user back to
      // /banks/callback with `?ref=<requisition_id>`.
      window.location.href = data.link_url;
    },
  });

  const sync = useMutation({
    mutationFn: async (id: number) => api.post(`/api/v1/bank-connections/${id}/sync`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sync-jobs"] });
    },
  });

  const disconnect = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/bank-connections/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["bank-connections"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  const filteredInstitutions = useMemo(() => {
    const lower = filter.toLowerCase().trim();
    const list = institutions.data ?? [];
    return lower ? list.filter((i) => i.name.toLowerCase().includes(lower)) : list;
  }, [institutions.data, filter]);

  const latestJobByConnection = useMemo(() => {
    const map = new Map<number, SyncJob>();
    for (const job of syncJobs.data ?? []) {
      const existing = map.get(job.bank_connection_id);
      if (!existing || job.started_at > existing.started_at) {
        map.set(job.bank_connection_id, job);
      }
    }
    return map;
  }, [syncJobs.data]);

  return (
    <>
      <PageHeader
        title="Banks"
        subtitle="Link a bank via Open Banking to import transactions automatically."
        right={
          <Button onClick={() => setShowPicker((s) => !s)}>
            <Plus className="h-4 w-4" />
            {showPicker ? "Cancel" : "Connect a bank"}
          </Button>
        }
      />

      {showPicker && (
        <Card className="mb-6 p-6">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <label className="md:col-span-1">
              <Label>Country</Label>
              <Select value={country} onChange={(e) => setCountry(e.target.value)}>
                {COMMON_COUNTRIES.map(([code, name]) => (
                  <option key={code} value={code}>
                    {code} — {name}
                  </option>
                ))}
              </Select>
            </label>
            <label className="md:col-span-2">
              <Label>Search</Label>
              <Input
                placeholder="Bank name…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </label>
          </div>

          <div className="mt-4 max-h-80 overflow-y-auto rounded-lg border border-slate-200">
            {institutions.isLoading && (
              <div className="px-4 py-6 text-sm text-slate-500">Loading banks…</div>
            )}
            {institutions.isError && (
              <div className="px-4 py-6 text-sm text-rose-700">
                {(institutions.error as Error).message}
              </div>
            )}
            {institutions.data && filteredInstitutions.length === 0 && (
              <div className="px-4 py-6 text-sm text-slate-500">No banks match that search.</div>
            )}
            <ul className="divide-y divide-slate-100">
              {filteredInstitutions.map((inst) => (
                <li key={inst.id} className="flex items-center gap-3 px-4 py-2">
                  {inst.logo_url ? (
                    <img
                      src={inst.logo_url}
                      alt=""
                      className="h-7 w-7 rounded object-contain"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
                      }}
                    />
                  ) : (
                    <div className="flex h-7 w-7 items-center justify-center rounded bg-slate-100 text-slate-500">
                      <Banknote className="h-4 w-4" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1 truncate text-sm text-slate-900">
                    {inst.name}
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => linkStart.mutate(inst.id)}
                    disabled={linkStart.isPending}
                  >
                    <Link2 className="h-4 w-4" /> Connect
                  </Button>
                </li>
              ))}
            </ul>
          </div>
          {linkStart.isError && (
            <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {(linkStart.error as Error).message}
            </div>
          )}
        </Card>
      )}

      {connections.data && connections.data.length === 0 ? (
        <EmptyState
          icon={<Banknote className="h-5 w-5" />}
          title="No banks linked yet"
          body="Connect a bank to start pulling transactions automatically."
        />
      ) : (
        <div className="space-y-3">
          {connections.data?.map((c) => {
            const days = daysUntil(c.requisition_expires_at);
            const latest = latestJobByConnection.get(c.id);
            return (
              <Card key={c.id} className="p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-sm font-semibold text-slate-900">
                        {c.institution_name}
                      </div>
                      <Badge tone={statusTone(c.status)}>{c.status}</Badge>
                    </div>
                    {days !== null && c.status === "linked" && (
                      <div
                        className={`mt-1 text-xs ${
                          days <= 7 ? "text-amber-700" : "text-slate-500"
                        }`}
                      >
                        Re-authenticate in {days} day{days === 1 ? "" : "s"}
                      </div>
                    )}
                    {latest && (
                      <div className="mt-2 text-xs text-slate-500 nums">
                        Last sync: {new Date(latest.started_at).toLocaleString()} ·{" "}
                        <Badge tone={latest.status === "failed" ? "rose" : "sky"}>
                          {latest.status}
                        </Badge>{" "}
                        {latest.transactions_imported} imported / {latest.transactions_fetched}{" "}
                        fetched
                        {latest.error_message && (
                          <span className="ml-2 text-rose-700">— {latest.error_message}</span>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="secondary"
                      onClick={() => sync.mutate(c.id)}
                      disabled={sync.isPending || c.status !== "linked"}
                    >
                      <RefreshCw
                        className={`h-4 w-4 ${sync.isPending ? "animate-spin" : ""}`}
                      />
                      Sync now
                    </Button>
                    <button
                      onClick={() => {
                        if (window.confirm(`Disconnect ${c.institution_name}?`)) {
                          disconnect.mutate(c.id);
                        }
                      }}
                      className="rounded p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                      title="Disconnect"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
