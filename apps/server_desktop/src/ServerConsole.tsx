// === ANCHOR: SERVER_CONSOLE_START ===
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getVersion } from "@tauri-apps/api/app";
import { type AppLogEntry, append, clearAppLogs, filterLogEntries, installAppLogCapture, saveAppLogSnapshot, subscribeAppLogs } from "./appLog";
import ServerConfigPanel from "./setup/ServerConfigPanel";
import TunnelDegradedBanner from "./TunnelDegradedBanner";
import DevicePanel from "./DevicePanel";

type ServerStatus = {
  running: boolean;
  pid: number | null;
  port: number | null;
  uptimeSecs: number | null;
  detail: string;
};

// Mirrors the Rust `TunnelStatus` (serde camelCase). `url` is the public viewer
// base (https://<rand>.trycloudflare.com) once the tunnel is live.
type TunnelStatus = {
  running: boolean;
  url: string | null;
  vport: number | null;
  uptimeSecs: number | null;
  detail: string;
  // P4.2: public tunnel dropped on its own. `running` is false, `url` is the
  // dead link. LAN viewing is unaffected; the degraded banner handles recovery.
  degraded: boolean;
};

const DEFAULT_PORT = 8000;

// Persist the "auto go live on start" preference. Default ON: a missing key
// reads as true so the first run already auto-publishes (the toggle lets an
// operator opt out, e.g. on LAN-only setups).
const AUTO_GOLIVE_KEY = "yeson-server-auto-golive";

function loadAutoGoLive(): boolean {
  try {
    return window.localStorage.getItem(AUTO_GOLIVE_KEY) !== "false";
  } catch {
    return true;
  }
}

function storeAutoGoLive(value: boolean): void {
  try {
    window.localStorage.setItem(AUTO_GOLIVE_KEY, value ? "true" : "false");
  } catch {
    /* localStorage may be unavailable; the in-memory toggle still works */
  }
}

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

// Auto-publish readiness gate. The server process binds the port (status =
// "running") ~1s BEFORE uvicorn actually accepts HTTP, so an immediate auto Go
// Live hits "connection refused" and aborts. Poll the SAME server-HTTP probe the
// Go Live gate uses — `live_session_count_cmd` does a real TCP+HTTP round-trip to
// 127.0.0.1:<port> (it REJECTS while uvicorn isn't accepting, RESOLVES once it
// is) — until it resolves without throwing. Returns true when HTTP is ready,
// false if it never came up within the budget (caller leaves the server on LAN).
const READINESS_POLL_INTERVAL_MS = 450;
const READINESS_TIMEOUT_MS = 12_000;

async function waitForServerHttpReady(): Promise<boolean> {
  const deadline = Date.now() + READINESS_TIMEOUT_MS;
  for (;;) {
    try {
      // Resolving (even to null) means the command ran end-to-end; when the
      // server is running it implies the HTTP probe round-tripped successfully.
      await invoke<number | null>("live_session_count_cmd");
      return true;
    } catch {
      if (Date.now() >= deadline) return false;
      await sleep(READINESS_POLL_INTERVAL_MS);
    }
  }
}

