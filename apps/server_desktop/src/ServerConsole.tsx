// === ANCHOR: SERVER_CONSOLE_START ===
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { type AppLogEntry, append, clearAppLogs, installAppLogCapture, subscribeAppLogs } from "./appLog";
import ServerConfigPanel from "./setup/ServerConfigPanel";

type ServerStatus = {
  running: boolean;
  pid: number | null;
  port: number | null;
  uptimeSecs: number | null;
  detail: string;
};

const DEFAULT_PORT = 8000;

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

function errorToText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatUptime(secs: number | null): string {
  if (secs == null) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export default function ServerConsole() {
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [port, setPort] = useState<number>(DEFAULT_PORT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<AppLogEntry[]>([]);
  const [showConfig, setShowConfig] = useState(false);
  const [, forceTick] = useState(0);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    installAppLogCapture();
    return subscribeAppLogs(setLogs);
  }, []);

  const refreshStatus = useCallback(async () => {
    if (!hasTauriRuntime()) return;
    try {
      const next = await invoke<ServerStatus>("server_status");
      setStatus(next);
    } catch (err) {
      setError(errorToText(err));
    }
  }, []);

  // Poll status while running so the uptime/PID display stays live.
  useEffect(() => {
    void refreshStatus();
    const id = window.setInterval(() => {
      void refreshStatus();
      forceTick((n) => n + 1); // re-render so derived uptime ticks each second
    }, 1000);
    return () => window.clearInterval(id);
  }, [refreshStatus]);

  // Auto-scroll the log body to the newest line.
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [logs]);

  const onStart = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const next = await invoke<ServerStatus>("start_server", { request: { port, provider: null } });
      setStatus(next);
      append({ level: "info", source: "console", message: `start requested on port ${port}` });
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "console", message: text });
    } finally {
      setBusy(false);
    }
  }, [port]);

  const onStop = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const next = await invoke<ServerStatus>("stop_server");
      setStatus(next);
      append({ level: "info", source: "console", message: "stop requested" });
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "console", message: text });
    } finally {
      setBusy(false);
    }
  }, []);

  const running = status?.running ?? false;
  const liveStatus = useMemo<ServerStatus | null>(() => status, [status]);

  return (
    <div style={styles.shell}>
      <header style={styles.header}>
        <div style={styles.headerRow}>
          <h1 style={styles.title}>yeson server console</h1>
          <span style={{ ...styles.badge, ...(running ? styles.badgeOn : styles.badgeOff) }}>
            {running ? "RUNNING" : "STOPPED"}
          </span>
        </div>
        <div style={styles.controls}>
          <label style={styles.portLabel}>
            port
            <input
              type="number"
              min={1}
              max={65535}
              value={port}
              disabled={running || busy}
              onChange={(e) => setPort(Number(e.target.value) || DEFAULT_PORT)}
              style={styles.portInput}
            />
          </label>
          {running ? (
            <button style={{ ...styles.button, ...styles.stop }} onClick={onStop} disabled={busy}>
              Stop
            </button>
          ) : (
            <button style={{ ...styles.button, ...styles.start }} onClick={onStart} disabled={busy}>
              Start
            </button>
          )}
          <button style={styles.button} onClick={() => clearAppLogs()}>
            Clear logs
          </button>
          <button style={styles.button} onClick={() => setShowConfig((v) => !v)}>
            {showConfig ? "Hide config" : "Config"}
          </button>
        </div>
        <dl style={styles.statusGrid}>
          <Stat label="status" value={running ? "running" : "stopped"} />
          <Stat label="bound port" value={liveStatus?.port != null ? String(liveStatus.port) : "—"} />
          <Stat label="pid" value={liveStatus?.pid != null ? String(liveStatus.pid) : "—"} />
          <Stat label="uptime" value={formatUptime(liveStatus?.uptimeSecs ?? null)} />
        </dl>
        {error ? <p style={styles.error}>{error}</p> : null}
        {!hasTauriRuntime() ? (
          <p style={styles.warn}>Not running inside Tauri — Start/Stop and live logs are disabled in the browser preview.</p>
        ) : null}
      </header>
      {showConfig ? (
        <div style={styles.configWrap}>
          <ServerConfigPanel />
        </div>
      ) : null}
      <main style={styles.logBody}>
        {logs.length === 0 ? (
          <p style={styles.empty}>No log output yet. Start the server to stream its logs here.</p>
        ) : (
          logs.map((entry) => (
            <div key={entry.id} style={{ ...styles.logLine, color: levelColor(entry.level) }}>
              <span style={styles.logTs}>{entry.ts.slice(11, 19)}</span>
              <span style={styles.logSource}>{entry.source}</span>
              <span>{entry.message}</span>
            </div>
          ))
        )}
        <div ref={logEndRef} />
      </main>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.stat}>
      <dt style={styles.statLabel}>{label}</dt>
      <dd style={styles.statValue}>{value}</dd>
    </div>
  );
}

