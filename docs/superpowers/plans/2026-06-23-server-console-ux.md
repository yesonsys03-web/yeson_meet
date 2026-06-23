# Server Console UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the server console a client-consistent sidebar layout, a filterable log viewer, and persistent on-disk log storage plus export — fixing the cramped config/devices panels and the missing log-save feature.

**Architecture:** Frontend (`apps/server_desktop/src`) gains pure log helpers (unit-tested with vitest) and a refactored `ServerConsole` with a left sidebar (`Logs/Config/Devices`) and a log toolbar. The Rust side (`apps/server_desktop/src-tauri`) writes every forwarded server line to a dated, redacted, 7-day-retained log file at the single `emit_backend_log` choke point, and exposes an `open_log_dir` command (the `save_app_log` export command already exists).

**Tech Stack:** React + TypeScript + Vite + vitest (frontend); Rust + Tauri v2 + `regex` (backend).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-23-server-console-ux-design.md`.
- Log file retention: **7 days**; pruned on server start.
- Dated log file path: `<app_data_dir>/logs/server-YYYY-MM-DD.log`.
- Export filename: `yeson-server-log-<ms>.txt` (existing `save_app_log` behavior — do not change).
- Level filter default: `all`. Line wrap default: on.
- Secrets are redacted **once in Rust** at `emit_backend_log`; the frontend `redact()` stays as a net.
- Global server controls (port, Start/Stop, Go live, status, tunnel) stay in the main top header, not the sidebar.
- Keep edits inside existing anchor regions where present (`// === ANCHOR: NAME_START/END ===`).
- Every commit message ends with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Frontend tests run from `apps/server_desktop` via `pnpm test` (added in Task 1).
- Rust tests run via `cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml`.

---

## File Structure

- `apps/server_desktop/package.json` — add `vitest` devDep + `"test"` script.
- `apps/server_desktop/src/appLog.ts` — add `filterLogEntries`, `formatAppLogEntry`, `formatAppLogSnapshot`, `saveAppLogSnapshot`, `downloadTextFile`; import `invoke`.
- `apps/server_desktop/src/appLog.test.ts` — **create**; unit tests for the pure helpers.
- `apps/server_desktop/src/ServerConsole.tsx` — sidebar nav + `activeView` + Logs toolbar; remove `panelsWrap`.
- `apps/server_desktop/src-tauri/src/diagnostics.rs` — add `open_log_dir` command (keep existing `save_app_log`).
- `apps/server_desktop/src-tauri/src/server_process.rs` — date/timestamp helpers, `redact`, file append in `emit_backend_log`, `prune_old_logs` on start; Rust unit tests.
- `apps/server_desktop/src-tauri/src/lib.rs` — register `open_log_dir`.
- `apps/server_desktop/src-tauri/Cargo.toml` — add `regex` if absent.

---

## Task 1: Frontend log helpers + vitest

**Files:**
- Modify: `apps/server_desktop/package.json`
- Modify: `apps/server_desktop/src/appLog.ts`
- Test: `apps/server_desktop/src/appLog.test.ts` (create)

**Interfaces:**
- Produces:
  - `filterLogEntries(entries: AppLogEntry[], level: AppLogLevel | "all", query: string): AppLogEntry[]`
  - `formatAppLogEntry(entry: AppLogEntry): string`
  - `formatAppLogSnapshot(snapshot: AppLogEntry[]): string`
  - `saveAppLogSnapshot(snapshot: AppLogEntry[]): Promise<string>`

- [ ] **Step 1: Add vitest devDep + test script**

In `apps/server_desktop/package.json`, add to `scripts`:
```json
    "test": "vitest run"
```
and to `devDependencies`:
```json
    "vitest": "^2"
```
Then install: `pnpm install`

- [ ] **Step 2: Write the failing test**

