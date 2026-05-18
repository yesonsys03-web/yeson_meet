// === ANCHOR: API_START ===
import type { UtteranceTranscribed } from "../types/events";

// === ANCHOR: API_API_BASE_START ===
const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

// === ANCHOR: API_FETCHBACKFILL_START ===
export async function fetchBackfill(
  token: string,
  since: number | null,
): Promise<{ utterances: UtteranceTranscribed[]; session_status: string }> {
  const u = new URL(`${API_BASE}/api/v1/viewer/utterances`, window.location.origin);
  u.searchParams.set("token", token);
  if (since !== null && since !== undefined) u.searchParams.set("since", String(since));
  u.searchParams.set("limit", "50");
  const res = await fetch(u.toString());
  if (!res.ok) throw new Error(`backfill failed: ${res.status}`);
  // === ANCHOR: API_BODY_START ===
  const body = (await res.json()) as {
    utterances: UtteranceTranscribed[];
    session_status?: string;
  };
  // === ANCHOR: API_BODY_END ===
  return {
    utterances: body.utterances ?? [],
// === ANCHOR: API_API_BASE_END ===
    session_status: body.session_status ?? "live",
// === ANCHOR: API_FETCHBACKFILL_END ===
  };
}

// === ANCHOR: API_VIEWERWSURL_START ===
export function viewerWsUrl(token: string): string {
  // === ANCHOR: API_BASE_START ===
  const base = (import.meta.env.VITE_WS_BASE ?? "").replace(/\/$/, "");
  if (base) return `${base}/ws/viewer?token=${encodeURIComponent(token)}`;
  // === ANCHOR: API_BASE_END ===
  // dev: relative path goes through Vite proxy
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
// === ANCHOR: API_VIEWERWSURL_END ===
  return `${proto}//${window.location.host}/ws/viewer?token=${encodeURIComponent(token)}`;
}
// === ANCHOR: API_END ===
