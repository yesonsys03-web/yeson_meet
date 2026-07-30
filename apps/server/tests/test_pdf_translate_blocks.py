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


def test_pdf_prompt_includes_house_style_section():
    """Task 18: 사람 납품본 실측 하우스 표기 목록이 프롬프트에 EXACT 지시로
    포함돼야 한다(house_style.py의 HOUSE_KO_CORRECTIONS와 동일 근거)."""
    p = build_pdf_prompt(["Thatherton walks in."])
    assert "House-style renderings (use EXACTLY these Korean forms):" in p
    assert "Joseph=죠셉" in p
    assert "Boomhauer=붐하우어" in p
    assert "Thatherton=대더튼" in p
    assert "Ray Roy=레이로이" in p
    assert "Char King Especiale=챠 킹 에스페시알레" in p
    assert "FX=효과" in p
    assert "props=소품" in p
    assert "ANGLE ON:=구도:" in p
    assert "ESTABLISHING=설정" in p
    assert "Camera move=카메라 무브" in p
    assert "Cam Pos.=카메라 포즈" in p
    assert "NEW ART=뉴 아트" in p


def test_pdf_prompt_includes_register_consistency_instruction():
    """Task 18(P4): 화계 혼용(행크가 같은 상대에게 해요체/반말 혼용) 방지
    지시가 프롬프트에 있어야 한다."""
    p = build_pdf_prompt(["HANK speaks to an employee."])
    assert "Register (화계) consistency" in p
    assert "HANK speaks politely" in p
    assert "해요체/합쇼체" in p
    assert "never 반말/하게체 to them" in p


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


# ── 동일 원문 번역 캐시(dedupe, Task 18) ────────────────────────────────

