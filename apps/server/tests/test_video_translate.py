from __future__ import annotations

import json

import pytest

from apps.server.domain.video_captions.srt import SubSegment
from apps.server.domain.video_captions import translate as tl


class FakeProvider:
    def __init__(self):
        self.batches: list[list[str]] = []

    async def translate_batch(self, texts):
        self.batches.append(list(texts))
        return [f"KO:{t}" for t in texts]


async def test_translate_segments_chunks_and_replaces_text():
    segs = [SubSegment(seq=i, start_ms=i * 1000, end_ms=i * 1000 + 900, text=f"line {i}")
            for i in range(1, 8)]
    provider = FakeProvider()
    out = await tl.translate_segments(segs, provider, chunk_size=3)
    assert [len(b) for b in provider.batches] == [3, 3, 1]
    assert out[0].text == "KO:line 1"
    assert out[0].start_ms == 1000  # 타이밍 보존
    assert segs[0].text == "line 1"  # 원본 불변


async def test_length_mismatch_raises():
    class Bad:
        async def translate_batch(self, texts):
            return ["only one"]

    with pytest.raises(tl.TranslationError):
        await tl.translate_segments(
            [SubSegment(1, 0, 1, "a"), SubSegment(2, 1, 2, "b")], Bad())


async def test_gemini_translator_parses_json_array(monkeypatch):
    captured = {}

    async def fake_generate(self, prompt):
        captured["prompt"] = prompt
        return json.dumps(["안녕", "잘 가"])

    monkeypatch.setattr(tl.GeminiFlashTranslator, "_generate", fake_generate)
    t = tl.GeminiFlashTranslator(api_key="k")
    out = await t.translate_batch(["hello", "goodbye"])
    assert out == ["안녕", "잘 가"]
    assert "glossary" in captured["prompt"].lower() or "용어" in captured["prompt"]


async def test_gemini_translator_rejects_bad_json(monkeypatch):
    async def fake_generate(self, prompt):
        return "not json"

    monkeypatch.setattr(tl.GeminiFlashTranslator, "_generate", fake_generate)
    with pytest.raises(tl.TranslationError):
        await tl.GeminiFlashTranslator(api_key="k").translate_batch(["hello"])
