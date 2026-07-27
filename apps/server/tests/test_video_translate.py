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


async def test_translate_segments_calls_async_progress_cb():
    segs = [SubSegment(seq=i, start_ms=i * 1000, end_ms=i * 1000 + 900, text=f"line {i}")
            for i in range(1, 8)]
    provider = FakeProvider()
    seen: list[float] = []

    async def progress_cb(frac: float) -> None:
        seen.append(frac)

    await tl.translate_segments(segs, provider, chunk_size=3, progress_cb=progress_cb)
    assert seen == pytest.approx([3 / 7, 6 / 7, 1.0])


async def test_translate_segments_recovers_from_count_mismatch():
    """LLM이 배치에서 한 줄을 누락(개수 불일치)해도 쪼개 재번역해 정렬을 복구한다."""
    class Dropper:
        async def translate_batch(self, texts):
            # >1줄이면 마지막 줄을 빠뜨려 개수를 어긋나게 (LLM 병합/누락 흉내)
            if len(texts) > 1:
                return [f"KO:{t}" for t in texts[:-1]]
            return [f"KO:{texts[0]}"]

    segs = [SubSegment(i, 0, 1, f"l{i}") for i in range(1, 5)]
    out = await tl.translate_segments(segs, Dropper(), chunk_size=4)
    assert [s.text for s in out] == ["KO:l1", "KO:l2", "KO:l3", "KO:l4"]


async def test_translate_segments_keeps_source_when_unrecoverable():
    """1줄까지 쪼개도 실패하면 그 줄은 원문 유지하고 작업은 중단 없이 완주한다."""
    class AlwaysError:
        async def translate_batch(self, texts):
            raise tl.TranslationError("boom")

    out = await tl.translate_segments(
        [SubSegment(1, 0, 1, "a"), SubSegment(2, 1, 2, "b")], AlwaysError())
    assert [s.text for s in out] == ["a", "b"]


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


def test_is_source_copy():
    """대상 선정용 — 번역기가 원문을 그대로 복사한 줄만 잡는다."""
    from apps.server.domain.video_captions.translate import is_source_copy

    src = "Margarita vibes, baby girl!"
    # 폴백 3경로는 전부 원문을 정확히 복사한다
    assert is_source_copy(src, src) is True
    assert is_source_copy(src, f"  {src}  ") is True  # 공백 차이 무시
    # 정상 번역
    assert is_source_copy(src, "마르가리타 분위기야, 자기!") is False
    # ★핵심 안전 속성: 사용자가 일부러 영문으로 남긴 편집은 대상이 아니다.
    # 여기서 True가 나오면 재번역이 사용자 작업을 지운다.
    assert is_source_copy(src, "Margarita mood, girl!") is False
    # 사용자가 의도적으로 비운 줄도 건드리지 않는다
    assert is_source_copy(src, "") is False
    assert is_source_copy(src, "   ") is False


def test_is_untranslated():
    """사후 확인용 — 재번역 결과가 여전히 영문인가(대상 선정에 쓰지 말 것)."""
    from apps.server.domain.video_captions.translate import is_untranslated

    src = "Margarita vibes, baby girl!"
    assert is_untranslated(src, src) is True
    assert is_untranslated(src, "마르가리타 분위기야, 자기!") is False
    # is_source_copy와 갈리는 지점 — 원문과 달라도 영어면 "아직 번역 안 됨"
    assert is_untranslated(src, "Margarita mood, girl!") is True
    assert is_untranslated(src, "") is False


async def test_maybe_aclose_translator():
    from apps.server.domain.video_captions.translate import maybe_aclose_translator

    closed = []

    class WithAclose:
        async def aclose(self):
            closed.append(True)

    class WithoutAclose:
        pass

    await maybe_aclose_translator(WithAclose())
    assert closed == [True]
    # aclose가 없는 번역기(gemini/CLI/apple)는 조용히 무시된다
    await maybe_aclose_translator(WithoutAclose())


async def test_resilient_translate_logs_the_reason(caplog):
    """번역이 원문으로 남을 때 '왜'가 로그에 남아야 한다.

    실기(윈도우): 306구간이 영문 그대로 나왔는데 서버 로그에 원인이 한 줄도
    없었다 — except TranslationError: pass 가 메시지를 버리고, 마지막 폴백
    로그도 원문만 찍었다. CLI가 설치돼 있어도(드롭다운 선택 가능) 로그인이
    안 됐거나 시간 초과면 같은 오류가 수백 번 반복되는데 아무도 모른다.
    """
    import logging

    from apps.server.domain.video_captions.translate import (
        TranslationError, _translate_resilient)

    class AlwaysFails:
        async def translate_batch(self, texts):
            raise TranslationError("'claude' CLI 로그인이 필요합니다")

    with caplog.at_level(logging.WARNING, logger="yeson.video.translate"):
        out = await _translate_resilient(AlwaysFails(), ["hello", "world"])

    assert out == ["hello", "world"], "실패해도 원문은 유지한다"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "로그인이 필요합니다" in joined, f"실패 원인이 로그에 없다:\n{joined}"
