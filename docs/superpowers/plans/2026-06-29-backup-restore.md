# Backup Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a restore counterpart to the existing backup feature so an operator can load a chosen backup (SQLite snapshot + storage zip) back into the server — for migration, rollback, or building a replica.

**Architecture:** A pure Python restore engine (`apps/server/domain/restore.py`, symmetric with `backup.py`) does inspect/validate/swap over file paths with the server stopped. The frozen bundle exposes it as env-gated one-shot modes in `server_entry.py` (mirroring the existing `YESON_BOOTSTRAP_ADMIN` / `YESON_REPORT_SELFTEST` modes). The console (Rust `restore.rs`) orchestrates: stop server → run the one-shot → start server → health-check, with auto-rollback from a safety backup. The console UI adds a "복원" section to the existing Backup tab.

**Tech Stack:** Python stdlib (`sqlite3`, `zipfile`, `json`, `shutil`, `os`), pytest; Rust/Tauri (`std::process::Command`, `rfd`); React/TypeScript.

## Global Constraints

- Zero new runtime dependencies — Python engine is stdlib only (matches `backup.py`).
- Live DB path: `{app_data}/yeson-meet.db`; storage: `{app_data}/storage` (set by `server_process.rs`).
- The DB swap MUST delete the `-wal` and `-shm` sidecars (stale WAL corrupts the new file).
- Restore only runs with the server STOPPED (console enforces stop→swap→start ordering).
- Full whole-replace only. No selective restore, no scheduled restore.
- Operator role gates any UI entry (console is operator-only already).
- Backup filenames: snapshot `yeson-meet-{stamp}.db`, storage `storage-{stamp}.zip`, manifest `yeson-meet-{stamp}.json`, stamp format `%Y%m%d-%H%M%S`.

## File Structure

- Create `apps/server/domain/restore.py` — restore engine (inspect/validate/perform + dataclasses).
- Create `apps/server/tests/test_restore.py` — engine unit tests (mirrors `test_backup.py`).
- Modify `apps/server/domain/backup.py` — write a `{stamp}.json` manifest in `create_backup`.
- Modify `apps/server/tests/test_backup.py` — assert manifest is written.
- Modify `apps/server_desktop/sidecar/server_entry.py` — add `_restore_mode()` + `_inspect_backup_mode()` env-gated one-shots + dispatch.
- Create `apps/server_desktop/src-tauri/src/restore.rs` — `inspect_backup` + `restore_backup` Tauri commands.
- Modify `apps/server_desktop/src-tauri/src/lib.rs` — `mod restore;` + register the two commands.
- Modify the console Backup panel UI (the React component that calls `pick_backup_dir`) — add the "복원" section.

---

### Task 1: Backup manifest (version guard prerequisite)

**Files:**
- Modify: `apps/server/domain/backup.py` (the `create_backup` function, `BackupResult` dataclass)
- Test: `apps/server/tests/test_backup.py`

**Interfaces:**
- Consumes: existing `create_backup(*, database_url, storage_root, dest_dir, stamp) -> BackupResult`.
- Produces: `create_backup` also writes `dest/yeson-meet-{stamp}.json` containing
  `{"stamp": str, "app_version": str|None, "schema": str, "snapshot_bytes": int}`.
  New module-level helper `schema_fingerprint(snapshot_path: Path) -> str` (sorted `table(col,col,...)` list, sha256 hex, first 16 chars). `BackupResult` gains `manifest_path: Path`.
  App version source: env `YESON_APP_VERSION` (Rust injects it; `None` when unset).

- [ ] **Step 1: Write the failing test**

```python
# in apps/server/tests/test_backup.py
import json

def test_create_backup_writes_manifest(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db, rows=2)
    dest = tmp_path / "dest"; dest.mkdir()
    result = create_backup(
        database_url=f"sqlite+aiosqlite:///{db}",
        storage_root=tmp_path / "storage",
        dest_dir=dest,
        stamp="20260629-1200",
    )
    manifest = dest / "yeson-meet-20260629-1200.json"
    assert manifest.is_file()
    assert result.manifest_path == manifest
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["stamp"] == "20260629-1200"
    assert isinstance(data["schema"], str) and len(data["schema"]) == 16
    assert "app_version" in data  # may be None when YESON_APP_VERSION unset
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project apps/server pytest apps/server/tests/test_backup.py::test_create_backup_writes_manifest -v`
Expected: FAIL (`manifest_path` attribute / file missing).

