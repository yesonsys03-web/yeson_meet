from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.server.api.v1 import translate_models as api
from apps.server.domain.video_captions import translate_catalog as tc
from apps.server.domain.video_captions import translate_models as tmods
from apps.server.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(tmods.to, "ollama_running", lambda: True)
    monkeypatch.setattr(tmods.to, "ollama_installed", lambda: True)
    monkeypatch.setattr(tmods.to, "qwen_ollama_available", lambda tag: False)
    tmods._downloading.clear()
    yield
    tmods._downloading.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_list_does_not_refresh_by_default(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "get_remote_models", lambda force=False: calls.append(force) or [])
    r = client.get("/api/v1/translate-models")
    assert r.status_code == 200
    assert calls == [False]
    assert {m["name"] for m in r.json()["models"]} >= {"qwen", "qwen_lite", "qwen_hifi"}


def test_list_refresh_forces_network(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "get_remote_models", lambda force=False: calls.append(force) or [])
    r = client.get("/api/v1/translate-models?refresh=true")
    assert r.status_code == 200
    assert calls == [True]


def test_download_unknown_model_404(client):
    assert client.post("/api/v1/translate-models/nope/download").status_code == 404


def test_download_unsupported_runtime_409_and_no_pull(client, monkeypatch):
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    spawned = {"n": 0}
    monkeypatch.setattr(api, "_spawn_download", lambda name: spawned.__setitem__("n", 1))
    r = client.post("/api/v1/translate-models/qwen_x/download")
    assert r.status_code == 409
    assert "실리콘맥 전용" in r.json()["detail"]
    assert spawned["n"] == 0  # 다운로드 스레드 자체가 뜨면 안 된다


def test_download_ollama_not_running_409(client, monkeypatch):
    monkeypatch.setattr(tmods.to, "ollama_running", lambda: False)
    r = client.post("/api/v1/translate-models/qwen/download")
    assert r.status_code == 409
    assert "Ollama" in r.json()["detail"]


def test_download_started(client, monkeypatch):
    spawned = {"n": 0}
    monkeypatch.setattr(api, "_spawn_download", lambda name: spawned.__setitem__("n", 1))
    r = client.post("/api/v1/translate-models/qwen/download")
    assert r.status_code == 202
    assert r.json() == {"status": "started"}
    assert spawned["n"] == 1


def test_delete_unsupported_runtime_409(client, monkeypatch):
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    r = client.delete("/api/v1/translate-models/qwen_x")
    assert r.status_code == 409
