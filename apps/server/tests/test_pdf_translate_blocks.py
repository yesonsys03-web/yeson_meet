from __future__ import annotations

import asyncio
import itertools

import pytest

from apps.server.domain.pdf_translate.translate_blocks import (
    build_pdf_prompt,
    translate_texts,
)
from apps.server.domain.video_captions.translate import TranslationError


class FakeTranslator:
    def __init__(self, script):
        self.script = list(script)  # 호출별 반환값 또는 예외
        self.calls = []

    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        if action == "echo-ko":
            return [f"KO:{t}" for t in texts]
        return action


def test_pdf_prompt_mentions_production_not_subtitles():
    p = build_pdf_prompt(["HANK walks."])
    assert "subtitle" not in p
    assert "JSON array" in p
    assert "HANK walks." in p


def test_pdf_prompt_mentions_panel_callout_convention():
    """패널 콜아웃 라벨(빨강 OCR) 지시 실측(Task 14): 단어부 번역+코드부
    유지, 순수 코드는 원문 복사(스킵 규칙과 연동)."""
    p = build_pdf_prompt(["HANK'S TRUCK"])
    assert "Panel callout labels" in p
    assert "CAR006A" in p and "차006A" in p


def test_pdf_prompt_mentions_speaker_line_convention():
    """수작업본(납품 기준) 관례 실측: 화자 줄은 '화자명: 대사'로,
    선행 큐 번호는 생략(2026-07-30 E2E 후속)."""
    p = build_pdf_prompt(["3 HANK/EMPLOYEES Propane."])
    assert "화자명: 대사" in p
    assert "cue number" in p


@pytest.mark.asyncio
async def test_translate_texts_happy_path():
    t = FakeTranslator(["echo-ko"])
    out = await translate_texts(["a", "b"], t)
    assert out == ["KO:a", "KO:b"]


@pytest.mark.asyncio
async def test_translate_texts_bisects_on_count_mismatch():
    # 1차: 2줄 요청에 1줄 반환(불일치) → 반으로 쪼개 재시도
    t = FakeTranslator([["하나"], ["A번역"], ["B번역"]])
    out = await translate_texts(["a", "b"], t)
    assert out == ["A번역", "B번역"]
    assert t.calls == [["a", "b"], ["a"], ["b"]]


@pytest.mark.asyncio
async def test_translate_texts_keeps_source_on_single_failure():
    t = FakeTranslator([TranslationError("boom"), TranslationError("boom")])
    out = await translate_texts(["a"], t)
    assert out == ["a"]  # 원문 유지 폴백 (is_source_copy 규약과 동일)


@pytest.mark.asyncio
async def test_progress_cb_called():
    fracs = []

    async def cb(f):
        fracs.append(f)

    t = FakeTranslator(["echo-ko", "echo-ko"])
    await translate_texts([str(i) for i in range(60)], t, chunk_size=50,
                          progress_cb=cb)
    assert fracs == [50 / 60, 1.0]


class SlowFirstChunkTranslator:
    """청크 병렬화 테스트용 — 첫 청크(원소 "0"으로 시작)만 느리게 완료돼,
    다른 청크가 먼저 끝나도 결과 순서는 입력 순서 그대로여야 함을 검증."""

    def __init__(self):
        self.calls: list[list[str]] = []

    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        if texts[0] == "0":
            await asyncio.sleep(0.05)
        else:
            await asyncio.sleep(0)
        return [f"KO:{t}" for t in texts]