Create `apps/server_desktop/src/appLog.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import {
  filterLogEntries,
  formatAppLogEntry,
  formatAppLogSnapshot,
  type AppLogEntry,
} from "./appLog";

const entry = (over: Partial<AppLogEntry>): AppLogEntry => ({
  id: 1,
  ts: "2026-06-23T08:00:01.000Z",
  level: "info",
  source: "server",
  message: "hello world",
  ...over,
});

describe("filterLogEntries", () => {
  const entries = [
    entry({ id: 1, level: "info", source: "server", message: "started ok" }),
    entry({ id: 2, level: "warn", source: "gemini", message: "slow turn" }),
    entry({ id: 3, level: "error", source: "server", message: "boom failure" }),
  ];

  it("returns all when level=all and query empty", () => {
    expect(filterLogEntries(entries, "all", "")).toHaveLength(3);
  });

  it("filters by level", () => {
    const out = filterLogEntries(entries, "warn", "");
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe(2);
  });

  it("filters by case-insensitive substring over source+message", () => {
    expect(filterLogEntries(entries, "all", "GEMINI")).toHaveLength(1);
    expect(filterLogEntries(entries, "all", "boom")).toHaveLength(1);
    expect(filterLogEntries(entries, "all", "nope")).toHaveLength(0);
  });

  it("combines level and query (AND)", () => {
    expect(filterLogEntries(entries, "error", "server")).toHaveLength(1);
    expect(filterLogEntries(entries, "error", "gemini")).toHaveLength(0);
  });
});

describe("formatAppLogEntry / formatAppLogSnapshot", () => {
  it("formats one entry as a single line", () => {
    const line = formatAppLogEntry(entry({ message: "ready" }));
    expect(line).toBe("[2026-06-23T08:00:01.000Z] INFO source=server message=ready");
  });

  it("wraps a snapshot with a header and entry count", () => {
    const text = formatAppLogSnapshot([entry({}), entry({ id: 2 })]);
    expect(text).toContain("yeson server console log");
    expect(text).toContain("entries=2");
    expect(text.split("\n").filter((l) => l.startsWith("[")).length).toBe(2);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd apps/server_desktop && pnpm test`
Expected: FAIL — `filterLogEntries`/`formatAppLogEntry`/`formatAppLogSnapshot` are not exported.

- [ ] **Step 4: Implement the helpers**

In `apps/server_desktop/src/appLog.ts`, change the top import to add `invoke`:
```ts
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
```
Then add inside the anchor region (before `function hasTauriRuntime`):
```ts
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

export function formatAppLogEntry(entry: AppLogEntry): string {
  return `[${entry.ts}] ${entry.level.toUpperCase()} source=${entry.source} message=${entry.message}`;
}

export function formatAppLogSnapshot(snapshot: AppLogEntry[]): string {
  return [
    "yeson server console log",
    `exported_at=${new Date().toISOString()}`,
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/server_desktop && pnpm test`
Expected: PASS (all `appLog.test.ts` cases green).

- [ ] **Step 6: Commit**

```bash
git add apps/server_desktop/package.json apps/server_desktop/pnpm-lock.yaml apps/server_desktop/src/appLog.ts apps/server_desktop/src/appLog.test.ts ../../pnpm-lock.yaml 2>/dev/null; git add -A apps/server_desktop
git commit -m "feat(server-console): log filter/format/save helpers + vitest

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Rust `open_log_dir` command

**Files:**
- Modify: `apps/server_desktop/src-tauri/src/diagnostics.rs`
- Modify: `apps/server_desktop/src-tauri/src/lib.rs:15-29`

**Interfaces:**
- Produces: Tauri command `open_log_dir() -> Result<(), String>` that reveals `<app_data_dir>/logs` in the OS file manager.

- [ ] **Step 1: Add the command**

In `apps/server_desktop/src-tauri/src/diagnostics.rs`, inside the anchor region after `save_app_log`, add:
```rust
/// Reveal the on-disk log directory (where the dated server-*.log files and
/// exported snapshots live) in the OS file manager. Best-effort: spawns the
/// platform opener and does not wait on it.
#[tauri::command]
pub fn open_log_dir(app: tauri::AppHandle) -> Result<(), String> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data dir: {error}"))?;
    let log_dir = app_data_dir.join("logs");
    fs::create_dir_all(&log_dir)
        .map_err(|error| format!("failed to create log dir {}: {error}", log_dir.display()))?;

    #[cfg(target_os = "macos")]
    let mut command = std::process::Command::new("open");
    #[cfg(target_os = "windows")]
    let mut command = std::process::Command::new("explorer");
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    let mut command = std::process::Command::new("xdg-open");

    command
        .arg(&log_dir)
        .spawn()
        .map_err(|error| format!("failed to open log dir {}: {error}", log_dir.display()))?;
    Ok(())
}
```

- [ ] **Step 2: Register the command**

In `apps/server_desktop/src-tauri/src/lib.rs`, add to the `generate_handler!` list (after `diagnostics::save_app_log,`):
```rust
            diagnostics::open_log_dir,
