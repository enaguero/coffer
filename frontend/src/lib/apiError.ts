/** The backend's FastAPI error detail when present, else the fallback —
 * axios's generic "Request failed with status code N" helps nobody. */
export function apiErrorDetail(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  // Pydantic validation errors arrive as a list of {msg, ...} objects.
  if (Array.isArray(detail) && detail.length > 0) {
    const msg = (detail[0] as { msg?: unknown })?.msg;
    if (typeof msg === "string") return msg.replace(/^Value error, /, "");
  }
  return fallback;
}
