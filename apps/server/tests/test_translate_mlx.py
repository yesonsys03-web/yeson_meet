from __future__ import annotations

import json

import pytest

from apps.server.domain.video_captions import translate_mlx as tm
from apps.server.domain.video_captions.translate import TranslationError


class _FakeClient:
    """MlxWorkerClient 시늉 — generate만 사용."""
    def __init__(self, *, model_id=None, reply="", start_error=False):
        self._reply = reply
        self._start_error = start_error
        self.model_id = model_id
        self.alive = False
        self.closed = False
        self.prompts: list[str] = []

    async def start(self):
        if self._start_error:
            from apps.server.ai.mlx_live_translate import MlxWorkerUnavailable
            raise MlxWorkerUnavailable("no model")
        self.alive = True

    async def generate(self, prompt, timeout):
        self.prompts.append(prompt)
        return self._reply

    async def close(self):
        self.closed = True
        self.alive = False


def _translator(reply="", start_error=False):
    def factory(*, model_id=None, **kw):
        return _FakeClient(model_id=model_id, reply=reply, start_error=start_error)
    return tm.QwenMlxTranslator("mlx-community/Qwen3.5-9B-4bit", client_factory=factory)


async def test_empty_returns_empty():
    out = await _translator().translate_batch([])
    assert out == []


async def test_batch_parses_json_array():
    t = _translator(reply='["안녕","잘 가"]')
    out = await t.translate_batch(["hello", "goodbye"])
    assert out == ["안녕", "잘 가"]
    await t.aclose()


async def test_worker_reused_across_calls():
    t = _translator(reply='["가"]')
    await t.translate_batch(["a"])
    client_after_first = t._client
    await t.translate_batch(["b"])
    assert t._client is client_after_first  # 재기동 없음


async def test_unparseable_raises_translation_error():
    t = _translator(reply="not json at all")
    with pytest.raises(TranslationError):
        await t.translate_batch(["hello"])


async def test_count_mismatch_raises_translation_error():
    t = _translator(reply='["one"]')
    with pytest.raises(TranslationError):
        await t.translate_batch(["a", "b"])


async def test_start_failure_raises_translation_error():
    t = _translator(start_error=True)
    with pytest.raises(TranslationError):
        await t.translate_batch(["hello"])


async def test_start_oserror_falls_back_to_translation_error():
    """client.start()이 MlxWorkerUnavailable 이외(예: OSError=spawn 실패)를 던져도
    TranslationError로 감싸져야 한다 — 원문 유지 폴백 계약 유지(하드 실패 금지)."""
    class _CrashingClient:
        def __init__(self, *, model_id=None, **kw):
            self.model_id = model_id

        async def start(self):
            raise OSError("spawn failed")

    t = tm.QwenMlxTranslator(
        "mlx-community/Qwen3.5-9B-4bit", client_factory=_CrashingClient,
    )
    with pytest.raises(TranslationError):
        await t.translate_batch(["hello"])


async def test_guard_reject_keeps_source():
    # 반복 환각(같은 10자+ 구절 3회) → guard 리젝트 → 원문(EN) 유지
    bad = "가나다라마바사아자차" * 3
    t = _translator(reply=json.dumps([bad], ensure_ascii=False))
    out = await t.translate_batch(["source english"])
    assert out == ["source english"]


def test_available_gating(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: True)
    monkeypatch.setattr(tm, "mlx_model_installed", lambda mid: True)
    assert tm.qwen_mlx_available("mlx-community/Qwen3.5-9B-4bit") is True
    monkeypatch.setattr(tm, "mlx_model_installed", lambda mid: False)
    assert tm.qwen_mlx_available("mlx-community/Qwen3.5-9B-4bit") is False
    monkeypatch.setattr(tm, "mlx_model_installed", lambda mid: True)
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    assert tm.qwen_mlx_available("mlx-community/Qwen3.5-9B-4bit") is False