```

- [ ] **Step 3: Verify it compiles**

Run: `cargo check --manifest-path apps/server_desktop/src-tauri/Cargo.toml`
Expected: compiles with no errors (warnings about unused are acceptable).

- [ ] **Step 4: Commit**

```bash
git add apps/server_desktop/src-tauri/src/diagnostics.rs apps/server_desktop/src-tauri/src/lib.rs
git commit -m "feat(server-console): open_log_dir Tauri command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Rust persistent file logging + redaction + 7-day prune

**Files:**
- Modify: `apps/server_desktop/src-tauri/Cargo.toml`
- Modify: `apps/server_desktop/src-tauri/src/server_process.rs` (`emit_backend_log` ~645-659; start path ~230-240; `tests` mod ~661-682)

**Interfaces:**
- Consumes: `emit_backend_log(app, level, source, message)` (existing choke point).
- Produces (module-private, unit-tested):
  - `redact(text: &str) -> String`
  - `log_date_string(epoch_secs: i64) -> String` → `"YYYY-MM-DD"`
  - `log_timestamp_string(epoch_secs: i64) -> String` → `"YYYY-MM-DD HH:MM:SS"`
  - `prune_old_logs(log_dir: &std::path::Path, max_age: std::time::Duration)`

- [ ] **Step 1: Ensure `regex` is a dependency**

Check `apps/server_desktop/src-tauri/Cargo.toml` `[dependencies]`. If `regex` is absent, add:
```toml
regex = "1"
```
Run: `cargo fetch --manifest-path apps/server_desktop/src-tauri/Cargo.toml`

- [ ] **Step 2: Write the failing tests**

In `apps/server_desktop/src-tauri/src/server_process.rs`, add inside the existing `#[cfg(test)] mod tests { ... }` block (after `port_probe_detects_occupied_and_free`):
```rust
    #[test]
    fn redact_masks_bearer_and_kv_secrets() {
        assert_eq!(
            redact("Authorization: Bearer abc.DEF-123_x"),
            "Authorization: Bearer <redacted>"
        );
        assert_eq!(redact("api_key=SUPERSECRET"), "api_key=<redacted>");
        assert_eq!(redact("password: hunter2"), "password: <redacted>");
        assert_eq!(redact("nothing to hide here"), "nothing to hide here");
    }

    #[test]
    fn log_date_and_timestamp_strings() {
        assert_eq!(log_date_string(0), "1970-01-01");
        // 1_700_000_000 = 2023-11-14 22:13:20 UTC
        assert_eq!(log_date_string(1_700_000_000), "2023-11-14");
        assert_eq!(log_timestamp_string(1_700_000_000), "2023-11-14 22:13:20");
    }

    #[test]
    fn prune_old_logs_removes_only_aged_files() {
        use std::time::Duration;
        let dir = std::env::temp_dir().join(format!("yeson-prune-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        let fresh = dir.join("server-fresh.log");
        let old = dir.join("server-old.log");
        fs::write(&fresh, "x").unwrap();
        fs::write(&old, "x").unwrap();

        // Backdate `old` to 8 days ago.
        let eight_days_ago = SystemTime::now() - Duration::from_secs(8 * 86_400);
        let ft = filetime::FileTime::from_system_time(eight_days_ago);
        filetime::set_file_mtime(&old, ft).unwrap();

        prune_old_logs(&dir, Duration::from_secs(7 * 86_400));

        assert!(fresh.exists(), "fresh log must survive");
        assert!(!old.exists(), "8-day-old log must be pruned");
        let _ = fs::remove_dir_all(&dir);
    }
```
Note: the prune test uses the `filetime` dev-dependency. Add to `apps/server_desktop/src-tauri/Cargo.toml`:
```toml
[dev-dependencies]
filetime = "0.2"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml redact_masks log_date prune_old`
Expected: FAIL — `redact`, `log_date_string`, `log_timestamp_string`, `prune_old_logs` not defined.

- [ ] **Step 4: Implement the helpers**