@pytest.mark.asyncio
async def test_translate_texts_dedupes_repeated_source_text(caplog):
    """동일 원문 3회 포함 6입력 → 유니크 4개만 번역 호출되고, 출력 6개
    전부 올바른 자리에 동일 번역으로 채워진다."""
    texts = ["a", "b", "c", "a", "d", "a"]
    t = FakeTranslator(["echo-ko"])
    with caplog.at_level(logging.INFO, logger="yeson.pdf.translate"):
        out = await translate_texts(texts, t)
    assert out == ["KO:a", "KO:b", "KO:c", "KO:a", "KO:d", "KO:a"]
    assert len(t.calls) == 1
    assert t.calls[0] == ["a", "b", "c", "d"]  # 유니크 4개만 호출
    # 리뷰 지적(Minor): "4" in r.message는 "14"에도 걸리는 느슨한 단언 —
    # 정확한 문자열로 좁힌다.
    assert any("dedupe: 6→4 unique" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_translate_texts_dedupe_normalization_preserves_sentence_final_punctuation():
    """정규화 키 충돌 실측(브리프): '...this?'(의문으로 흐리는 조각)와
    'This...'(새로 여는 조각)는 문말 부호를 지우면 같은 키로 뭉쳐 서로
    다른 번역을 공유하게 된다 — 문말 부호(? ! . …)는 키에서 지우지 않아
    이 둘이 서로 다른 번역을 유지해야 한다."""
    a = "37 BOBBY (CONT.) ...this?"
    b = "37 BOBBY (CONT.) This..."
    t = FakeTranslator([["질문 번역", "새 문장 번역"]])
    out = await translate_texts([a, b], t)
    assert len(t.calls[0]) == 2  # dedupe로 뭉쳐지지 않음 — 별개 호출
    assert out == ["질문 번역", "새 문장 번역"]


@pytest.mark.asyncio
async def test_translate_texts_dedupe_with_house_style_and_number_gate():
    """dedupe + 숫자 게이트/하우스 치환 상호작용: 동일 원문 중복 위치 모두
    하우스 치환·숫자 게이트를 통과한 동일 최종 결과로 팬아웃돼야 한다."""
    src = "Thatherton, confirm sc103"
    t = FakeTranslator([["태더튼, 씬109 확인", "기타 번역"]])
    out = await translate_texts([src, "other text", src], t)
    assert t.calls == [[src, "other text"]]  # 유니크 2개만 호출
    assert out[0] == out[2] == "대더튼, 씬103 확인"  # 하우스 치환 + 숫자 교정
    assert out[1] == "기타 번역"


@pytest.mark.asyncio
async def test_translate_texts_applies_house_style_before_number_gate(monkeypatch):
    """통합 순서 고정(브리프 1번): house_style 적용은 apply_ko_corrections
    다음, 숫자 게이트 이전 — 실행 순서를 스파이로 직접 관찰해 잠근다."""
    import apps.server.domain.pdf_translate.translate_blocks as tb

    order: list[str] = []
    orig_house = tb.apply_house_style

    def spy_house(ko):
        order.append("house")
        return orig_house(ko)

    orig_gate = tb._verify_and_fix_numbers

    async def spy_gate(provider, src, ko):
        order.append("gate")
        return await orig_gate(provider, src, ko)

    monkeypatch.setattr(tb, "apply_house_style", spy_house)
    monkeypatch.setattr(tb, "_verify_and_fix_numbers", spy_gate)

    t = FakeTranslator([["태더튼, sc103 확인"]])
    await translate_texts(["Thatherton, confirm sc103"], t)
    assert order == ["house", "gate"]


# ── dedupe × 원문 유지 폴백 상호작용 (리뷰 Important 1) ──────────────────
# task-18-review.md: dedupe 정규화 키는 대소문자·구분자만 다른 서로 다른
# 원문을 하나로 묶을 수 있다("CAM ADJ"/"Cam-Adj" → 둘 다 "cam adj"). 이때
# 원문을 그대로 돌려주는 두 폴백 경로(_resilient 1블록 실패, 숫자 게이트
# 미해결)가 발동하면, 고치기 전에는 유니크 대표(첫 등장) 원문이 다른
# 위치로 새어 나갔다 — 아래 두 테스트가 각 경로를 잠근다.

@pytest.mark.asyncio
async def test_translate_texts_dedupe_collision_translation_failure_returns_own_source():
    """dedupe 키가 뭉친 두 원문(대소문자·구분자만 다름) 중 번역이 실패해
    원문 유지 폴백이 발동해도, 각 위치는 자기 자신의 원문을 받아야 한다 —
    유니크 대표의 원문이 다른 위치로 새면 안 된다."""
    texts = ["CAM ADJ", "Cam-Adj"]
    t = FakeTranslator([TranslationError("boom")])
    out = await translate_texts(texts, t)
    assert len(t.calls) == 1  # dedupe로 유니크 1개만 호출
    assert out == ["CAM ADJ", "Cam-Adj"]  # 각자 자기 원문 — 뒤섞이지 않음


@pytest.mark.asyncio
async def test_translate_texts_dedupe_collision_number_gate_unresolved_returns_own_source():
    """같은 리뷰 항목, 숫자 게이트 미해결 폴백 경로: 대소문자/구분자만
    다른 두 원문이 dedupe로 뭉쳤을 때, 숫자 오염이 재번역 후에도
    unresolved면(원문 유지) 각 위치가 자기 원문을 받아야 한다."""
    a = "Move sc103 near sc105 stage"
    b = "move-sc103 near-sc105 stage"
    t = FakeTranslator([
        ["씬109 근처로 무대를 옮겨주세요."],  # 1차: 모호 오염
        ["씬209 근처로 무대를 옮겨주세요."],  # 재번역도 모호 오염
    ])
    out = await translate_texts([a, b], t)
    assert len(t.calls) == 2  # dedupe(유니크 1개) + 재번역 1회
    assert out == [a, b]  # 각자 자기 원문 — 유니크 대표 원문이 새지 않음


@pytest.mark.asyncio
async def test_translate_texts_number_gate_retry_applies_house_style():
    """재번역 폴백 경로(_verify_and_fix_numbers 내부)에도 apply_house_style이
    걸려야 한다 — 정상 경로만 테스트하면 재번역으로 숫자가 해결된 블록만
    하우스 표기가 빠지는 비일관이 생길 수 있다(리뷰 Minor)."""
    src = "Thatherton, move sc103 near sc105"
    t = FakeTranslator([
        ["태더튼, 씬109 근처로."],  # 1차: 모호 오염(103/105 둘 다 길이3 후보)
        ["태더튼, 씬103 근처로."],  # 재번역: 숫자 해결 — 여기서 하우스치환 확인
    ])
    out = await translate_texts([src], t)
    assert out == ["대더튼, 씬103 근처로."]  # 재번역 결과에도 하우스 치환 적용
    assert len(t.calls) == 2


# ── props/prop 글로서리 모순 예외 (리뷰 Minor) ───────────────────────────

def test_pdf_prompt_notes_props_house_style_overrides_glossary():
    """리뷰 지적: _HOUSE_STYLE_BLOCK의 "props=소품" 지시 직후 glossary_block()
    이 "prop→프롭"을 덧붙여 프롬프트가 자기모순처럼 보인다 — 하우스 표기가
    우선한다는 예외 문장이 있어야 한다."""
    p = build_pdf_prompt(["a prop on the table"])
    assert "prop" in p and "소품" in p
    assert "takes precedence" in p
