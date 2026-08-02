import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Account } from "../api/types";
import { useAuth } from "../contexts/useAuth";

/**
 * The user's display currency: their explicit setting when present, else the
 * most common currency across their accounts — ties break alphabetically,
 * mirroring the backend's resolve_display_currency so both sides always
 * elect the same code — else USD.
 */
export function useUserCurrency(): string {
  const { user } = useAuth();
  const { data } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/api/v1/accounts")).data,
    staleTime: 60_000,
  });
  if (user?.display_currency) return user.display_currency;
  if (!data || data.length === 0) return "USD";
  const counts: Record<string, number> = {};
  for (const a of data) counts[a.currency] = (counts[a.currency] ?? 0) + 1;
  return Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
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
