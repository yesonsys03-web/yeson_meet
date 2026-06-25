// === ANCHOR: KNOWLEDGE_API_START ===
import { apiBase } from "./sessionApi";

// Shape mirrors the server's SessionListItem Pydantic model (S2).
// `snippets` is present whenever `q` is set, absent/empty otherwise —
// both the FTS5 path and the LIKE fallback return the identical shape.
export type SessionListItem = {
  external_id: string;
  title: string;
  client_label: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
  owner_user_id: number;
  visibility: string;
  utterance_count: number;
  report_ready: boolean;
  snippets?: string[];
};

export type SessionListOut = {
  items: SessionListItem[];
  has_more: boolean;
};

export type ListSessionsParams = {
  q?: string;
  limit?: number;
  offset?: number;
  scope?: "all" | "mine";
  status?: "ended" | "live" | "all";
  operatorToken: string;
};

// GET /api/v1/sessions with optional full-text search.
// Returns paginated items + has_more; the server defaults status=ended.
export async function listSessions({
  q,
  limit = 30,
  offset = 0,
  scope = "all",
  status = "ended",
  operatorToken,
}: ListSessionsParams): Promise<SessionListOut> {
  const url = new URL(`${apiBase()}/api/v1/sessions`);
  if (q !== undefined && q !== "") url.searchParams.set("q", q);
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("offset", String(offset));
  url.searchParams.set("scope", scope);
  url.searchParams.set("status", status);

  const response = await fetch(url.toString(), {
    headers: {
      Authorization: `Bearer ${operatorToken}`,
    },
  });

  if (!response.ok) {
    throw new Error(`List sessions failed: HTTP ${response.status}`);
  }

  return (await response.json()) as SessionListOut;
}
// === ANCHOR: KNOWLEDGE_API_END ===
