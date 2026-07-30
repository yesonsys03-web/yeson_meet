"""하우스 표기 강제 치환 단위 테스트 — Task 18.

HOUSE_KO_CORRECTIONS 각 항목은 사람 납품본 실측(1090쌍, 2026-07-30)으로
치환 방향이 검증됐다(브리프 표 참고). 여기서는 (1) 각 치환이 실제로
일어나는지, (2) 멱등성, (3) "프로판"/"프로너츠" 같은 무관 문자열이 오폭으로
바뀌지 않는지, (4) 적용 순서가 결과에 영향을 주지 않는지를 잠근다.

리뷰 후속(2026-07-30, 라운드 1): "이펙트 연기"/"연기 이펙트" 리터럴 2항목은
HOUSE_KO_PATTERN_CORRECTIONS(FX 정규식 규칙)로 교체됐다 — 아래 "FX 규칙"
절이 그 실제 동작(세그먼트 경계, 순서 고정, 오폭 가드)을 실측대로 잠근다.
"""
from __future__ import annotations

import pytest

from apps.server.domain.pdf_translate.house_style import (
    HOUSE_KO_CORRECTIONS,
    apply_house_style,
)

# ── 개별 치환 케이스 (브리프 표 그대로 — 13항목, 이펙트 2항목은 FX 규칙으로 이전) ──

CASES = [
    ("조셉이 대답했다.", "죠셉이 대답했다."),
    ("붐하워가 웃었다.", "붐하우어가 웃었다."),
    ("새더튼 씨가 들어왔다.", "대더튼 씨가 들어왔다."),
    ("태더튼 씨가 들어왔다.", "대더튼 씨가 들어왔다."),
    ("레이 로이가 도착했다.", "레이로이가 도착했다."),
    ("차 킹 에스페시알레", "챠 킹 에스페시알레"),
    ("효과음: 문 여는 소리", "효과: 문 여는 소리"),
    ("프롭 준비 완료", "소품 준비 완료"),
    ("앵글 온: 행크", "구도: 행크"),
    ("설정 샷 1", "설정 1"),
    ("카메라 이동", "카메라 무브"),
    ("카메라 위치 조정", "카메라 포즈 조정"),
    ("새 아트로 교체", "뉴 아트로 교체"),
]


@pytest.mark.parametrize("before,after", CASES)
def test_house_style_substitution(before, after):
    assert apply_house_style(before) == after


def test_house_style_all_table_entries_covered():
    """CASES가 HOUSE_KO_CORRECTIONS의 모든 항목을 실제로 발동시키는지
    확인 — 표에 항목을 추가하고 테스트를 빼먹는 사고를 방지."""
    exercised = {wrong for before, _ in CASES for wrong, _ in HOUSE_KO_CORRECTIONS
                 if wrong in before}
    all_wrongs = {wrong for wrong, _ in HOUSE_KO_CORRECTIONS}
    assert exercised == all_wrongs


# ── 멱등성 ──────────────────────────────────────────────────────────────

def test_house_style_is_idempotent():
    text = ("조셉과 붐하워, 새더튼 씨, 태더튼 씨, 레이 로이, 차 킹이 모였다. "
            "효과음:, 프롭, 앵글 온:, 설정 샷, 카메라 이동, "
            "카메라 위치, 새 아트도 함께.")
    once = apply_house_style(text)
    twice = apply_house_style(once)
    assert once == twice


@pytest.mark.parametrize("before,after", CASES)
def test_house_style_idempotent_per_case(before, after):
    once = apply_house_style(before)
    assert apply_house_style(once) == once
    assert once == after


# ── 적용 순서 무관(새더튼/태더튼 둘 다 대더튼으로) ───────────────────────

def test_house_style_thatherton_both_forms_converge():
    """("새더튼"→"대더튼")과 ("태더튼"→"대더튼")의 표 내 순서와 무관하게
    두 오표기 모두 같은 결과로 수렴해야 한다."""
    assert apply_house_style("새더튼") == "대더튼"
    assert apply_house_style("태더튼") == "대더튼"
    assert apply_house_style("새더튼과 태더튼") == "대더튼과 대더튼"


def test_house_style_order_independent_when_reversed():
    """치환 목록 순서를 뒤집어도(리스트를 직접 뒤집어 재적용) 결과가
    같아야 함을 확인한다. ⚠ 범위: 이 성질은 HOUSE_KO_CORRECTIONS(리터럴
    13항목)에 한정된다 — 리뷰(task-18-review.md Important 2)가 지적한
    대로, 예전에 여기 있던 "이펙트 연기"/"연기 이펙트" 리터럴 2항목은
    서로 겹쳐 순서에 따라 결과가 달랐다(그 두 항목은 이제
    HOUSE_KO_PATTERN_CORRECTIONS로 옮겨졌고, 그쪽의 순서 의존성은 아래
    "FX 규칙" 절의 `test_house_style_fx_rule_order_is_fixed_and_locked`가
    실측대로 별도로 잠근다 — 이 테스트가 그 성질까지 보증하지 않는다)."""
    def apply_reversed(ko: str) -> str:
        for wrong, right in reversed(HOUSE_KO_CORRECTIONS):
            if wrong in ko:
                ko = ko.replace(wrong, right)
        return ko

    text = ("새더튼과 태더튼, 조셉과 붐하워, 레이 로이, 차 킹, 프롭과 효과음:, "
            "앵글 온:, 설정 샷, 카메라 이동, 카메라 위치, 새 아트")
    assert apply_house_style(text) == apply_reversed(text)


