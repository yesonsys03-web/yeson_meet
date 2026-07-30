"""하우스 표기 강제 치환 — Task 18 (사람 번역본 전수 비교 P2).

캐릭터명·제작 용어의 하우스 표기가 사람 납품본(1090쌍 실측, 2026-07-30)과
불일치하거나 우리 출력 내부에서 혼재(Thatherton → 태더튼/새더튼)하는 문제를
KO→KO 결정적 치환으로 고정한다. 치환 방향은 전부 실측 검증됨(브리프 표 참고)
— 근거 없는 항목 추가 금지(강제 치환은 틀리면 맞는 번역을 틀리게 덮는다).

리뷰 후속(2026-07-30, 라운드 1): "이펙트 연기"/"연기 이펙트" 리터럴 2항목은
전수 1090쌍 재검증에서 실측 20건 중 6건만 잡는 것으로 드러났다(팀 리드 측정,
task-18-review.md Important 2) — 사람은 "이펙트"를 단 한 번도 쓰지 않고
항상 "<X> 효과"다. 리터럴 나열은 다음 에피소드(FX Water/FX Dust 등 미열거
항목)에서 깨지므로, 이 둘을 HOUSE_KO_PATTERN_CORRECTIONS(정규식 2개)로
교체했다.
"""
from __future__ import annotations

import re

# 사람 납품본 실측 기반 하우스 표기 (전수 비교 2026-07-30). KO→KO 결정적 치환.
# 표 12행(Thatherton은 새더튼·태더튼 2항목) 그대로 — 근거 없는 항목 추가 금지.
HOUSE_KO_CORRECTIONS: list[tuple[str, str]] = [
    ("조셉", "죠셉"), ("붐하워", "붐하우어"),
    ("새더튼", "대더튼"), ("태더튼", "대더튼"),
    ("레이 로이", "레이로이"), ("차 킹", "챠 킹"),
    # 제작 용어 — 과잉 치환 방지 위해 구체 패턴만:
    ("효과음:", "효과:"), ("프롭", "소품"), ("앵글 온:", "구도:"),
    ("설정 샷", "설정"), ("카메라 이동", "카메라 무브"),
    ("카메라 위치", "카메라 포즈"), ("새 아트", "뉴 아트"),
]

# 세그먼트 경계 — 문자열 시작/끝뿐 아니라, action 블록이 다른 지시문과
# " / "나 ". "로 이어붙는 실물 사례(전수 코퍼스 20건 중 2건 실측: "FX Fire"가
# 다른 액션노트와 " / "로 병합된 블록, "A long beat. FX Smoke"처럼 앞 문장
# 뒤에 곧장 붙는 블록)도 경계로 인정한다. 순수 ^/$ 앵커만 쓰면 이 2건을
# 놓친다(실측 확인, task-18-fix-report 참고). 이 두 구분자 이상으로
# 일반화하지 않는다(실측 근거 없는 델리미터 추가 금지).
_SEG_START = r"(?:^|(?<=[./]\s))"
_SEG_END = r"(?=\s[./]|$)"

# "FX <X>"/"<X> FX" → "<X> 효과" — 팀 리드 실측(전수 1090쌍, 2026-07-30
# 리뷰): 사람은 "이펙트"를 한 번도 쓰지 않고 전부 "<X> 효과"로 일관 — FX
# 뒤(앞)에 오는 대상 단어가 무엇이든 같은 규칙이 적용된다. 순서 고정(앞→뒤,
# 팀 리드 지시) — "이펙트"가 한 문자열에 두 번 나오는 이론적 edge case는
# 순서에 따라 결과가 달라지지만(테스트로 실측 고정 — 실물 코퍼스엔 이런
# 이중 출현이 없다), 정상 케이스(문자열당 1회)는 순서 무관하다. 사람이
# "카메라 플래시"를 "카메라 플래쉬"로 달리 쓰는 표기 차이(실측 n=1)는 이
# 규칙과 무관한 별개 이슈라 손대지 않는다.
HOUSE_KO_PATTERN_CORRECTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_SEG_START + r"이펙트 (.+?)" + _SEG_END), r"\1 효과"),
    (re.compile(_SEG_START + r"(.+?) 이펙트" + _SEG_END), r"\1 효과"),
]


def apply_house_style(ko: str) -> str:
    """HOUSE_KO_CORRECTIONS(리터럴)를 순서대로 적용한 뒤,
    HOUSE_KO_PATTERN_CORRECTIONS(정규식, FX 규칙)를 순서대로 적용한다.
    멱등 — 두 치환 모두 치환 결과 쪽에 자신의 좌변 문자열("이펙트" 포함)을
    재도입하지 않는다."""
    if not ko:
        return ko
    for wrong, right in HOUSE_KO_CORRECTIONS:
        if wrong in ko:
            ko = ko.replace(wrong, right)
    for pattern, replacement in HOUSE_KO_PATTERN_CORRECTIONS:
        ko = pattern.sub(replacement, ko)
    return ko
