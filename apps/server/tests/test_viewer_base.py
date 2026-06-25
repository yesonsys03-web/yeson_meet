"""Runtime viewer-base override file resolution (Go Live without restart).

`_viewer_base()` resolves with precedence: runtime file > env VIEWER_BASE >
default. The desktop writes `{STORAGE_ROOT}/viewer_base.txt` to publish a public
viewer base without restarting the server, and deletes it to revert. These are
pure-unit tests (no DB/HTTP) using a tmp STORAGE_ROOT.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.api.v1.sessions import _viewer_base

_DEFAULT = "http://localhost:5173"


def _write_override(storage_root: Path, value: str) -> None:
    storage_root.mkdir(parents=True, exist_ok=True)
    (storage_root / "viewer_base.txt").write_text(value, encoding="utf-8")


def test_file_value_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("VIEWER_BASE", "https://env.example")
    _write_override(tmp_path, "https://public.example")
    assert _viewer_base() == "https://public.example"


def test_falls_back_to_env_when_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("VIEWER_BASE", "https://env.example")
    # no viewer_base.txt written
    assert _viewer_base() == "https://env.example"


def test_falls_back_to_default_when_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("VIEWER_BASE", raising=False)
    assert _viewer_base() == _DEFAULT


def test_precedence_file_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("VIEWER_BASE", "https://env.example")
    _write_override(tmp_path, "https://file.example")
    assert _viewer_base() == "https://file.example"
    # Deleting the file reverts to env (the "stop public" path).
    (tmp_path / "viewer_base.txt").unlink()
    assert _viewer_base() == "https://env.example"


def test_empty_file_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("VIEWER_BASE", "https://env.example")
    _write_override(tmp_path, "   \n")  # whitespace-only → treated as absent
    assert _viewer_base() == "https://env.example"


def test_unreadable_file_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A viewer_base.txt that cannot be read (e.g. it is a directory) must fall
    through to env/default, never raise."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("VIEWER_BASE", "https://env.example")
    # Make viewer_base.txt a DIRECTORY so read_text() raises IsADirectoryError.
    (tmp_path / "viewer_base.txt").mkdir(parents=True, exist_ok=True)
    assert _viewer_base() == "https://env.example"


def test_strips_whitespace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("VIEWER_BASE", raising=False)
    _write_override(tmp_path, "  https://trim.example  \n")
    assert _viewer_base() == "https://trim.example"
