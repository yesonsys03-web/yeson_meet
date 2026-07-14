from __future__ import annotations

import httpx
import pytest

from apps.server.domain.video_captions import translate_cli as tc
from apps.server.domain.video_captions import translate_ollama as to
from apps.server.domain.video_captions.translate import TranslationError


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=None, response=None)  # type: ignore[arg-type]

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _reset_avail_cache():
    # /api/tags 결과는 TTL 캐시됨 — 테스트 간 격리를 위해 매번 만료시킨다.
    to._avail_cache["at"] = -1e9
    to._avail_cache["models"] = frozenset()
    yield


# ── qwen_ollama_model: 기본 태그 + env 오버라이드 ───────────────────────────
def test_qwen_ollama_model_defaults():
    assert to.qwen_ollama_model("qwen") == "qwen3.5:9b"
    assert to.qwen_ollama_model("qwen_lite") == "qwen3.5:4b"
    assert to.qwen_ollama_model("qwen_hifi") == "qwen3.5:9b-q8_0"
    assert to.qwen_ollama_model("nope") is None


def test_qwen_ollama_model_env_override(monkeypatch):
    monkeypatch.setenv("YESON_OLLAMA_QWEN_MODEL", "qwen3.6:27b")
    assert to.qwen_ollama_model("qwen") == "qwen3.6:27b"
    # 다른 티어는 영향 없음
    assert to.qwen_ollama_model("qwen_lite") == "qwen3.5:4b"


def test_ollama_base_url_env(monkeypatch):
    assert to.ollama_base_url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("YESON_OLLAMA_URL", "http://box:11434")
    assert to.ollama_base_url() == "http://box:11434"


# ── qwen_ollama_available: /api/tags 파싱 ───────────────────────────────────
def test_available_true_when_pulled(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(
        {"models": [{"name": "qwen3.5:9b"}, {"name": "llama3:8b"}]}))
    assert to.qwen_ollama_available("qwen3.5:9b") is True


def test_available_false_when_not_pulled(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(
        {"models": [{"name": "llama3:8b"}]}))
    assert to.qwen_ollama_available("qwen3.5:9b") is False


def test_available_false_when_server_down(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(httpx, "get", boom)
    assert to.qwen_ollama_available("qwen3.5:9b") is False


def test_available_false_for_none():
    assert to.qwen_ollama_available(None) is False


def test_up_with_zero_models(monkeypatch):
    # 서버는 떠 있지만(200) pull된 모델이 하나도 없음 — 'down'과 구분돼야 한다
    # (_get_tags 통합의 핵심 이득: up=True인데 모델 목록은 빈 집합).
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse({"models": []}))
    assert to.ollama_running() is True
    assert to.qwen_ollama_available("qwen3.5:9b") is False


# ── OllamaTranslator.translate_batch ────────────────────────────────────────
async def test_translate_batch_parses_array(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen["url"] = url
        seen["model"] = json["model"]
        seen["think"] = json.get("think")
        seen["format"] = json.get("format")
        return FakeResponse({"response": '["안녕","잘 가"]'})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = await to.OllamaTranslator("qwen3.5:9b").translate_batch(["hello", "bye"])
    assert out == ["안녕", "잘 가"]
    assert seen["url"].endswith("/api/generate")
    assert seen["model"] == "qwen3.5:9b"
    # thinking OFF(빈 response 방지) + format:json 미사용(배열 계약 유지) — 2026-07-14 실측
    assert seen["think"] is False
    assert seen["format"] is None


async def test_translate_batch_object_wrapped_array(monkeypatch):
    # format:json 하에서 모델이 배열을 객체로 감싸도 _extract_json_array가 회수.
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(
        {"response": '{"result": ["가","나"]}'}))
    out = await to.OllamaTranslator("qwen3.5:9b").translate_batch(["a", "b"])
    assert out == ["가", "나"]


async def test_translate_batch_guard_keeps_source(monkeypatch):
    # 두 번째 줄은 한자(외국 문자) — 가드 불합격 → 원문 EN 유지.
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(
        {"response": '["안녕하세요","汉字漢字"]'}))
    out = await to.OllamaTranslator("qwen3.5:9b").translate_batch(["hello", "world"])
    assert out[0] == "안녕하세요"
    assert out[1] == "world"  # 가드가 원문으로 되돌림


async def test_translate_batch_count_mismatch_raises(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(
        {"response": '["하나"]'}))
    with pytest.raises(TranslationError):
        await to.OllamaTranslator("qwen3.5:9b").translate_batch(["a", "b", "c"])


async def test_translate_batch_http_error_raises(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(TranslationError):
        await to.OllamaTranslator("qwen3.5:9b").translate_batch(["a"])


async def test_translate_batch_empty_is_noop():
    assert await to.OllamaTranslator("qwen3.5:9b").translate_batch([]) == []


# ── create_translator 런타임 분기 (translate_cli) ───────────────────────────
def test_create_translator_falls_back_to_ollama(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda _id: False)
    monkeypatch.setattr(to, "qwen_ollama_available", lambda tag: True)
    t = tc.create_translator("qwen")
    assert isinstance(t, to.OllamaTranslator)


def test_create_translator_raises_when_no_runtime(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda _id: False)
    monkeypatch.setattr(to, "qwen_ollama_available", lambda tag: False)
    with pytest.raises(TranslationError):
        tc.create_translator("qwen_hifi")


# ── list_translate_engines: Ollama만 가용해도 qwen 활성, 라벨에 MLX 없음 ────
def test_engines_qwen_available_via_ollama(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda _id: False)
    monkeypatch.setattr(to, "qwen_ollama_available", lambda tag: True)
    engines = {e["value"]: e for e in tc.list_translate_engines()}
    for v in ("qwen", "qwen_lite", "qwen_hifi"):
        assert engines[v]["available"] is True
        assert "MLX" not in engines[v]["label"]
