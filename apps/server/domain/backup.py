# === ANCHOR: BACKUP_START ===
"""Meeting-record backup engine (S1).

Produces a transactionally-consistent single-file SQLite snapshot via
``VACUUM INTO`` and verifies it with ``PRAGMA integrity_check``, then archives
the ``storage/`` artifact tree (reports/exports) into a zip. All outputs land in
one operator-chosen destination directory, timestamped.

``VACUUM INTO`` runs on a *separate* read connection, so it is safe against the
live WAL-mode server connection (WAL permits concurrent readers) and never
copies a half-written ``-wal`` page the way a raw file copy would. Pure-stdlib
(``sqlite3`` + ``zipfile``) — zero new deps, runs synchronously from the API
layer's threadpool.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


class BackupError(RuntimeError):
    """Raised when a backup cannot be produced or fails its integrity check."""


# === ANCHOR: BACKUP_DB_PATH_FROM_URL_START ===
def db_path_from_url(database_url: str) -> Path:
    """Extract the SQLite file path from a ``sqlite+aiosqlite:///…`` URL.

    The spawn path (``server_process.rs``) builds the URL as
    ``sqlite+aiosqlite:///{db_path}``; on POSIX an absolute ``db_path`` adds a
    leading ``/`` (four slashes total), on Windows it is ``C:\\…`` after three.
    Splitting on the first ``:///`` recovers the original path on both.
    """
    marker = ":///"
    idx = database_url.find(marker)
    if idx == -1:
        raise BackupError(f"unsupported database url: {database_url!r}")
    return Path(database_url[idx + len(marker) :])
# === ANCHOR: BACKUP_DB_PATH_FROM_URL_END ===


@dataclass(frozen=True)
class BackupResult:
    """Manifest of one backup run."""

    snapshot_path: Path
    storage_zip_path: Path | None
    snapshot_bytes: int
    integrity_ok: bool
    stamp: str


# === ANCHOR: BACKUP_CREATE_START ===
def create_backup(
    *,
    database_url: str,
    storage_root: str | Path,
    dest_dir: str | Path,
    stamp: str,
) -> BackupResult:
    """Write a verified DB snapshot + storage zip into ``dest_dir``.

    ``stamp`` is the caller-supplied timestamp (e.g. ``20260625-1530``) used in
    the output filenames; injecting it keeps the engine deterministic/testable.
    Raises :class:`BackupError` if ``dest_dir`` is not a directory or the
    produced snapshot fails ``PRAGMA integrity_check``.
    """
    dest = Path(dest_dir)
    if not dest.is_dir():
        raise BackupError(f"destination is not a directory: {dest}")

    db_file = db_path_from_url(database_url)
    if not db_file.is_file():
        raise BackupError(f"database file not found: {db_file}")

    snapshot = dest / f"yeson-meet-{stamp}.db"
    # VACUUM INTO refuses to overwrite an existing file, so clear a same-stamp
    # leftover first (re-running a backup must be idempotent, not an error).
    snapshot.unlink(missing_ok=True)

    src = sqlite3.connect(str(db_file))
    try:
        escaped = str(snapshot).replace("'", "''")
        src.execute(f"VACUUM INTO '{escaped}'")
    finally:
        src.close()

    chk = sqlite3.connect(str(snapshot))
    try:
        integrity_ok = chk.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        chk.close()
    if not integrity_ok:
        snapshot.unlink(missing_ok=True)
        raise BackupError(f"snapshot failed integrity check: {snapshot}")

    storage_zip_path = _archive_storage(Path(storage_root), dest, stamp)

    return BackupResult(
        snapshot_path=snapshot,
        storage_zip_path=storage_zip_path,
        snapshot_bytes=snapshot.stat().st_size,
        integrity_ok=integrity_ok,
        stamp=stamp,
    )
# === ANCHOR: BACKUP_CREATE_END ===


# === ANCHOR: BACKUP_MULTI_START ===
@dataclass(frozen=True)
class DestinationResult:
    """Outcome of writing the backup into one destination directory."""

    dest_dir: Path
    ok: bool
    snapshot_path: Path | None
    storage_zip_path: Path | None
    pruned: int
    error: str | None


@dataclass(frozen=True)
class MultiBackupResult:
    """Manifest of a multi-destination backup run."""

    stamp: str
    snapshot_bytes: int
    integrity_ok: bool
    destinations: list[DestinationResult]


def backup_to_destinations(
    *,
    database_url: str,
    storage_root: str | Path,
    dest_dirs: list[str | Path],
    stamp: str,
    keep: int,
) -> MultiBackupResult:
    """Write one verified backup to several destinations, each isolated.

    The snapshot + storage zip are produced ONCE in a temp staging dir (a single
    ``VACUUM INTO``, not one per destination), then copied into each destination
    and pruned to ``keep`` most-recent backups there. A destination that is
    offline/typo'd (e.g. an unmounted NAS) is recorded as a failed
    :class:`DestinationResult` — it never aborts the other destinations. ``keep``
    ``<= 0`` disables pruning. Raises :class:`BackupError` only when no
    destinations are given (nothing to write).
    """
    if not dest_dirs:
        raise BackupError("no backup destinations configured")

    staging = Path(tempfile.mkdtemp(prefix="yeson-backup-"))
    try:
        staged = create_backup(
            database_url=database_url,
            storage_root=storage_root,
            dest_dir=staging,
            stamp=stamp,
        )
        results: list[DestinationResult] = []
        for raw in dest_dirs:
            dest = Path(raw)
            try:
                if not dest.is_dir():
                    raise BackupError(f"destination is not a directory: {dest}")
                snap_dest = dest / staged.snapshot_path.name
                shutil.copy2(staged.snapshot_path, snap_dest)
                zip_dest: Path | None = None
                if staged.storage_zip_path is not None:
                    zip_dest = dest / staged.storage_zip_path.name
                    shutil.copy2(staged.storage_zip_path, zip_dest)
                pruned = _prune(dest, keep)
                results.append(
                    DestinationResult(dest, True, snap_dest, zip_dest, pruned, None)
                )
            except Exception as exc:  # noqa: BLE001 — isolate per-destination failure
                results.append(DestinationResult(dest, False, None, None, 0, str(exc)))
        return MultiBackupResult(
            stamp=stamp,
            snapshot_bytes=staged.snapshot_bytes,
            integrity_ok=staged.integrity_ok,
            destinations=results,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _prune(dest: Path, keep: int) -> int:
    """Keep the ``keep`` newest snapshot + zip files in ``dest``; delete the rest.

    Filenames carry a fixed-width ``YYYYMMDD-HHMMSS`` stamp, so a reverse lexical
    sort is newest-first. Returns the number of files removed.
    """
    if keep <= 0:
        return 0
    pruned = 0
    for pattern in ("yeson-meet-*.db", "storage-*.zip"):
        files = sorted(dest.glob(pattern), reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
            pruned += 1
    return pruned
# === ANCHOR: BACKUP_MULTI_END ===


def _archive_storage(storage_root: Path, dest: Path, stamp: str) -> Path | None:
    """Zip ``storage_root`` into ``dest`` preserving its tree; ``None`` if empty."""
    if not storage_root.is_dir():
        return None
    files = [p for p in sorted(storage_root.rglob("*")) if p.is_file()]
    if not files:
        return None
    zip_path = dest / f"storage-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(storage_root).as_posix())
    return zip_path
# === ANCHOR: BACKUP_END ===
