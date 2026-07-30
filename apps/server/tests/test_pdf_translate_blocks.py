from __future__ import annotations

import asyncio
import itertools
import logging

import pytest

from apps.server.domain.pdf_translate.translate_blocks import (
    _verify_numbers,
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


def test_pdf_prompt_forbids_word_for_word_literalism():
    """Task 15 E2E 후속: 직역 금지 지시 실측(사용자 리포트 — "그래야 화살표가
    말이 되지!" 같은 축자번역 대신 의역을 요구)."""
    p = build_pdf_prompt(["HANK walks."])
    assert "word-for-word" in p


def test_pdf_prompt_mentions_context_instruction():
    """Task 15: 배열 내 이웃 블록(같은 에피소드 연속 스토리보드)을 대명사·
    (CONT.) 이어붙임·미완성 문장 해석의 문맥으로 쓰라는 지시가 있어야 한다."""
    p = build_pdf_prompt(["HANK walks."])
    assert "neighboring items" in p
    assert "context" in p


def test_pdf_prompt_includes_style_examples():
    """Task 15: 수작업본(납품 기준)에서 큐레이션한 few-shot 예시가 프롬프트에
    포함돼야 한다 — 사용자가 직접 지목한 '화살표' 쌍(GABE01 A1 p963-964)
    포함 확인. 리뷰 후속(2026-07-30): 원문 주석에 실재하던 '바비:' 화자
    접두도 함께 복원돼 있어야 한다(화자줄 관례를 이 예시로도 시연)."""
    p = build_pdf_prompt(["HANK walks."])
    assert "EN:" in p and "KO:" in p
    assert "바비: 화살표가 이해되게 함께 서보자.." in p


def test_pdf_prompt_examples_disclose_merged_length_contract():
    """리뷰 후속(2026-07-30, Finding 2): 일부 few-shot 예시는 연속 패널의
    텍스트를 문맥용으로 결합해 보여준다 — 이게 "출력을 병합/분할해도 된다"는
    뜻으로 오독되지 않게, 입력 배열 항목당 정확히 1개 번역이라는 계약을
    명시하는 문장이 있어야 한다."""
    p = build_pdf_prompt(["HANK walks."])
    assert "exactly one translation per input array item" in p
    assert "never merge or split items" in p


def test_pdf_prompt_includes_digit_preservation_instruction():
    """Task 16: 사용자 실기 오류 신고(103→109 오염) 대응 — 프롬프트가 숫자열을
    있는 그대로 베끼라고 명시해야 한다(코드 레벨 게이트는 보완재일 뿐,
    확률을 낮추는 프롬프트 지시가 1차 방어선)."""
    p = build_pdf_prompt(["match sc103."])
    assert "Copy every digit sequence" in p
    assert "never alter, swap, or invent digits" in p


# ── _verify_numbers 단위 테스트 (Task 16) ──────────────────────────────

def test_verify_numbers_fixes_single_candidate_contamination():
    """사용자 리포트 원본 사례: 103→109 오염이 정확히 103으로 치환된다."""
    src = "Please hook up Bobby's screen to match sc103."
    ko = "바비의 화면을 씬109에 맞춰 훅업해주세요."
    fixed, verdict = _verify_numbers(src, ko)
    assert verdict == "fixed"
    assert "103" in fixed
    assert "109" not in fixed


def test_verify_numbers_allows_intentional_cue_number_drop():
    """화자줄 관례가 선행 큐 번호를 의도적으로 생략 — 누락 단독은 오류가 아니다."""
    src = "3 HANK/EMPLOYEES Propane."
    ko = "행크: 프로판."
    fixed, verdict = _verify_numbers(src, ko)
    assert verdict == "ok"
    assert fixed == ko


def test_verify_numbers_allows_spelled_out_number_translation():
    """"two trucks"→"트럭 2대"는 정당한 변환 — 같은 자릿수 소스측 누락이
    없으므로(소스에 숫자 자체가 없음) 오탐하지 않는다."""
    src = "two trucks"
    ko = "트럭 2대"
    fixed, verdict = _verify_numbers(src, ko)
    assert verdict == "ok"
    assert fixed == ko


def test_verify_numbers_ambiguous_candidates_are_unresolved():
    """같은 자릿수 누락 후보가 2개 이상이면 자동 치환하지 않고 unresolved."""
    src = "Move sc103 near sc105 stage."
    ko = "씬109 근처로 무대를 옮겨주세요."
    fixed, verdict = _verify_numbers(src, ko)
    assert verdict == "unresolved"
    assert fixed == ko  # 자동 치환 금지 — 원문 그대로 반환


def test_verify_numbers_replaces_all_occurrences_of_repeated_foreign():
    """foreign 숫자열이 KO에 2회 이상 나오면 전부 동일하게 치환한다."""
    src = "Match sc103 to sc103 again."
    ko = "씬109에 다시 씬109를 맞춰주세요."
    fixed, verdict = _verify_numbers(src, ko)
    assert verdict == "fixed"
    assert fixed == "씬103에 다시 씬103를 맞춰주세요."
    assert "109" not in fixed


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


# ── translate_texts × 숫자 보존 게이트 통합 (Task 16) ────────────────────

@pytest.mark.asyncio
async def test_translate_texts_number_gate_fixes_without_retranslation():
    """단일 후보 오염은 재번역 없이 그 자리에서 치환돼 채택된다."""
    src = "Please hook up Bobby's screen to match sc103."
    t = FakeTranslator([["바비의 화면을 씬109에 맞춰 훅업해주세요."]])
    out = await translate_texts([src], t)
    assert out == ["바비의 화면을 씬103에 맞춰 훅업해주세요."]
    assert len(t.calls) == 1  # 재번역 호출 없음


@pytest.mark.asyncio
async def test_translate_texts_number_gate_retranslates_once_on_unresolved():
    """모호한 오염(unresolved)은 블록 단건 재번역을 1회 시도하고, 재번역이
    깨끗하면 그 결과를 채택한다."""
    src = "Move sc103 near sc105 stage."
    t = FakeTranslator([
        ["씬109 근처로 무대를 옮겨주세요."],  # 1차: 모호 오염(후보 2개)
        ["씬103 근처로 무대를 옮겨주세요."],  # 재번역(단건): 정상
    ])
    out = await translate_texts([src], t)
    assert out == ["씬103 근처로 무대를 옮겨주세요."]
    assert len(t.calls) == 2
    assert t.calls[0] == [src]
    assert t.calls[1] == [src]


@pytest.mark.asyncio
async def test_translate_texts_number_gate_keeps_source_when_still_contaminated(
        caplog):
    """재번역도 여전히 오염되면(unresolved 재현) 원문 유지 폴백으로 합류하고
    경고를 로그로 남긴다."""
    src = "Move sc103 near sc105 stage."
    t = FakeTranslator([
        ["씬109 근처로 무대를 옮겨주세요."],  # 1차: 모호 오염
        ["씬209 근처로 무대를 옮겨주세요."],  # 재번역도 모호 오염
    ])
    with caplog.at_level(logging.WARNING, logger="yeson.pdf.translate"):
        out = await translate_texts([src], t)
    assert out == [src]  # 원문 유지 폴백
    assert len(t.calls) == 2
    assert any("숫자" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_translate_texts_number_gate_logs_info_on_fix(caplog):
    """자동 치환(fixed) 시 원문/오염/교정 값을 info 로그로 남긴다."""
    src = "Please hook up Bobby's screen to match sc103."
    t = FakeTranslator([["바비의 화면을 씬109에 맞춰 훅업해주세요."]])
    with caplog.at_level(logging.INFO, logger="yeson.pdf.translate"):
        await translate_texts([src], t)
    assert any("109" in r.message and "103" in r.message for r in caplog.records)
