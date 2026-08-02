import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, FileWarning, PlayCircle, ShieldCheck } from "lucide-react";

import { api } from "../api/client";
import type { AccountIntegrity, Integrity as IntegrityData, Replay, ReplayReport } from "../api/types";
import { Badge, Card, EmptyState, PageHeader, WarningBanner } from "../components/ui";
import { fmtMoney, fmtMoneySigned } from "../lib/format";

const STATUS_TONE: Record<ReplayReport["status"], "emerald" | "amber" | "rose"> = {
  ok: "emerald",
  drift: "amber",
  file_missing: "rose",
  parse_failed: "rose",
};

const STATUS_LABEL: Record<ReplayReport["status"], string> = {
  ok: "matches ledger",
  drift: "drift",
  file_missing: "file missing",
  parse_failed: "parse failed",
};

function AccountCard({ a }: { a: AccountIntegrity }) {
  const healthy = a.files_missing === 0 && a.missing_months.length === 0 && a.chain_breaks.length === 0;
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">{a.name}</h2>
        <span className="text-xs text-slate-500">
          {a.statement_count} statement{a.statement_count === 1 ? "" : "s"}
          {a.first_documented && (
            <>
              {" "}
              · <span className="nums">{a.first_documented.slice(0, 7)}</span> →{" "}
              <span className="nums">{a.last_documented?.slice(0, 7)}</span>
            </>
          )}
        </span>
      </div>

      {a.statement_count === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No statements imported yet — nothing to verify.</p>
      ) : healthy ? (
        <p className="mt-2 flex items-center gap-1.5 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4" /> Coverage is continuous and the balance chain holds.
        </p>
      ) : (
        <div className="mt-2 space-y-2">
          {a.missing_months.length > 0 && (
            <WarningBanner>
              No statements cover:{" "}
              {a.missing_months.map((m) => (
                <span key={m} className="mr-1 font-mono text-xs">
                  {m}
                </span>
              ))}
              — download those months from your bank and import them.
            </WarningBanner>
          )}
          {a.chain_breaks.map((b, i) => (
            <WarningBanner key={i}>
              Between <span className="nums">{b.prev_as_of}</span> and <span className="nums">{b.as_of}</span>{" "}
              the bank's balances imply {fmtMoneySigned(b.delta, a.currency)} of activity the ledger
              doesn't have (expected {fmtMoney(b.expected, a.currency)}, statement says{" "}
              {fmtMoney(b.attested, a.currency)}).
            </WarningBanner>
          ))}
          {a.files_missing > 0 && (
            <WarningBanner>
              {a.files_missing} stored statement file{a.files_missing === 1 ? "" : "s"} missing from disk —
              restore from a Coffer Archive to keep replay possible.
            </WarningBanner>
          )}
        </div>
      )}
    </Card>
  );
}

function ReplayResults({ replay, accountName }: { replay: Replay; accountName: (id: number) => string }) {
  const clean = replay.files_with_drift === 0 && replay.files_missing === 0 && replay.files_failed === 0;
  return (
    <div className="mt-4">
      {clean ? (
        <p className="flex items-center gap-1.5 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4" /> All {replay.files_ok} statement
          {replay.files_ok === 1 ? "" : "s"} re-parsed and every row matches the ledger.
        </p>
      ) : (
        <WarningBanner>
          {replay.files_with_drift > 0 && <>{replay.files_with_drift} file(s) drifted from the ledger. </>}
          {replay.files_missing > 0 && <>{replay.files_missing} original file(s) missing. </>}
          {replay.files_failed > 0 && <>{replay.files_failed} file(s) no longer parse.</>}
        </WarningBanner>
      )}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="py-2 text-left font-medium">File</th>
              <th className="py-2 text-left font-medium">Account</th>
              <th className="py-2 text-left font-medium">Status</th>
              <th className="py-2 text-right font-medium">Rows</th>
              <th className="py-2 text-right font-medium">Matched</th>
              <th className="py-2 text-right font-medium">Missing</th>
              <th className="py-2 text-right font-medium">Altered</th>
            </tr>
          </thead>
          <tbody>
            {replay.files.map((f) => (
              <tr key={f.statement_id} className="border-t border-slate-100 align-top">
                <td className="max-w-[220px] truncate py-2" title={f.filename}>
                  {f.filename}
                  {(f.missing_from_ledger.length > 0 || f.altered.length > 0) && (
                    <ul className="mt-1 space-y-0.5 text-xs text-slate-500">
                      {f.missing_from_ledger.map((r) => (
                        <li key={r.external_id}>
                          <FileWarning className="mr-1 inline h-3 w-3 text-amber-600" />
                          missing: {r.posted_on} {r.description} ({r.amount})
                        </li>
                      ))}
                      {f.altered.map((r) => (
                        <li key={r.external_id}>
                          <FileWarning className="mr-1 inline h-3 w-3 text-amber-600" />
                          edited: {r.description} — statement {r.amount}, ledger {r.ledger_amount}
                        </li>
                      ))}
                    </ul>
                  )}
                  {f.error && <div className="mt-1 text-xs text-rose-600">{f.error}</div>}
                </td>
                <td className="py-2 text-xs text-slate-500">{accountName(f.account_id)}</td>
                <td className="py-2">
                  <Badge tone={STATUS_TONE[f.status]}>{STATUS_LABEL[f.status]}</Badge>
                </td>
                <td className="py-2 text-right nums">{f.parsed_rows}</td>
                <td className="py-2 text-right nums">{f.matched}</td>
                <td className="py-2 text-right nums">{f.missing_count || ""}</td>
                <td className="py-2 text-right nums">{f.altered_count || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Integrity() {
  const integrity = useQuery({
    queryKey: ["integrity"],
    queryFn: async () => (await api.get<IntegrityData>("/api/v1/integrity")).data,
  });
  const replay = useMutation({
    mutationFn: async () => (await api.post<Replay>("/api/v1/integrity/replay")).data,
  });

  const accounts = integrity.data?.accounts ?? [];
  const withStatements = accounts.filter((a) => a.statement_count > 0);
  const nameById = new Map(accounts.map((a) => [a.account_id, a.name]));

  return (
    <>
      <PageHeader
        title="Integrity"
        subtitle="Your statements are the source documents — this page proves the ledger still matches them."
        right={
          <button
            onClick={() => replay.mutate()}
            disabled={replay.isPending || withStatements.length === 0}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            <PlayCircle className="h-4 w-4" />
            {replay.isPending ? "Replaying…" : "Replay statements"}
          </button>
        }
      />

      {accounts.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="h-5 w-5" />}
          title="Nothing to verify yet"
          body="Add an account and import a statement — coverage and balance checks appear here."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {accounts.map((a) => (
            <AccountCard key={a.account_id} a={a} />
          ))}
        </div>
      )}

      {replay.isError && (
        <WarningBanner className="mt-6">Replay failed — check the backend logs and try again.</WarningBanner>
      )}
      {replay.data && (
        <Card className="mt-6 p-6">
          <h2 className="text-sm font-semibold text-slate-900">Replay results</h2>
          <p className="mt-1 text-xs text-slate-500">
            Every stored original was re-parsed with the current import engine and compared to the
            ledger, row by row. Read-only — nothing was changed.
          </p>
          <ReplayResults replay={replay.data} accountName={(id) => nameById.get(id) ?? `#${id}`} />
        </Card>
      )}
    </>
  );
}
