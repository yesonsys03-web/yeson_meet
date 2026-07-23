// === ANCHOR: APP_LOG_START ===
// Minimal app-log store that consumes the SAME `app-log` backend event contract
// as the client app (apps/desktop/src/diagnostics/appLog.ts). The Rust
// `server_process` forwarder emits `{ level, source, message }` per line of the
// server's stdout/stderr; this collects them for the log viewer (AC3.2).
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export type AppLogLevel = "debug" | "info" | "warn" | "error";

export type AppLogEntry = {
  id: number;
  ts: string;
  level: AppLogLevel;
  source: string;
  message: string;
};

type BackendLogPayload = {
  level: AppLogLevel;
  source: string;
  message: string;
};
type Subscriber = (entries: AppLogEntry[]) => void;
type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

const MAX_LOG_ENTRIES = 1000;
const BACKEND_LOG_EVENT = "app-log";
const subscribers = new Set<Subscriber>();

let entries: AppLogEntry[] = [];
let nextId = 1;
let captureInstalled = false;

export function installAppLogCapture(): void {
  if (captureInstalled) return;
  captureInstalled = true;
  if (!hasTauriRuntime()) return;
  void listen<BackendLogPayload>(BACKEND_LOG_EVENT, (event) => {
    append(event.payload);
  }).catch((error) => {
    append({ level: "warn", source: "diagnostics", message: `log listener failed: ${errorToText(error)}` });
  });
}

export function append(input: BackendLogPayload): void {
  const entry: AppLogEntry = {
    id: nextId,
    ts: new Date().toISOString(),
    level: input.level,
    source: input.source,
    message: redact(input.message),
  };
  nextId += 1;
  entries = [...entries, entry].slice(-MAX_LOG_ENTRIES);
  notify();
}

export function clearAppLogs(): void {
  entries = [];
  notify();
}

export function subscribeAppLogs(subscriber: Subscriber): () => void {
  subscribers.add(subscriber);
  subscriber(snapshot());
  return () => {
    subscribers.delete(subscriber);
  };
}

function snapshot(): AppLogEntry[] {
  return [...entries];
}

function notify(): void {
  const snap = snapshot();
  subscribers.forEach((subscriber) => subscriber(snap));
}

export function filterLogEntries(
  entries: AppLogEntry[],
  level: AppLogLevel | "all",
  query: string,
): AppLogEntry[] {
  const q = query.trim().toLowerCase();
  return entries.filter((entry) => {
    if (level !== "all" && entry.level !== level) return false;
    if (q && !`${entry.source} ${entry.message}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

// 내보내기 표기: 로컬 시간 + UTC 오프셋. 저장(ts)은 UTC ISO지만 사람이 읽는
// 내보내기 파일이 UTC 그대로면 시계가 틀려 보인다(실기 Windows 보고 — KST에서
// 9시간 어긋나 보임). 오프셋을 명시해 기계 파싱 가능성은 유지한다.
export function formatLocalTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const abs = Math.abs(off);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}

export function formatAppLogEntry(entry: AppLogEntry): string {
  return `[${formatLocalTimestamp(entry.ts)}] ${entry.level.toUpperCase()} source=${entry.source} message=${entry.message}`;
}

export function formatAppLogSnapshot(snapshot: AppLogEntry[]): string {
  return [
    "yeson server console log",
    `exported_at=${formatLocalTimestamp(new Date().toISOString())}`,
    `entries=${snapshot.length}`,
    "",
    ...snapshot.map(formatAppLogEntry),
    "",
  ].join("\n");
}

export async function saveAppLogSnapshot(snapshot: AppLogEntry[]): Promise<string> {
  const contents = formatAppLogSnapshot(snapshot);
  if (hasTauriRuntime()) {
    const result = await invoke<{ path: string }>("save_app_log", { contents });
    return result.path;
  }
  const filename = `yeson-server-log-${Date.now()}.txt`;
  downloadTextFile(filename, contents);
  return `download:${filename}`;
}

function downloadTextFile(filename: string, contents: string): void {
  const blob = new Blob([contents], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

function errorToText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Mirrors the authoritative redaction in apps/server_desktop/src-tauri/src/server_process.rs `redact`.
// The Rust side redacts every backend line before it reaches the UI; this is a defense-in-depth net.
function redact(text: string): string {
  return text
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/-]+/gi, "$1<redacted>")
    .replace(/((?:password|token|api[_-]?key|secret)["']?\s*[:=]\s*["']?)[^"'\s,}]+/gi, "$1<redacted>");
}
// === ANCHOR: APP_LOG_END ===
