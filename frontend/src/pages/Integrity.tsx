import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, PlayCircle, ShieldCheck } from "lucide-react";

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
  const healthy = a.files_missing === 0 && a.missing_month_count === 0 && a.chain_break_count === 0;
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
          {a.missing_month_count > 0 && (
            <WarningBanner>
              No statements cover:{" "}
              {a.missing_months.map((m) => (
                <span key={m} className="mr-1 font-mono text-xs">
                  {m}
                </span>
              ))}
              {a.missing_month_count > a.missing_months.length && (
                <> …and {a.missing_month_count - a.missing_months.length} more </>
              )}
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
          {a.chain_break_count > a.chain_breaks.length && (
            <WarningBanner>
              …and {a.chain_break_count - a.chain_breaks.length} more balance-chain breaks — a
              systematic offset (missing statement run, duplicate import) is likely.
            </WarningBanner>
          )}
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

function FileRow({ f, currency, accountName }: { f: ReplayReport; currency: string; accountName: string }) {
  const details = f.missing_from_ledger.length > 0 || f.altered.length > 0 || f.error || f.skipped > 0;
  const undetailed = f.missing_count - f.missing_from_ledger.length + (f.altered_count - f.altered.length);
  return (
    <>
      <tr className="border-t border-slate-100">
        <td className="max-w-[220px] truncate py-2" title={f.filename}>
          {f.filename}
        </td>
        <td className="py-2 text-xs text-slate-500">{accountName}</td>
        <td className="py-2">
          <Badge tone={STATUS_TONE[f.status]}>{STATUS_LABEL[f.status]}</Badge>
        </td>
        <td className="py-2 text-right nums">{f.parsed_rows}</td>
        <td className="py-2 text-right nums">{f.matched}</td>
        <td className="py-2 text-right nums">{f.missing_count > 0 ? f.missing_count : "—"}</td>
        <td className="py-2 text-right nums">{f.altered_count > 0 ? f.altered_count : "—"}</td>
      </tr>
      {details && (
        <tr>
          <td colSpan={7} className="pb-2 pl-4 text-xs text-slate-500">
            <ul className="space-y-0.5 whitespace-normal break-words">
              {f.skipped > 0 && (
                <li className="text-slate-400">
                  {f.skipped} row{f.skipped === 1 ? "" : "s"} you deselected at import — not counted as drift.
                </li>
              )}
              {f.missing_from_ledger.map((r, i) => (
                <li key={`m${i}`} className="text-amber-700">
                  missing from ledger: {r.posted_on} {r.description} ({fmtMoneySigned(r.amount, currency)})
                </li>
              ))}
              {f.altered.map((r, i) => (
                <li key={`a${i}`} className="text-amber-700">
                  edited: {r.description} — statement {fmtMoneySigned(r.amount, currency)}, ledger{" "}
                  {fmtMoneySigned(r.ledger_amount, currency)}
                  {r.ledger_posted_on && r.ledger_posted_on !== r.posted_on && <> (on {r.ledger_posted_on})</>}
                </li>
              ))}
              {undetailed > 0 && <li className="text-slate-400">…and {undetailed} more rows not shown.</li>}
              {f.error && <li className="text-rose-600">{f.error}</li>}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}

export default function Integrity() {
  const integrity = useQuery({
    queryKey: ["integrity"],
    queryFn: async () => (await api.get<IntegrityData>("/api/v1/integrity")).data,
  });

  const accounts = integrity.data?.accounts ?? [];
  const withStatements = accounts.filter((a) => a.statement_count > 0);
  const byId = new Map(accounts.map((a) => [a.account_id, a]));

  // One request per account keeps each replay small — a decade of PDFs in a
  // single request would pin the backend for minutes.
  const replay = useMutation({
    mutationFn: async () => {
      const parts: Replay[] = [];
      for (const a of withStatements) {
        parts.push((await api.post<Replay>(`/api/v1/integrity/replay?account_id=${a.account_id}`)).data);
      }
      return {
        files: parts.flatMap((p) => p.files),
        files_ok: parts.reduce((n, p) => n + p.files_ok, 0),
        files_with_drift: parts.reduce((n, p) => n + p.files_with_drift, 0),
        files_missing: parts.reduce((n, p) => n + p.files_missing, 0),
        files_failed: parts.reduce((n, p) => n + p.files_failed, 0),
      } satisfies Replay;
    },
  });

  const r = replay.data;
  const clean = r && r.files_with_drift === 0 && r.files_missing === 0 && r.files_failed === 0;

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

      {integrity.isError ? (
        <WarningBanner>
          Couldn't load the integrity summary — the check did not run. Try again.
        </WarningBanner>
      ) : integrity.isPending ? (
        <p className="text-sm text-slate-500">Checking coverage and balance chains…</p>
      ) : accounts.length === 0 ? (
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
        <WarningBanner className="mt-6">Replay failed part-way — results below may be incomplete.</WarningBanner>
      )}
      {r && (
        <Card className="mt-6 p-6">
          <h2 className="text-sm font-semibold text-slate-900">Replay results</h2>
          <p className="mt-1 text-xs text-slate-500">
            Every stored original was re-parsed with the current import engine and compared to the
            ledger by date and amount (descriptions aren't compared). Read-only — nothing was changed.
          </p>
          <div className="mt-4">
            {clean ? (
              <p className="flex items-center gap-1.5 text-sm text-emerald-700">
                <CheckCircle2 className="h-4 w-4" /> All {r.files_ok} statement
                {r.files_ok === 1 ? "" : "s"} re-parsed and every row matches the ledger.
              </p>
            ) : (
              <WarningBanner>
                {r.files_with_drift > 0 && <>{r.files_with_drift} file(s) drifted from the ledger. </>}
                {r.files_missing > 0 && <>{r.files_missing} original file(s) missing. </>}
                {r.files_failed > 0 && <>{r.files_failed} file(s) no longer parse.</>}
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
                  {r.files.map((f) => (
                    <FileRow
                      key={f.statement_id}
                      f={f}
                      currency={byId.get(f.account_id)?.currency ?? "USD"}
                      accountName={byId.get(f.account_id)?.name ?? `#${f.account_id}`}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      )}
    </>
  );
}