function levelColor(level: AppLogEntry["level"]): string {
  switch (level) {
    case "error":
      return "#ff6b6b";
    case "warn":
      return "#ffd166";
    case "debug":
      return "#7f8c9b";
    default:
      return "#d4dde6";
  }
}

const styles: Record<string, React.CSSProperties> = {
  // Fixed-height column: header never scrolls, log body owns the only scroll.
  shell: {
    height: "100%",
    display: "flex",
    flexDirection: "column",
    background: "#0e141b",
    color: "#d4dde6",
    fontFamily: "system-ui, -apple-system, sans-serif",
  },
  header: {
    flex: "0 0 auto",
    padding: "16px 20px",
    borderBottom: "1px solid #1d2733",
    background: "#11181f",
  },
  headerRow: { display: "flex", alignItems: "center", gap: 12 },
  title: { fontSize: 18, margin: 0, fontWeight: 600 },
  badge: { fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, letterSpacing: 0.5 },
  badgeOn: { background: "#13351f", color: "#4ade80" },
  badgeOff: { background: "#2a1414", color: "#f87171" },
  controls: { display: "flex", alignItems: "center", gap: 10, marginTop: 14 },
  portLabel: { display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "#8ea0b2" },
  portInput: {
    width: 84,
    padding: "5px 8px",
    background: "#0b1117",
    border: "1px solid #25323f",
    borderRadius: 6,
    color: "#d4dde6",
  },
  button: {
    padding: "7px 16px",
    borderRadius: 6,
    border: "1px solid #25323f",
    background: "#1b2530",
    color: "#d4dde6",
    cursor: "pointer",
    fontSize: 13,
  },
  start: { background: "#15803d", borderColor: "#15803d", color: "#fff", fontWeight: 600 },
  stop: { background: "#b91c1c", borderColor: "#b91c1c", color: "#fff", fontWeight: 600 },
  statusGrid: { display: "grid", gridTemplateColumns: "repeat(4, max-content)", gap: 24, margin: "16px 0 0" },
  stat: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "#6b7c8d", margin: 0 },
  statValue: { fontSize: 15, fontWeight: 600, margin: 0, fontVariantNumeric: "tabular-nums" },
  error: { margin: "12px 0 0", color: "#ff6b6b", fontSize: 13 },
  warn: { margin: "12px 0 0", color: "#ffd166", fontSize: 12 },
  // Config block: capped, independently-scrollable region between the fixed
  // header and the log body so it never collapses the single-scroll layout.
  configWrap: { flex: "0 0 auto", maxHeight: "55%", overflowY: "auto" },
  logBody: {
    flex: "1 1 auto",
    overflowY: "auto",
    padding: "12px 20px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: 12,
    lineHeight: 1.55,
  },
  empty: { color: "#5a6b7c" },
  logLine: { display: "flex", gap: 10, whiteSpace: "pre-wrap", wordBreak: "break-word" },
  logTs: { color: "#5a6b7c", flex: "0 0 auto" },
  logSource: { color: "#7f8c9b", flex: "0 0 auto", minWidth: 92 },
};
// === ANCHOR: SERVER_CONSOLE_END ===
