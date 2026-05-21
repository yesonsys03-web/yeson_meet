// === ANCHOR: APP_LOG_START ===
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export type AppLogLevel = "debug" | "info" | "warn" | "error";

export type AppLogEntry = {
  id: number;
  ts: string;
  level: AppLogLevel;
  source: string;
  message: string;
  detail?: string;
  durationMs?: number;
};

type AppLogInput = Omit<AppLogEntry, "id" | "ts"> & { ts?: string };
type AppLogSubscriber = (entries: AppLogEntry[]) => void;
type BackendLogPayload = {
  level: AppLogLevel;
  source: string;
  message: string;
};
type SaveAppLogResult = { path: string };
type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

const MAX_LOG_ENTRIES = 1000;
const BACKEND_LOG_EVENT = "app-log";
const subscribers = new Set<AppLogSubscriber>();

let entries: AppLogEntry[] = [];
let nextId = 1;
let captureInstalled = false;

export const appLogger = {
  debug(source: string, message: string, options: Pick<AppLogInput, "detail" | "durationMs"> = {}) {
    appendAppLog({ level: "debug", source, message, ...options });
  },
  info(source: string, message: string, options: Pick<AppLogInput, "detail" | "durationMs"> = {}) {
    appendAppLog({ level: "info", source, message, ...options });
  },
  warn(source: string, message: string, options: Pick<AppLogInput, "detail" | "durationMs"> = {}) {
    appendAppLog({ level: "warn", source, message, ...options });
  },
  error(source: string, message: string, options: Pick<AppLogInput, "detail" | "durationMs"> = {}) {
    appendAppLog({ level: "error", source, message, ...options });
  },
  latency(source: string, message: string, durationMs: number, options: Pick<AppLogInput, "detail"> = {}) {
    appendAppLog({ level: "info", source, message, durationMs, ...options });
  },
};

export function installAppLogCapture(): void {
  if (captureInstalled) return;
  captureInstalled = true;
  patchConsole();
  appLogger.info("diagnostics", "App log capture started");

  if (!hasTauriRuntime()) return;

  void listen<BackendLogPayload>(BACKEND_LOG_EVENT, (event) => {
    appendAppLog({ ...event.payload });
  }).catch((error) => {
    appendAppLog({ level: "warn", source: "diagnostics", message: "Backend log listener failed", detail: errorToText(error) });
  });
}

export function appendAppLog(input: AppLogInput): void {
  const entry: AppLogEntry = {
    id: nextId,
    ts: input.ts ?? new Date().toISOString(),
    level: input.level,
    source: input.source,
    message: redactSensitiveText(input.message),
    detail: input.detail ? redactSensitiveText(input.detail) : undefined,
    durationMs: input.durationMs,
  };
  nextId += 1;
  entries = [...entries, entry].slice(-MAX_LOG_ENTRIES);
  notifySubscribers();
}

export function clearAppLogs(): void {
  entries = [];
  notifySubscribers();
}

export function subscribeAppLogs(subscriber: AppLogSubscriber): () => void {
  subscribers.add(subscriber);
  subscriber(snapshotEntries());
  return () => subscribers.delete(subscriber);
}

export async function saveAppLogSnapshot(snapshot: AppLogEntry[] = entries): Promise<string> {
  const contents = formatAppLogSnapshot(snapshot);
  if (hasTauriRuntime()) {
    const result = await invoke<SaveAppLogResult>("save_app_log", { contents });
    return result.path;
  }
  const filename = `yeson-meet-log-${Date.now()}.txt`;
  downloadTextFile(filename, contents);
  return `download:${filename}`;
}

export function formatAppLogSnapshot(snapshot: AppLogEntry[]): string {
  return [
    "yeson-meet app log",
    `exported_at=${new Date().toISOString()}`,
    `entries=${snapshot.length}`,
    "",
    ...snapshot.map(formatAppLogEntry),
    "",
  ].join("\n");
}

export function formatAppLogEntry(entry: AppLogEntry): string {
  const duration = typeof entry.durationMs === "number" ? ` duration_ms=${Math.round(entry.durationMs)}` : "";
  const detail = entry.detail ? ` detail=${entry.detail}` : "";
  return `[${entry.ts}] ${entry.level.toUpperCase()} source=${entry.source}${duration} message=${entry.message}${detail}`;
}

function snapshotEntries(): AppLogEntry[] {
  return [...entries];
}

function notifySubscribers(): void {
  const snapshot = snapshotEntries();
  subscribers.forEach((subscriber) => subscriber(snapshot));
}

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

function patchConsole(): void {
  const originalDebug = console.debug.bind(console);
  const originalInfo = console.info.bind(console);
  const originalLog = console.log.bind(console);
  const originalWarn = console.warn.bind(console);
  const originalError = console.error.bind(console);

  console.debug = (...args: unknown[]) => {
    originalDebug(...args);
    appendAppLog({ level: "debug", source: "console", message: formatConsoleArgs(args) });
  };
  console.info = (...args: unknown[]) => {
    originalInfo(...args);
    appendAppLog({ level: "info", source: "console", message: formatConsoleArgs(args) });
  };
  console.log = (...args: unknown[]) => {
    originalLog(...args);
    appendAppLog({ level: "info", source: "console", message: formatConsoleArgs(args) });
  };
  console.warn = (...args: unknown[]) => {
    originalWarn(...args);
    appendAppLog({ level: "warn", source: "console", message: formatConsoleArgs(args) });
  };
  console.error = (...args: unknown[]) => {
    originalError(...args);
    appendAppLog({ level: "error", source: "console", message: formatConsoleArgs(args) });
  };
}

function formatConsoleArgs(args: unknown[]): string {
  return args.map(formatUnknown).join(" ");
}

function formatUnknown(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || value === null || value === undefined) return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function errorToText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function redactSensitiveText(text: string): string {
  return text
    .replace(/(Bearer\s+)[A-Za-z0-9._~+\/-]+/gi, "$1<redacted>")
    .replace(/((?:password|token|api[_-]?key|deviceApiKey)["']?\s*[:=]\s*["']?)[^"'\s,}]+/gi, "$1<redacted>");
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
// === ANCHOR: APP_LOG_END ===
