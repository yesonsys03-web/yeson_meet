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
    HOUSE_KO_PATTERN_CORRECTIONS,
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
    # FL102 실측 추가분(2026-07-31) — 싸이클 14/사이클 0, 참고 2/레퍼런스 0,
    # 씬밖 9+1/신밖 1. GABE01에는 이 단어들이 등장하지 않아 쇼 간 충돌 없음.
    ("사이클 1/3", "싸이클 1/3"),
    ("레퍼런스 패널", "참고 패널"),
    ("바비(신밖):이 생활환경이", "바비(씬밖):이 생활환경이"),
    # FL104 실측 추가분(2026-08-03) — 사람 납품본 전수 대조에서 한쪽이 정확히
    # 0인 항목만: 파티광 57/0, 걷는 싸이클 6/0, 눈 흘깃보는 4/0.
    ("여자 스프링 브레이커 #1", "여자 파티광 #1"),
    ("워크 싸이클 B 1/2", "걷는 싸이클 B 1/2"),
    ("눈동자 움직임 싸이클 1/2", "눈 흘깃보는 싸이클 1/2"),
    # 재실행 실측 추가분 — 음역을 막자 의역으로 샜다(봄방학생 12·봄방학족 4).
    ("여자 봄방학생 #2:첼시, 기다려!", "여자 파티광 #2:첼시, 기다려!"),
    ("봄방학족이 몰려온다", "파티광이 몰려온다"),
    # 재재실행 실측(2026-08-03, FL104_FNL_Nrev 209페이지): 리터럴을 막자
    # 이번엔 **띄어쓰기를 없애고** 새 접미사로 샜다 — 붙여쓴
    # `스프링브레이커` 8건·`스프링브레이크` 9건·`봄방학객` 8건(사람 0건).
    ("여성 스프링브레이커 #1", "여성 파티광 #1"),
    ("여자 봄방학객 #2:첼시, 기다려!", "여자 파티광 #2:첼시, 기다려!"),
    # 행사(SPRING BREAK)는 `봄방학` — 사람 표기(`봄방학 좀비군중1`)와 맞춘다.
    ("스프링브레이크", "봄방학"),
    ("스프링 브레이크 좀비군중1", "봄방학 좀비군중1"),
]


def test_house_style_keeps_spring_break_event_term():
    """`봄방학`(SPRING BREAK, 행사)은 사람도 쓰는 정상 번역이라 건드리지
    않는다 — 역할명(`스프링브레이커`·`봄방학객` 등)만 `파티광`으로 고친다.

    접미사를 요구하는 좌변이라 단독 `봄방학`은 어느 규칙에도 걸리지 않는다."""
    assert apply_house_style("봄방학 시즌이다") == "봄방학 시즌이다"
    assert apply_house_style("봄방학 좀비군중1") == "봄방학 좀비군중1"


def test_house_style_role_rule_runs_before_event_rule():
    """순서 계약: `스프링브레이크`는 `스프링브레이커`의 접두라, 행사 규칙이
    먼저 돌면 `봄방학어`가 된다. 역할 규칙이 앞에 있어야 한다."""
    assert apply_house_style("스프링브레이커") == "파티광"
    assert "봄방학어" not in apply_house_style("여성 스프링브레이커 #3")


def test_house_style_spring_break_rules_do_not_join_lines():
    """`\\s*`가 아니라 `[ \\t]*`인 이유 — 줄이 갈린 자리를 한 줄로 합치지
    않는다(같은 이유로 씬 번호 규칙도 개행을 피한다)."""
    assert apply_house_style("스프링\n브레이커") == "스프링\n브레이커"


def test_house_style_is_idempotent_for_spring_break():
    """치환 결과(`파티광`·`봄방학`)가 다시 어느 좌변에도 맞지 않는다.

    실물 형태로 확인한다 — 이 문서군의 17건은 전부 라벨/역할명이라 조사가
    뒤따르지 않는다(실측). 조사 일치는 이 파일의 다른 KO→KO 치환과 마찬가지로
    다루지 않는다."""
    once = apply_house_style("여성 스프링브레이커 #1 / 스프링브레이크좀비군중1")
    assert once == "여성 파티광 #1 / 봄방학좀비군중1"
    assert apply_house_style(once) == once


