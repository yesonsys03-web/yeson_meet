"""슬레이트 텍스트 → 씬/시퀀스 경계 계산 (순수 함수, I/O 없음).

작품마다 슬레이트 포맷이 달라(예: "HH0307_020_0150_AC_v01" vs
"Seq 07_S08 - Panel 3") 파서를 하드코딩하지 않는다. OCR이 읽은 텍스트를
구분자로 토큰화하고, 사용자가 지정한 토큰 인덱스(SlateRule)로 그룹 키와
파일명 라벨을 만든다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# grouping_key 내부 결합자 — 슬레이트 토큰에 등장하지 않는 제어문자(US)라
# "020"+"0150"과 "0200"+"150" 같은 충돌을 막는다.
_KEY_SEP = "\x1f"


@dataclass
class SlateRule:
    delimiters: list[str]
    seq_tokens: list[int]
    scene_tokens: list[int]


@dataclass
class FrameSample:
    index: int
    t_ms: int
    text: str


@dataclass
class Segment:
    label: str
    start_ms: int
    end_ms: int


def tokenize(text: str, delimiters: list[str]) -> list[str]:
    """구분자(기본 _, 공백, -)로 분해. 빈 토큰은 버린다(하이픈 양옆 공백 등)."""
    if not delimiters:
        parts = [text]
    else:
        pattern = "|".join(re.escape(d) for d in delimiters)
        parts = re.split(pattern, text)
    return [p for p in (s.strip() for s in parts) if p]


def grouping_key(tokens: list[str], indices: list[int]) -> str | None:
    """선택된 토큰들을 결합해 그룹 키를 만든다. 인덱스가 범위를 벗어나면
    (판독 실패로 토큰이 모자란 경우) None."""
    if not indices or any(i < 0 or i >= len(tokens) for i in indices):
        return None
    return _KEY_SEP.join(tokens[i] for i in indices)


def build_label(tokens: list[str], upto_index: int) -> str:
    """파일명 라벨 = tokens[0..upto_index]를 "_"로 결합. 선택 토큰 앞의 고정
    접두(쇼넘버 등)가 자연히 포함된다."""
    if upto_index < 0 or upto_index >= len(tokens):
        return ""
    return "_".join(tokens[: upto_index + 1])


def _mode_indices(rule: SlateRule, mode: str) -> list[int]:
    if mode == "sequence":
        return rule.seq_tokens
    return rule.seq_tokens + rule.scene_tokens


def hold_keys(
    samples: list[FrameSample], rule: SlateRule, mode: str,
) -> list[tuple[int, str | None, str | None]]:
    """각 프레임을 (t_ms, grouping_key, label)로 매핑. 판독 실패(빈 텍스트·
    토큰 부족) 프레임은 직전 유효값으로 홀드한다."""
    indices = _mode_indices(rule, mode)
    upto = max(indices) if indices else -1
    out: list[tuple[int, str | None, str | None]] = []
    last_key: str | None = None
    last_label: str | None = None
    for s in samples:
        toks = tokenize(s.text, rule.delimiters) if s.text else []
        key = grouping_key(toks, indices)
        if key is not None:
            last_key = key
            last_label = build_label(toks, upto)
        out.append((s.t_ms, last_key, last_label))
    return out


def compute_boundaries(
    keyed: list[tuple[int, str | None, str | None]],
    total_ms: int,
    min_ms: int = 2000,
) -> list[Segment]:
    """연속된 동일 키 구간을 세그먼트로 묶는다. 각 구간은
    [start_ms, 다음 구간 start_ms) (마지막은 total_ms). min_ms 미만 구간은
    직전 구간에 흡수해 오독 1프레임 튐을 제거한다."""
    runs: list[tuple[str, str, int]] = []  # (key, label, start_ms)
    for t_ms, key, label in keyed:
        if key is None:
            continue
        if not runs or runs[-1][0] != key:
            runs.append((key, label or "", t_ms))
    if not runs:
        return []

    # (start, end) 부여
    spans: list[list] = []
    for i, (key, label, start) in enumerate(runs):
        end = runs[i + 1][2] if i + 1 < len(runs) else total_ms
        spans.append([key, label, start, end])

    # min_ms 미만 흡수 — 직전 구간에 합치고, 없으면 다음 구간 시작을 앞당긴다.
    merged: list[list] = []
    for span in spans:
        key, label, start, end = span
        if end - start < min_ms and merged:
            merged[-1][3] = end  # 직전 구간 끝을 연장(흡수)
            continue
        merged.append(span)
    # 첫 구간이 짧고 흡수할 직전이 없으면 다음 구간과 병합
    if len(merged) >= 2 and merged[0][3] - merged[0][2] < min_ms:
        merged[1][2] = merged[0][2]
        merged.pop(0)

    # 인접한 동일 키 구간 병합 (흡수 후 같은 키가 인접할 수 있음)
    if merged:
        final: list[list] = [merged[0]]
        for i in range(1, len(merged)):
            if merged[i][0] == final[-1][0]:  # 같은 키
                final[-1][3] = merged[i][3]  # 끝을 연장
            else:
                final.append(merged[i])
        merged = final

    return [Segment(label=lbl, start_ms=st, end_ms=en) for _, lbl, st, en in merged]
