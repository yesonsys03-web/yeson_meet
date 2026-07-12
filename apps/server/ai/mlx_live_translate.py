# === ANCHOR: MLX_LIVE_TRANSLATE_START ===
"""하이브리드 B: 파이널 번역만 MLX 로컬 LLM으로 정제하는 데코레이터 프로바이더.

스펙: docs/superpowers/specs/2026-07-12-hybrid-b-mlx-live-translate-design.md
- 파셜은 inner(Apple) 그대로 통과, 파이널은 홀드 후 MLX KO로 확정(가드 통과 시).
- 가드 불합격/타임아웃/워커 사망/백로그 초과 → Apple KO 폴백. 자막 무중단이 최우선.
"""
from __future__ import annotations

import re

# --- 환각 가드 --------------------------------------------------------------
# 2026-07-12 벤치 실측 실패 유형을 각각 겨냥한 5규칙. 전부 정규식/문자열 연산.
_FOREIGN_RE = re.compile(
    "[一-鿿"      # CJK 한자
    "぀-ヿ"        # 히라가나+가타카나
    "Ѐ-ӿ"        # 키릴
    "฀-๿"        # 태국 문자
    "�]"              # 깨진 문자
)
_DIGIT_RUN_RE = re.compile(r"\d+")
_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
# 10자 이상 구절이 (원본 포함) 3회 이상 등장 = 같은 구절이 2회 더 반복
_REPEAT_RE = re.compile(r"(.{10,}?)(?:.*?\1){2,}", re.DOTALL)

_LEN_RATIO_MIN = 0.2
_LEN_RATIO_MAX = 3.0
_ASCII_LEAK_MAX = 0.6


def guard_mlx_ko(en: str, ko: str) -> str | None:
    """MLX 번역 결과 검증. 통과 시 None, 불합격 시 사유 문자열."""
    ko_stripped = ko.strip()
    if not ko_stripped:
        return "empty"
    if _FOREIGN_RE.search(ko_stripped):
        return "foreign_script"
    en_digits = set(_DIGIT_RUN_RE.findall(en))
    for run in _DIGIT_RUN_RE.findall(ko_stripped):
        if run not in en_digits:
            return "invented_number"
    ratio = len(ko_stripped) / max(1, len(en.strip()))
    if not (_LEN_RATIO_MIN <= ratio <= _LEN_RATIO_MAX):
        return "length_ratio"
    ascii_alpha = len(_ASCII_ALPHA_RE.findall(ko_stripped))
    if ascii_alpha / max(1, len(ko_stripped)) > _ASCII_LEAK_MAX:
        return "english_leak"
    if _REPEAT_RE.search(ko_stripped):
        return "repetition"
    return None
# === ANCHOR: MLX_LIVE_TRANSLATE_END ===