class ConcurrencyTrackingTranslator:
    """동시 실행 중인 translate_batch 호출 수를 추적 — 실제로 청크가
    병렬 실행되는지(순차 루프가 아닌지) 직접 관찰하기 위한 계측용."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._lock = asyncio.Lock()

    async def translate_batch(self, texts):
        async with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        async with self._lock:
            self.active -= 1
        return [f"KO:{t}" for t in texts]


@pytest.mark.asyncio
async def test_translate_texts_runs_chunks_concurrently(monkeypatch):
    """청크 병렬화 핵심 계약: workers>=2면 청크가 실제로 동시에 실행돼야
    한다(순차 for 루프였다면 max_active는 항상 1)."""
    monkeypatch.setenv("YESON_PDF_TRANSLATE_WORKERS", "3")
    t = ConcurrencyTrackingTranslator()
    texts = [str(i) for i in range(150)]  # chunk_size 50 → 3청크
    await translate_texts(texts, t, chunk_size=50)
    assert t.max_active >= 2


@pytest.mark.asyncio
async def test_translate_texts_preserves_order_when_chunks_finish_out_of_order(
        monkeypatch):
    """3청크(120텍스트, chunk 50) 중 첫 청크가 가장 느리게 끝나도, 반환
    순서는 입력 순서 그대로여야 한다(청크 인덱스로 재조립)."""
    monkeypatch.setenv("YESON_PDF_TRANSLATE_WORKERS", "3")
    t = SlowFirstChunkTranslator()
    texts = [str(i) for i in range(120)]
    out = await translate_texts(texts, t, chunk_size=50)
    assert out == [f"KO:{i}" for i in range(120)]
    assert len(t.calls) == 3


@pytest.mark.asyncio
async def test_translate_texts_progress_monotonic_even_out_of_order(monkeypatch):
    """진행률은 완료 청크 블록 수 누적이라 완료 순서와 무관하게 단조
    증가해야 하고 최종값은 1.0이어야 한다."""
    monkeypatch.setenv("YESON_PDF_TRANSLATE_WORKERS", "3")
    t = SlowFirstChunkTranslator()
    texts = [str(i) for i in range(120)]
    fracs: list[float] = []

    async def cb(f):
        fracs.append(f)

    await translate_texts(texts, t, chunk_size=50, progress_cb=cb)
    assert len(fracs) == 3
    assert all(a <= b for a, b in itertools.pairwise(fracs))
    assert fracs[-1] == 1.0


@pytest.mark.asyncio
async def test_translate_texts_workers_one_calls_chunks_in_submission_order(
        monkeypatch):
    """workers=1이면(기존 직렬과 동일 동작) 첫 청크가 느려도 CLI 호출
    순서는 청크 제출 순서를 그대로 따른다."""
    monkeypatch.setenv("YESON_PDF_TRANSLATE_WORKERS", "1")
    t = SlowFirstChunkTranslator()
    texts = [str(i) for i in range(120)]
    await translate_texts(texts, t, chunk_size=50)
    assert t.calls == [
        [str(i) for i in range(50)],
        [str(i) for i in range(50, 100)],
        [str(i) for i in range(100, 120)],
    ]


@pytest.mark.asyncio
async def test_translate_texts_cancellation_propagates_and_leaves_no_orphan_tasks(
        monkeypatch):
    """progress_cb가 CancelledError를 던지면(러너의 세대 가드 흉내) 전체
    gather가 취소되고, 아직 실행 중이던 청크 태스크도 정리돼야 한다."""
    monkeypatch.setenv("YESON_PDF_TRANSLATE_WORKERS", "3")

    class DelayedTranslator:
        def __init__(self):
            self.calls: list[list[str]] = []

        async def translate_batch(self, texts):
            self.calls.append(list(texts))
            if texts[0] == "100":
                await asyncio.sleep(5)  # 취소되지 않으면 테스트가 타임아웃
            else:
                await asyncio.sleep(0)
            return [f"KO:{t}" for t in texts]

    progress_calls: list[float] = []

    async def cb(f):
        progress_calls.append(f)
        if len(progress_calls) == 2:
            raise asyncio.CancelledError

    t = DelayedTranslator()
    texts = [str(i) for i in range(120)]  # 청크: 0-49, 50-99, 100-119(느림)
    current = asyncio.current_task()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            translate_texts(texts, t, chunk_size=50, progress_cb=cb), timeout=2.0)
    pending = [tk for tk in asyncio.all_tasks() if tk is not current and not tk.done()]
    assert pending == []