- [ ] **Step 3: Write minimal implementation**

In `apps/server/domain/backup.py` add imports `import json`, `import os`, `import hashlib`, then:

```python
def schema_fingerprint(snapshot_path: Path) -> str:
    """Stable 16-hex fingerprint of the DB schema (sorted table+column names)."""
    conn = sqlite3.connect(str(snapshot_path))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        parts = []
        for t in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")]
            parts.append(f"{t}({','.join(sorted(cols))})")
    finally:
        conn.close()
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
```

Add `manifest_path: Path` to `BackupResult`. In `create_backup`, after the integrity check passes and before/after `_archive_storage`, write the manifest and pass it through:

```python
    manifest_path = dest / f"yeson-meet-{stamp}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "stamp": stamp,
                "app_version": os.environ.get("YESON_APP_VERSION") or None,
                "schema": schema_fingerprint(snapshot),
                "snapshot_bytes": snapshot.stat().st_size,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
```

Add `manifest_path=manifest_path` to the `BackupResult(...)` return. In `backup_to_destinations`, copy the manifest into each destination alongside the snapshot/zip (mirror the `shutil.copy2(staged.snapshot_path, ...)` lines: `shutil.copy2(staged.manifest_path, dest / staged.manifest_path.name)`), and add `"yeson-meet-*.json"` to the `_prune` patterns tuple.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --project apps/server pytest apps/server/tests/test_backup.py -v`
Expected: PASS (all existing + new).

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/backup.py apps/server/tests/test_backup.py
git commit -m "feat(server): write version+schema manifest alongside each backup"
```

---

### Task 2: Restore engine — `apps/server/domain/restore.py`

**Files:**
- Create: `apps/server/domain/restore.py`
- Test: `apps/server/tests/test_restore.py`

**Interfaces:**
- Consumes: `backup.create_backup`, `backup.schema_fingerprint`, `backup.BackupError`.
- Produces:
  - `RestoreError(RuntimeError)`
  - `@dataclass BackupInfo`: `snapshot_path: Path, stamp: str, integrity_ok: bool, app_version: str|None, schema: str|None, session_count: int, utterance_count: int, snapshot_bytes: int, storage_zip_path: Path|None`
  - `inspect_backup(snapshot_path: Path) -> BackupInfo`
  - `@dataclass RestoreValidation`: `ok: bool, level: str ("ok"|"warn"|"block"), reason: str`
  - `validate_restore(info: BackupInfo, current_version: str|None) -> RestoreValidation`
  - `@dataclass RestoreResult`: `db_path: Path, restored_bytes: int, integrity_ok: bool, storage_restored: bool, safety_dir: Path`
  - `perform_restore(*, snapshot_path: Path, storage_zip_path: Path|None, db_path: Path, storage_root: Path, safety_dir: Path, stamp: str) -> RestoreResult`

- [ ] **Step 1: Write the failing tests**

