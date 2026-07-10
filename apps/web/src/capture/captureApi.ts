// === ANCHOR: CAPTURE_API_START ===
// 데스크탑 콘솔 sessionApi.ts의 웹 등가물. 프로덕션에선 서버가 이 SPA를 직접
// 서빙하므로 상대 경로(fetch "/api/...")가 그대로 서버에 닿는다. dev(5173)는
// vite proxy가 /api·/ws를 localhost:8000으로 넘긴다.
import type { UtteranceTranscribed } from "../types/events";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

type WsLocation = { protocol: string; host: string };

function wsBase(loc: WsLocation): string {
  const override = (import.meta.env.VITE_WS_BASE ?? "").replace(/\/$/, "");
  if (override) return override;
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${loc.host}`;
}

export function captureWsUrl(loc: WsLocation = window.location): string {
  return `${wsBase(loc)}/ws/capture`;
}

async function parseJson<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw new Error(`${action} failed: HTTP ${response.status}`);
  return (await response.json()) as T;
}

function authHeaders(operatorToken: string): HeadersInit {
  return { Authorization: `Bearer ${operatorToken}`, "Content-Type": "application/json" };
}

export async function loginOperator(email: string, password: string): Promise<{ access_token: string; refresh_token: string }> {
  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return parseJson(response, "Login");
}

export async function fetchCaptureToken(operatorToken: string, sessionId: string): Promise<string> {
  const response = await fetch(
    `${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/capture-token`,
    { method: "POST", headers: authHeaders(operatorToken) },
  );
  const body = await parseJson<{ token: string; expires_at: string }>(response, "Issue capture token");
  return body.token;
}

export async function createCaptureSession(operatorToken: string, title: string): Promise<{ session_id: string; viewer_url: string }> {
  const response = await fetch(`${API_BASE}/api/v1/sessions`, {
    method: "POST",
    headers: authHeaders(operatorToken),
    body: JSON.stringify({ title, client_label: "web-capture", visibility: "org" }),
  });
  return parseJson(response, "Create session");
}

export async function endCaptureSession(operatorToken: string, sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/end`, {
    method: "POST",
    headers: authHeaders(operatorToken),
  });
  if (!response.ok) throw new Error(`End session failed: HTTP ${response.status}`);
}

export async function fetchOperatorBackfill(
  operatorToken: string,
  sessionId: string,
): Promise<{ utterances: UtteranceTranscribed[]; session_status: string }> {
  const url = new URL(`${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/utterances`, window.location.origin);
  url.searchParams.set("limit", "50");
  const response = await fetch(url.toString(), { headers: { Authorization: `Bearer ${operatorToken}` } });
  const body = await parseJson<{ utterances: UtteranceTranscribed[]; session_status?: string }>(response, "Fetch subtitles");
  return { utterances: body.utterances ?? [], session_status: body.session_status ?? "live" };
}
// === ANCHOR: CAPTURE_API_END ===
