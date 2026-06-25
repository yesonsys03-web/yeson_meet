# === ANCHOR: BACKUP_API_START ===
"""Backup router (S1): operator-triggered meeting-record snapshot.

``POST /backup/run`` writes a verified SQLite snapshot + storage zip into an
operator-chosen destination directory. The destination is a server-local
filesystem path (the packaged server and console run on the same machine);
operator role is required because a backup exports the full meeting record.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from apps.server.auth.deps import require_operator
from apps.server.db.models import AppUser
from apps.server.domain.backup import BackupError, backup_to_destinations

router = APIRouter(tags=["backup"], prefix="/backup")

# Default number of most-recent backups to retain per destination (~2 weeks of
# daily snapshots). Operator-overridable per request.
DEFAULT_KEEP = 14


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def _storage_root() -> str:
    return os.environ.get("STORAGE_ROOT", "/var/lib/yeson-meet/storage")


# === ANCHOR: BACKUP_API_RUNIN_START ===
class BackupRunIn(BaseModel):
    dest_dirs: list[str]
    keep: int = DEFAULT_KEEP
# === ANCHOR: BACKUP_API_RUNIN_END ===


# === ANCHOR: BACKUP_API_DESTOUT_START ===
class DestinationOut(BaseModel):
    dest_dir: str
    ok: bool
    snapshot_path: str | None
    storage_zip_path: str | None
    pruned: int
    error: str | None
# === ANCHOR: BACKUP_API_DESTOUT_END ===


# === ANCHOR: BACKUP_API_RUNOUT_START ===
class BackupRunOut(BaseModel):
    stamp: str
    snapshot_bytes: int
    integrity_ok: bool
    destinations: list[DestinationOut]
# === ANCHOR: BACKUP_API_RUNOUT_END ===


@router.post("/run", response_model=BackupRunOut)
# === ANCHOR: BACKUP_API_RUN_START ===
async def run_backup(
    body: BackupRunIn,
    _operator: Annotated[AppUser, Depends(require_operator)],
) -> BackupRunOut:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        result = await run_in_threadpool(
            backup_to_destinations,
            database_url=_database_url(),
            storage_root=_storage_root(),
            dest_dirs=list(body.dest_dirs),
            stamp=stamp,
            keep=body.keep,
        )
    except BackupError as exc:
        # Only "no destinations" reaches here; per-destination failures are
        # reported in-band as ok=False entries (partial success, not a 4xx).
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return BackupRunOut(
        stamp=result.stamp,
        snapshot_bytes=result.snapshot_bytes,
        integrity_ok=result.integrity_ok,
        destinations=[
            DestinationOut(
                dest_dir=str(d.dest_dir),
                ok=d.ok,
                snapshot_path=str(d.snapshot_path) if d.snapshot_path else None,
                storage_zip_path=(
                    str(d.storage_zip_path) if d.storage_zip_path else None
                ),
                pruned=d.pruned,
                error=d.error,
            )
            for d in result.destinations
        ],
    )
# === ANCHOR: BACKUP_API_RUN_END ===
# === ANCHOR: BACKUP_API_END ===
