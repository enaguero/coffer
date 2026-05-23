import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type {
  Account,
  DiscoveredAccount,
  LinkCompleteResponse,
} from "../api/types";
import { Badge, Button, Card, Label, PageHeader, Select } from "../components/ui";

/** GoCardless redirects back here with `?ref=<requisition_id>`. We POST to
 * /link/complete, then offer to map each discovered bank-side account to a
 * Coffer Account (existing or new). */
export default function BankCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const ref = params.get("ref");

  const accounts = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
  });

  const complete = useMutation({
    mutationFn: async (requisitionId: string) => {
      const { data } = await api.post<LinkCompleteResponse>(
        "/api/v1/bank-connections/link/complete",
        { requisition_id: requisitionId },
      );
      return data;
    },
  });

  useEffect(() => {
    if (ref && !complete.data && !complete.isPending && !complete.isError) {
      complete.mutate(ref);
    }
  }, [ref, complete]);

  const mapAccount = useMutation({
    mutationFn: async (vars: {
      connectionId: number;
      external_account_id: string;
      account_id: number | null;
      name: string | null;
      currency: string | null;
    }) =>
      api.post(`/api/v1/bank-connections/${vars.connectionId}/map-account`, {
        external_account_id: vars.external_account_id,
        account_id: vars.account_id,
        name: vars.name,
        currency: vars.currency,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });

  if (!ref) {
    return (
      <>
        <PageHeader title="Bank link" />
        <Card className="p-6 text-sm text-rose-700">
          Missing requisition reference. Try connecting again from the Banks page.
        </Card>
      </>
    );
  }

  if (complete.isPending) {
    return (
      <>
        <PageHeader title="Finishing the connection…" />
        <Card className="flex items-center gap-3 p-6 text-sm text-slate-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          Asking the bank for your account list…
        </Card>
      </>
    );
  }

  if (complete.isError) {
    return (
      <>
        <PageHeader title="Bank link failed" />
        <Card className="p-6 text-sm text-rose-700">
          {(complete.error as Error).message}
        </Card>
      </>
    );
  }

  if (!complete.data) return null;

  return (
    <>
      <PageHeader
        title={`Connected: ${complete.data.institution_name}`}
        subtitle="Map each bank-side account to a Coffer Account. You can do this later from the Banks page as well."
        right={
          <Button onClick={() => navigate("/banks")}>
            <CheckCircle2 className="h-4 w-4" />
            Done
          </Button>
        }
      />
      <div className="space-y-3">
        {complete.data.accounts.map((a) => (
          <AccountMapperRow
            key={a.external_account_id}
            connectionId={complete.data.bank_connection_id}
            account={a}
            existingAccounts={accounts.data ?? []}
            onSubmit={mapAccount.mutate}
            saving={mapAccount.isPending}
          />
        ))}
      </div>
    </>
  );
}

function AccountMapperRow({
  connectionId,
  account,
  existingAccounts,
  onSubmit,
  saving,
}: {
  connectionId: number;
  account: DiscoveredAccount;
  existingAccounts: Account[];
  onSubmit: (vars: {
    connectionId: number;
    external_account_id: string;
    account_id: number | null;
    name: string | null;
    currency: string | null;
  }) => void;
  saving: boolean;
}) {
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [accountId, setAccountId] = useState("");
  const [name, setName] = useState(account.name ?? `Account ${account.iban_last4 ?? ""}`);
  const [currency] = useState(account.currency ?? "EUR");
  const [done, setDone] = useState(false);

  const label = `${account.name ?? "Bank account"}${
    account.iban_last4 ? ` · ••${account.iban_last4}` : ""
  }${account.currency ? ` · ${account.currency}` : ""}`;

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-900">{label}</div>
          <div className="text-xs text-slate-500">{account.external_account_id}</div>
        </div>
        {done && <Badge tone="emerald">Mapped</Badge>}
      </div>
      {!done && (
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4">
          <label>
            <Label>Action</Label>
            <Select value={mode} onChange={(e) => setMode(e.target.value as "new" | "existing")}>
              <option value="new">Create new Coffer account</option>
              <option value="existing">Attach to existing</option>
            </Select>
          </label>
          {mode === "new" ? (
            <label className="md:col-span-2">
              <Label>Name</Label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
              />
            </label>
          ) : (
            <label className="md:col-span-2">
              <Label>Existing account</Label>
              <Select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                <option value="">Choose…</option>
                {existingAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </label>
          )}
          <div className="flex items-end">
            <Button
              disabled={saving || (mode === "existing" && !accountId)}
              onClick={() => {
                onSubmit({
                  connectionId,
                  external_account_id: account.external_account_id,
                  account_id: mode === "existing" ? Number(accountId) : null,
                  name: mode === "new" ? name : null,
                  currency: mode === "new" ? currency : null,
                });
                setDone(true);
              }}
            >
              Save
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
