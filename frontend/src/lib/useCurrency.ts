import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Account } from "../api/types";

/**
 * Derive a display currency without a user-level setting.
 *
 * Picks the most common currency across the user's accounts, falling back to
 * USD if there are none yet. This is a stop-gap until we add a real
 * user.preferred_currency column — but it correctly honors the data already on
 * each Account, so dashboards stop assuming dollars when the user holds GBP.
 */
export function useUserCurrency(): string {
  const { data } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
    staleTime: 60_000,
  });
  if (!data || data.length === 0) return "USD";
  const counts: Record<string, number> = {};
  for (const a of data) counts[a.currency] = (counts[a.currency] ?? 0) + 1;
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
}

/** Map account_id -> currency, for per-row formatting in transaction tables. */
export function useAccountCurrencyMap(): Map<number, string> {
  const { data } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
    staleTime: 60_000,
  });
  const map = new Map<number, string>();
  for (const a of data ?? []) map.set(a.id, a.currency);
  return map;
}