def test_house_style_leaves_agreed_cycle_terms_untouched():
    """사람과 우리가 이미 일치하는 용어(불/가스/군중 싸이클)는 표에 없다 —
    FL104 대조의 대조군이라 회귀로 잡아 둔다."""
    for text in ("불 싸이클 1/3", "가스 싸이클 2/3", "군중 싸이클 1/2"):
        assert apply_house_style(text) == text


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
            "카메라 위치, 새 아트도 함께. "
            "사이클 1/3, 레퍼런스 패널, 바비(신밖):대사.")
    once = apply_house_style(text)
    twice = apply_house_style(once)
    assert once == twice


# (리뷰 라운드 2 볼륨 판정: 케이스별 파라미터화 멱등 테스트 13개가
# `assert once == after`로 위 test_house_style_substitution을 그대로
# 되풀이했다 — 멱등성 자체는 아래 test_house_style_is_idempotent가 통합
# 텍스트로 이미 덮으므로, 신호 손실 없이 제거했다.)


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


@pytest.mark.parametrize("before", [before for before, _ in FX_CASES])
def test_house_style_fx_rule_standalone_is_idempotent(before):
    once = apply_house_style(before)
    assert apply_house_style(once) == once


def test_house_style_pattern_corrections_all_covered():
    """HOUSE_KO_PATTERN_CORRECTIONS(FX 정규식 2개) 각각이 FX_CASES로
    실제로 발동되는지 확인 — HOUSE_KO_CORRECTIONS 쪽의
    `test_house_style_all_table_entries_covered`와 대응하는 자체검증.
    향후 패턴이 추가되고 테스트가 빠지는 사고를 방지한다(리뷰 지적)."""
    triggered = {
        pattern.pattern
        for pattern, _ in HOUSE_KO_PATTERN_CORRECTIONS
        for before, _ in FX_CASES
        if pattern.search(before)
    }
    all_patterns = {pattern.pattern for pattern, _ in HOUSE_KO_PATTERN_CORRECTIONS}
    assert triggered == all_patterns


