# Backup Restore — Design Spec

Date: 2026-06-29
Status: Approved (design phase)
Related: backup feature (`apps/server/domain/backup.py`, `apps/server/api/v1/backup.py`, console Backup tab)

## Problem

The server can produce verified backups (a `VACUUM INTO` SQLite snapshot + a
`storage-*.zip` of the reports/exports tree) to operator-chosen destinations,
but there is **no way to restore one**. A backup with no restore path is
half a feature: migrating to a new server PC, rolling back after DB
corruption / operator error, and standing up a rehearsal/replica environment
all require restoring a backup. This spec adds restore.

## Scope

In scope (all three restore scenarios share one mechanism — full whole-replace):

- **Migration** — load a past backup into a fresh install on a new PC/server.
- **Rollback** — overwrite the current (same-server) state with an earlier backup.
- **Rehearsal / replica** — copy production data into a test/secondary server.

Out of scope (YAGNI):

- Selective restore (a single meeting / date range merged into the live DB).
- Scheduled / automatic restore.
- Anything beyond full whole-replace of `{db file}` + `{storage tree}`.

## Key facts that shape the design

- The live DB is `{app_data}/yeson-meet.db`; storage is `{app_data}/storage`.
  Both paths are set by the console in `server_process.rs` via `DATABASE_URL`
  / `STORAGE_ROOT` env.
- The server holds the DB open in **WAL mode** while running, so a file swap
  must happen with the server **stopped**. The console already performs a
  graceful stop that waits for lifespan teardown + aiosqlite to release the
  connection (`server_process.rs`).
- **The frozen bundle does not run Alembic.** On boot `server_entry.py` calls
  `create_schema()` (`Base.metadata.create_all`, idempotent) and the server
  runs manual in-place backfills (e.g. `db/search.py` FTS backfill on upgrade).
  So an older DB file dropped in place and restarted is already a supported
  path — restore reuses exactly that reconciliation. (Note: `create_all` adds
  missing *tables* but not missing *columns*; existing in-place-upgrade
  behavior is the compatibility contract restore inherits.)
- `server_entry.py` currently boots straight to uvicorn; it has no subcommand
  dispatch yet (integration point below).

## Approach

**Console (Tauri/Rust) orchestration.** The console stops the bundled server,
runs the restore with the DB released, then restarts. The risky SQLite/zip
logic lives in a pure **Python restore engine** (symmetric with `backup.py`,
unit-testable with pytest); the console invokes it as a **one-shot CLI** while
the server is down. Rejected alternative: an online `POST /backup/restore`
endpoint — unsafe against the live WAL connection + async pool, can't swap the
storage tree under a running server, and doesn't serve fresh-PC migration.

## Components

### 1. Python restore engine — `apps/server/domain/restore.py`

Pure functions over file paths (no running server required). Mirrors
`backup.py` structure and `BackupError` style.

- `inspect_backup(snapshot_path) -> BackupInfo`
  - Opens the snapshot read-only; runs `PRAGMA integrity_check`.
  - Reads the sidecar **manifest** (below) if present: app version + schema
    fingerprint. Falls back to "unknown version" when absent.
  - Returns a preview: stamp, integrity_ok, app_version|None, session count,
    utterance count, snapshot bytes, whether a matching `storage-*.zip` exists.
- `validate_restore(info, current_app_version) -> RestoreValidation`
  - Blocks when the snapshot's app version is **newer** than the running
    server (downgrade is unsafe — newer schema into older code). Allows
    same-or-older. When version is unknown (no manifest), returns a
    `warn` (proceed allowed) rather than a hard block.
