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


@dataclass
class SceneRun:
    """지문 컷 감지가 만든 런 — 인접 컷 사이 구간과 그 안에서 읽은 슬레이트.
    런은 시간축에서 연속이다(runs[i].end_ms == runs[i+1].start_ms)."""
    start_ms: int
    end_ms: int
    text: str


def tokenize(text: str, delimiters: list[str]) -> list[str]:
    """구분자(기본 _, 공백, -)로 분해. 빈 토큰은 버린다(하이픈 양옆 공백 등)."""
    if not delimiters:
        parts = [text]
    else:
        pattern = "|".join(re.escape(d) for d in delimiters)
        parts = re.split(pattern, text)
    return [p for p in (s.strip() for s in parts) if p]


def _squash_ws(token: str) -> str:
    """토큰 내부 공백을 모두 제거. OCR이 같은 필드를 프레임마다 "Seq01B"/"Seq 01B"로
    들쭉날쭉 읽어도(실기 관측) 같은 값으로 취급되게 한다 — 이 정규화가 없으면 공백
    한 칸 차이로 스퓨리어스 경계가 생긴다. 파일명에도 공백이 안 들어가 깔끔하다."""
    return "".join(token.split())


def grouping_key(tokens: list[str], indices: list[int]) -> str | None:
    """선택된 토큰들을 결합해 그룹 키를 만든다. 인덱스가 범위를 벗어나면
    (판독 실패로 토큰이 모자란 경우) None. 토큰 내부 공백은 정규화한다
    (OCR 공백 노이즈로 인한 스퓨리어스 경계 방지)."""
    if not indices or any(i < 0 or i >= len(tokens) for i in indices):
        return None
    return _KEY_SEP.join(_squash_ws(tokens[i]) for i in indices)