function errorToText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Log timestamps are stored as UTC ISO (toISOString); display them on the
// operator's local clock so the console time matches the wall clock. Falls back
// to the raw HH:MM:SS slice if the value isn't a parseable date.
function formatLogClock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.slice(11, 19);
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
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
  type View = "logs" | "config" | "devices";
  const [activeView, setActiveView] = useState<View>("logs");
  const [logLevel, setLogLevel] = useState<AppLogEntry["level"] | "all">("all");
  const [logQuery, setLogQuery] = useState("");
  const [logWrap, setLogWrap] = useState(true);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [tunnel, setTunnel] = useState<TunnelStatus | null>(null);
  // null = server stopped (no meeting possible); number = live-meeting count.
  const [liveSessions, setLiveSessions] = useState<number | null>(null);
  const [tunnelBusy, setTunnelBusy] = useState(false);
  const [autoGoLive, setAutoGoLive] = useState<boolean>(loadAutoGoLive);
  const [appVersion, setAppVersion] = useState<string>("");
  const [, forceTick] = useState(0);
  const logBodyRef = useRef<HTMLDivElement>(null);
  // Latest onGoLive, so onStart can trigger auto-publish without a forward
  // reference (onGoLive is declared below) or recreating onStart on its deps.
  const goLiveRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    installAppLogCapture();
    return subscribeAppLogs(setLogs);
  }, []);

  // App version from the Tauri bundle (tauri.conf.json). Best-effort: in the
  // browser preview there is no Tauri runtime, so the version line stays hidden.
  useEffect(() => {
    if (!hasTauriRuntime()) return;
    getVersion()
      .then(setAppVersion)
      .catch(() => {
        /* version is a cosmetic footer; ignore failures */
      });
  }, []);

  const refreshStatus = useCallback(async () => {
    if (!hasTauriRuntime()) return;
    try {
      const next = await invoke<ServerStatus>("server_status");
      setStatus(next);
    } catch (err) {
      setError(errorToText(err));
    }
    // Tunnel + live-meeting state for the "Go live (public)" control. Best-effort:
    // a probe failure (e.g. server momentarily down) must not spam the error line.
    try {
      setTunnel(await invoke<TunnelStatus>("tunnel_status_cmd"));
    } catch {
      /* tunnel status is non-critical for the main console */
    }
    try {
      setLiveSessions(await invoke<number | null>("live_session_count_cmd"));
    } catch {
      setLiveSessions(null);
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

  // Stick to the newest line only when the user is already near the bottom.
  // If they've scrolled up to read older output, leave their position alone so
  // a busy log stream doesn't keep yanking them back down.
  useEffect(() => {
    const el = logBodyRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [logs]);

  const onStart = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const next = await invoke<ServerStatus>("start_server", { request: { port, provider: null } });
      setStatus(next);
      append({ level: "info", source: "console", message: `start requested on port ${port}` });
      // Auto go live: once the server's HTTP is actually ready, optionally
      // publish the tunnel. BEST-EFFORT — onGoLive swallows its own errors (logs
      // + error line + degraded banner), so a tunnel failure never fails the
      // server start; the server stays up on LAN. The process binds the port
      // ~1s before uvicorn accepts HTTP, so we WAIT for a real HTTP round-trip
      // (waitForServerHttpReady) before publishing — otherwise the gate probe
      // hits "connection refused" and aborts. Readiness wait is auto-path only;
      // manual Go Live is user-initiated and untouched.
      if (autoGoLive && next.running) {
        append({ level: "info", source: "console", message: "auto go live: waiting for server to be ready…" });
        const ready = await waitForServerHttpReady();
        if (ready) {
          append({ level: "info", source: "console", message: "auto go live: publishing tunnel…" });
          await goLiveRef.current();
        } else {
          append({ level: "warn", source: "console", message: "auto go live skipped: server not ready — running on LAN (use Go live to publish)" });
        }
      }
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "console", message: text });
    } finally {
      setBusy(false);
    }
  }, [port, autoGoLive]);

  const onToggleAutoGoLive = useCallback((value: boolean) => {
    setAutoGoLive(value);
    storeAutoGoLive(value);
  }, []);

  const onStop = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      // Symmetric with auto-go-live on Start: take the public tunnel down too so
      // "Stop" means fully offline — no public URL left pointing at a stopped
      // server, and the next Start mints a fresh tunnel. Best-effort: a tunnel
      // stop failure must not block stopping the server. (The manual "stop
      // public" button still exists to drop only the tunnel.)
      try {
        setTunnel(await invoke<TunnelStatus>("stop_tunnel_cmd"));
      } catch {
        /* tunnel may already be down; ignore */
      }
      const next = await invoke<ServerStatus>("stop_server");
      setStatus(next);
      append({ level: "info", source: "console", message: "stop requested (server + public tunnel)" });
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "console", message: text });
    } finally {
      setBusy(false);
    }
  }, []);

  const onGoLive = useCallback(async () => {
    setError(null);
    setTunnelBusy(true);
    try {
      const serverPort = status?.port ?? port;
      const next = await invoke<TunnelStatus>("start_tunnel_cmd", { serverPort });
      setTunnel(next);
      append({ level: "info", source: "console", message: `public mode on: ${next.url ?? "(no url)"}` });
      await refreshStatus();
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "console", message: text });
    } finally {
      setTunnelBusy(false);
    }
  }, [status, port, refreshStatus]);

  // Keep the ref pointed at the current onGoLive so onStart's auto-publish always
  // calls the latest closure (with up-to-date status/port) without re-creating it.
  useEffect(() => {
    goLiveRef.current = onGoLive;
  }, [onGoLive]);

  const onStopPublic = useCallback(async () => {
    setError(null);
    setTunnelBusy(true);
    try {
      const next = await invoke<TunnelStatus>("stop_tunnel_cmd");
      setTunnel(next);
      append({ level: "info", source: "console", message: "public mode off" });
      await refreshStatus();
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "console", message: text });
    } finally {
      setTunnelBusy(false);
    }
  }, [refreshStatus]);

  const onSaveLogs = useCallback(async () => {
    setSaveMsg(null);
    try {
      const path = await saveAppLogSnapshot(logs);
      setSaveMsg(`saved: ${path}`);
    } catch (err) {
      setSaveMsg(`save failed: ${errorToText(err)}`);
    }
  }, [logs]);

  const onOpenLogDir = useCallback(async () => {
    setSaveMsg(null);
    try {
      await invoke("open_log_dir");
    } catch (err) {
      setSaveMsg(`open failed: ${errorToText(err)}`);
    }
  }, []);

  const running = status?.running ?? false;
  const liveStatus = useMemo<ServerStatus | null>(() => status, [status]);
  const tunnelOn = tunnel?.running ?? false;
  const meetingLive = (liveSessions ?? 0) > 0;
  const tunnelDegraded = tunnel?.degraded ?? false;

  const navItems: Array<{ view: View; label: string }> = [
    { view: "logs", label: "Logs" },
    { view: "config", label: "Config" },
    { view: "devices", label: "Devices" },
  ];

  const visibleLogs = filterLogEntries(logs, logLevel, logQuery);

  return (
    <div style={styles.shell}>
      <aside style={styles.sidebar}>
        <p style={styles.brand}>yeson server console</p>
        {appVersion ? <span style={styles.version}>v{appVersion}</span> : null}
        <span style={{ ...styles.badge, ...(running ? styles.badgeOn : styles.badgeOff) }}>
          {running ? "RUNNING" : "STOPPED"}
        </span>
        <nav style={styles.nav} aria-label="Server console sections">
          {navItems.map((item) => (
            <button
              key={item.view}
              type="button"
              onClick={() => setActiveView(item.view)}
              style={{ ...styles.navButton, ...(activeView === item.view ? styles.navButtonActive : null) }}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </aside>
      <div style={styles.column}>
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
            <label
              style={styles.autoGoLiveLabel}
              title="서버 시작 시 자동으로 공개(터널)합니다. 실패해도 LAN은 유지됩니다."
            >
              <input
                type="checkbox"
                checked={autoGoLive}
                disabled={busy}
                onChange={(e) => onToggleAutoGoLive(e.target.checked)}
              />
              시작 시 자동 공개
            </label>
          </div>
          <dl style={styles.statusGrid}>
            <Stat label="status" value={running ? "running" : "stopped"} />
            <Stat label="bound port" value={liveStatus?.port != null ? String(liveStatus.port) : "—"} />
            <Stat label="pid" value={liveStatus?.pid != null ? String(liveStatus.pid) : "—"} />
            <Stat label="uptime" value={formatUptime(liveStatus?.uptimeSecs ?? null)} />
          </dl>
          {/* Public mode (P4.1b): expose the per-meeting viewer URL over a cloudflared
              quick-tunnel. "Go live" is hidden while a meeting is active — going
              public restarts the server, which would interrupt a live meeting. */}
          <div style={styles.tunnelRow}>
            <span style={{ ...styles.badge, ...(tunnelOn ? styles.badgeOn : styles.badgeOff) }}>
              {tunnelOn ? "PUBLIC" : "LAN ONLY"}
            </span>
            {tunnelOn ? (
              <button
                style={{ ...styles.button, ...styles.stop }}
                onClick={onStopPublic}
                disabled={tunnelBusy}
              >
                Stop public
              </button>
            ) : (
              <button
                style={{ ...styles.button, ...styles.start }}
                onClick={onGoLive}
                disabled={tunnelBusy || !running || meetingLive}
                title={
                  !running
                    ? "start the server first"
                    : meetingLive
                      ? "end the meeting before going public (it would be interrupted)"
                      : "publish the per-meeting viewer URL over a cloudflared tunnel"
                }
              >
                Go live (public)
              </button>
            )}
            {tunnelOn && tunnel?.url ? (
              <a style={styles.tunnelUrl} href={tunnel.url} target="_blank" rel="noreferrer">
                {tunnel.url}
              </a>
            ) : (
              <span style={styles.tunnelHint}>
                {tunnelBusy
                  ? "starting tunnel…"
                  : !running
                    ? "server stopped — start it to enable public mode"
                    : meetingLive
                      ? "a meeting is live — end it before going public"
                      : "viewer URL stays LAN-only until you go public"}
              </span>
            )}
          </div>
          <TunnelDegradedBanner
            degraded={tunnelDegraded}
            deadUrl={tunnel?.url ?? null}
            serverPort={status?.port ?? port}
            running={running}
            meetingLive={meetingLive}
            busy={tunnelBusy}
            onRepublish={onGoLive}
            onFallbackLan={onStopPublic}
          />
          {error ? <p style={styles.error}>{error}</p> : null}
          {!hasTauriRuntime() ? (
            <p style={styles.warn}>Not running inside Tauri — Start/Stop and live logs are disabled in the browser preview.</p>
          ) : null}
        </header>
        <main style={styles.content}>
          <section hidden={activeView !== "logs"} style={activeView === "logs" ? styles.viewFill : undefined}>
            <div style={styles.logToolbar}>
              <select value={logLevel} onChange={(e) => setLogLevel(e.target.value as typeof logLevel)} style={styles.select}>
                <option value="all">All</option>
                <option value="info">Info</option>
                <option value="warn">Warn</option>
                <option value="error">Error</option>
                <option value="debug">Debug</option>
              </select>
              <input
                value={logQuery}
                onChange={(e) => setLogQuery(e.target.value)}
                placeholder="search…"
                style={styles.search}
              />
              <label style={styles.wrapLabel}>
                <input type="checkbox" checked={logWrap} onChange={(e) => setLogWrap(e.target.checked)} /> wrap
              </label>
              <span style={styles.toolbarSpacer} />
              <button style={styles.button} onClick={() => clearAppLogs()}>Clear</button>
              <button style={styles.button} onClick={onSaveLogs}>Export</button>
              <button style={styles.button} onClick={onOpenLogDir}>Open folder</button>
            </div>
            {saveMsg ? <p style={saveMsg.startsWith("saved:") ? styles.success : styles.warn}>{saveMsg}</p> : null}
            <div ref={logBodyRef} className="log-scroll" style={styles.logBody}>
              {visibleLogs.length === 0 ? (
                <p style={styles.empty}>
                  {logs.length === 0
                    ? "No log output yet. Start the server to stream its logs here."
                    : "No matching log output."}
                </p>
              ) : (
                visibleLogs.map((entry) => (
                  <div key={entry.id} style={{ ...styles.logLine, color: levelColor(entry.level), whiteSpace: logWrap ? "pre-wrap" : "pre" }}>
                    <span style={styles.logTs}>{formatLogClock(entry.ts)}</span>
                    <span style={styles.logSource}>{entry.source}</span>
                    <span>{entry.message}</span>
                  </div>
                ))
              )}
            </div>
          </section>
          <section hidden={activeView !== "config"} style={activeView === "config" ? styles.viewScroll : undefined}>
            <ServerConfigPanel />
          </section>
          <section hidden={activeView !== "devices"} style={activeView === "devices" ? styles.viewScroll : undefined}>
            <DevicePanel serverPort={status?.port ?? null} running={running} />
          </section>
        </main>
      </div>
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
      return "var(--ys-danger-text)";
    case "warn":
      return "var(--ys-warning-text)";
    case "debug":
      return "var(--ys-text-muted)";
    default:
      return "var(--ys-text-body)";
  }
}

// Styled with the shared @yeson-meet/ui tokens (var(--ys-*)) so this console
// reads as the same product as the Operator app. Layout/dimensions are kept
// deliberately compact (this is a control plane); only the brand language —
// colors, surfaces, radii, typography — is unified.
const styles: Record<string, React.CSSProperties> = {
  // Row layout: fixed sidebar + right-hand column (header + content area).
  shell: {
    height: "100%",
    display: "flex",
    flexDirection: "row",
    background: "var(--ys-bg-app)",
    color: "var(--ys-text-body)",
    fontFamily: "var(--ys-font-ui)",
  },
  sidebar: {
    flex: "0 0 auto",
    width: "var(--ys-sidebar-width)",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 10,
    padding: 22,
    borderRight: "1px solid var(--ys-border-subtle)",
    background: "var(--ys-bg-sidebar)",
  },
  brand: {
    fontSize: 13,
    fontWeight: "var(--ys-weight-black)" as React.CSSProperties["fontWeight"],
    margin: 0,
    color: "var(--ys-accent)",
    letterSpacing: ".08em",
    textTransform: "uppercase",
  },
  version: { fontSize: 11, color: "var(--ys-text-faint)", fontVariantNumeric: "tabular-nums", letterSpacing: ".04em" },
  nav: { display: "flex", flexDirection: "column", gap: 4, marginTop: 8 },
  navButton: {
    textAlign: "left",
    padding: "12px 14px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid transparent",
    background: "transparent",
    color: "var(--ys-text-label)",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: "var(--ys-weight-bold)" as React.CSSProperties["fontWeight"],
  },
  navButtonActive: {
    background: "var(--ys-accent-soft)",
    borderColor: "var(--ys-accent-strong)",
    color: "var(--ys-on-accent)",
    fontWeight: "var(--ys-weight-black)" as React.CSSProperties["fontWeight"],
  },
  column: {
    flex: "1 1 auto",
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
    background: "var(--ys-bg-app-gradient)",
  },
  header: {
    flex: "0 0 auto",
    padding: "16px 20px",
    borderBottom: "1px solid var(--ys-border-subtle)",
    background: "transparent",
  },
  headerRow: { display: "flex", alignItems: "center", gap: 12 },
  title: { fontSize: 18, margin: 0, fontWeight: "var(--ys-weight-bold)" as React.CSSProperties["fontWeight"], color: "var(--ys-text-strong)" },
  badge: { fontSize: 11, fontWeight: "var(--ys-weight-black)" as React.CSSProperties["fontWeight"], padding: "3px 9px", borderRadius: "var(--ys-radius-pill)", letterSpacing: 0.5, border: "1px solid transparent" },
  badgeOn: { background: "var(--ys-success-bg)", color: "var(--ys-success-text)", borderColor: "var(--ys-success-border)" },
  badgeOff: { background: "var(--ys-danger-bg)", color: "var(--ys-danger-text)", borderColor: "var(--ys-danger-border)" },
  controls: { display: "flex", alignItems: "center", gap: 10, marginTop: 14 },
  portLabel: { display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--ys-text-muted)" },
  autoGoLiveLabel: { display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--ys-text-muted)", cursor: "pointer" },
  portInput: {
    width: 84,
    padding: "7px 10px",
    background: "var(--ys-bg-app)",
    border: "1px solid var(--ys-border-strong)",
    borderRadius: "var(--ys-radius-control)",
    color: "var(--ys-text-body)",
  },
  button: {
    padding: "8px 16px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-strong)",
    background: "transparent",
    color: "var(--ys-text-label)",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: "var(--ys-weight-bold)" as React.CSSProperties["fontWeight"],
  },
  start: { background: "var(--ys-accent-strong)", borderColor: "var(--ys-accent-strong)", color: "var(--ys-on-accent)", fontWeight: "var(--ys-weight-black)" as React.CSSProperties["fontWeight"] },
  stop: { background: "var(--ys-danger)", borderColor: "var(--ys-danger)", color: "#fff", fontWeight: "var(--ys-weight-black)" as React.CSSProperties["fontWeight"] },
  statusGrid: { display: "grid", gridTemplateColumns: "repeat(4, max-content)", gap: 24, margin: "16px 0 0" },
  stat: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--ys-text-faint)", margin: 0 },
  statValue: { fontSize: 15, fontWeight: "var(--ys-weight-bold)" as React.CSSProperties["fontWeight"], margin: 0, fontVariantNumeric: "tabular-nums", color: "var(--ys-text-strong)" },
  error: { margin: "12px 0 0", color: "var(--ys-danger-text)", fontSize: 13 },
  warn: { margin: "12px 0 0", color: "var(--ys-warning-text)", fontSize: 12 },
  success: { margin: "8px 20px 0", color: "var(--ys-success-text)", fontSize: 12 },
  tunnelRow: { display: "flex", alignItems: "center", gap: 10, marginTop: 14, flexWrap: "wrap" },
  tunnelUrl: {
    fontFamily: "var(--ys-font-mono)",
    fontSize: 12,
    color: "var(--ys-success)",
    wordBreak: "break-all",
  },
  tunnelHint: { fontSize: 12, color: "var(--ys-text-faint)" },
  content: { flex: "1 1 auto", minHeight: 0, display: "flex", flexDirection: "column" },
  viewFill: { flex: "1 1 auto", minHeight: 0, display: "flex", flexDirection: "column" },
  viewScroll: { flex: "1 1 auto", minHeight: 0, overflowY: "auto", padding: "12px 20px" },
  logToolbar: { display: "flex", alignItems: "center", gap: 8, padding: "10px 20px", borderBottom: "1px solid var(--ys-border-subtle)" },
  select: { padding: "6px 9px", background: "var(--ys-bg-app)", border: "1px solid var(--ys-border-strong)", borderRadius: "var(--ys-radius-md)", color: "var(--ys-text-body)", fontSize: "var(--ys-font-control)" },
  search: { flex: "0 1 240px", padding: "6px 9px", background: "var(--ys-bg-app)", border: "1px solid var(--ys-border-strong)", borderRadius: "var(--ys-radius-md)", color: "var(--ys-text-body)", fontSize: "var(--ys-font-control)" },
  wrapLabel: { display: "flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--ys-text-muted)" },
  toolbarSpacer: { flex: "1 1 auto" },
  logBody: {
    flex: "1 1 auto",
    minHeight: 0,
    overflowY: "auto",
    padding: "12px 20px",
    fontFamily: "var(--ys-font-mono)",
    fontSize: "var(--ys-font-log)",
    lineHeight: 1.55,
  },
  empty: { color: "var(--ys-text-faint)" },
  logLine: { display: "flex", gap: 10, whiteSpace: "pre-wrap", wordBreak: "break-word" },
  logTs: { color: "var(--ys-text-faint)", flex: "0 0 auto" },
  logSource: { color: "var(--ys-text-muted)", flex: "0 0 auto", minWidth: 92 },
};
// === ANCHOR: SERVER_CONSOLE_END ===