def test_house_style_fx_rule_embedded_with_slash_delimiter():
    """실물 코퍼스(전수 1090쌍): "FX Fire"가 다른 액션노트와 " / "로 병합된
    블록에서도 규칙이 발동해야 한다 — 전체 문자열 시작/끝에만 앵커링하면
    (팀 리드가 제시한 `^이펙트 (.+)$`/`(.+) 이펙트$` 그대로는) 이 경우를
    놓친다(실측으로 확인, apply_house_style은 " / "도 세그먼트 경계로
    인정해 잡는다).

    Task 20 후속: 기대값의 "sc47"이 "씬 47"로 바뀌었다 — 같은 함수에 씬 번호
    표기 규칙이 추가됐기 때문이고, 사람 납품본 관례(14/14 `씬`)에 맞는
    방향의 변화다. FX 규칙 자체의 검증(슬래시 구분자 세그먼트 경계)은
    "불 이펙트" → "불 효과" 부분이 그대로 담당한다."""
    before = ("예전 고객들이 돌아다니며 제품을 살펴본다. 품질에 진심으로 "
              "감탄한 모습이다. / 불 이펙트 / 팔 위치가 일치하도록 인시덴털 "
              "#2000을 이전 sc47에 훅업해 주세요.")
    after = ("예전 고객들이 돌아다니며 제품을 살펴본다. 품질에 진심으로 "
             "감탄한 모습이다. / 불 효과 / 팔 위치가 일치하도록 인시덴털 "
             "#2000을 이전 씬 47에 훅업해 주세요.")
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
    마침표·개행 경계 없음) 세그먼트 경계 신호가 없으면 건드리지 않는다.
    ⚠ 이건 순수한 오폭 방지 가드가 아니라 **커버리지 축소**이기도 하다
    (리뷰 재지적) — 제거된 리터럴 항목 `("연기 이펙트", "연기 효과")`는
    부분 문자열 치환이라 이 문자열도 실제로 바꿨을 것이다("연기 효과
    연기"). 코퍼스 실측상 이 모양(경계 없는 문장 중간 이펙트)은 0건이라
    감수한 트레이드오프다."""
    assert apply_house_style("연기 이펙트 연기") == "연기 이펙트 연기"


# ── _SEG_END 비대칭 수정 (리뷰 라운드 2 Important) ───────────────────────

def test_house_style_fx_rule_seg_end_symmetric_does_not_swallow_following_sentence():
    """라운드 1의 `_SEG_END`는 세그먼트 시작(". " 뒤 인정)과 비대칭이라,
    실제 문장(마침표가 단어에 바로 붙음, 공백 없음)에서 리딩 규칙의
    비탐욕 캡처가 다음 경계까지 뒤 문장 전체를 삼켰다 — 캡처가 `효과`
    앞으로 이동하므로 삼켜진 문장째로 라벨이 엉뚱한 문장 뒤에 붙는
    틀린 주석이 됐다(리뷰가 실행으로 재현한 두 케이스를 그대로 잠근다)."""
    assert (apply_house_style("이펙트 연기. 다음 문장은 그대로 있어야 한다. / 세 번째")
            == "연기 효과. 다음 문장은 그대로 있어야 한다. / 세 번째")
    assert (apply_house_style("이펙트 연기. 다음 문장.")
            == "연기 효과. 다음 문장.")


def test_house_style_fx_rule_guards_dialogue_false_positive():
    """대사 속 일반 명사 "이펙트"(예: "특수 이펙트.")는 세그먼트 경계
    신호가 없으므로 `_SEG_END`에 `\\.\\s`를 추가한 뒤에도 건드리면 안
    된다 — 마침표 뒤에 공백이 없는 문장 끝(문자열 그대로 종료)은
    경계가 아니다(리뷰가 이 가드 유지를 확인)."""
    assert apply_house_style("행크: 그거 특수 이펙트.") == "행크: 그거 특수 이펙트."


def test_house_style_fx_rule_residual_trailing_period_is_awkward_but_lossless():
    """알려진 잔여 케이스(팀 리드 판단 보류 — Task 19 출력 정규화가 흡수할지
    그쪽에서 결정) — 문자열이 마침표로 끝나면(공백 없이) 라벨이 문장 뒤로
    빠져 어색하지만 내용 손실·중복은 없다. 개선 대상이 아니라 현재 동작을
    회귀 감지용으로 잠근다."""
    assert apply_house_style("이펙트 연기.") == "연기. 효과"


# ── 개행(\r/\n) 경계 (리뷰 라운드 2 — Task 19가 실물로 만들 것) ──────────

def test_house_style_fx_rule_converts_on_own_line_after_newline():
    """Task 19가 액션 블록에 실제 줄바꿈을 도입한다(슬러그라인 join) —
    이전 줄 뒤에 개행으로 이어지는 FX 라벨도 규칙이 잡아야 한다(개행도
    세그먼트 경계)."""
    assert apply_house_style("line one\n이펙트 연기") == "line one\n연기 효과"
    assert (apply_house_style("첫째 줄\n불 이펙트\n셋째 줄")
            == "첫째 줄\n불 효과\n셋째 줄")


def test_house_style_fx_rule_does_not_swallow_trailing_carriage_return():
    """캡처 끝에 \\r가 갇혀 라벨이 줄바꿈 뒤로 밀리는 결함(리뷰 Minor,
    현재 코퍼스엔 미발현) — \\r/\\n을 경계로 인정해 라벨이 줄바꿈 앞에
    정확히 붙어야 한다."""
    assert apply_house_style("줄바꿈 뒤. 이펙트 연기\r") == "줄바꿈 뒤. 연기 효과\r"


def test_house_style_fx_rule_never_captures_across_newline():
    """re.DOTALL을 쓰지 않으므로(의도적 — 쓰면 캡처가 줄 경계를 넘어 다른
    줄의 텍스트까지 삼킬 수 있다) 다음 줄 내용은 그대로 보존돼야 한다."""
    assert (apply_house_style("이펙트 연기\n다음 줄은 그대로")
            == "연기 효과\n다음 줄은 그대로")
    assert (apply_house_style("이펙트 연기\r\n두 번째 줄")
            == "연기 효과\r\n두 번째 줄")


def test_house_style_fx_rule_does_not_split_crlf_when_capture_would_be_only_cr():
    """라운드 2→3 결함: 캡처가 `.+?`였을 때 "이펙트 " 바로 뒤에 CR이 오면
    그 한 글자가 캡처에 딸려가 CRLF를 떠돌이 `\\r`+`\\n`으로 쪼갰다(내용
    손실은 없지만 줄 구조 손상). 캡처를 `[^\\r\\n]+?`로 좁혀 개행 문자
    자체가 캡처에 들어가지 않게 했다 — 그 결과 이 형태는 캡처할 문자가
    없어 규칙이 발동하지 않고 원문 그대로 남는다(줄을 쪼개거나 넘기느니
    미변환이 낫다는 방침, 팀 리드 지시)."""
    assert apply_house_style("이펙트 \r\n연기") == "이펙트 \r\n연기"


# ── 문말 부호 집합 대칭 (리뷰 라운드 3 Important — dedupe 키와 일치) ─────

def test_house_style_fx_rule_seg_end_symmetric_for_full_sentence_final_set():
    """라운드 2의 `_SEG_END` 수정은 `.`에만 대칭이었다 — 이 태스크가 이미
    dedupe 키(translate_blocks.py의 `_DEDUPE_KEY_RE`)에서 문말 부호로
    정의해 둔 `? ! . …` 중 `.` 하나만 인정하면 같은 결함 클래스가
    `?`·`!`·`…`에 남는다(리뷰가 실행으로 재현). 세 부호 전부 대칭이어야
    한다."""
    assert (apply_house_style("이펙트 연기! 다음 문장.")
            == "연기 효과! 다음 문장.")
    assert (apply_house_style("이펙트 연기? 다음 문장.")
            == "연기 효과? 다음 문장.")
    assert (apply_house_style("이펙트 연기… 다음 문장.")
            == "연기 효과… 다음 문장.")


def test_house_style_fx_rule_order_is_fixed_and_locked():
    """FX 규칙 두 개(앞→뒤 고정)의 적용 순서가 실제로 결과를 바꾸는 이론적
    edge case(한 문자열에 "이펙트"가 두 번)를 실측값 그대로 잠근다 —
    실물 코퍼스엔 이런 이중 출현이 없지만, 순서를 바꾸는 사람이 이 테스트로
    변화를 알아채야 한다(리뷰 Important 2가 지적한 "코드가 갖지 않은 성질을
    테스트가 보증"하는 실수를 반복하지 않기 위해, "순서 무관"이 아니라
    "이 순서에서는 이 값"이라고 명시적으로 단언한다)."""
    assert apply_house_style("이펙트 연기 이펙트") == "연기 이펙트 효과"


# ── 씬 번호 표기 규칙 (Task 20, 사용자 지적 2026-07-31) ──────────────────
#
# 애니메이션 스토리보드에서 `sc<숫자>`는 씬 번호다. 전수 실측(1095쌍)에서
# 사람은 14/14 전부 `씬`으로 옮겼고, 우리는 9/14만 맞았다 — 결정적 치환으로
# 고정한 규칙의 동작·오폭 가드·숫자 불변을 잠근다.

SCENE_CASES = [
    ("이전 sc49와 맞춰주세요.", "이전 씬 49와 맞춰주세요."),
    ("책상을 SC13에 훅업.", "책상을 씬 13에 훅업."),
    ("sc 7 참고", "씬 7 참고"),
    ("Sc103 화면", "씬 103 화면"),
    ("sc1과 sc2를 잇는다.", "씬 1과 씬 2를 잇는다."),
    # 리뷰 지적(2026-07-31): 한글이 공백 없이 바로 앞에 오는 형태. 파이썬
    # `\b`는 한글을 단어 문자로 취급해 여기서 경계가 성립하지 않으므로 옛
    # `\bsc` 가드로는 통째로 놓쳤다. 근거 범위는 house_style.py 주석 참고 —
    # 사람 납품본에 `한글+sc숫자`는 0건이고(사람은 `sc`를 남기지 않는다),
    # 실측된 건 씬 토큰이 앞 한글에 붙는 표기(`전씬49` 2건)다. 이 규칙의
    # 입력은 LLM 출력이라 이 모양이 나올 수 있다.
    ("이전sc49와 맞춰주세요.", "이전씬 49와 맞춰주세요."),
    # 퇴화 입력 — LLM이 `씬`을 이미 붙여 놓고 `sc`도 남긴 경우. `씬씬 103`은
    # **좋은 출력이 아니지만** 무해하다: 숫자가 보존되고 멱등이며, 이런
    # 입력이 나왔다는 건 이미 LLM 쪽이 어긋났다는 뜻이다. 규칙이 여기서
    # 무슨 짓을 하는지 명시적으로 고정해 두려고 넣은 케이스지 바람직한
    # 결과를 잠그는 게 아니다.
    ("씬sc103", "씬씬 103"),
]


@pytest.mark.parametrize("before,after", SCENE_CASES)
def test_house_style_scene_ref(before, after):
    assert apply_house_style(before) == after


# 오폭 가드 — `sc`가 단어 일부이거나 숫자가 따라오지 않으면 불변이어야 한다.
SCENE_NON_CASES = [
    "scene 12를 확인",      # 뒤에 숫자가 있지만 sc가 단어 일부(scene)
    "score 100점",          # 같은 형태(score)
    "discuss 3가지",        # 앞이 라틴 문자(i)
    "sc 없이 진행",          # sc 뒤에 숫자 없음
    "disc03 파일",          # 파일명 안의 sc + 숫자 (앞이 라틴 문자 i)
    "HANKSC12 코드",        # 자산 코드 안의 SC + 숫자 (앞이 K)
    "BG_sc12 레이어",       # 앞이 `_` — 자산 코드 관례 보호(아래 테스트 참고)
    "5LBW03sc01",           # 앞이 숫자
]


@pytest.mark.parametrize("text", SCENE_NON_CASES)
def test_house_style_scene_ref_does_not_overreach(text):
    assert apply_house_style(text) == text


def test_house_style_scene_ref_preserves_digits_exactly():
    """사용자 요구의 핵심 — "특히 숫자는 틀리면 안 된다". 이 치환은 캡처
    그룹을 그대로 옮길 뿐이므로 숫자열이 완전히 보존돼야 한다(자릿수·값
    모두). 숫자 보존 게이트(Task 16)가 이 뒤에 또 한 겹 있지만, 이
    규칙 자체가 숫자를 건드리지 않는다는 성질을 여기서 직접 잠근다."""
    import re
    for src in ["sc49", "sc0103", "sc 7", "SC13", "sc999999"]:
        out = apply_house_style(f"{src} 확인")
        assert re.findall(r"\d+", out) == re.findall(r"\d+", src)


def test_house_style_scene_ref_left_guard_excludes_latin_digit_underscore_only():
    """왼쪽 가드가 `\\b`가 아니라 `(?<![0-9A-Za-z_])`인 이유를 양방향으로
    잠근다 — 한글 인접은 **잡히고**(옛 `\\b`가 놓치던 실물 형태), 라틴
    문자·숫자·`_` 인접은 **막힌다**(자산 코드 오폭 방지).

    `_`를 배제 집합에 남긴 건 `\\b` 시절 동작 보존이다: 파이썬 정규식에서
    `_`는 단어 문자라 `\\b`가 이미 `_sc12`를 막고 있었고, 리뷰가 제안한
    `(?<![0-9A-Za-z])`만 쓰면 그 보호가 조용히 풀린다."""
    assert apply_house_style("이전sc49") == "이전씬 49"      # 한글 인접: 잡힘
    assert apply_house_style("BG_sc12") == "BG_sc12"        # `_` 인접: 막힘
    assert apply_house_style("V01sc12") == "V01sc12"        # 숫자 인접: 막힘
    assert apply_house_style("Xsc12") == "Xsc12"            # 라틴 인접: 막힘


def test_house_style_scene_ref_never_crosses_newline():
    """공백 클래스가 `[ \\t]`인 이유 — `\\s`였다면 "sc\\n49"에서 **다음 줄의
    숫자**를 씬 번호로 끌어와 줄 구조와 의미를 함께 망가뜨린다. Task 19가
    액션 블록에 실제 줄바꿈을 도입했으므로 실물에서 가능한 형태다."""
    assert apply_house_style("sc\n49 확인") == "sc\n49 확인"
    assert apply_house_style("sc\r\n49 확인") == "sc\r\n49 확인"


def test_house_style_scene_ref_is_idempotent():
    once = apply_house_style("이전 sc49와 sc 7 참고")
    assert apply_house_style(once) == once


def test_house_style_scene_ref_runs_after_fx_rule():
    """적용 순서 고정(리터럴 → FX 정규식 → 씬 규칙) — 한 문자열에 둘 다
    있을 때 두 규칙이 서로를 막지 않고 모두 발동하는지 확인한다."""
    assert (apply_house_style("이펙트 연기. 이전 sc49와 맞춤.")
            == "연기 효과. 이전 씬 49와 맞춤.")