# ── 오폭 방지("프롭"→"소품"이 "프로판"/"프로너츠"를 건드리면 안 됨) ──────

@pytest.mark.parametrize("text", ["프로판", "프로너츠", "프로판과 부탄", "이 프로너츠는 별로야"])
def test_house_style_does_not_false_positive_on_propane_or_pronuts(text):
    assert apply_house_style(text) == text


def test_house_style_empty_input():
    assert apply_house_style("") == ""


# ── FX 규칙(정규식, 리뷰 Important 2) ────────────────────────────────────
# "이펙트 연기"/"연기 이펙트" 리터럴 2항목은 전수 1090쌍 재검증에서 실측
# 20건 중 6건만 잡는 것으로 드러나(팀 리드 측정), HOUSE_KO_PATTERN_CORRECTIONS
# (앞→뒤 정규식 2개)로 교체됐다. 아래는 실물 코퍼스의 네 가지 실제 모양 +
# 오폭 가드 + 순서 고정을 실측대로 잠근다(전부 apply_house_style을 직접
# 실행해 확인한 값 — 손으로 추정한 값이 아니다).

FX_CASES = [
    ("불 이펙트", "불 효과"),                      # FX Fire — trailing form
    ("이펙트 연기", "연기 효과"),                    # FX Smoke — leading form
    ("연기 이펙트", "연기 효과"),                    # Smoke FX — trailing form
    ("이펙트 카메라 플래시", "카메라 플래시 효과"),      # FX Camera Flash — leading, 다중 단어
]


@pytest.mark.parametrize("before,after", FX_CASES)
def test_house_style_fx_rule_standalone(before, after):
    assert apply_house_style(before) == after


@pytest.mark.parametrize("before,after", FX_CASES)
def test_house_style_fx_rule_standalone_is_idempotent(before, after):
    once = apply_house_style(before)
    assert once == after
    assert apply_house_style(once) == once


def test_house_style_fx_rule_embedded_with_slash_delimiter():
    """실물 코퍼스(전수 1090쌍): "FX Fire"가 다른 액션노트와 " / "로 병합된
    블록에서도 규칙이 발동해야 한다 — 전체 문자열 시작/끝에만 앵커링하면
    (팀 리드가 제시한 `^이펙트 (.+)$`/`(.+) 이펙트$` 그대로는) 이 경우를
    놓친다(실측으로 확인, apply_house_style은 " / "도 세그먼트 경계로
    인정해 잡는다)."""
    before = ("예전 고객들이 돌아다니며 제품을 살펴본다. 품질에 진심으로 "
              "감탄한 모습이다. / 불 이펙트 / 팔 위치가 일치하도록 인시덴털 "
              "#2000을 이전 sc47에 훅업해 주세요.")
    after = ("예전 고객들이 돌아다니며 제품을 살펴본다. 품질에 진심으로 "
             "감탄한 모습이다. / 불 효과 / 팔 위치가 일치하도록 인시덴털 "
             "#2000을 이전 sc47에 훅업해 주세요.")
    assert apply_house_style(before) == after


def test_house_style_fx_rule_embedded_after_sentence_period():
    """실물 코퍼스: "A long beat. FX Smoke"처럼 앞 문장 마침표 뒤에 곧장
    붙는 블록에서도 규칙이 발동해야 한다(". "도 세그먼트 경계로 인정)."""
    assert apply_house_style("긴 정적이 흐른다. 이펙트 연기") == "긴 정적이 흐른다. 연기 효과"


def test_house_style_fx_rule_guards_bare_effect_with_no_adjacent_word():
    """"이펙트" 혼자만 있고 앞뒤에 붙을 단어가 없으면 손대지 않는다 —
    과잉치환 방지 가드."""
    assert apply_house_style("이펙트") == "이펙트"


def test_house_style_fx_rule_leaves_single_interior_occurrence_untouched():
    """"이펙트"가 문장 중간에 단독으로 끼고(양옆 다 일반 단어, 슬래시·
    마침표 경계 없음) 세그먼트 경계 신호가 없으면 건드리지 않는다 —
    오폭 방지(실측: 문자열 전체가 바뀌지 않는다)."""
    assert apply_house_style("연기 이펙트 연기") == "연기 이펙트 연기"


def test_house_style_fx_rule_order_is_fixed_and_locked():
    """FX 규칙 두 개(앞→뒤 고정)의 적용 순서가 실제로 결과를 바꾸는 이론적
    edge case(한 문자열에 "이펙트"가 두 번)를 실측값 그대로 잠근다 —
    실물 코퍼스엔 이런 이중 출현이 없지만, 순서를 바꾸는 사람이 이 테스트로
    변화를 알아채야 한다(리뷰 Important 2가 지적한 "코드가 갖지 않은 성질을
    테스트가 보증"하는 실수를 반복하지 않기 위해, "순서 무관"이 아니라
    "이 순서에서는 이 값"이라고 명시적으로 단언한다)."""
    assert apply_house_style("이펙트 연기 이펙트") == "연기 이펙트 효과"
