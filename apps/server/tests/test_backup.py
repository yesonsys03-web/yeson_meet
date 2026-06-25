"""S1 backup engine: consistent SQLite snapshot + storage zip + integrity check."""
from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from apps.server.domain.backup import (
    BackupError,
    backup_to_destinations,
    create_backup,
    db_path_from_url,
)


def _make_db(path: Path, rows: int = 3) -> None:
    """Create a WAL-mode SQLite db with a known row count (mirrors the live server)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE utterance (id INTEGER PRIMARY KEY, text_ko TEXT)")
        conn.executemany(
            "INSERT INTO utterance (text_ko) VALUES (?)",
            [(f"발화 {i}",) for i in range(rows)],
        )
        conn.commit()
    finally:
        conn.close()


def test_db_path_from_url_posix() -> None:
    assert db_path_from_url("sqlite+aiosqlite:////Users/op/yeson-meet.db") == Path(
        "/Users/op/yeson-meet.db"
    )


def test_db_path_from_url_windows() -> None:
    assert db_path_from_url(
        "sqlite+aiosqlite:///C:\\Users\\op\\yeson-meet.db"
    ) == Path("C:\\Users\\op\\yeson-meet.db")


def test_create_backup_produces_consistent_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db, rows=5)
    storage = tmp_path / "storage"
    (storage / "abc").mkdir(parents=True)
    (storage / "abc" / "report.md").write_text("# 보고서", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()

    result = create_backup(
        database_url=f"sqlite+aiosqlite:///{db}",
        storage_root=storage,
        dest_dir=dest,
        stamp="20260625-1530",
    )

    # Snapshot exists, passed integrity check, and round-trips the live rows.
    assert result.integrity_ok is True
    assert result.snapshot_path == dest / "yeson-meet-20260625-1530.db"
    assert result.snapshot_path.is_file()
    snap = sqlite3.connect(str(result.snapshot_path))
    try:
        assert snap.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert snap.execute("SELECT COUNT(*) FROM utterance").fetchone()[0] == 5
    finally:
        snap.close()

    # storage/ archived with its tree preserved.
    assert result.storage_zip_path == dest / "storage-20260625-1530.zip"
    assert result.storage_zip_path.is_file()
    with zipfile.ZipFile(result.storage_zip_path) as zf:
        assert "abc/report.md" in zf.namelist()
        assert zf.read("abc/report.md").decode("utf-8") == "# 보고서"


def test_create_backup_skips_zip_when_storage_empty(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db)
    dest = tmp_path / "dest"
    dest.mkdir()

    result = create_backup(
        database_url=f"sqlite+aiosqlite:///{db}",
        storage_root=tmp_path / "missing-storage",
        dest_dir=dest,
        stamp="20260625-1600",
    )

    assert result.storage_zip_path is None
    assert result.snapshot_path.is_file()


def test_create_backup_overwrites_same_stamp(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db)
    dest = tmp_path / "dest"
    dest.mkdir()
    kwargs = dict(
        database_url=f"sqlite+aiosqlite:///{db}",
        storage_root=tmp_path / "none",
        dest_dir=dest,
        stamp="20260625-1700",
    )

    create_backup(**kwargs)
    # Re-running with the same stamp must not error on the pre-existing snapshot.
    result = create_backup(**kwargs)
    assert result.snapshot_path.is_file()


def test_create_backup_raises_on_missing_dest_dir(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db)

    with pytest.raises(BackupError):
        create_backup(
            database_url=f"sqlite+aiosqlite:///{db}",
            storage_root=tmp_path / "storage",
            dest_dir=tmp_path / "does-not-exist",
            stamp="20260625-1800",
        )


# --- S2: multi-destination + retention + per-destination failure isolation ---


def test_backup_to_destinations_writes_all(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db, rows=4)
    storage = tmp_path / "storage"
    (storage / "s").mkdir(parents=True)
    (storage / "s" / "report.md").write_text("x", encoding="utf-8")
    d1 = tmp_path / "cloud"
    d2 = tmp_path / "nas"
    d1.mkdir()
    d2.mkdir()

    result = backup_to_destinations(
        database_url=f"sqlite+aiosqlite:///{db}",
        storage_root=storage,
        dest_dirs=[d1, d2],
        stamp="20260625-1530",
        keep=14,
    )

    assert result.integrity_ok is True
    assert len(result.destinations) == 2
    for dr, d in zip(result.destinations, [d1, d2]):
        assert dr.ok is True
        assert dr.error is None
        assert (d / "yeson-meet-20260625-1530.db").is_file()
        assert (d / "storage-20260625-1530.zip").is_file()
        # The DB copy is a real, openable snapshot (not a half-written file).
        snap = sqlite3.connect(str(d / "yeson-meet-20260625-1530.db"))
        try:
            assert snap.execute("SELECT COUNT(*) FROM utterance").fetchone()[0] == 4
        finally:
            snap.close()


def test_backup_to_destinations_isolates_failure(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db)
    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / "unmounted-nas"  # never created → simulates an offline dest

    result = backup_to_destinations(
        database_url=f"sqlite+aiosqlite:///{db}",
        storage_root=tmp_path / "none",
        dest_dirs=[good, bad],
        stamp="20260625-1600",
        keep=14,
    )

    by_dir = {dr.dest_dir: dr for dr in result.destinations}
    # The good destination still succeeds despite the bad one failing.
    assert by_dir[good].ok is True
    assert (good / "yeson-meet-20260625-1600.db").is_file()
    # The bad destination is recorded as failed, not raised.
    assert by_dir[bad].ok is False
    assert by_dir[bad].error


def test_backup_to_destinations_retention_prunes_old(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db)
    dest = tmp_path / "dest"
    dest.mkdir()
    common = dict(
        database_url=f"sqlite+aiosqlite:///{db}",
        storage_root=tmp_path / "none",
        dest_dirs=[dest],
        keep=2,
    )

    for stamp in ["20260625-1000", "20260625-1100", "20260625-1200"]:
        backup_to_destinations(**common, stamp=stamp)

    snaps = sorted(p.name for p in dest.glob("yeson-meet-*.db"))
    # Only the 2 newest snapshots survive; the oldest (1000) is pruned.
    assert snaps == ["yeson-meet-20260625-1100.db", "yeson-meet-20260625-1200.db"]


def test_backup_to_destinations_raises_when_no_destinations(tmp_path: Path) -> None:
    db = tmp_path / "yeson-meet.db"
    _make_db(db)
    with pytest.raises(BackupError):
        backup_to_destinations(
            database_url=f"sqlite+aiosqlite:///{db}",
            storage_root=tmp_path / "none",
            dest_dirs=[],
            stamp="20260625-1700",
            keep=14,
        )
