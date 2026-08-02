import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, LogOut, UserPlus, Users, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Household as HouseholdData, HouseholdInvite, SharedView } from "../api/types";
import { Badge, Button, Card, Input, Label, PageHeader, WarningBanner } from "../components/ui";
import { apiErrorDetail } from "../lib/apiError";
import { fmtMoney } from "../lib/format";

const SOURCE_TONE: Record<string, "emerald" | "sky" | "slate"> = {
  statement: "emerald",
  manual: "sky",
  derived: "slate",
  opening: "slate",
};

export default function Household() {
  const qc = useQueryClient();
  const household = useQuery({
    queryKey: ["household"],
    queryFn: async () => (await api.get<HouseholdData | null>("/api/v1/household")).data,
  });
  const shared = useQuery({
    queryKey: ["household-shared"],
    queryFn: async () => (await api.get<SharedView>("/api/v1/household/shared")).data,
    enabled: Boolean(household.data),
  });

  const [name, setName] = useState("");
  const [joinToken, setJoinToken] = useState("");
  const [copied, setCopied] = useState<number | null>(null);

  const invalidate = () => {
    for (const key of ["household", "household-shared"]) qc.invalidateQueries({ queryKey: [key] });
  };
  const createMut = useMutation({
    mutationFn: async () => api.post("/api/v1/household", { name }),
    onSuccess: () => {
      setName("");
      invalidate();
    },
  });
  const joinMut = useMutation({
    mutationFn: async () => api.post("/api/v1/household/join", { token: joinToken.trim() }),
    onSuccess: () => {
      setJoinToken("");
      invalidate();
    },
  });
  const isOwnerRole = household.data?.my_role === "owner";
  const invites = useQuery({
    queryKey: ["household-invites"],
    queryFn: async () => (await api.get<HouseholdInvite[]>("/api/v1/household/invites")).data,
    enabled: isOwnerRole,
  });
  const invalidateInvites = () => qc.invalidateQueries({ queryKey: ["household-invites"] });
  const inviteMut = useMutation({
    mutationFn: async () => (await api.post<HouseholdInvite>("/api/v1/household/invites")).data,
    onSuccess: invalidateInvites,
  });
  const revokeInviteMut = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/v1/household/invites/${id}`),
    onSuccess: invalidateInvites,
  });
  const removeMut = useMutation({
    mutationFn: async (userId: number) => api.delete(`/api/v1/household/members/${userId}`),
    onSuccess: () => {
      invalidate();
      invalidateInvites();
    },
  });

  function onCreate(e: FormEvent) {
    e.preventDefault();
    if (name.trim()) createMut.mutate();
  }

  function onJoin(e: FormEvent) {
    e.preventDefault();
    if (joinToken.trim()) joinMut.mutate();
  }

  async function copyToken(inv: HouseholdInvite) {
    try {
      await navigator.clipboard.writeText(inv.token);
      setCopied(inv.id);
    } catch {
      // Clipboard can be blocked — the token stays visible for manual copy.
    }
  }

  const h = household.data;
  const me = h?.members.find((m) => m.is_me);
  const isOwner = h?.my_role === "owner";

  return (
    <>
      <PageHeader
        title="Household"
        subtitle="Yours, mine, ours — share chosen account balances read-only with the people you budget with."
      />

      {household.isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : household.isError ? (
        <WarningBanner>Couldn't load your household — try again.</WarningBanner>
      ) : !h ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card className="p-6">
            <h2 className="text-sm font-semibold text-slate-900">Start a household</h2>
            <p className="mt-1 text-xs text-slate-500">
              Create it, then send your partner an invite token. Each person keeps their own login
              and data — only accounts explicitly marked “Household” are ever visible, and only as
              read-only balances.
            </p>
            <form onSubmit={onCreate} className="mt-4 flex items-end gap-2">
              <label className="flex-1">
                <Label>Household name</Label>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. The Agueros" />
              </label>
              <Button type="submit" disabled={!name.trim() || createMut.isPending}>
                <Users className="h-4 w-4" /> Create
              </Button>
            </form>
            {createMut.isError && (
              <p className="mt-2 text-xs text-rose-600">{apiErrorDetail(createMut.error, "Couldn't create it.")}</p>
            )}
          </Card>

          <Card className="p-6">
            <h2 className="text-sm font-semibold text-slate-900">Join with an invite</h2>
            <p className="mt-1 text-xs text-slate-500">
              Paste the token you were sent. Tokens are single-use and expire after 7 days.
            </p>
            <form onSubmit={onJoin} className="mt-4 flex items-end gap-2">
              <label className="flex-1">
                <Label>Invite token</Label>
                <Input value={joinToken} onChange={(e) => setJoinToken(e.target.value)} placeholder="paste token…" />
              </label>
              <Button type="submit" disabled={!joinToken.trim() || joinMut.isPending}>
                <UserPlus className="h-4 w-4" /> Join
              </Button>
            </form>
            {joinMut.isError && (
              <p className="mt-2 text-xs text-rose-600">
                {apiErrorDetail(joinMut.error, "That invite isn't valid.")}
              </p>
            )}
          </Card>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <Card className="p-6">
              <div className="flex items-baseline justify-between">
                <h2 className="text-sm font-semibold text-slate-900">{h.name}</h2>
                <Badge tone={isOwner ? "brand" : "slate"}>{h.my_role}</Badge>
              </div>
              <ul className="mt-3 divide-y divide-slate-100">
                {h.members.map((m) => (
                  <li key={m.user_id} className="flex items-center gap-2 py-2 text-sm">
                    <span className="min-w-0 flex-1 truncate">
                      {m.full_name ?? m.email}
                      {m.is_me && <span className="ml-1 text-xs text-slate-400">(you)</span>}
                    </span>
                    <Badge tone={m.role === "owner" ? "brand" : "slate"}>{m.role}</Badge>
                    {(m.is_me || (isOwner && m.role !== "owner")) && (
                      <button
                        onClick={() => removeMut.mutate(m.user_id)}
                        className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                        title={
                          !m.is_me
                            ? "Remove member (their shared accounts turn private; open invites are revoked)"
                            : h.members.length === 1
                              ? "Leave — you're the last member, so the household is deleted"
                              : isOwner
                                ? "Leave — ownership passes to the longest-standing member"
                                : "Leave household (your shared accounts turn private)"
                        }
                      >
                        {m.is_me ? <LogOut className="h-4 w-4" /> : <X className="h-4 w-4" />}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              {removeMut.isError && (
                <p className="mt-2 text-xs text-rose-600">
                  {apiErrorDetail(removeMut.error, "Couldn't change membership — reload and try again.")}
                </p>
              )}

              {isOwner && (
                <div className="mt-4 border-t border-slate-100 pt-4">
                  <Button className="!py-1.5" onClick={() => inviteMut.mutate()} disabled={inviteMut.isPending}>
                    <UserPlus className="h-4 w-4" /> New invite token
                  </Button>
                  {inviteMut.isError && (
                    <p className="mt-2 text-xs text-rose-600">
                      {apiErrorDetail(inviteMut.error, "Couldn't create an invite.")}
                    </p>
                  )}
                  {(invites.data?.length ?? 0) > 0 && (
                    <ul className="mt-2 space-y-2">
                      {invites.data?.map((inv) => (
                        <li key={inv.id}>
                          <div className="flex items-center gap-2">
                            <code className="min-w-0 flex-1 truncate rounded bg-slate-100 px-2 py-1 text-xs">
                              {inv.token}
                            </code>
                            <button
                              onClick={() => copyToken(inv)}
                              className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                              title="Copy token"
                            >
                              {copied === inv.id ? (
                                <Check className="h-4 w-4 text-emerald-600" />
                              ) : (
                                <Copy className="h-4 w-4" />
                              )}
                            </button>
                            <button
                              onClick={() => revokeInviteMut.mutate(inv.id)}
                              className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                              title="Revoke this invite"
                            >
                              <X className="h-4 w-4" />
                            </button>
                          </div>
                          <p className="mt-0.5 text-xs text-slate-400">
                            Single-use, expires {new Date(inv.expires_at).toLocaleString()}.
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </Card>

            <Card className="p-6 lg:col-span-2">
              <h2 className="text-sm font-semibold text-slate-900">Shared accounts</h2>
              <p className="mt-1 text-xs text-slate-500">
                Read-only balances of accounts members chose to share. Mark yours on the{" "}
                <Link to="/accounts" className="text-brand-700 underline">
                  Accounts page
                </Link>{" "}
                (“Sharing” column).
              </p>
              {shared.isPending ? (
                <p className="mt-3 text-sm text-slate-500">Loading…</p>
              ) : shared.isError ? (
                <WarningBanner className="mt-3">Couldn't load shared accounts.</WarningBanner>
              ) : (shared.data?.accounts.length ?? 0) === 0 ? (
                <p className="mt-3 text-sm text-slate-500">
                  Nothing shared yet{me ? " — including by you" : ""}.
                </p>
              ) : (
                <>
                  <div className="mt-3 overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="text-xs uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="py-2 text-left font-medium">Account</th>
                          <th className="py-2 text-left font-medium">Owner</th>
                          <th className="py-2 text-left font-medium">As of</th>
                          <th className="py-2 text-right font-medium">Balance</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shared.data?.accounts.map((a) => (
                          <tr key={a.account_id} className="border-t border-slate-100">
                            <td className="py-2">
                              {a.name}{" "}
                              <span className="text-xs text-slate-400">{a.type.replace("_", " ")}</span>{" "}
                              <Badge tone={SOURCE_TONE[a.source] ?? "slate"}>{a.source}</Badge>
                            </td>
                            <td className="py-2 text-xs text-slate-500">{a.owner_name}</td>
                            <td className="py-2 text-xs text-slate-500 nums">{a.as_of ?? "—"}</td>
                            <td className="py-2 text-right nums font-medium">
                              {fmtMoney(a.balance, a.currency)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="mt-3 border-t border-slate-100 pt-3 text-sm">
                    {shared.data?.totals.map((t) => (
                      <span key={t.currency} className="mr-4 nums">
                        <span className="text-slate-500">{t.currency}:</span>{" "}
                        <span className="font-semibold">{fmtMoney(t.total, t.currency)}</span>
                      </span>
                    ))}
                    <span className="block pt-1 text-xs text-slate-400">
                      Totals are per currency — each member keeps their own FX rates, so mixing
                      currencies here would be dishonest.
                    </span>
                  </div>
                </>
              )}
            </Card>
          </div>
        </>
      )}

    </>
  );
}
