# apps/server/domain/restore.py
"""Backup restore engine (inverse of backup.py). Pure stdlib; runs with the
server STOPPED so the SQLite file can be swapped safely."""
from __future__ import annotations

import json
import os
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
    conn = None
    try:
        conn = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
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
        if conn is not None:
            conn.close()

    stamp = _stamp_from_name(snapshot_path)
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
    os.replace(tmp_db, db_path)
    for sidecar in (db_path.name + "-wal", db_path.name + "-shm"):
        (db_path.parent / sidecar).unlink(missing_ok=True)

    # 3. Replace the storage tree from the zip (swap dirs so a partial extract
    #    never leaves a half-populated tree).
    storage_restored = False
    if storage_zip_path is not None and Path(storage_zip_path).is_file():
        storage_root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="yeson-restore-storage-", dir=storage_root.parent))
        old = None
        try:
            with zipfile.ZipFile(storage_zip_path) as zf:
                zf.extractall(staging)
            if storage_root.exists():
                old = storage_root.with_name(storage_root.name + f".old-{stamp}")
                os.replace(storage_root, old)        # move live tree aside (atomic, same fs)
            os.replace(staging, storage_root)        # move new tree in (atomic, same fs)
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
            storage_restored = True
        finally:
            shutil.rmtree(staging, ignore_errors=True)  # no-op once renamed
            if old is not None and old.exists():
                shutil.rmtree(old, ignore_errors=True)

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