In `apps/server_desktop/src-tauri/src/server_process.rs`, add near the other free functions (e.g. just above `emit_backend_log`). Ensure these `use` items exist at the top of the file (add any missing):
```rust
use std::io::Write as _;
use std::sync::OnceLock;
use regex::Regex;
```
Then:
```rust
/// Mask Bearer tokens and `key=value` secrets before a log line is emitted to
/// the UI or written to disk. Ports apps/server_desktop/src/appLog.ts `redact`.
fn redact(text: &str) -> String {
    static BEARER: OnceLock<Regex> = OnceLock::new();
    static KV: OnceLock<Regex> = OnceLock::new();
    let bearer = BEARER.get_or_init(|| Regex::new(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+").unwrap());
    let kv = KV.get_or_init(|| {
        Regex::new(r#"(?i)((?:password|token|api[_-]?key|secret)["']?\s*[:=]\s*["']?)[^"'\s,}]+"#)
            .unwrap()
    });
    let masked = bearer.replace_all(text, "$1<redacted>").into_owned();
    kv.replace_all(&masked, "$1<redacted>").into_owned()
}

/// Civil date (UTC) from a Unix epoch seconds value — Howard Hinnant's
/// days-from-civil inverse. Avoids pulling chrono just for a filename stamp.
fn ymd_from_epoch_secs(secs: i64) -> (i64, u32, u32) {
    let days = secs.div_euclid(86_400);
    let z = days + 719_468;
    let era = (if z >= 0 { z } else { z - 146_096 }) / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    (y + if m <= 2 { 1 } else { 0 }, m, d)
}

fn log_date_string(secs: i64) -> String {
    let (y, m, d) = ymd_from_epoch_secs(secs);
    format!("{y:04}-{m:02}-{d:02}")
}

fn log_timestamp_string(secs: i64) -> String {
    let (y, m, d) = ymd_from_epoch_secs(secs);
    let tod = secs.rem_euclid(86_400);
    let (hh, mm, ss) = (tod / 3_600, (tod % 3_600) / 60, tod % 60);
    format!("{y:04}-{m:02}-{d:02} {hh:02}:{mm:02}:{ss:02}")
}

/// Delete dated log files whose mtime is older than `max_age`. Best-effort.
fn prune_old_logs(log_dir: &std::path::Path, max_age: std::time::Duration) {
    let now = SystemTime::now();
    let Ok(entries) = std::fs::read_dir(log_dir) else { return };
    for entry in entries.flatten() {
        let Ok(meta) = entry.metadata() else { continue };
        let Ok(modified) = meta.modified() else { continue };
        if now.duration_since(modified).map(|age| age > max_age).unwrap_or(false) {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// Append one redacted line to <app_data_dir>/logs/server-YYYY-MM-DD.log.
/// Best-effort: any failure is swallowed so logging never blocks the forwarder.
fn append_log_file(app: &tauri::AppHandle, level: &str, source: &str, message: &str) {
    let Ok(app_data_dir) = app.path().app_data_dir() else { return };
    let log_dir = app_data_dir.join("logs");
    if std::fs::create_dir_all(&log_dir).is_err() {
        return;
    }
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let path = log_dir.join(format!("server-{}.log", log_date_string(secs)));
    let line = format!(
        "[{}] {} source={source} message={message}\n",
        log_timestamp_string(secs),
        level.to_uppercase()
    );
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = file.write_all(line.as_bytes());
    }
}
```

- [ ] **Step 5: Redact + write-to-file inside `emit_backend_log`**

Replace the body of `emit_backend_log` (~645-659) with:
```rust
fn emit_backend_log(
    app: &tauri::AppHandle,
    level: &'static str,
    source: &'static str,
    message: impl Into<String>,
) {
    let message = redact(&message.into());
    append_log_file(app, level, source, &message);
    let _ = app.emit(
        "app-log",
        BackendLogEvent {
            level,
            source,
            message,
        },
    );
}
```
(If `BackendLogEvent.message` is typed `&str`/`String`, it already takes `String` from the existing `message.into()` — confirm the struct field is `String`; it is, per `message: message.into()` in the current code.)

- [ ] **Step 6: Prune on server start**

In the start path where `app_data_dir`/`storage_root` are created (~230-238, right after `std::fs::create_dir_all(&storage_root)`), add:
```rust
    let log_dir = app_data_dir.join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    prune_old_logs(&log_dir, std::time::Duration::from_secs(7 * 86_400));
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml`
Expected: PASS — `redact_masks_bearer_and_kv_secrets`, `log_date_and_timestamp_strings`, `prune_old_logs_removes_only_aged_files`, plus the existing `port_probe_detects_occupied_and_free`.

- [ ] **Step 8: Commit**