```python
# apps/server/tests/test_restore.py
import sqlite3, zipfile
from pathlib import Path
import pytest
from apps.server.domain.restore import (
    RestoreError, inspect_backup, validate_restore, perform_restore,
)

def _db(path: Path, rows: int) -> None:
    c = sqlite3.connect(str(path))
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE session (id INTEGER PRIMARY KEY)")
        c.execute("CREATE TABLE utterance (id INTEGER PRIMARY KEY, text_ko TEXT)")
        c.executemany("INSERT INTO utterance (text_ko) VALUES (?)", [(f"발화{i}",) for i in range(rows)])
        c.commit()
    finally:
        c.close()

def test_inspect_backup_reports_counts_and_integrity(tmp_path: Path) -> None:
    snap = tmp_path / "yeson-meet-20260629-1200.db"
    _db(snap, rows=4)
    info = inspect_backup(snap)
    assert info.integrity_ok is True
    assert info.stamp == "20260629-1200"
    assert info.utterance_count == 4

def test_inspect_backup_corrupt_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "yeson-meet-x.db"
    snap.write_bytes(b"not a database")
    with pytest.raises(RestoreError):
        inspect_backup(snap)

def test_validate_blocks_newer_version(tmp_path: Path) -> None:
    snap = tmp_path / "yeson-meet-20260629-1200.db"; _db(snap, 1)
    info = inspect_backup(snap)
    object.__setattr__(info, "app_version", "0.9.20")
    v = validate_restore(info, current_version="0.9.12")
    assert v.level == "block"

def test_validate_warns_unknown_version(tmp_path: Path) -> None:
    snap = tmp_path / "yeson-meet-20260629-1200.db"; _db(snap, 1)
    info = inspect_backup(snap)  # no manifest → app_version None
    v = validate_restore(info, current_version="0.9.12")
    assert v.level == "warn" and v.ok is True

def test_perform_restore_swaps_db_and_clears_wal(tmp_path: Path) -> None:
    live = tmp_path / "yeson-meet.db"; _db(live, rows=1)
    # leave a stale WAL sidecar that must be removed
    (tmp_path / "yeson-meet.db-wal").write_bytes(b"stale")
    snap = tmp_path / "yeson-meet-20260629-1200.db"; _db(snap, rows=7)
    storage = tmp_path / "storage"; storage.mkdir()
    (storage / "old.txt").write_text("old")
    zip_path = tmp_path / "storage-20260629-1200.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("new/report.md", "# new")
    safety = tmp_path / "safety"; safety.mkdir()
    result = perform_restore(
        snapshot_path=snap, storage_zip_path=zip_path, db_path=live,
        storage_root=storage, safety_dir=safety, stamp="20260629-1200",
    )
    assert result.integrity_ok is True
    assert not (tmp_path / "yeson-meet.db-wal").exists()
    c = sqlite3.connect(str(live))
    try:
        assert c.execute("SELECT COUNT(*) FROM utterance").fetchone()[0] == 7
    finally:
        c.close()
    assert (storage / "new" / "report.md").is_file()
    assert not (storage / "old.txt").exists()  # storage tree replaced
    assert list(safety.glob("yeson-meet-*.db"))  # safety backup created
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --project apps/server pytest apps/server/tests/test_restore.py -v`
Expected: FAIL (module `apps.server.domain.restore` does not exist).

- [ ] **Step 3: Write the implementation**