def build_label(tokens: list[str], upto_index: int) -> str:
    """파일명 라벨 = tokens[0..upto_index]를 "_"로 결합. 선택 토큰 앞의 고정
    접두(쇼넘버 등)가 자연히 포함된다. grouping_key와 동일하게 토큰 내부 공백을
    정규화해 같은 그룹이 항상 같은 파일명을 갖게 한다."""
    if upto_index < 0 or upto_index >= len(tokens):
        return ""
    return "_".join(_squash_ws(t) for t in tokens[: upto_index + 1])


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
    interval_ms: int = 0,
    absorb_single: bool = False,
) -> list[Segment]:
    """연속된 동일 키 구간을 세그먼트로 묶는다. 각 구간은
    [start_ms, 다음 구간 start_ms) (마지막은 total_ms). min_ms 미만 구간은
    직전 구간에 흡수해 오독 1프레임 튐을 제거한다.

    interval_ms>0이면 (a) 내부 경계를 두 샘플의 중간으로 당겨(컷을 첫 새 샘플이
    아니라 그 절반 앞) 샘플링 격자로 인한 이웃 블리드를 절반으로 줄이고,
    (b) absorb_single=True면 내부(양쪽에 이웃이 있는) 1샘플 고립 구간을 직전에
    흡수한다(시퀀스 모드 오독 제거 — 시퀀스가 1샘플만 지속될 리 없다)."""
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

    # 내부 1샘플 고립 흡수(시퀀스 모드) — 첫/마지막은 제외(끝단은 확신이 낮음),
    # 양옆에 이웃이 있는 1샘플 구간만 직전에 흡수한다.
    if absorb_single and interval_ms > 0 and len(merged) >= 3:
        kept: list[list] = [merged[0]]
        for i in range(1, len(merged) - 1):
            span = merged[i]
            if span[3] - span[2] <= interval_ms:
                kept[-1][3] = span[3]  # 직전 끝을 연장(흡수)
            else:
                kept.append(span)
        kept.append(merged[-1])
        merged = kept

    # 인접한 동일 키 구간 병합 (흡수 후 같은 키가 인접할 수 있음)
    if merged:
        final: list[list] = [merged[0]]
        for i in range(1, len(merged)):
            if merged[i][0] == final[-1][0]:  # 같은 키
                final[-1][3] = merged[i][3]  # 끝을 연장
            else:
                final.append(merged[i])
        merged = final

    # 경계 중앙 정렬 — 컷을 (직전 마지막 샘플, 첫 새 샘플)의 중간으로 당긴다.
    # 실제 전환은 두 샘플 사이 어딘가라 중간이 기대 오차를 최소화(±interval/2).
    if interval_ms > 0 and len(merged) > 1:
        half = interval_ms // 2
        for i in range(1, len(merged)):
            b = merged[i][2] - half
            if b > merged[i - 1][2]:  # 구간 역전 방지
                merged[i][2] = b
                merged[i - 1][3] = b

    # 첫 구간 시작 당김 — 앞머리가 판독실패(타이틀카드 등, 키 None)면 첫 구간이
    # 첫 유효 샘플에서 시작해 실제 시작보다 최대 interval만큼 늦다(실기 010
    # 첫 1초 유실). 내부 경계와 동일하게 반 간격 당긴다.
    if interval_ms > 0 and merged and keyed and merged[0][2] > keyed[0][0]:
        merged[0][2] = max(keyed[0][0], merged[0][2] - interval_ms // 2)

    return [Segment(label=lbl, start_ms=st, end_ms=en) for _, lbl, st, en in merged]


def token_shape(token: str) -> str:
    """토큰을 문자종류(U=대문자, L=소문자, D=숫자, X=기타) 런과 길이로 요약.
    프론트 sceneSplitLogic.tokenShape와 동일 규칙 — 경계 계산의 단일 출처는
    서버라, 오독 정규화도 서버가 한다."""
    out = ""
    cur = ""
    count = 0
    for c in _squash_ws(token):
        kind = ("D" if c.isdigit() else "U" if c.isupper()
                else "L" if c.islower() else "X")
        if kind == cur:
            count += 1
            continue
        if cur:
            out += f"{cur}{count}"
        cur, count = kind, 1
    if cur:
        out += f"{cur}{count}"
    return out


def _mode_of(values: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = None
    best_n = 0
    for k, n in counts.items():
        if n > best_n:
            best, best_n = k, n
    return best


def label_template(texts: list[str], delimiters: list[str]) -> list[str] | None:
    """텍스트 집합의 대표 모양 — 최빈 토큰 개수를 고르고, 그 개수를 가진
    텍스트들에서 위치별 최빈 모양을 뽑는다. 작품별 포맷을 하드코딩하지 않고
    데이터 자신의 다수 모양을 기준으로 삼는다."""
    tokenized = [tokenize(t, delimiters) for t in texts]
    modal = _mode_of([str(len(t)) for t in tokenized if t])
    if not modal:
        return None
    n = int(modal)
    rows = [t for t in tokenized if len(t) == n]
    template: list[str] = []
    for i in range(n):
        shape = _mode_of([token_shape(r[i]) for r in rows])
        if not shape:
            return None
        template.append(shape)
    return template


_SHAPE_RE = re.compile(r"([ULDX])(\d+)")


def _reparse(text: str, template: list[str], delimiters: list[str]) -> str | None:
    """구분자를 잃고 붙은 텍스트를 템플릿 모양대로 다시 쪼갠다. 템플릿을
    확신 있게 채우지 못하면(문자 부족·종류 불일치·숫자 잔여) None — 억지
    교정은 하지 않는다. 프론트 reparse의 confident 판정과 동일."""
    toks = tokenize(text, delimiters)
    if (len(toks) == len(template)
            and all(token_shape(t) == s for t, s in zip(toks, template))):
        return None  # 이미 정상
    flat = "".join(_squash_ws(t) for t in toks)

    def cls(c: str) -> str:
        return ("D" if c.isdigit() else "U" if c.isupper()
                else "L" if c.islower() else "X")

    pos = 0
    out: list[str] = []
    for shape in template:
        piece = ""
        for kind, length in _SHAPE_RE.findall(shape):
            for _ in range(int(length)):
                if pos >= len(flat) or cls(flat[pos]) != kind:
                    return None
                piece += flat[pos]
                pos += 1
        out.append(piece)
    if pos < len(flat) and cls(flat[pos]) == "D":
        return None  # 자릿수가 남으면 어디서 끊을지 모호 — 자동 교정 제외
    return "_".join(out)


def canonicalize_texts(texts: list[str], delimiters: list[str]) -> list[str]:
    """런 텍스트들을 데이터 자신의 최빈 템플릿으로 정규화한다(확신 교정만).

    지문 방식은 런 중간 — 가짜 컷을 만든 흐릿한 프레임 근처 — 을 읽어 구분자
    유실 오독률이 간격 스캔의 10배(실기 11.5%)다. 그룹핑 전에 정규화해야
    오독 하나가 세그먼트 하나로 굳는 것을 원천 차단한다(실기: 시퀀스 322→147).
    교정 못 하는 텍스트(문자 오독·잔여 숫자·판독 실패)는 원문 그대로 둔다 —
    이후 클러스터 흡수(runs_to_segments absorb_flanked_ms)가 받는다."""
    template = label_template([t for t in texts if t], delimiters)
    if not template:
        return list(texts)
    out: list[str] = []
    for text in texts:
        fixed = _reparse(text, template, delimiters) if text else None
        out.append(fixed if fixed is not None else text)
    return out


def runs_to_segments(runs: list[SceneRun], rule: SlateRule,
                     mode: str, absorb_flanked_ms: int = 0) -> list[Segment]:
    """지문 런 → 세그먼트(순수 함수, 지문 방식의 compute_boundaries 대응).

    경계는 이미 프레임 정확한 컷이므로 min_ms 흡수·중앙정렬·정밀화가 없다.
    같은 키의 연속 런은 병합한다 — 씬 내부 가짜 컷(반투명 바 뒤 애니 움직임)이
    이 병합으로 흡수된다. 판독 실패 런은 직전 세그먼트의 연속으로 본다
    (hold_keys와 같은 홀드 규칙 — 슬레이트는 씬 내내 떠 있으므로 새 라벨이
    읽히기 전까지는 같은 씬이다). 선두의 판독 실패 런(타이틀카드 등)은 버린다 —
    첫 유효 런의 시작이 곧 실제 컷이라 간격 방식처럼 시작을 당길 필요가 없다.

    absorb_flanked_ms>0이면 같은 키 두 그룹 사이에 낀 '연속' 다른-키 블록
    (총 길이 ≤ 이 값)을 통째로 흡수한다 — canonical화가 못 고친 오독(문자
    오독·꼬리 잘림)은 연속 클러스터(A|X|Y|A)로 남는데, 단일 낀 것만 잡는
    정리로는 부족하다(실기 시퀀스 322→104 잔존, 클러스터 흡수로 19). 캡이
    진짜 비단조(A|B|A에서 B가 긴 경우)를 보존한다."""
    indices = _mode_indices(rule, mode)
    upto = max(indices) if indices else -1
    # [key, label, start, end] 그룹 — 흡수 패스가 키를 봐야 해서 키를 유지한다.
    groups: list[list] = []
    last_key: str | None = None
    for run in runs:
        toks = tokenize(run.text, rule.delimiters) if run.text else []
        key = grouping_key(toks, indices)
        if key is None:
            if groups:
                groups[-1][3] = run.end_ms
            continue
        if groups and key == last_key:
            groups[-1][3] = run.end_ms
        else:
            groups.append([key, build_label(toks, upto),
                           run.start_ms, run.end_ms])
        last_key = key

    if absorb_flanked_ms > 0:
        for _ in range(8):  # 흡수로 새 인접 동일 키가 생기면 반복(유한)
            merged: list[list] = []
            i = 0
            changed = False
            while i < len(groups):
                if merged:
                    # 직전 그룹과 같은 키가 다시 나오는 지점까지의 블록을 찾아,
                    # 블록 총 길이가 캡 이하면 (블록+복귀 그룹)을 통째로 잇는다.
                    j = i
                    while j < len(groups) and groups[j][0] != merged[-1][0]:
                        j += 1
                    if (j < len(groups) and j > i
                            and groups[j - 1][3] - groups[i][2] <= absorb_flanked_ms):
                        merged[-1][3] = groups[j][3]
                        i = j + 1
                        changed = True
                        continue
                merged.append(list(groups[i]))
                i += 1
            groups = merged
            if not changed:
                break

    return [Segment(label=lbl, start_ms=st, end_ms=en)
            for _key, lbl, st, en in groups]


def label_matches(text_label: str, target: str, other: str,
                  delimiters: list[str]) -> bool:
    """정밀화 오라클용 라벨 판정 — 구분자를 제거한(squash) 접두 일치.

    OCR이 구분자를 놓쳐 토큰이 붙어도("HH0307_1200010"; 실기에서 경계를 2초+
    지각시킨 오독) 목표 라벨("HH0307_120")과 같은 쪽으로 분류한다. other
    (반대쪽 세그먼트 라벨)가 target 이상 길이로 함께 접두 일치하면(중복 라벨
    _02 접미사처럼 한 라벨이 다른 라벨의 접두인 경우) 보수적으로 불일치."""
    def sq(s: str) -> str:
        return "".join(_squash_ws(t) for t in tokenize(s, delimiters))
    t, o, x = sq(target), sq(other), sq(text_label)
    if not t or not x or not x.startswith(t):
        return False
    if o and x.startswith(o) and len(o) >= len(t):
        return False
    return True


def dedupe_labels(labels: list[str]) -> list[str]:
    """중복 라벨에 _02, _03 … 접미사를 붙여 파일명 충돌을 막는다. 첫 등장은
    그대로 두고, 이후 같은 라벨만 순번을 붙인다(비단조 슬레이트 순서 대비)."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for label in labels:
        n = seen.get(label, 0) + 1
        seen[label] = n
        out.append(label if n == 1 else f"{label}_{n:02d}")
    return out
