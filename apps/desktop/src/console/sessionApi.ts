// === ANCHOR: SESSION_API_START ===
import { appLogger } from "../diagnostics/appLog";
import { httpBaseFromWs, loadValues } from "../setup/setupValues";
import type { CreatedSession, EndedSession, MeetingDraft, TokenPair, UtteranceTranscribed } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

export function apiBase(): string {
  if (API_BASE) return API_BASE;
  return httpBaseFromWs(loadValues().serverWsBase).replace(/\/$/, "");
}

export function operatorWsUrl(sessionId: string, operatorToken: string): string {
  const url = new URL(`${apiBase().replace(/^http:/, "ws:").replace(/^https:/, "wss:")}/ws/operator`);
  url.searchParams.set("session", sessionId);
  url.searchParams.set("access", operatorToken);
  return url.toString();
}

function authHeaders(operatorToken: string): HeadersInit {
  return {
    Authorization: `Bearer ${operatorToken}`,
    "Content-Type": "application/json",
  };
}

async function parseJsonResponse<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) {
    throw new Error(`${action} failed: HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function sessionRequestBody(draft: MeetingDraft) {
  return {
    title: draft.title,
    client_label: draft.clientLabel || null,
    visibility: draft.visibility,
  };
}

async function timedFetch(action: string, url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? "GET";
  const startedAt = performance.now();
  try {
    const response = await fetch(url, init);
    appLogger.latency("network", `${action} ${method} ${safeApiPath(url)}`, performance.now() - startedAt, { detail: `HTTP ${response.status}` });
    return response;
  } catch (error) {
    appLogger.error("network", `${action} ${method} ${safeApiPath(url)} failed`, {
      durationMs: performance.now() - startedAt,
      detail: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
}

export async function loginOperator(email: string, password: string): Promise<TokenPair> {
  const response = await timedFetch("Login", `${apiBase()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return parseJsonResponse<TokenPair>(response, "Login");
}

export async function createSession(draft: MeetingDraft): Promise<CreatedSession> {
  const response = await timedFetch("Create session", `${apiBase()}/api/v1/sessions`, {
    method: "POST",
    headers: authHeaders(draft.operatorToken),
    body: JSON.stringify(sessionRequestBody(draft)),
  });
  return parseJsonResponse<CreatedSession>(response, "Create session");
}

export async function endSession(sessionId: string, operatorToken: string): Promise<EndedSession> {
  const response = await timedFetch("End session", `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/end`, {
    method: "POST",
    headers: authHeaders(operatorToken),
  });
  return parseJsonResponse<EndedSession>(response, "End session");
}

export async function fetchSessionViewerUrl(sessionId: string, operatorToken: string): Promise<string> {
  // Tunnel recovery: the viewer URL minted at creation goes stale when the
  // server console re-publishes the public tunnel mid-meeting (new random
  // trycloudflare host, same viewer token). This re-fetches the CURRENT link.
  const response = await timedFetch(
    "Refresh viewer URL",
    `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/viewer-url`,
    { headers: { Authorization: `Bearer ${operatorToken}` } },
  );
  const body = await parseJsonResponse<{ session_id: string; viewer_url: string }>(
    response,
    "Refresh viewer URL",
  );
  return body.viewer_url;
}

export async function fetchSessionReport(sessionId: string, operatorToken: string): Promise<string> {
  const response = await timedFetch("Download report", `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/report`, {
    headers: { Authorization: `Bearer ${operatorToken}` },
  });
  if (!response.ok) throw new Error(`Download report failed: HTTP ${response.status}`);
  return response.text();
}

export async function fetchSessionReportHtml(sessionId: string, operatorToken: string): Promise<string> {
  const response = await timedFetch("Download report HTML", `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/report.html`, {
    headers: { Authorization: `Bearer ${operatorToken}` },
  });
  if (!response.ok) throw new Error(`Download report HTML failed: HTTP ${response.status}`);
  return response.text();
}

export type ReportFormat = "md" | "html" | "docx" | "pdf";

export async function fetchSessionReportBytes(
  sessionId: string,
  operatorToken: string,
  fmt: ReportFormat,
): Promise<{ ok: boolean; data?: Uint8Array; status: number }> {
  const suffix = fmt === "md" ? "report" : `report.${fmt}`;
  const url = `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/${suffix}`;
  try {
    const response = await timedFetch(`Download report (${fmt})`, url, {
      headers: { Authorization: `Bearer ${operatorToken}` },
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    const buffer = await response.arrayBuffer();
    return { ok: true, data: new Uint8Array(buffer), status: response.status };
  } catch (error) {
    appLogger.error("network", `Download report (${fmt}) fetch error`, {
      detail: error instanceof Error ? error.message : String(error),
    });
    return { ok: false, status: 0 };
  }
}

export async function fetchSessionSummary(
  sessionId: string,
  operatorToken: string,
): Promise<{ ok: boolean; text?: string; status: number }> {
  const url = `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/report.summary`;
  try {
    const response = await timedFetch("Download summary", url, {
      headers: { Authorization: `Bearer ${operatorToken}` },
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    return { ok: true, text: await response.text(), status: response.status };
  } catch (error) {
    appLogger.error("network", "Download summary fetch error", {
      detail: error instanceof Error ? error.message : String(error),
    });
    return { ok: false, status: 0 };
  }
}

export async function fetchSessionSummaryHtml(
  sessionId: string,
  operatorToken: string,
): Promise<{ ok: boolean; html?: string; status: number }> {
  const url = `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/report.summary.html`;
  try {
    const response = await timedFetch("Download summary HTML", url, {
      headers: { Authorization: `Bearer ${operatorToken}` },
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    return { ok: true, html: await response.text(), status: response.status };
  } catch (error) {
    appLogger.error("network", "Download summary HTML fetch error", {
      detail: error instanceof Error ? error.message : String(error),
    });
    return { ok: false, status: 0 };
  }
}

export async function fetchSessionSummaryBytes(
  sessionId: string,
  operatorToken: string,
  fmt: ReportFormat,
): Promise<{ ok: boolean; data?: Uint8Array; status: number }> {
  const suffix = fmt === "md" ? "report.summary" : `report.summary.${fmt}`;
  const url = `${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/${suffix}`;
  try {
    const response = await timedFetch(`Download summary (${fmt})`, url, {
      headers: { Authorization: `Bearer ${operatorToken}` },
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    const buffer = await response.arrayBuffer();
    return { ok: true, data: new Uint8Array(buffer), status: response.status };
  } catch (error) {
    appLogger.error("network", `Download summary (${fmt}) fetch error`, {
      detail: error instanceof Error ? error.message : String(error),
    });
    return { ok: false, status: 0 };
  }
}

export async function fetchOperatorBackfill(
  sessionId: string,
  operatorToken: string,
): Promise<{ utterances: UtteranceTranscribed[]; session_status: string }> {
  const url = new URL(`${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/utterances`);
  url.searchParams.set("limit", "50");
  const response = await timedFetch("Fetch subtitles", url.toString(), {
    headers: { Authorization: `Bearer ${operatorToken}` },
  });
  return parseJsonResponse<{ utterances: UtteranceTranscribed[]; session_status: string }>(response, "Fetch subtitles");
}

export async function selfEnrollDevice(operatorToken: string, name: string): Promise<string> {
  const response = await timedFetch("Self-enroll device", `${apiBase()}/api/v1/devices/self-enroll`, {
    method: "POST",
    headers: authHeaders(operatorToken),
    body: JSON.stringify({ name }),
  });
  const body = await parseJsonResponse<{ id: number; name: string; api_key: string }>(response, "Self-enroll device");
  return body.api_key;
}

// Device-key mint/list/revoke moved to the SERVER console (apps/server_desktop):
// issuing/revoking device keys is an admin control-plane action and no longer
// lives in this operator client. The client only RECEIVES a device key (pasted
// in the QuickStart register flow / manual field) and stores it in the keychain.

function safeApiPath(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname;
  } catch {
    return url;
  }
}
// === ANCHOR: SESSION_API_END ===