```python
# apps/server/domain/restore.py
"""Backup restore engine (inverse of backup.py). Pure stdlib; runs with the
server STOPPED so the SQLite file can be swapped safely."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from apps.server.domain.backup import BackupError, create_backup, schema_fingerprint


class RestoreError(RuntimeError):
    """Raised when a backup cannot be inspected or restored."""


@dataclass(frozen=True)
class BackupInfo:
    snapshot_path: Path
    stamp: str
    integrity_ok: bool
    app_version: str | None
    schema: str | None
    session_count: int
    utterance_count: int
    snapshot_bytes: int
    storage_zip_path: Path | None


def _stamp_from_name(path: Path) -> str:
    # yeson-meet-20260629-1200.db → 20260629-1200
    name = path.stem
    return name[len("yeson-meet-"):] if name.startswith("yeson-meet-") else name


def inspect_backup(snapshot_path: Path) -> BackupInfo:
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.is_file():
        raise RestoreError(f"snapshot not found: {snapshot_path}")
    conn = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        integrity_ok = conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        if not integrity_ok:
            raise RestoreError(f"snapshot failed integrity check: {snapshot_path}")

        def _count(table: str) -> int:
            try:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                return 0

        sessions = _count("session")
        utterances = _count("utterance")
        schema = schema_fingerprint(snapshot_path)
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"not a valid SQLite snapshot: {snapshot_path} ({exc})") from exc
    finally:
        conn.close()

    stamp = _stamp_from_name(snapshot_path)
    import json
    manifest = snapshot_path.with_suffix(".json")
    app_version: str | None = None
    if manifest.is_file():
        try:
            app_version = json.loads(manifest.read_text(encoding="utf-8")).get("app_version")
        except (ValueError, OSError):
            app_version = None
    zip_candidate = snapshot_path.parent / f"storage-{stamp}.zip"
    return BackupInfo(
        snapshot_path=snapshot_path,
        stamp=stamp,
        integrity_ok=True,
        app_version=app_version,
        schema=schema,
        session_count=sessions,
        utterance_count=utterances,
        snapshot_bytes=snapshot_path.stat().st_size,
        storage_zip_path=zip_candidate if zip_candidate.is_file() else None,
    )


@dataclass(frozen=True)
class RestoreValidation:
    ok: bool
    level: str  # "ok" | "warn" | "block"
    reason: str


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.strip().lstrip("v").split(".") if p.isdigit())


def validate_restore(info: BackupInfo, current_version: str | None) -> RestoreValidation:
    if info.app_version is None or current_version is None:
        return RestoreValidation(True, "warn", "backup version unknown — proceed with care")
    try:
        if _ver_tuple(info.app_version) > _ver_tuple(current_version):
            return RestoreValidation(
                False, "block",
                f"backup is from a newer version ({info.app_version}) than this server "
                f"({current_version}); downgrade is not supported",
            )
    except ValueError:
        return RestoreValidation(True, "warn", "version compare failed — proceed with care")
    return RestoreValidation(True, "ok", "")


@dataclass(frozen=True)
class RestoreResult:
    db_path: Path
    restored_bytes: int
    integrity_ok: bool
    storage_restored: bool
    safety_dir: Path


def perform_restore(
    *,
    snapshot_path: Path,
    storage_zip_path: Path | None,
    db_path: Path,
    storage_root: Path,
    safety_dir: Path,
    stamp: str,
) -> RestoreResult:
    snapshot_path = Path(snapshot_path)
    db_path = Path(db_path)
    storage_root = Path(storage_root)
    safety_dir = Path(safety_dir)
    if not snapshot_path.is_file():
        raise RestoreError(f"snapshot not found: {snapshot_path}")

    # 1. Safety backup of the CURRENT state (reversible restore). Only when a
    #    live DB exists (fresh-install migration has none).
    safety_dir.mkdir(parents=True, exist_ok=True)
    if db_path.is_file():
        try:
            create_backup(
                database_url=f"sqlite+aiosqlite:///{db_path}",
                storage_root=storage_root,
                dest_dir=safety_dir,
                stamp=f"pre-restore-{stamp}",
            )
        except BackupError as exc:
            raise RestoreError(f"safety backup failed, aborting restore: {exc}") from exc

    # 2. Swap the DB atomically; remove stale WAL/SHM sidecars.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = db_path.with_name(db_path.name + ".restore-tmp")
    shutil.copy2(snapshot_path, tmp_db)
    import os
    os.replace(tmp_db, db_path)
    for sidecar in (db_path.name + "-wal", db_path.name + "-shm"):
        (db_path.parent / sidecar).unlink(missing_ok=True)

    # 3. Replace the storage tree from the zip (swap dirs so a partial extract
    #    never leaves a half-populated tree).
    storage_restored = False
    if storage_zip_path is not None and Path(storage_zip_path).is_file():
        staging = Path(tempfile.mkdtemp(prefix="yeson-restore-storage-"))
        try:
            with zipfile.ZipFile(storage_zip_path) as zf:
                zf.extractall(staging)
            if storage_root.exists():
                shutil.rmtree(storage_root)
            shutil.move(str(staging), str(storage_root))
            storage_restored = True
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # 4. Verify the now-live DB.
    chk = sqlite3.connect(str(db_path))
    try:
        integrity_ok = chk.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        chk.close()
    if not integrity_ok:
        raise RestoreError("restored DB failed integrity check")

    return RestoreResult(
        db_path=db_path,
        restored_bytes=db_path.stat().st_size,
        integrity_ok=integrity_ok,
        storage_restored=storage_restored,
        safety_dir=safety_dir,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --project apps/server pytest apps/server/tests/test_restore.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/restore.py apps/server/tests/test_restore.py
git commit -m "feat(server): restore engine (inspect/validate/perform) with safety backup + WAL cleanup"
```

---

### Task 3: One-shot CLI modes in `server_entry.py`

**Files:**
- Modify: `apps/server_desktop/sidecar/server_entry.py`

**Interfaces:**
- Consumes: `restore.inspect_backup`, `restore.validate_restore`, `restore.perform_restore`.
- Produces: two env-gated one-shots that print machine-readable JSON and exit without uvicorn:
  - `YESON_INSPECT_BACKUP=1` + `YESON_SNAPSHOT_PATH` → prints `INSPECT_RESULT={json}` (BackupInfo + validation vs `YESON_CURRENT_VERSION`).
  - `YESON_RESTORE=1` + `YESON_SNAPSHOT_PATH`, `DATABASE_URL`, `STORAGE_ROOT`, optional `YESON_STORAGE_ZIP`, `YESON_SAFETY_DIR` → runs `perform_restore`, prints `RESTORE_RESULT={json}`, exits non-zero on `RestoreError`.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_restore.py — add a subprocess-free direct test of the