- `perform_restore(*, snapshot_path, storage_zip_path|None, db_path, storage_root, safety_dir, stamp) -> RestoreResult`
  1. **Safety backup**: snapshot current `db_path` + `storage_root` into
     `safety_dir` (reuse `backup.create_backup`) so the restore is reversible.
  2. **DB swap**: copy snapshot to `db_path.tmp`, fsync, then atomic
     `os.replace` over `db_path`; **delete `db_path-wal` and `db_path-shm`**
     (stale WAL would corrupt the new file).
  3. **Storage swap**: when a zip is given, replace the storage tree
     (extract into a temp dir, then swap directories) so a partial extract
     never leaves a half-populated tree.
  4. Re-run `PRAGMA integrity_check` on the now-live DB; raise on failure.

### 2. One-shot CLI entry

Add a subcommand branch at the top of `server_entry.py` (before the uvicorn
boot path): when invoked as `… restore --snapshot P --db-path D --storage-root S [--storage-zip Z] [--safety-dir F]`,
it calls the engine and exits with 0 / non-zero + a JSON result on stdout.
Keeps all SQLite/zip work in Python, runnable with the server stopped.

### 3. Console (Rust) — `apps/server_desktop/src-tauri/src/restore.rs`

- `inspect_backup(path)` command — read-only preview while the server is up.
  Invokes the one-shot CLI in `inspect` mode (read-only; opens the chosen
  snapshot, never the live DB) and returns the `BackupInfo` JSON to the UI.
- `restore_backup(snapshot_path, storage_zip_path)` command:
  1. Stop the server (existing graceful stop).
  2. Invoke the one-shot CLI `restore …` with the live `db_path` /
     `storage_root` (the same values `server_process.rs` already computes).
  3. Start the server; poll `/api/v1/health` until ok.
  4. On start failure, auto-rollback from the safety backup and surface the
     error.

### 4. Console UI — Backup tab, new "복원" section

Folder picker (reuse `rfd`, as backup does) → list stamped backups (pair
`yeson-meet-*.db` with its `storage-*.zip`) → select one → show preview
(date, #sessions, app version, integrity ok/▲) → confirm. Overwriting an
existing non-empty DB requires a typed confirmation. Progress + result
(restarted / rolled-back).

### 5. Backup manifest (small addition to the existing backup engine)

At backup time, write `yeson-meet-{stamp}.json` next to the snapshot:
`{ "app_version": "0.9.x", "schema": "<fingerprint>", "stamp": "...", "created_at": "..." }`.
Restore reads it for the version-downgrade guard. Pre-existing backups without
a manifest restore on integrity-only with a warning.

## Data flow

```
pick backup → inspect (read-only) → validate version
  → confirm (typed for overwrite)
  → [server STOP]
  → safety-backup current db+storage → swap db (+ delete -wal/-shm) → swap storage
  → [server START] → health + integrity check
  → report (restored / rolled-back)
```

## Error handling

- Snapshot fails `integrity_check` → abort before touching live state.
- Snapshot app version newer than server → block with a clear message.
- Server fails to restart after swap → auto-rollback from safety backup,
  surface the error.
- `storage-*.zip` missing → restore DB only, warn (reports/exports tree
  left as-is).
- Destination/permission errors → reported, live state untouched (the swap is
  the only mutating step and is atomic).

## Testing

- **pytest** (`apps/server/tests/test_restore.py`, `test_restore` style mirrors
  `test_backup.py`): integrity detection; `-wal`/`-shm` removal on swap;
  version-downgrade block; safety-backup creation; storage extraction;
  idempotency / re-run; manifest present vs absent.
- **Rust**: unit/integration for the orchestration command where feasible
  (stop → swap → start sequencing, rollback-on-failure).
- **E2E (manual, tauri:dev)**: back up, mutate/clear, restore, verify the
  meeting record + reports return; verify rollback when restart is forced to
  fail.

## Integration points / open items for the plan

- Confirm `server_entry.py` subcommand dispatch shape (argv parsing before the
  uvicorn boot path).
- Confirm the `current_app_version` source available to the engine/CLI
  (tauri.conf version vs a runtime constant).
- Decide the schema "fingerprint" content (e.g. sorted table+column list hash)
  — minimal, only for the downgrade guard.
