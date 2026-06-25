"""S1 backup API: POST /api/v1/backup/run (operator-gated)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from apps.server.auth.deps import require_operator
from apps.server.main import app


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE utterance (id INTEGER PRIMARY KEY, text_ko TEXT)")
        conn.execute("INSERT INTO utterance (text_ko) VALUES ('안녕')")
        conn.commit()
    finally:
        conn.close()


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    db = tmp_path / "yeson-meet.db"
    _make_db(db)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    app.dependency_overrides[require_operator] = lambda: object()
    return TestClient(app)


def test_backup_run_returns_manifest(monkeypatch, tmp_path: Path) -> None:
    d1 = tmp_path / "cloud"
    d2 = tmp_path / "nas"
    d1.mkdir()
    d2.mkdir()
    client = _client(monkeypatch, tmp_path)
    try:
        resp = client.post(
            "/api/v1/backup/run", json={"dest_dirs": [str(d1), str(d2)]}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["integrity_ok"] is True
    assert len(body["destinations"]) == 2
    for dr in body["destinations"]:
        assert dr["ok"] is True
        assert Path(dr["snapshot_path"]).is_file()


def test_backup_run_isolates_bad_destination(monkeypatch, tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / "nope"
    client = _client(monkeypatch, tmp_path)
    try:
        resp = client.post(
            "/api/v1/backup/run", json={"dest_dirs": [str(good), str(bad)]}
        )
    finally:
        app.dependency_overrides.clear()

    # Partial success: 200 with one ok and one failed destination, not a 4xx.
    assert resp.status_code == 200, resp.text
    by_dir = {d["dest_dir"]: d for d in resp.json()["destinations"]}
    assert by_dir[str(good)]["ok"] is True
    assert by_dir[str(bad)]["ok"] is False
    assert by_dir[str(bad)]["error"]


def test_backup_run_rejects_empty_destinations(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    try:
        resp = client.post("/api/v1/backup/run", json={"dest_dirs": []})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400