# mode functions (import them from the entry module).
def test_restore_mode_round_trips(tmp_path, monkeypatch):
    import sqlite3
    from apps.server_desktop.sidecar import server_entry
    live = tmp_path / "yeson-meet.db"
    c = sqlite3.connect(str(live)); c.execute("CREATE TABLE utterance(id INTEGER PRIMARY KEY, text_ko TEXT)"); c.commit(); c.close()
    snap = tmp_path / "yeson-meet-20260629-1200.db"
    c = sqlite3.connect(str(snap)); c.execute("CREATE TABLE utterance(id INTEGER PRIMARY KEY, text_ko TEXT)"); c.execute("INSERT INTO utterance(text_ko) VALUES ('x')"); c.commit(); c.close()
    monkeypatch.setenv("YESON_SNAPSHOT_PATH", str(snap))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{live}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("YESON_SAFETY_DIR", str(tmp_path / "safety"))
    rc = server_entry._restore_mode()
    assert rc == 0
    c = sqlite3.connect(str(live))
    assert c.execute("SELECT COUNT(*) FROM utterance").fetchone()[0] == 1
    c.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project apps/server pytest apps/server/tests/test_restore.py::test_restore_mode_round_trips -v`
Expected: FAIL (`_restore_mode` not defined).

- [ ] **Step 3: Write the implementation**

Add near the other `_*_mode()` functions in `server_entry.py`:

```python
def _inspect_backup_mode() -> int:
    """Read-only backup preview (YESON_INSPECT_BACKUP=1). Prints INSPECT_RESULT=json."""
    import json
    from pathlib import Path
    from apps.server.domain.restore import RestoreError, inspect_backup, validate_restore

    snap = os.environ.get("YESON_SNAPSHOT_PATH", "")
    if not snap:
        print("INSPECT_ERROR=missing YESON_SNAPSHOT_PATH", file=sys.stderr)
        return 2
    try:
        info = inspect_backup(Path(snap))
    except RestoreError as exc:
        print(f"INSPECT_ERROR={exc}", file=sys.stderr)
        return 1
    v = validate_restore(info, os.environ.get("YESON_CURRENT_VERSION") or None)
    print("INSPECT_RESULT=" + json.dumps({
        "stamp": info.stamp, "integrity_ok": info.integrity_ok,
        "app_version": info.app_version, "session_count": info.session_count,
        "utterance_count": info.utterance_count, "snapshot_bytes": info.snapshot_bytes,
        "has_storage_zip": info.storage_zip_path is not None,
        "validation": {"ok": v.ok, "level": v.level, "reason": v.reason},
    }, ensure_ascii=False))
    return 0


def _restore_mode() -> int:
    """Swap a backup into the live DB+storage (YESON_RESTORE=1). Server must be stopped."""
    import json
    from datetime import datetime
    from pathlib import Path
    from apps.server.domain.backup import db_path_from_url
    from apps.server.domain.restore import RestoreError, perform_restore

    snap = os.environ.get("YESON_SNAPSHOT_PATH", "")
    if not snap:
        print("RESTORE_ERROR=missing YESON_SNAPSHOT_PATH", file=sys.stderr)
        return 2
    db_path = db_path_from_url(_resolve_database_url())
    storage_root = Path(os.environ.get("STORAGE_ROOT", str(db_path.parent / "storage")))
    zip_env = os.environ.get("YESON_STORAGE_ZIP", "")
    safety_dir = Path(os.environ.get("YESON_SAFETY_DIR", str(db_path.parent / "pre-restore")))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        result = perform_restore(
            snapshot_path=Path(snap),
            storage_zip_path=Path(zip_env) if zip_env else None,
            db_path=db_path, storage_root=storage_root,
            safety_dir=safety_dir, stamp=stamp,
        )
    except RestoreError as exc:
        print(f"RESTORE_ERROR={exc}", file=sys.stderr)
        return 1
    print("RESTORE_RESULT=" + json.dumps({
        "integrity_ok": result.integrity_ok,
        "restored_bytes": result.restored_bytes,
        "storage_restored": result.storage_restored,
        "safety_dir": str(result.safety_dir),
    }, ensure_ascii=False))
    return 0
