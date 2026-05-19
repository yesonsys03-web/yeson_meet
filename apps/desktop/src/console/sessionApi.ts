// === ANCHOR: SESSION_API_START ===
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

export async function loginOperator(email: string, password: string): Promise<TokenPair> {
  const response = await fetch(`${apiBase()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return parseJsonResponse<TokenPair>(response, "Login");
}

export async function createSession(draft: MeetingDraft): Promise<CreatedSession> {
  const response = await fetch(`${apiBase()}/api/v1/sessions`, {
    method: "POST",
    headers: authHeaders(draft.operatorToken),
    body: JSON.stringify(sessionRequestBody(draft)),
  });
  return parseJsonResponse<CreatedSession>(response, "Create session");
}

export async function endSession(sessionId: string, operatorToken: string): Promise<EndedSession> {
  const response = await fetch(`${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/end`, {
    method: "POST",
    headers: authHeaders(operatorToken),
  });
  return parseJsonResponse<EndedSession>(response, "End session");
}

export async function fetchSessionReport(sessionId: string, operatorToken: string): Promise<string> {
  const response = await fetch(`${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/report`, {
    headers: { Authorization: `Bearer ${operatorToken}` },
  });
  if (!response.ok) throw new Error(`Download report failed: HTTP ${response.status}`);
  return response.text();
}

export async function fetchOperatorBackfill(
  sessionId: string,
  operatorToken: string,
): Promise<{ utterances: UtteranceTranscribed[]; session_status: string }> {
  const url = new URL(`${apiBase()}/api/v1/sessions/${encodeURIComponent(sessionId)}/utterances`);
  url.searchParams.set("limit", "50");
  const response = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${operatorToken}` },
  });
  return parseJsonResponse<{ utterances: UtteranceTranscribed[]; session_status: string }>(response, "Fetch subtitles");
}
// === ANCHOR: SESSION_API_END ===
