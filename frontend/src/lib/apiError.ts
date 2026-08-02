/** The backend's FastAPI error detail when present, else the fallback —
 * axios's generic "Request failed with status code N" helps nobody. */
export function apiErrorDetail(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