```

Then in the `main()` dispatch (where `YESON_BOOTSTRAP_ADMIN` / `YESON_REPORT_SELFTEST` / `YESON_SEARCH_SELFTEST` are checked), add — BEFORE the uvicorn boot — :

```python
    if os.environ.get("YESON_INSPECT_BACKUP") == "1":
        sys.exit(_inspect_backup_mode())
    if os.environ.get("YESON_RESTORE") == "1":
        sys.exit(_restore_mode())
```

(Match the exact style of the surrounding `if os.environ.get(...) == "1": return/sys.exit(...)` checks — read the dispatch block first and mirror it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --project apps/server pytest apps/server/tests/test_restore.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server_desktop/sidecar/server_entry.py apps/server/tests/test_restore.py
git commit -m "feat(server-desktop): YESON_INSPECT_BACKUP + YESON_RESTORE one-shot bundle modes"
```

---

### Task 4: Rust orchestration — `restore.rs` + lib.rs registration

**Files:**
- Create: `apps/server_desktop/src-tauri/src/restore.rs`
- Modify: `apps/server_desktop/src-tauri/src/lib.rs` (add `mod restore;` and register two commands)

**Interfaces:**
- Consumes: `server_process::{locate_bundled_server, stop_server_inner, start_server_inner, emit_backend_log, set_no_window, ServerProcessState}` (mirror `server_process::bootstrap_admin` at `server_process.rs:684` for the one-shot spawn shape and `app.path().app_data_dir()` db/storage computation). The app version is `app.package_info().version.to_string()`.
- Produces two Tauri commands:
  - `inspect_backup(app, snapshot_path: String) -> Result<serde_json::Value, String>` — runs the bundle with `YESON_INSPECT_BACKUP=1`, parses the `INSPECT_RESULT=` line, returns the JSON object.
  - `restore_backup(app, state, snapshot_path: String, storage_zip_path: Option<String>) -> Result<serde_json::Value, String>` — `stop_server_inner` → run bundle with `YESON_RESTORE=1` (+ `YESON_STORAGE_ZIP`, `YESON_SAFETY_DIR={app_data}/pre-restore`, `YESON_APP_VERSION`) → `start_server_inner` → poll health; on start failure, restore the newest `pre-restore-*.db` safety snapshot back over the DB and surface the error.

- [ ] **Step 1: Write `restore.rs`**

Mirror `bootstrap_admin` (read `server_process.rs:684-760` first). Skeleton (fill the env + parse, reuse helpers — do NOT re-implement spawning conventions, copy them):

```rust
//! Backup restore orchestration (stop → one-shot swap → start), console side.
use crate::server_process::{
    emit_backend_log, locate_bundled_server, set_no_window, start_server_inner,
    stop_server_inner, ServerProcessState,
};
use std::process::{Command, Stdio};
use tauri::Manager;

fn run_bundle_oneshot(app: &tauri::AppHandle, extra_env: &[(&str, String)]) -> Result<String, String> {
    let app_data_dir = app.path().app_data_dir()
        .map_err(|e| format!("app data dir: {e}"))?;
    std::fs::create_dir_all(&app_data_dir).map_err(|e| format!("mkdir app data: {e}"))?;
    let db_path = app_data_dir.join("yeson-meet.db");
    let storage_root = app_data_dir.join("storage");
    let database_url = format!("sqlite+aiosqlite:///{}", db_path.display());
    let server_bin = locate_bundled_server().ok_or("bundled yeson-server not found")?;
    let mut command = Command::new(&server_bin);
    command
        .env("DATABASE_URL", &database_url)
        .env("STORAGE_ROOT", &storage_root)
        .env("YESON_APP_VERSION", app.package_info().version.to_string())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    for (k, v) in extra_env { command.env(k, v); }
    set_no_window(&mut command);
    let out = command.output().map_err(|e| format!("spawn failed: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        let detail = stderr.trim().lines().last().unwrap_or("one-shot failed");
        return Err(detail.to_string());
    }
    Ok(stdout)
}

fn parse_marker(stdout: &str, marker: &str) -> Result<serde_json::Value, String> {
    let line = stdout.lines().find_map(|l| l.strip_prefix(marker))
        .ok_or_else(|| format!("missing {marker} in output"))?;
    serde_json::from_str(line.trim()).map_err(|e| format!("bad json: {e}"))
}

#[tauri::command]
pub fn inspect_backup(app: tauri::AppHandle, snapshot_path: String) -> Result<serde_json::Value, String> {
    let env = [
        ("YESON_INSPECT_BACKUP", "1".to_string()),
        ("YESON_SNAPSHOT_PATH", snapshot_path),
        ("YESON_CURRENT_VERSION", app.package_info().version.to_string()),
    ];
    let stdout = run_bundle_oneshot(&app, &env)?;
    parse_marker(&stdout, "INSPECT_RESULT=")
}

#[tauri::command]
pub fn restore_backup(
    app: tauri::AppHandle,
    state: tauri::State<'_, ServerProcessState>,
    snapshot_path: String,
    storage_zip_path: Option<String>,
) -> Result<serde_json::Value, String> {
    emit_backend_log(&app, "info", "server", "restore: stopping server".into());
    let _ = stop_server_inner(&state);
    let mut env = vec![
        ("YESON_RESTORE", "1".to_string()),
        ("YESON_SNAPSHOT_PATH", snapshot_path),
    ];
    if let Some(z) = storage_zip_path { env.push(("YESON_STORAGE_ZIP", z)); }
    let result = run_bundle_oneshot(&app, &env)
        .and_then(|out| parse_marker(&out, "RESTORE_RESULT="));
    // Restart regardless so the operator is never left with a stopped server.
    emit_backend_log(&app, "info", "server", "restore: starting server".into());
    let started = start_server_inner(&app, &state);
    match (result, started) {
        (Ok(v), Ok(_)) => Ok(v),
        (Ok(_), Err(e)) => Err(format!("restore done but server restart failed: {e}")),
        (Err(e), _) => Err(format!("restore failed: {e}")),
    }
}
```

