# Server Console UX Improvements — Design

Date: 2026-06-23
App: `apps/server_desktop` (yeson server console, Tauri)
Status: approved (brainstorming) — pending implementation plan

## Problem

The server console (`apps/server_desktop/src/ServerConsole.tsx`) has three UX issues
surfaced after Windows server+client E2E testing:

1. **Cramped panels.** Config and Devices share one combined region capped at 55%
   height (`styles.panelsWrap`). Opening both stacks them vertically inside that
   capped, scrollable strip, so each is too short to use comfortably.
2. **Log viewing is inconvenient.** The log body is a plain scrolling `<div>` with
   no level filter, no text search, no line-wrap control.
3. **No log saving.** The client app can export its in-app log
   (`apps/desktop/src/diagnostics/appLog.ts` → `saveAppLogSnapshot` → Rust
   `save_app_log`), but the server console has only "Clear logs". Server logs are
   lost when the window's 1000-line buffer overflows or the app closes.

## Goals

- Visual consistency with the client app's left-sidebar navigation.
- Config and Devices each get the full content area (no cramping).
- A more usable log viewer (filter, search, wrap).
- Persistent server log storage on disk plus on-demand export — the client has a
  log feature; the server should too, and stronger (full retention, not capped).

## Non-Goals

- No change to server start/stop/tunnel logic or the bundled FastAPI server.
- No code signing / installer changes (tracked separately).
- No log streaming to a remote service.

## Current State (reference)

- `ServerConsole.tsx`: fixed header (title, controls, status grid, tunnel row,
  banner) → `panelsWrap` (Config + Devices, `flex 0 1 auto; maxHeight 55%;
  overflowY auto`) → `logBody` (`flex 1 1 auto; overflowY auto`).
- `appLog.ts`: consumes the `app-log` Tauri event (`{level, source, message}`)
  emitted per server stdout/stderr line by `server_process.rs` →
  `spawn_output_forwarder` → `emit_backend_log`. In-memory, capped at
  `MAX_LOG_ENTRIES = 1000`. Has `append`, `clearAppLogs`, `subscribeAppLogs`,
  `redact`. **No save/format functions.**
- Client reference: `apps/desktop/src/diagnostics/appLog.ts` has
  `saveAppLogSnapshot` / `formatAppLogSnapshot` / `formatAppLogEntry`; the client
  Rust side exposes a `save_app_log` command. `apps/desktop/src/console/ConsoleNav.tsx`
  + `DesktopConsole.tsx` define the left-sidebar pattern
  (`aside` brand + nav buttons; `main` with `hidden`-toggled sections).

## Design

### 1. Layout — left sidebar (match the client)

Adopt the client's sidebar pattern in the server console:

```
┌────────────┬───────────────────────────────┐
│ yeson      │  port[8000] Start  Go live      │  main top: global controls
│ server     │  status pid uptime  [PUBLIC]     │  (server-only; always visible)
│ console    │  tunnel url / degraded banner    │
│ [RUNNING]  ├───────────────────────────────┤
│ ▸ Logs     │                               │
│   Config   │   active view fills the rest    │  one view at a time,
│   Devices  │   (Logs / Config / Devices)     │  full height
└────────────┴───────────────────────────────┘
```

- New `aside` sidebar: brand text + RUNNING/STOPPED badge + nav buttons
  `Logs · Config · Devices`. Style mirrors the client `consoleStyles`
  sidebar/nav/navButton(+active) look, themed to the server's dark palette
  (`#0e141b`/`#11181f`) for in-app consistency.
- Replace `showConfig`/`showDevices` booleans with a single
  `activeView: "logs" | "config" | "devices"` (default `"logs"`).
- The global control header (port, Start/Stop, Go live, status grid, tunnel row,
  degraded banner, error/warn lines) **stays at the top of `main`** — these are
  server-only and must be visible regardless of the active view. (Confirmed with
  user: keep in main top, not moved into the sidebar.)
- Remove `panelsWrap` and its 55% cap. Config and Devices render in the content
  area with their own scroll, full height.
- Each view rendered with `hidden` toggling like the client
  (`hidden={activeView !== "config"}`), so component state (e.g. DevicePanel
  fetches) is preserved across switches.

### 2. Log viewer (Logs view)

A toolbar above the log lines:

```
[level: All ▾] [🔍 search…] [wrap ☐]        [Clear] [Export] [Open folder]
```

- **Level filter**: `all | info | warn | error | debug`; default `all`.
- **Search**: case-insensitive substring match over `source` + `message`.
- **Wrap toggle**: switches `whiteSpace` between `pre-wrap` (wrap) and `pre`
  (single-line, horizontal scroll); default wrap on (current behavior).
