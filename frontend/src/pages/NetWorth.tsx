import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Landmark, PiggyBank, RefreshCw, Scale } from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";
import type { Account, Allowances, FxRate, FxRefreshOut, NetWorth as NetWorthData } from "../api/types";
import {
  Badge,
  Button,
  Card,
  Input,
  Label,
  PageHeader,
  ProgressBar,
  Select,
  StatCard,
  WarningBanner,
} from "../components/ui";
import { useAuth } from "../contexts/useAuth";
import { fmtMoney, fmtMonthYear, toNum } from "../lib/format";
import { useUserCurrency } from "../lib/useCurrency";

const WRAPPER_LABEL: Record<string, string> = {
  isa: "ISA allowance",
  lisa: "Lifetime ISA allowance",
  pension: "Pension annual allowance",
};

const SOURCE_TONE: Record<string, "emerald" | "sky" | "slate"> = {
  statement: "emerald",
  manual: "sky",
  derived: "slate",
  opening: "slate",
};

// Rates travel as strings so high-precision values survive; parseFloat
// would silently truncate inputs like "1,000" to 1.
const RATE_RE = /^(\d+\.?\d*|\.\d+)$/;

export default function NetWorth() {
  const qc = useQueryClient();
  const currency = useUserCurrency();
  const { user, refresh } = useAuth();
  const fxRates = useQuery({
    queryKey: ["fx"],
    queryFn: async () => (await api.get<FxRate[]>("/api/v1/fx")).data,
  });
  const [newFxCurrency, setNewFxCurrency] = useState("");
  const [newFxRate, setNewFxRate] = useState("");
  // Per-row draft edits, keyed by currency — committed on blur, reverted when
  // invalid. Auto rows stay editable: a manual PUT flips them to "manual".
  const [rateEdits, setRateEdits] = useState<Record<string, string>>({});
  const rateValid = RATE_RE.test(newFxRate) && Number(newFxRate) > 0;
  const invalidateFx = (keys: string[]) => {
    for (const key of keys) qc.invalidateQueries({ queryKey: [key] });
  };
  // One PUT for both the add-rate form and per-row edits; the form clears its
  // own fields via a per-call onSuccess so a row commit can't wipe a
  // half-typed new rate.
  const putRate = useMutation({
    mutationFn: async (vars: { currency: string; rate: string }) =>
      api.put("/api/v1/fx", [{ currency: vars.currency, rate: vars.rate }]),
    onSuccess: () => invalidateFx(["fx", "networth"]),
  });
  const deleteRate = useMutation({
    mutationFn: async (currency: string) => api.delete(`/api/v1/fx/${currency}`),
    onSuccess: () => invalidateFx(["fx", "networth"]),
  });
  const setFxAutoRefresh = useMutation({
    mutationFn: async (fx_auto_refresh: boolean) => api.patch("/api/v1/auth/me", { fx_auto_refresh }),
    onSuccess: () => {
      // Enabling lets the next /fx read pull fresh auto rates — refetch them
      // AND the converted totals that depend on them, or the page keeps
      // showing pre-toggle numbers. Invalidate before refresh() so a failed
      // user refetch can't strand stale rates (mirrors setDisplayCurrency's
      // ordering).
      invalidateFx(["fx", "networth", "forecast"]);
      void refresh().catch(() => {});
    },
  });
  const refreshRates = useMutation({
    mutationFn: async () => (await api.post<FxRefreshOut>("/api/v1/fx/refresh")).data,
    onSuccess: () => invalidateFx(["fx", "networth", "forecast"]),
  });
  const setDisplayCurrency = useMutation({
    // null clears the setting back to automatic (most-common currency).
    mutationFn: async (display_currency: string | null) =>
      api.patch("/api/v1/auth/me", { display_currency }),
    onSuccess: () => {
      // The server deletes saved rates on a display change — refetch them too.
      // Invalidate before refresh() so a failed user refetch can't strand
      // stale totals.
      invalidateFx(["fx", "networth", "forecast"]);
      void refresh().catch(() => {});
    },
  });
  const networth = useQuery({
    queryKey: ["networth"],
    queryFn: async () => (await api.get<NetWorthData>("/api/v1/insights/networth")).data,
  });
  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });
  const allowances = useQuery({
    queryKey: ["allowances"],
    queryFn: async () => (await api.get<Allowances>("/api/v1/insights/allowances")).data,
    // Only ask once the accounts list shows a wrapped account — saves a wasted
    // request for users with no UK wrappers.
    enabled: accounts.data?.some((a) => a.uk_wrapper != null) ?? false,
  });

  const [valAccount, setValAccount] = useState("");
  const [valDate, setValDate] = useState(new Date().toISOString().slice(0, 10));
  const [valAmount, setValAmount] = useState("");

  const addValuation = useMutation({
    mutationFn: async () =>
      api.post(`/api/v1/accounts/${valAccount}/snapshots`, { as_of: valDate, balance: valAmount }),
    onSuccess: () => {
      setValAmount("");
      qc.invalidateQueries({ queryKey: ["networth"] });
    },
  });

  function onAddValuation(e: FormEvent) {
    e.preventDefault();
    if (!valAccount || !valAmount) return;
    addValuation.mutate();
  }

  function commitRateEdit(r: FxRate) {
    const draft = rateEdits[r.currency];
    if (draft === undefined) return;
    setRateEdits((prev) => {
      const next = { ...prev };
      delete next[r.currency];
      return next;
    });
    if (draft === r.rate || !RATE_RE.test(draft) || !(Number(draft) > 0)) return;
    putRate.mutate({ currency: r.currency, rate: draft });
  }

  // Staleness anchor for the refresh-failure message: the newest saved as_of.
  const newestAsOf =
    fxRates.data?.reduce<string | null>(
      (acc, r) => (r.as_of && (acc === null || r.as_of > acc) ? r.as_of : acc),
      null,
    ) ?? null;

  const nw = networth.data;
  // The response says which currency the totals were converted into — trust
  // it over the hook so a tie-broken fallback can't mislabel the numbers.
  const displayCcy = nw?.display_currency ?? currency;
  const chartData =
    nw?.series.map((p) => ({
      on: p.on.slice(0, 7),
      net: toNum(p.net),
      assets: toNum(p.assets),
      liabilities: toNum(p.liabilities),
    })) ?? [];
  const drifting = (nw?.accounts ?? []).filter((a) => a.drift !== null);

  return (
    <>
      <PageHeader
        title="Net worth"
        subtitle={`Everything you own minus everything you owe${
          nw?.display_currency ? ` — shown in ${nw.display_currency}, converted at your saved rates` : ""
        }.`}
      />

      {(nw?.excluded_currencies?.length ?? 0) > 0 && (
        <WarningBanner className="mb-4">
          Accounts in {nw?.excluded_currencies.join(", ")} are <strong>excluded from the totals</strong> —
          no exchange rate saved. Add rates below to include them.
        </WarningBanner>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatCard
          label="Net worth"
          value={fmtMoney(nw?.net, displayCcy)}
          icon={<Scale className="h-5 w-5" />}
          accent={nw && toNum(nw.net) >= 0 ? "brand" : "rose"}
        />
        <StatCard
          label="Assets"
          value={fmtMoney(nw?.assets, displayCcy)}
          icon={<PiggyBank className="h-5 w-5" />}
          accent="emerald"
        />
        <StatCard
          label="Liabilities"
          value={fmtMoney(nw?.liabilities, displayCcy)}
          icon={<Landmark className="h-5 w-5" />}
          accent="rose"
        />
      </div>

      {drifting.length > 0 && (
        <div className="mt-4 space-y-2">
          {drifting.map((a) => (
            <div
              key={a.id}
              className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
            >
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>
                <strong>{a.name}</strong>: the bank's stated balance differs from your imported
                transactions by {fmtMoney(a.drift, a.currency)} — a statement may be missing.
              </span>
            </div>
          ))}
        </div>
      )}

      <Card className="mt-6 p-6">
        <h2 className="text-sm font-semibold text-slate-900">Trend</h2>
        {chartData.length > 1 ? (
          <div className="mt-4 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <defs>
                  <linearGradient id="nwFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="on" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} width={80} tickFormatter={(v) => fmtMoney(v, displayCcy)} />
                <Tooltip formatter={(v) => fmtMoney(v as number, displayCcy)} />
                <Area type="monotone" dataKey="net" stroke="#10b981" fill="url(#nwFill)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            The trend builds as statements and valuations accumulate — worthless at month one,
            priceless at month twenty-four.
          </p>
        )}
      </Card>

      {(allowances.data?.meters.length ?? 0) > 0 && (
        <Card className="mt-6 p-6">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-semibold text-slate-900">UK tax-year allowances</h2>
            <span className="text-xs text-slate-500 nums">
              {allowances.data?.tax_year_start} → {allowances.data?.tax_year_end} ·{" "}
              <strong>{allowances.data?.days_left} days left</strong> — unused allowance doesn't
              roll over
            </span>
          </div>
          <div className="mt-4 grid grid-cols-1 gap-5 md:grid-cols-3">
            {allowances.data?.meters.map((m) => {
              const usedPct = toNum(m.used) / Math.max(toNum(m.allowance), 1);
              return (
                <div key={m.wrapper}>
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="font-medium text-slate-900">
                      {WRAPPER_LABEL[m.wrapper] ?? m.wrapper}
                    </span>
                    <span className="text-xs text-slate-500 nums">
                      {fmtMoney(m.used, "GBP")} / {fmtMoney(m.allowance, "GBP")}
                    </span>
                  </div>
                  <ProgressBar
                    value={usedPct}
                    tone={usedPct >= 1 ? "rose" : usedPct >= 0.75 ? "amber" : "emerald"}
                    className="mt-2"
                  />
                  <div className="mt-1 text-xs text-slate-500 nums">
                    {fmtMoney(m.remaining, "GBP")} remaining
                    {m.wrapper === "isa" && toNum(m.lisa_portion) > 0 && (
                      <> (includes {fmtMoney(m.lisa_portion, "GBP")} via LISA)</>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Counted from positive transactions into wrapper-tagged GBP accounts this tax year
            (rows described as interest are excluded). Transfers between your own ISAs may be
            miscounted, and pension figures omit employer contributions and tax relief — treat
            the pension meter as a floor. Tag accounts on the Accounts page.
          </p>
        </Card>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="p-6 lg:col-span-2">
          <h2 className="text-sm font-semibold text-slate-900">Accounts</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 text-left font-medium">Account</th>
                  <th className="py-2 text-left font-medium">As of</th>
                  <th className="py-2 text-left font-medium">Paid off</th>
                  <th className="py-2 text-right font-medium">Balance</th>
                </tr>
              </thead>
              <tbody>
                {nw?.accounts.map((a) => (
                  <tr key={a.id} className="border-t border-slate-100">
                    <td className="py-2">
                      {a.name} <Badge tone={SOURCE_TONE[a.source] ?? "slate"}>{a.source}</Badge>
                      {!a.converted && <Badge tone="amber">no rate — excluded</Badge>}
                    </td>
                    <td className="py-2 text-xs text-slate-500 nums">{a.as_of ?? "—"}</td>
                    <td className="py-2" />
                    <td
                      className={`py-2 text-right nums font-medium ${
                        toNum(a.balance) < 0 ? "text-rose-600" : ""
                      }`}
                    >
                      {fmtMoney(a.balance, a.currency)}
                    </td>
                  </tr>
                ))}
                {nw?.register_debts.map((d) => (
                  <tr key={`debt-${d.id}`} className="border-t border-slate-100">
                    <td className="py-2">
                      {d.name} <Badge tone="rose">debt</Badge>
                      {!d.converted && <Badge tone="amber">no rate — excluded</Badge>}
                    </td>
                    <td className="py-2 text-xs text-slate-500">register</td>
                    <td className="py-2 text-xs text-slate-500 nums">
                      {/* Null even for a payable debt when the portfolio is
                          unpayable at minimums — hence the plain em-dash. */}
                      {d.payoff_date ? (
                        <span title="At contractual minimums">~ {fmtMonthYear(d.payoff_date)}</span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-2 text-right nums font-medium text-rose-600">
                      −{fmtMoney(d.balance, d.currency ?? displayCcy)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="p-6">
          <h2 className="text-sm font-semibold text-slate-900">Record a valuation</h2>
          <p className="mt-1 text-xs text-slate-500">
            For assets without statements — pension, property, ISA. Create an account of type
            "other" for each, then value it here every so often.
          </p>
          <form onSubmit={onAddValuation} className="mt-4 space-y-3">
            <label className="block">
              <Label>Account</Label>
              <Select value={valAccount} onChange={(e) => setValAccount(e.target.value)} required>
                <option value="">Choose…</option>
                {accounts.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </label>
            <label className="block">
              <Label>Date</Label>
              <Input type="date" value={valDate} onChange={(e) => setValDate(e.target.value)} required />
            </label>
            <label className="block">
              <Label>Value</Label>
              <Input
                value={valAmount}
                onChange={(e) => setValAmount(e.target.value)}
                placeholder="e.g. 42000"
                required
              />
            </label>
            <Button type="submit" disabled={addValuation.isPending}>
              Save valuation
            </Button>
          </form>

          <div className="mt-6 border-t border-slate-200 pt-4">
            <h2 className="text-sm font-semibold text-slate-900">Currency</h2>
            <label className="mt-2 block">
              <Label>Display currency (all totals shown in this)</Label>
              <Select
                value={user?.display_currency ?? ""}
                onChange={(e) => setDisplayCurrency.mutate(e.target.value || null)}
                disabled={setDisplayCurrency.isPending}
              >
                <option value="">Automatic ({displayCcy})</option>
                {[...new Set([...(accounts.data?.map((a) => a.currency) ?? []), user?.display_currency ?? ""])]
                  .filter(Boolean)
                  .sort()
                  .map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
              </Select>
            </label>
            <div className="mt-3">
              <Label>Exchange rates (1 unit = X {displayCcy})</Label>
              <ul className="mt-1 space-y-1">
                {fxRates.data?.map((r) => (
                  <li key={r.currency} className="flex items-center gap-2 text-sm">
                    <span className="w-10 font-mono">{r.currency}</span>
                    <Input
                      value={rateEdits[r.currency] ?? r.rate}
                      onChange={(e) =>
                        setRateEdits((prev) => ({ ...prev, [r.currency]: e.target.value }))
                      }
                      onBlur={() => commitRateEdit(r)}
                      className="!w-28 !py-1 nums"
                      aria-label={`Rate for ${r.currency}`}
                    />
                    <Badge tone={r.source === "auto" ? "sky" : "slate"}>{r.source}</Badge>
                    <span className="flex-1 text-right text-xs text-slate-400 nums">
                      {r.as_of ?? "—"}
                    </span>
                    <button
                      onClick={() => deleteRate.mutate(r.currency)}
                      className="text-xs text-slate-400 hover:text-rose-600"
                    >
                      remove
                    </button>
                  </li>
                ))}
              </ul>
              <div className="mt-2 flex items-center gap-2">
                <Input
                  placeholder="CLP"
                  value={newFxCurrency}
                  onChange={(e) => setNewFxCurrency(e.target.value.toUpperCase())}
                  maxLength={3}
                  className="!w-20 uppercase"
                />
                <Input
                  placeholder="0.00082"
                  value={newFxRate}
                  onChange={(e) => setNewFxRate(e.target.value)}
                  className="!w-32"
                />
                <Button
                  className="!py-1.5"
                  disabled={!/^[A-Z]{3}$/.test(newFxCurrency) || !rateValid || putRate.isPending}
                  onClick={() =>
                    putRate.mutate(
                      { currency: newFxCurrency, rate: newFxRate },
                      {
                        onSuccess: () => {
                          setNewFxCurrency("");
                          setNewFxRate("");
                        },
                      },
                    )
                  }
                >
                  Save rate
                </Button>
              </div>
              {newFxRate !== "" && !rateValid && (
                <p className="mt-1 text-xs text-rose-600">
                  Enter a positive number with a dot decimal, e.g. 0.00082 — no commas.
                </p>
              )}
              <div className="mt-3 border-t border-slate-100 pt-3">
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={user?.fx_auto_refresh ?? false}
                    onChange={(e) => setFxAutoRefresh.mutate(e.target.checked)}
                    disabled={setFxAutoRefresh.isPending}
                    className="mt-0.5"
                  />
                  <span>
                    Fetch rates automatically (daily)
                    <span className="block text-xs text-slate-400">
                      Calls an external exchange-rate service; manual rates always win.
                    </span>
                  </span>
                </label>
                {user?.fx_auto_refresh && (
                  <div className="mt-2">
                    <Button
                      variant="secondary"
                      className="!py-1.5"
                      disabled={refreshRates.isPending}
                      onClick={() => refreshRates.mutate()}
                    >
                      <RefreshCw
                        className={`h-3.5 w-3.5 ${refreshRates.isPending ? "animate-spin" : ""}`}
                      />
                      Refresh now
                    </Button>
                    {refreshRates.isError && (
                      <p className="mt-1 text-xs text-amber-600">
                        Refresh failed — showing last-known rates
                        {newestAsOf ? ` from ${newestAsOf}` : ""}.
                      </p>
                    )}
                    {/* A 200 with zero rows written carries skipped_reason
                        saying why — don't let the click look like it worked. */}
                    {refreshRates.isSuccess && refreshRates.data.skipped_reason === "provider_error" && (
                      <p className="mt-1 text-xs text-amber-600">
                        Provider unreachable — showing last-known rates
                        {newestAsOf ? ` from ${newestAsOf}` : ""}.
                      </p>
                    )}
                    {refreshRates.isSuccess && refreshRates.data.skipped_reason === "cooldown" && (
                      <p className="mt-1 text-xs text-amber-600">
                        Recently failed — retry in a few minutes.
                      </p>
                    )}
                    {refreshRates.isSuccess &&
                      refreshRates.data.refreshed_count === 0 &&
                      !refreshRates.data.skipped_reason && (
                        <p className="mt-1 text-xs text-amber-600">
                          Nothing refreshed — showing last-known rates
                          {newestAsOf ? ` from ${newestAsOf}` : ""}.
                        </p>
                      )}
                  </div>
                )}
              </div>
              <p className="mt-2 text-xs text-slate-400">
                Enter rates by hand, or opt in above to fetch them daily — hand-entered rates are
                never overwritten. Changing the display currency clears saved rates (they were
                defined against the old currency).
              </p>
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}