> NOTE for the implementer: verify the exact signatures of `start_server_inner` / `stop_server_inner` / `locate_bundled_server` / `set_no_window` / `emit_backend_log` in `server_process.rs` and adjust calls (some take `&app`, some `&state`). If any are not `pub`, make them `pub(crate)`.

- [ ] **Step 2: Register in `lib.rs`**

Add `mod restore;` to the module list (after `mod orphan_reaper;`) and add to `generate_handler![...]`:

```rust
            restore::inspect_backup,
            restore::restore_backup,
```

- [ ] **Step 3: Compile**

Run: `cargo check --manifest-path apps/server_desktop/src-tauri/Cargo.toml`
Expected: compiles (fix any signature/visibility mismatches surfaced).

- [ ] **Step 4: Commit**

```bash
git add apps/server_desktop/src-tauri/src/restore.rs apps/server_desktop/src-tauri/src/lib.rs
git commit -m "feat(server-desktop): restore orchestration commands (stop→swap→start)"
```

---

### Task 5: Console UI — "복원" section in the Backup panel

**Files:**
- Modify: the React component that renders the Backup tab (the one calling `pick_backup_dir` — find with `grep -rln "pick_backup_dir" apps/server_desktop/src`)
- Test: co-located `*.test.ts(x)` if the panel already has one; otherwise a small logic test for the backup-list pairing helper.

