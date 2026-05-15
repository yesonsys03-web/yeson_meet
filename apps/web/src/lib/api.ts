import type { UtteranceTranscribed } from "../types/events";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

export async function fetchBackfill(
  token: string,
  since: number | null,
): Promise<UtteranceTranscribed[]> {
  const u = new URL(`${API_BASE}/api/v1/viewer/utterances`, window.location.origin);
  u.searchParams.set("token", token);
  if (since !== null && since !== undefined) u.searchParams.set("since", String(since));
  u.searchParams.set("limit", "50");
  const res = await fetch(u.toString());
  if (!res.ok) throw new Error(`backfill failed: ${res.status}`);
  const body = (await res.json()) as { utterances: UtteranceTranscribed[] };
  return body.utterances ?? [];
}

export function viewerWsUrl(token: string): string {
  const base = (import.meta.env.VITE_WS_BASE ?? "").replace(/\/$/, "");
  if (base) return `${base}/ws/viewer?token=${encodeURIComponent(token)}`;
  // dev: relative path goes through Vite proxy
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/viewer?token=${encodeURIComponent(token)}`;
}