```bash
git add apps/server_desktop/src-tauri/Cargo.toml apps/server_desktop/src-tauri/Cargo.lock apps/server_desktop/src-tauri/src/server_process.rs
git commit -m "feat(server-console): persistent redacted log file with 7-day retention

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: ServerConsole sidebar + log toolbar (UI)

**Files:**
- Modify: `apps/server_desktop/src/ServerConsole.tsx`

**Interfaces:**
- Consumes: `filterLogEntries`, `saveAppLogSnapshot` from `./appLog`; `open_log_dir` Tauri command.
- Produces: no exported API change (default export `ServerConsole` unchanged).

- [ ] **Step 1: Swap panel booleans for a single active view**

In `ServerConsole.tsx`, replace:
```tsx
  const [showConfig, setShowConfig] = useState(false);
  const [showDevices, setShowDevices] = useState(false);
```
with:
```tsx
  type View = "logs" | "config" | "devices";
  const [activeView, setActiveView] = useState<View>("logs");
  const [logLevel, setLogLevel] = useState<AppLogEntry["level"] | "all">("all");
  const [logQuery, setLogQuery] = useState("");
  const [logWrap, setLogWrap] = useState(true);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
```
Update the `appLog` import to include the helpers:
```tsx
import { type AppLogEntry, append, clearAppLogs, filterLogEntries, installAppLogCapture, saveAppLogSnapshot, subscribeAppLogs } from "./appLog";
```

- [ ] **Step 2: Add export/open handlers**

Add near the other `useCallback`s:
```tsx
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
    try {
      await invoke("open_log_dir");
    } catch (err) {
      setSaveMsg(`open failed: ${errorToText(err)}`);
    }
  }, []);
