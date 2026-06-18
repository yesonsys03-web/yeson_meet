// === ANCHOR: APP_LOG_START ===
// Minimal app-log store that consumes the SAME `app-log` backend event contract
// as the client app (apps/desktop/src/diagnostics/appLog.ts). The Rust
// `server_process` forwarder emits `{ level, source, message }` per line of the
// server's stdout/stderr; this collects them for the log viewer (AC3.2).
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

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

function errorToText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function redact(text: string): string {
  return text
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/-]+/gi, "$1<redacted>")
    .replace(/((?:password|token|api[_-]?key|secret)["']?\s*[:=]\s*["']?)[^"'\s,}]+/gi, "$1<redacted>");
}
// === ANCHOR: APP_LOG_END ===
