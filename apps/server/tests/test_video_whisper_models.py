from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.domain.video_captions import whisper_models as wm


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


def test_catalog_has_expected_models():
    assert set(wm.CATALOG) == {"tiny", "base", "small", "medium", "large-v3"}
    assert wm.CATALOG["small"].repo_id == "Systran/faster-whisper-small"


def test_is_downloaded_false_then_true_after_model_bin(tmp_path: Path):
    assert wm.is_downloaded("small") is False
    d = wm.model_dir("small")
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x" * 10)
    assert wm.is_downloaded("small") is True
    entry = next(m for m in wm.list_models() if m["name"] == "small")
    assert entry["downloaded"] is True
    assert entry["disk_bytes"] == 10


def test_download_model_calls_snapshot_download(monkeypatch):
    calls = {}

    def fake_snapshot(repo_id, local_dir, **kw):
        calls["repo_id"] = repo_id
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / "model.bin").write_bytes(b"ok")

    monkeypatch.setattr(wm, "_snapshot_download", fake_snapshot)
    wm.download_model("tiny")
    assert calls["repo_id"] == "Systran/faster-whisper-tiny"
    assert wm.is_downloaded("tiny") is True
    assert wm._downloading.get("tiny") is not True


def test_unknown_model_rejected():
    with pytest.raises(KeyError):
        wm.download_model("giant")


def test_delete_model(tmp_path: Path):
    d = wm.model_dir("base")
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x")
    wm.delete_model("base")
    assert not d.exists()


def test_download_model_skips_when_already_downloading(monkeypatch):
    calls = []
    monkeypatch.setattr(wm, "_snapshot_download", lambda repo_id, local_dir: calls.append(repo_id))
    wm._downloading["small"] = True
    try:
        wm.download_model("small")
    finally:
        wm._downloading["small"] = False
    assert calls == []


def test_download_model_clears_flag_on_failure(monkeypatch):
    def boom(repo_id, local_dir):
        raise RuntimeError("network down")

    monkeypatch.setattr(wm, "_snapshot_download", boom)
    with pytest.raises(RuntimeError, match="network down"):
        wm.download_model("tiny")
    assert wm._downloading.get("tiny") is False


def test_delete_model_refuses_while_downloading(tmp_path: Path):
    wm._downloading["base"] = True
    try:
        with pytest.raises(RuntimeError, match="다운로드 중"):
            wm.delete_model("base")
    finally:
        wm._downloading["base"] = False
