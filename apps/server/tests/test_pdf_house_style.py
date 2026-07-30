"""하우스 표기 강제 치환 단위 테스트 — Task 18.

HOUSE_KO_CORRECTIONS 각 항목은 사람 납품본 실측(1090쌍, 2026-07-30)으로
치환 방향이 검증됐다(브리프 표 참고). 여기서는 (1) 각 치환이 실제로
일어나는지, (2) 멱등성, (3) "프로판"/"프로너츠" 같은 무관 문자열이 오폭으로
바뀌지 않는지, (4) 적용 순서가 결과에 영향을 주지 않는지를 잠근다.
"""
from __future__ import annotations

import pytest

from apps.server.domain.pdf_translate.house_style import (
    HOUSE_KO_CORRECTIONS,
    apply_house_style,
)

# ── 개별 치환 케이스 (브리프 표 그대로) ──────────────────────────────────

CASES = [
    ("조셉이 대답했다.", "죠셉이 대답했다."),
    ("붐하워가 웃었다.", "붐하우어가 웃었다."),
    ("새더튼 씨가 들어왔다.", "대더튼 씨가 들어왔다."),
    ("태더튼 씨가 들어왔다.", "대더튼 씨가 들어왔다."),
    ("레이 로이가 도착했다.", "레이로이가 도착했다."),
    ("차 킹 에스페시알레", "챠 킹 에스페시알레"),
    ("이펙트 연기가 피어오른다.", "연기 효과가 피어오른다."),
    ("연기 이펙트가 피어오른다.", "연기 효과가 피어오른다."),
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
            "이펙트 연기, 효과음:, 프롭, 앵글 온:, 설정 샷, 카메라 이동, "
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
    같아야 함을 확인 — 순서 의존성이 없음을 회귀로 잠근다."""
    def apply_reversed(ko: str) -> str:
        for wrong, right in reversed(HOUSE_KO_CORRECTIONS):
            if wrong in ko:
                ko = ko.replace(wrong, right)
        return ko

    text = "새더튼과 태더튼, 조셉과 붐하워, 프롭과 효과음:"
    assert apply_house_style(text) == apply_reversed(text)


# ── 오폭 방지("프롭"→"소품"이 "프로판"/"프로너츠"를 건드리면 안 됨) ──────

@pytest.mark.parametrize("text", ["프로판", "프로너츠", "프로판과 부탄", "이 프로너츠는 별로야"])
def test_house_style_does_not_false_positive_on_propane_or_pronuts(text):
    assert apply_house_style(text) == text


def test_house_style_empty_and_none_like_input():
    assert apply_house_style("") == ""