- **Auto-scroll**: keep scrolling to newest; pause auto-scroll when the user has
  scrolled up from the bottom, resume when they return to bottom.
- Filtering is view-only (does not drop entries from the store); the unfiltered
  store is still what gets exported/persisted.
- Buttons: `Clear` (existing `clearAppLogs`), `Export` (snapshot to file),
  `Open folder` (reveal the on-disk log directory).

### 3. Log saving — auto file logging + export (both)

**Persistent file logging (Rust).** The authoritative full log is written on the
Rust side, because the frontend store is capped at 1000 lines and lost on close.

- At the `emit_backend_log` call site (`server_process.rs`) — the single point
  every forwarded server line and console event passes through — also append the
  line to a dated file: `<app_data_dir>/logs/server-YYYY-MM-DD.log`.
- Line format mirrors the export format:
  `[<ISO ts>] <LEVEL> source=<source> message=<message>`.
- Redaction is done **once in Rust at `emit_backend_log`** (port the frontend
  `redact()` regex to Rust), so both the `app-log` event payload and the file
  line are clean. The existing frontend `redact()` stays as a defense-in-depth
  net. On-disk logs never contain Bearer tokens / api keys / passwords.
- **Retention: 7 days.** On server start, prune `logs/server-*.log` files whose
  date is older than 7 days.

**Tauri commands.**
- `save_app_log(contents: String) -> { path }` — mirror the client command:
  write the provided snapshot text to a user-visible file
  (`yeson-server-log-<ts>.txt`) and return its path.
- `open_log_dir() -> ()` — reveal `<app_data_dir>/logs` in Finder/Explorer.

**Frontend (`apps/server_desktop/src/appLog.ts`).**
- Add `formatAppLogEntry`, `formatAppLogSnapshot`, `saveAppLogSnapshot`
  mirroring the client (invoke `save_app_log` in Tauri; browser fallback
  downloads a blob).
- `Export` button calls `saveAppLogSnapshot(currentEntries)`; `Open folder`
  calls `open_log_dir`.

## Components / Files

- `apps/server_desktop/src/ServerConsole.tsx` — sidebar + `activeView` + Logs
  toolbar; remove `panelsWrap`. (May extract `ConsoleNav` and `LogView` into
  small sibling files if `ServerConsole.tsx` grows past a comfortable size —
  it is already 410 lines.)
- `apps/server_desktop/src/appLog.ts` — add format/save helpers.
- `apps/server_desktop/src-tauri/src/server_process.rs` — file sink at
  `emit_backend_log`; startup prune.
- `apps/server_desktop/src-tauri/src/diagnostics.rs` (or `lib.rs`) — `save_app_log`,
  `open_log_dir` commands; register in the Tauri builder.

## Data Flow

```
bundled server stdout/stderr ─► spawn_output_forwarder ─► emit_backend_log ──┬─► Tauri "app-log" event ─► appLog.ts store (≤1000) ─► Logs view (filter/search)
                                                                             └─► append to logs/server-YYYY-MM-DD.log (full, redacted, 7-day retention)

Export button ─► saveAppLogSnapshot(store) ─► invoke save_app_log ─► yeson-server-log-<ts>.txt
Open folder   ─► invoke open_log_dir ─► reveal logs/
```

## Error Handling

- File-sink write failures must never crash or block the server forwarder —
  best-effort; on error, emit one warn-level `app-log` line and continue.
- `save_app_log` / `open_log_dir` failures surface as the existing console error
  line (same pattern as start/stop).
- Browser preview (no Tauri runtime): Export uses the blob-download fallback;
  Open folder is disabled with a hint (mirrors existing `hasTauriRuntime` gating).

## Testing

- **Frontend unit (vitest)**: `formatAppLogEntry`/`formatAppLogSnapshot` output
  shape; level-filter + search filtering logic (extract pure helpers); redaction
  preserved. Mirror the client's appLog tests where applicable.
- **Rust**: redaction helper unit test; retention prune selects only >7-day-old
  files (table-driven by filename date).
- **Manual (Windows + macOS)**: open Config and Devices — each fills the area, no
  cramping; level filter + search narrow the log; Export writes a file and
  returns a path; Open folder reveals the dated log; confirm a `server-*.log`
  grows while the server runs and survives an app restart; confirm redaction in
  the on-disk file.

## Open defaults (decided)

- Level filter default: `All`.
- Export filename: `yeson-server-log-<ts>.txt` (client parity).
- Retention: 7 days.
- Start/Stop + Go live: remain in main top header (not in sidebar).
