from __future__ import annotations

import asyncio
import itertools
import logging

import pytest

from apps.server.ai.glossary import apply_ko_corrections
from apps.server.domain.pdf_translate.translate_blocks import (
    _normalize_quotes,
    _normalize_speaker_colon,
    _post_process,
    _verify_numbers,
    apply_output_normalization,
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
    """수작업본(납품 기준) 관례 실측: 화자 줄은 '화자명:대사'(공백 0)로,
    선행 큐 번호는 생략. 공백 표기(2026-07-30 E2E 후속 당시 잠정 규칙)는
    Task 19 전수 비교(1090쌍, 사람 127/127 붙임)로 되돌렸다 — few-shot·
    출력 정규화(apply_output_normalization)와 일관시킨다."""
    p = build_pdf_prompt(["3 HANK/EMPLOYEES Propane."])
    assert "화자명:대사" in p
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
    접두도 함께 복원돼 있어야 한다(화자줄 관례를 이 예시로도 시연).
    Task 19: 콜론 붙임(공백 0)으로 되돌린 형태로 확인한다."""
    p = build_pdf_prompt(["HANK walks."])
    assert "EN:" in p and "KO:" in p
    assert "바비:화살표가 이해되게 함께 서보자.." in p


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
    """통합 순서 고정(브리프 1번, Task 19로 확장): apply_house_style →
    출력 정규화(apply_output_normalization) → 숫자 게이트 — 실행 순서를
    스파이로 직접 관찰해 잠근다."""
    import apps.server.domain.pdf_translate.translate_blocks as tb

    order: list[str] = []
    orig_house = tb.apply_house_style

    def spy_house(ko):
        order.append("house")
        return orig_house(ko)

    orig_normalize = tb.apply_output_normalization

    def spy_normalize(ko):
        order.append("normalize")
        return orig_normalize(ko)

    orig_gate = tb._verify_and_fix_numbers

    async def spy_gate(provider, src, ko):
        order.append("gate")
        return await orig_gate(provider, src, ko)

    monkeypatch.setattr(tb, "apply_house_style", spy_house)
    monkeypatch.setattr(tb, "apply_output_normalization", spy_normalize)
    monkeypatch.setattr(tb, "_verify_and_fix_numbers", spy_gate)

    t = FakeTranslator([["태더튼, sc103 확인"]])
    await translate_texts(["Thatherton, confirm sc103"], t)
    assert order == ["house", "normalize", "gate"]


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


# ── 프롬프트 단언: 개행 보존·M.F. 예외 (Task 19) ─────────────────────────

def test_pdf_prompt_includes_newline_preservation_instruction():
    """Task 19: 슬러그라인 개행(추출 병합 단계, storyboard.py) 도입에 맞춰
    프롬프트에도 개행 보존 지시를 보조로 추가한다 — 주 기전은 추출 단계
    join이고 이 지시는 LLM이 스스로 개행을 옮기거나 지우지 않게 하는
    보조 방어선이다."""
    p = build_pdf_prompt(["INT. HOUSE - DAY\nHank walks in."])
    assert "Preserve" in p and "line breaks" in p


def test_pdf_prompt_includes_mf_abbreviation_exception():
    """Task 19: 전수 비교(1090쌍)에서 "M.F.?!"가 미번역으로 남은 n=1 사례
    ('56 HANK M.F.?! What are you doing here?' → 사람 '이런 젠장?!') —
    대문자 약어를 애셋 코드로 오인하지 말라는 예외 1문장."""
    p = build_pdf_prompt(["HANK M.F.?! What are you doing here?"])
    assert "M.F." in p
    assert "NOT asset codes" in p
    assert "expletives" in p


# ── 출력 하우스 정규화: 따옴표·콜론 (Task 19, 전수 1090쌍 실측) ──────────

def test_normalize_quotes_converts_bounded_span():
    assert _normalize_quotes("행크가 'Owner' 표시를 봤다") == '행크가 "Owner" 표시를 봤다'


def test_normalize_quotes_does_not_fire_on_english_possessive_apostrophe():
    """어포스트로피(영어 소유격 's)는 닫는 홑따옴표가 근방에 없으면
    스팬 패턴에 안 걸려야 한다 — 오폭 방지(브리프 명시 가드)."""
    text = "행크's 트럭이 도착했다"
    assert _normalize_quotes(text) == text


def test_normalize_quotes_ignores_spans_longer_than_bound():
    """40자를 넘는 홑따옴표 간격은 스팬으로 보지 않는다(길이 초과 가드 —
    아래 word-boundary 가드와는 별개 방어선)."""
    long_text = "'" + "가" * 41 + "'"
    assert _normalize_quotes(long_text) == long_text


# ── 따옴표 경계 가드 (리뷰 라운드 1 Important 2 재현, 라운드 2 정정) ─────
# 리뷰가 실행으로 재현한 오폭: 40자 안에 어포스트로피 두 개(둘 다 소유격)가
# 있으면 그 사이를 스팬으로 오인했다. 라운드 1은 여는/닫는 따옴표 양쪽에
# `\w`(유니코드 인식이라 한글 포함) 경계 조건을 추가해 고쳤지만, 그러면
# 닫는 따옴표 바로 뒤에 한글 조사가 붙는 정상 문형(예: "'쓰리 아미고스
# 경례'를")까지 "닫는 따옴표 아님"으로 오판해 정당한 변환의 36%를
# 놓쳤다(라운드 2 리뷰 실측: 코퍼스 34건 중 21건만 변환). 경계를
# 라틴 문자·숫자 전용(`(?<![A-Za-z0-9])'...'(?![A-Za-z])`)으로 좁혀 두
# 요구를 동시에 만족한다 — 아래는 그 수정을 실측대로 잠근다.

def test_normalize_quotes_does_not_misfire_on_two_possessives_within_bound():
    """소유격 어포스트로피가 40자 안에 두 번 있으면 예전엔 그 사이를
    스팬으로 오인했다 — 라틴 전용 경계로 여전히 막힌다(닫는 쪽 바로 뒤가
    영문 's'라 라틴 경계에 걸림)."""
    text = "행크's 트럭과 페기's 차"
    assert _normalize_quotes(text) == text
    text2 = "바비's 개와 행크's 트럭이 보인다"
    assert _normalize_quotes(text2) == text2


def test_normalize_quotes_does_not_misfire_on_year_apostrophe_before_possessive():
    """실제 코퍼스 형태(M.F. 예외 문장의 연호 표기 "'56 HANK M.F.?!")의
    선행 연도 아포스트로피 + 후행 소유격 조합 — 가공 케이스가 아니다."""
    text = "'56년 행크's 트럭"
    assert _normalize_quotes(text) == text


def test_normalize_quotes_converts_when_particle_attaches_after_closing_quote():
    """리뷰 라운드 2 Important 1 재현: 라운드 1의 `\\w` 경계는 닫는
    따옴표 뒤에 한글 조사가 바로 붙는 정상 문형까지 오판해 막았다(코퍼스
    실측: 34건 중 12건, 36% 손실 — 전부 조사 부착형). 라틴 전용 경계는
    한글 조사를 막지 않으므로 이 케이스들이 다시 변환돼야 한다."""
    assert (_normalize_quotes("'쓰리 아미고스 경례'를 외쳤다")
            == '"쓰리 아미고스 경례"를 외쳤다')
    assert _normalize_quotes("'프로너츠'도 맛있다") == '"프로너츠"도 맛있다'
    assert (_normalize_quotes("'저질스럽다'는 반응이었다")
            == '"저질스럽다"는 반응이었다')


def test_normalize_quotes_symmetric_boundary_rejects_digit_adjacency():
    """리뷰 라운드 3 Minor 재현: 라운드 2의 경계는 여는 쪽만 숫자를 받고
    닫는 쪽은 문자만 받아 비대칭이었다 — 닫는 따옴표 바로 뒤가 숫자면
    걸러지지 않아 두 형태가 오폭했다. 양쪽 다 `[A-Za-z0-9]`로 맞춰
    수정됐음을 잠근다."""
    assert _normalize_quotes("'56년과 '57년 사이") == "'56년과 '57년 사이"
    assert (_normalize_quotes("행크's 트럭 '90s 스타일")
            == "행크's 트럭 '90s 스타일")


def test_normalize_speaker_colon_removes_space_after_leading_colon():
    assert _normalize_speaker_colon("행크: 프로판.") == "행크:프로판."
    assert _normalize_speaker_colon("행크/직원들: 프로판.") == "행크/직원들:프로판."


def test_normalize_speaker_colon_only_touches_leading_colon():
    """첫 콜론만 대상 — 대사 중간의 콜론은 손대지 않는다."""
    text = "행크: 시간 확인해봐, 지금 몇 시야: 늦었나?"
    assert _normalize_speaker_colon(text) == "행크:시간 확인해봐, 지금 몇 시야: 늦었나?"


def test_normalize_speaker_colon_preserves_url_and_time_notation():
    """URL(콜론 뒤 공백 없음)·시각 표기(숫자는 문자 클래스에 없음)는
    선두에 와도 매치되지 않아야 한다(브리프 명시 가드)."""
    url_text = "http://example.com 참고하세요"
    assert _normalize_speaker_colon(url_text) == url_text
    time_text = "8:30에 만나자"
    assert _normalize_speaker_colon(time_text) == time_text


# ── 화자 콜론 좁히기 (리뷰 라운드 1 Fold-in) ──────────────────────────────

def test_normalize_speaker_colon_does_not_tighten_latin_prefixed_labels():
    """전수 실측(리뷰): 사람 쪽 붙임 콜론 717건 전부 라틴 문자 0개(순수
    한글 화자명) — "NOTE:"/"SFX:"/"PLEASE:" 같은 제작진 지시문·효과음
    라벨은 화자 줄이 아니므로 붙임 대상이 아니다."""
    assert (_normalize_speaker_colon("NOTE: 데스크를 SC13에 연결하세요")
            == "NOTE: 데스크를 SC13에 연결하세요")
    assert _normalize_speaker_colon("SFX: 문 여는 소리") == "SFX: 문 여는 소리"
    assert _normalize_speaker_colon("PLEASE: 확인하세요") == "PLEASE: 확인하세요"


def test_normalize_speaker_colon_does_not_swallow_newline_after_colon():
    """리뷰 재현: `\\s+`는 개행도 삼켜 "행크:\\n대사" → "행크:대사"로 이
    태스크가 도입한 개행 보존과 충돌했다 — `[^\\S\\r\\n]+`로 좁혀 콜론
    바로 뒤가 개행뿐이면(공백 없음) 건드리지 않는다. 리뷰 라운드 2
    Minor: 라운드 1의 `[^\\S\\n]+`는 `\\n`만 뺐지 `\\r`은 여전히
    삼켰다 — 사람 납품본이 실제로 쓰는 줄 구분자(`\\r`)라 함께 잠근다."""
    assert _normalize_speaker_colon("행크:\n대사입니다.") == "행크:\n대사입니다."
    assert _normalize_speaker_colon("행크:\r대사입니다.") == "행크:\r대사입니다."


def test_apply_output_normalization_combines_quotes_and_colon():
    assert (apply_output_normalization("행크: 'Owner' 표시를 봤다")
            == '행크:"Owner" 표시를 봤다')


def test_apply_output_normalization_skips_pure_english_fallback_text():
    """폴백 안전판: has_hangul(ko)이 False면(영문 원문 그대로인 폴백) 손대지
    않는다 — translate_texts의 "원문 유지 폴백" 식별은 폴백 값이 영문
    원문과 바이트 그대로 같다는 데 기댄다.

    리뷰 라운드 2 Important 2 재현: 예전 픽스처("NOTE: PLEASE HOOKUP DESK
    TO SC13")는 라운드 1 Fold-in 2가 콜론 패턴에서 A-Za-z를 뺀 뒤로는
    가드 없이도 두 규칙 중 어느 쪽도 발동하지 않는다 — 즉 has_hangul
    가드를 통째로 지워도 이 테스트는 여전히 통과해, 정작 지켜야 할
    안전판이 공허 테스트가 돼 있었다. 따옴표 규칙은 여전히 좌변에 한글
    요구가 없어 영문에도 발동하므로(콜론 패턴과 달리), 그 규칙이 실제로
    건드릴 문장으로 픽스처를 바꿔 가드를 다시 의미 있게 잠근다."""
    text = "A SIGN READS 'OWNER' ON THE DOOR"
    assert apply_output_normalization(text) == text
    # 가드가 없다면 이 값이 됐을 것 — 가드가 실제로 막고 있음을 대조 확인.
    assert _normalize_quotes(text) != text


def test_apply_output_normalization_empty_string():
    assert apply_output_normalization("") == ""


@pytest.mark.asyncio
async def test_translate_texts_applies_output_normalization_in_pipeline():
    """통합: translate_texts 전체 경로에서 따옴표·콜론 정규화가 실제로
    반영돼야 한다."""
    src = "3 HANK/EMPLOYEES Propane."
    t = FakeTranslator([["행크/직원들: 'Propane' 입니다."]])
    out = await translate_texts([src], t)
    assert out == ['행크/직원들:"Propane" 입니다.']


# ── FX 규칙 × 실물 개행 상호작용 (Task 19) ────────────────────────────────

@pytest.mark.asyncio
async def test_translate_texts_fx_rule_converts_across_real_multiline_action_block():
    """Task 18의 house_style FX 규칙은 개행을 세그먼트 경계로 미리 대비해
    뒀지만(가상 문자열 단위 테스트만 존재), Task 19가 실제로 개행 있는
    액션 블록(슬러그라인 join)을 만드는 첫 사례다 — translate_texts 전체
    파이프라인(dedupe→house_style→출력 정규화→숫자 게이트)을 통과한 뒤에도
    두 번째 줄의 FX 라벨이 정확히 변환되는지 실물 경로로 확인한다."""
    src = "INT. STRICKLAND PROPANE - SALES FLOOR - MORNING\nFX Fire in the corner."
    t = FakeTranslator([["스트릭랜드 프로판 내부-매장-아침\n이펙트 불 코너에서 발생."]])
    out = await translate_texts([src], t)
    assert out == ["스트릭랜드 프로판 내부-매장-아침\n불 코너에서 발생. 효과"]


# ── 후처리 체인의 "영문 불변" 계약 (전브랜치 리뷰 I-1) ────────────────────
# 브랜치 전체가 기대는 안전 성질: 번역 실패 폴백값(영문 원문)은 후처리를
# 통과해도 바이트 그대로 남는다. 그래야 translate_texts의 폴백 식별 →
# pdf_run의 kept_as_source 집계 → "모든 블록 번역에 실패했습니다"(effective
# == 0) 가드가 성립한다.
#
# 아래 테스트들이 잠그는 건 **동작이 아니라 전제**다. 기본 치환표만으로는
# 전제가 우연히 참이라(좌변이 전부 한글) 어떤 테스트도 깨지지 않았고, 그게
# I-1이 태스크별 리뷰 19라운드를 통과한 구조적 이유다. 그래서 전제를 깨는
# 입력 — 운영자가 콘솔(PUT /api/v1/glossary/{name})로 넣을 수 있는 **영문
# 좌변 오버라이드** — 을 실제로 심고 검증한다.

def test_post_process_leaves_english_untouched_even_with_english_lhs_override(
        tmp_path, monkeypatch):
    """운영자 오버라이드에 영문 좌변이 들어와도 _post_process는 한글 없는
    값을 바이트 그대로 돌려줘야 한다.

    첫 단언이 픽스처 자체의 유효성을 잠근다 — 오버라이드가 실제로 로드돼
    발동하지 않으면(경로·캐시·파싱 중 하나라도 어긋나면) 두 번째 단언은
    아무것도 증명하지 않는 공허한 테스트가 된다."""
    monkeypatch.setenv("YESON_GLOSSARY_KO_PATH",
                       str(tmp_path / "glossary_ko.txt"))
    (tmp_path / "glossary_ko.txt").write_text("props => 소품\n", encoding="utf-8")
    src = "NOTE: PLEASE MOVE THE props TO SC13"

    # 픽스처 유효성: 가드가 없으면 이 값이 실제로 오염된다
    assert apply_ko_corrections(src) == "NOTE: PLEASE MOVE THE 소품 TO SC13"
    # 계약: 후처리 체인 전체는 한글 없는 값을 건드리지 않는다
    assert _post_process(src) == src


def test_post_process_still_applies_full_chain_to_korean(tmp_path, monkeypatch):
    """가드가 정상 번역까지 막지 않는다는 반대편 잠금 — 한글이 있으면 세
    단계(ko교정·하우스표기·출력정규화)가 모두 걸린다. 이게 없으면 위
    테스트를 `return ko` 한 줄로도 통과시킬 수 있다."""
    monkeypatch.setenv("YESON_GLOSSARY_KO_PATH",
                       str(tmp_path / "glossary_ko.txt"))
    (tmp_path / "glossary_ko.txt").write_text("붐하워 => 붐하우어\n",
                                              encoding="utf-8")
    # 태더튼=하우스표기(house_style), 홑따옴표=출력정규화, 콜론 뒤 공백 제거
    assert _post_process("행크: 태더튼이 '프로판'을 판다") \
        == '행크:대더튼이 "프로판"을 판다'


@pytest.mark.asyncio
async def test_english_lhs_override_does_not_break_fallback_detection(
        tmp_path, monkeypatch):
    """통합: 영문 좌변 오버라이드가 심긴 상태에서 번역이 실패하면, 폴백값은
    여전히 원문과 바이트 그대로 같아야 한다(그래야 pdf_run이 실패로 센다).

    가드 이전에는 이 케이스가 'NOTE: PLEASE MOVE THE 소품 TO SC13'을
    돌려줘 폴백 식별이 False가 됐고, 번역 전량 실패가 'done'으로 납품됐다."""
    monkeypatch.setenv("YESON_GLOSSARY_KO_PATH",
                       str(tmp_path / "glossary_ko.txt"))
    (tmp_path / "glossary_ko.txt").write_text("props => 소품\n", encoding="utf-8")
    src = "NOTE: PLEASE MOVE THE props TO SC13"
    t = FakeTranslator([TranslationError("boom")])
    out = await translate_texts([src], t)
    assert out == [src]


@pytest.mark.asyncio
async def test_english_lhs_override_does_not_break_retranslation_fallback(
        tmp_path, monkeypatch):
    """같은 계약, 두 번째 후처리 호출부(_verify_and_fix_numbers의 재번역 결과).

    숫자 게이트가 unresolved라 재번역을 걸었는데 그 재번역마저 실패하면
    _resilient가 영문 원문을 돌려주고, 그 값이 이 경로에서 다시 후처리된다 —
    헬퍼를 _run_chunk 한쪽에만 쓰면 여기서만 가드가 빠져, 오염된 영문이
    숫자 검증을 'ok'로 통과해 **번역 성공**으로 납품된다."""
    monkeypatch.setenv("YESON_GLOSSARY_KO_PATH",
                       str(tmp_path / "glossary_ko.txt"))
    (tmp_path / "glossary_ko.txt").write_text("stage => 무대\n", encoding="utf-8")
    src = "Move sc103 near sc105 stage"
    t = FakeTranslator([
        ["씬109 근처로 무대를 옮겨주세요."],  # 1차: 모호 오염 → unresolved
        TranslationError("boom"),             # 재번역 실패 → 원문 그대로 반환
    ])
    out = await translate_texts([src], t)
    assert out == [src]
