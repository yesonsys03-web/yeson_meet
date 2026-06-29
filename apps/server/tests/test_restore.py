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