**Interfaces:**
- Consumes Tauri commands via `@tauri-apps/api/core` `invoke`: `pick_backup_dir` (existing), `inspect_backup`, `restore_backup`.
- Produces: a "복원" section: pick folder → list stamped backups (pair `yeson-meet-*.db` with `storage-*.zip`) → select → call `inspect_backup` → show preview (date, #sessions, version, integrity, validation level) → confirm (typed `복원` confirmation when validation level is `block`-overridable is NOT allowed; `warn`/`ok` proceed with a normal confirm) → call `restore_backup` → show progress + result.

- [ ] **Step 1: Write the failing test for the pairing helper**

```ts
// pairBackups groups files in a folder into restore candidates.
import { pairBackups } from "./backupRestore";
test("pairs snapshot with its storage zip by stamp", () => {
  const files = [
    "yeson-meet-20260629-1200.db",
    "storage-20260629-1200.zip",
    "yeson-meet-20260628-0900.db",
  ];
  const pairs = pairBackups(files);
  expect(pairs).toEqual([
    { stamp: "20260629-1200", snapshot: "yeson-meet-20260629-1200.db", storageZip: "storage-20260629-1200.zip" },
    { stamp: "20260628-0900", snapshot: "yeson-meet-20260628-0900.db", storageZip: null },
  ]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @yeson-meet/server-desktop test -- backupRestore` (adjust filter to the package name in `apps/server_desktop/package.json`)
Expected: FAIL (module/function missing).

- [ ] **Step 3: Implement `pairBackups` + wire the UI**

```ts
// backupRestore.ts
export type BackupPair = { stamp: string; snapshot: string; storageZip: string | null };
export function pairBackups(files: string[]): BackupPair[] {
  const snaps = files.filter((f) => /^yeson-meet-\d{8}-\d{6}\.db$/.test(f));
  const zips = new Set(files.filter((f) => /^storage-\d{8}-\d{6}\.zip$/.test(f)));
  return snaps
    .map((s) => {
      const stamp = s.slice("yeson-meet-".length, -".db".length);
      const zip = `storage-${stamp}.zip`;
      return { stamp, snapshot: s, storageZip: zips.has(zip) ? zip : null };
    })
    .sort((a, b) => (a.stamp < b.stamp ? 1 : -1)); // newest first
}
```

Wire into the panel: a "복원" block under the existing backup controls — folder picker reuses `pick_backup_dir`; after a folder is chosen, read its entries (add a tiny Rust `list_dir(path) -> Vec<String>` command if no existing one — check first), run `pairBackups`, render a select; on select call `invoke("inspect_backup", { snapshotPath })` and show the preview; the 복원 button (disabled when `validation.level === "block"`) opens a confirm (typed-text confirm) then calls `invoke("restore_backup", { snapshotPath, storageZipPath })` and shows the returned result. Match the panel's existing styling/log patterns.

- [ ] **Step 4: Run tests**

Run: `pnpm --filter @yeson-meet/server-desktop test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server_desktop/src
git commit -m "feat(server-desktop): Backup tab 복원 section (pick → preview → restore)"
```

---

### Task 6: End-to-end verification (manual, tauri:dev)

**Files:** none (verification only).

- [ ] **Step 1: Re-freeze + run**

```bash
bash apps/server_desktop/scripts/build-server.sh
cd apps/server_desktop && pnpm tauri:dev
```

- [ ] **Step 2: Round-trip**

1. Run a backup to a folder (existing feature).
2. End/clear a meeting (mutate state).
3. Backup tab → 복원 → pick the folder → select the backup → verify preview (date/#sessions/integrity).
4. Confirm restore → server restarts → verify the meeting record + reports return (history view, export a report).
5. Verify `{app_data}/pre-restore/` holds the auto safety backup.

- [ ] **Step 3: Negative check**

Hand-craft a manifest with a higher `app_version`; verify `inspect` shows `validation.level=block` and the 복원 button is disabled.

- [ ] **Step 4: Commit any doc updates**

```bash
git add -A && git commit -m "docs: backup-restore E2E verified" --allow-empty
```

---

## Self-Review

- **Spec coverage:** inspect/validate/perform engine (Task 2) ✓; console orchestration stop→swap→start + rollback (Task 4) ✓; UI section (Task 5) ✓; manifest/version guard (Task 1) ✓; one-shot CLI (Task 3) ✓; safety backup (Task 2 `perform_restore`) ✓; WAL sidecar cleanup (Task 2) ✓; storage swap (Task 2) ✓; testing pytest+rust+E2E (Tasks 2/4/6) ✓; error handling (integrity abort, version block, restart→rollback, missing zip) ✓.
- **Placeholder scan:** Rust task intentionally defers exact helper signatures to "verify in `server_process.rs`" — flagged inline as NOTE, not a silent TODO (the conventions are copied from `bootstrap_admin`). The UI task references the existing Backup panel found via grep rather than a hard path because the panel filename must be confirmed in-repo.
- **Type consistency:** `BackupInfo` / `RestoreValidation` / `RestoreResult` fields used consistently across Tasks 2/3; marker strings `INSPECT_RESULT=` / `RESTORE_RESULT=` match between Task 3 (print) and Task 4 (parse); env var names (`YESON_RESTORE`, `YESON_SNAPSHOT_PATH`, `YESON_STORAGE_ZIP`, `YESON_SAFETY_DIR`, `YESON_INSPECT_BACKUP`, `YESON_CURRENT_VERSION`, `YESON_APP_VERSION`) consistent between Task 3 and Task 4.
