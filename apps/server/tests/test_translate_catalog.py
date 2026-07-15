from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.server.domain.video_captions import catalog_fetch as cf
from apps.server.domain.video_captions import translate_catalog as tc


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


def _payload(*models: dict) -> str:
    return json.dumps({"version": 1, "models": list(models)})


BOTH = {"name": "qwen_next", "label": "Qwen 12B (로컬)",
        "mlx_repo": "mlx-community/Qwen3.6-12B-4bit", "mlx_bytes": 7_000_000_000,
        "ollama_tag": "qwen3.6:12b", "ollama_bytes": 9_000_000_000}
MLX_ONLY = {"name": "qwen_mlxonly", "label": "MLX 전용",
            "mlx_repo": "mlx-community/X-4bit", "mlx_bytes": 1_000}
OLLAMA_ONLY = {"name": "qwen_ollamaonly", "label": "Ollama 전용",
               "ollama_tag": "x:1b", "ollama_bytes": 1_000}


def _fetch(monkeypatch, *models: dict) -> None:
    monkeypatch.setattr(cf, "_http_get", lambda url: _payload(*models))
    monkeypatch.setattr(cf, "_now", lambda: 1000.0)
    tc.get_remote_models(force=True)


def test_cache_path_is_directly_under_storage_root(tmp_path: Path):
    # mlx_models/ 밑이 아니다 — MLX가 없는 윈도우 서버에 MLX 디렉터리를 만들지 않는다.
    assert tc._cache_path() == tmp_path / "translate_catalog.cache.json"


def test_builtin_only_when_no_remote():
    assert set(tc.get_catalog()) == {"qwen", "qwen_lite", "qwen_hifi"}


def test_remote_adds_new_tier(monkeypatch):
    _fetch(monkeypatch, BOTH)
    cat = tc.get_catalog()
    assert cat["qwen_next"].mlx_repo == "mlx-community/Qwen3.6-12B-4bit"
    assert cat["qwen_next"].ollama_tag == "qwen3.6:12b"
    assert cat["qwen_next"].ollama_bytes == 9_000_000_000


def test_remote_overrides_builtin(monkeypatch):
    _fetch(monkeypatch, {**BOTH, "name": "qwen", "label": "덮어씀"})
    assert tc.get_catalog()["qwen"].label == "덮어씀"


def test_remote_cannot_delete_builtin(monkeypatch):
    _fetch(monkeypatch, BOTH)
    assert {"qwen", "qwen_lite", "qwen_hifi"} <= set(tc.get_catalog())


def test_single_runtime_entries_are_valid(monkeypatch):
    _fetch(monkeypatch, MLX_ONLY, OLLAMA_ONLY)
    cat = tc.get_catalog()
    assert cat["qwen_mlxonly"].ollama_tag is None
    assert cat["qwen_mlxonly"].ollama_bytes == 0      # 없는 쪽은 0으로 정규화
    assert cat["qwen_ollamaonly"].mlx_repo is None
    assert cat["qwen_ollamaonly"].mlx_bytes == 0


def test_skips_malformed_entries_keeps_valid(monkeypatch):
    bad = [
        {"name": "no_runtime", "label": "x"},                                    # 양쪽 다 없음
        {"name": "bad name!", "label": "x", "ollama_tag": "a:1", "ollama_bytes": 1},
        {"name": ".", "label": "x", "ollama_tag": "a:1", "ollama_bytes": 1},
        {"name": "..", "label": "x", "ollama_tag": "a:1", "ollama_bytes": 1},
        {"name": "neg", "label": "x", "ollama_tag": "a:1", "ollama_bytes": -1},  # 음수
        {"name": "boolbytes", "label": "x", "ollama_tag": "a:1", "ollama_bytes": True},
        {"name": "nolabel", "ollama_tag": "a:1", "ollama_bytes": 1},             # label 없음
        "not-a-dict",
    ]
    _fetch(monkeypatch, BOTH, *bad)
    cat = tc.get_catalog()
    assert "qwen_next" in cat
    for name in ("no_runtime", "neg", "boolbytes", "nolabel", "."):
        assert name not in cat


def test_ollama_env_key_reproduces_existing_keys():
    # 하위호환 — 기존 3키를 규칙이 그대로 재현해야 한다.
    assert tc.ollama_env_key("qwen") == "YESON_OLLAMA_QWEN_MODEL"
    assert tc.ollama_env_key("qwen_lite") == "YESON_OLLAMA_QWEN_LITE_MODEL"
    assert tc.ollama_env_key("qwen_hifi") == "YESON_OLLAMA_QWEN_HIFI_MODEL"


def test_unsupported_reason_on_silicon(monkeypatch):
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: True)
    assert tc.runtime() == "mlx"
    assert tc.unsupported_reason(tc.BUILTIN["qwen"]) is None
    ollama_only = tc.TranslateModel("x", "x", None, 0, "x:1b", 10)
    assert tc.unsupported_reason(ollama_only) == "Ollama 전용"


def test_unsupported_reason_off_silicon(monkeypatch):
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    assert tc.runtime() == "ollama"
    assert tc.unsupported_reason(tc.BUILTIN["qwen"]) is None
    mlx_only = tc.TranslateModel("x", "x", "a/b", 10, None, 0)
    assert tc.unsupported_reason(mlx_only) == "실리콘맥 전용"