```

- [ ] **Step 3: Replace the controls' Config/Devices/Clear buttons**

Remove the three buttons (`Clear logs`, `Config`, `Devices`) from `styles.controls` (~210-218). Leave port + Start/Stop in the header controls. (Clear/Export/Open move into the Logs toolbar in Step 5.)

- [ ] **Step 4: Render the sidebar + main, replacing the panelsWrap/logBody return**

Replace the JSX from `{showConfig || showDevices ? (...)}` through the closing of `<main style={styles.logBody}>` with a sidebar + main structure. Replace the outer `return (<div style={styles.shell}>...` wrapper so the shell is a row: sidebar + column. Concretely, wrap the existing `<header>` and the new content in a right-hand column:
```tsx
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
          {/* keep the EXISTING header inner content: title row badge, controls
              (port + Start/Stop only), statusGrid, tunnelRow, TunnelDegradedBanner,
              error/warn lines — unchanged from the current file */}
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
            {saveMsg ? <p style={styles.warn}>{saveMsg}</p> : null}
            <div style={styles.logBody}>
              {visibleLogs.length === 0 ? (
                <p style={styles.empty}>No matching log output.</p>
              ) : (
                visibleLogs.map((entry) => (
                  <div key={entry.id} style={{ ...styles.logLine, color: levelColor(entry.level), whiteSpace: logWrap ? "pre-wrap" : "pre" }}>
                    <span style={styles.logTs}>{entry.ts.slice(11, 19)}</span>
                    <span style={styles.logSource}>{entry.source}</span>
                    <span>{entry.message}</span>
                  </div>
                ))
              )}
              <div ref={logEndRef} />
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
```
Keep the existing `<header>` inner JSX exactly as it is now (title row, controls with port + Start/Stop, statusGrid, tunnelRow, banner, error/warn) — only the Config/Devices/Clear buttons were removed in Step 3.

- [ ] **Step 5: Update styles**

In the `styles` object: remove `panelsWrap`. Change `shell` to a row and add the new keys:
```tsx
  shell: {
    height: "100%",
    display: "flex",
    flexDirection: "row",
    background: "#0e141b",
    color: "#d4dde6",
    fontFamily: "system-ui, -apple-system, sans-serif",
  },
  sidebar: {
    flex: "0 0 180px",
    display: "flex",
    flexDirection: "column",
    gap: 10,
    padding: "16px 12px",
    borderRight: "1px solid #1d2733",
    background: "#0b1117",
  },
  brand: { fontSize: 13, fontWeight: 700, margin: 0, color: "#8ea0b2" },
  nav: { display: "flex", flexDirection: "column", gap: 4, marginTop: 8 },
  navButton: {
    textAlign: "left",
    padding: "8px 10px",
    borderRadius: 6,
    border: "1px solid transparent",
    background: "transparent",
    color: "#b6c2cf",
    cursor: "pointer",
    fontSize: 13,
  },
  navButtonActive: { background: "#1b2530", borderColor: "#25323f", color: "#fff", fontWeight: 600 },
  column: { flex: "1 1 auto", display: "flex", flexDirection: "column", minWidth: 0 },
  content: { flex: "1 1 auto", minHeight: 0, display: "flex", flexDirection: "column" },
  viewFill: { flex: "1 1 auto", minHeight: 0, display: "flex", flexDirection: "column" },
  viewScroll: { flex: "1 1 auto", minHeight: 0, overflowY: "auto", padding: "12px 20px" },
  logToolbar: { display: "flex", alignItems: "center", gap: 8, padding: "10px 20px", borderBottom: "1px solid #1d2733" },
  select: { padding: "5px 8px", background: "#0b1117", border: "1px solid #25323f", borderRadius: 6, color: "#d4dde6", fontSize: 12 },
  search: { flex: "0 1 240px", padding: "5px 8px", background: "#0b1117", border: "1px solid #25323f", borderRadius: 6, color: "#d4dde6", fontSize: 12 },
  wrapLabel: { display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "#8ea0b2" },
  toolbarSpacer: { flex: "1 1 auto" },
```
Keep the existing `logBody`, `empty`, `logLine`, `logTs`, `logSource`, `button`, `warn`, etc. (logBody stays `flex:1 1 auto; minHeight:0; overflowY:auto; padding 12px 20px; …`).

- [ ] **Step 6: Type-check / build**

Run: `cd apps/server_desktop && pnpm build:vite`
Expected: `tsc --noEmit` passes and vite build succeeds (no unused `showConfig`/`useMemo` leftovers — remove the now-unused `liveStatus` `useMemo` only if it becomes unused; it is still used by the status grid, so keep it).

- [ ] **Step 7: Commit**

```bash
git add apps/server_desktop/src/ServerConsole.tsx
git commit -m "feat(server-console): sidebar nav + filterable log toolbar

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Manual end-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Run the console**

Run: `cd apps/server_desktop && pnpm tauri:dev`

- [ ] **Step 2: Layout**
  - Click `Config` and `Devices` in the sidebar — each fills the full content area, no cramped 55% strip.
  - The top header (port, Start/Stop, status, Go live, tunnel) stays visible in all views.

- [ ] **Step 3: Log viewer**
  - Press `Start`; logs stream under the `Logs` view.
  - Level filter `Error` shows only error lines; `All` restores.
  - Type a substring in search — list narrows; clearing restores.
  - Toggle `wrap` — long lines wrap / stop wrapping.

- [ ] **Step 4: Log saving**
  - `Export` → `saveMsg` shows a `saved: <path>` to a `yeson-server-log-<ms>.txt`.
  - `Open folder` → the OS file manager opens `<app_data_dir>/logs`.
  - Confirm a `server-YYYY-MM-DD.log` exists and is growing while the server runs.
  - Quit and relaunch the app — the `server-*.log` from before is still present (persistent).
  - Open the `server-*.log`: confirm any secret-looking values are `<redacted>`.

- [ ] **Step 5: Update memory note**

Update the project memory note `project_win_server_console_bugs` (or add a new `project_server_console_ux` note) to record that the cramped-panels issue is resolved via sidebar nav and that the server now has persistent + exportable logging. (Per the user's auto-memory workflow.)

---

## Self-Review

- **Spec coverage:**
  - Sidebar layout / no cramping → Task 4 (+ styles). ✓
  - Log viewer filter/search/wrap/auto-scroll → Task 4 (auto-scroll: existing `logEndRef` effect retained). ✓
  - Persistent disk logging, dated, redacted, 7-day retention → Task 3. ✓
  - Export button + open folder → Task 1 (`saveAppLogSnapshot`) + Task 2 (`open_log_dir`) + Task 4 (buttons). ✓
  - Redaction once in Rust → Task 3 Step 5. ✓
  - Controls stay in main top header → Task 4 Steps 3-4. ✓
- **Placeholder scan:** code shown for every code step; manual-only steps (Task 5) are verification, not code. ✓
- **Type consistency:** `filterLogEntries`, `formatAppLogEntry`, `formatAppLogSnapshot`, `saveAppLogSnapshot` signatures identical across Task 1 (definition) and Task 4 (use); `open_log_dir` name identical across Task 2 (def/register) and Task 4 (invoke). ✓
