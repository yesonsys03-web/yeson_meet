from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.domain.video_captions import translate_catalog as tc
from apps.server.domain.video_captions import translate_models as tm


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    tm._downloading.clear()
    yield
    tm._downloading.clear()


def _ollama_server(monkeypatch, running: bool = True) -> None:
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(tm.to, "ollama_running", lambda: running)
    monkeypatch.setattr(tm.to, "ollama_installed", lambda: True)
    monkeypatch.setattr(tm.to, "qwen_ollama_available", lambda tag: False)


def test_list_models_exposes_repo_and_tag(monkeypatch):
    _ollama_server(monkeypatch)
    out = tm.list_models()
    qwen = next(m for m in out["models"] if m["name"] == "qwen")
    assert qwen["mlx_repo"] == "mlx-community/Qwen3.5-9B-4bit"
    assert qwen["ollama_tag"] == "qwen3.5:9b"
    assert qwen["approx_bytes"] == 6_600_000_000   # ollama 런타임이므로 ollama_bytes
    assert qwen["mlx_bytes"] == 5_000_000_000      # 런타임과 무관한 카탈로그 값
    assert qwen["reason"] is None
    assert qwen["downloadable"] is True


def test_list_models_marks_unsupported_tier(monkeypatch):
    _ollama_server(monkeypatch)
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    entry = tm.list_models()["models"][0]
    assert entry["reason"] == "실리콘맥 전용"
    assert entry["downloadable"] is False


def test_download_model_rejects_unsupported_runtime(monkeypatch):
    _ollama_server(monkeypatch)
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    pulled = {"n": 0}
    monkeypatch.setattr(tm.to, "pull_model",
                        lambda tag, on_progress=None: pulled.__setitem__("n", 1))
    with pytest.raises(RuntimeError, match="실리콘맥 전용"):
        tm.download_model("qwen_x")
    assert pulled["n"] == 0  # pull_model(None)이 나가면 안 된다


def test_delete_model_rejects_unsupported_runtime(monkeypatch):
    _ollama_server(monkeypatch)
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    with pytest.raises(RuntimeError, match="실리콘맥 전용"):
        tm.delete_model("qwen_x")


def test_download_model_unknown_name(monkeypatch):
    _ollama_server(monkeypatch)
    with pytest.raises(KeyError):
        tm.download_model("nope")


def test_is_installed_ollama_only_tier_on_silicon(monkeypatch):
    # mlx_repo=None을 mlx_model_installed에 넘기면 터진다(실리콘맥 크래시).
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: True)
    ollama_only = tc.TranslateModel("qwen_y", "Ollama 전용", None, 0, "y:1b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_y": ollama_only})
    assert tm.is_installed("qwen_y") is False
