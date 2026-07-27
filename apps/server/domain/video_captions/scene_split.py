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

# 판독불가 블록을 '다음 씬 머리'로 넘기는 컷 세기 비율 하한. 들어컷이 나가컷의
# 3배 이상이면 시각 컷이 블록 앞 — 실기 분포: 이동해야 할 케이스 15~90배,
# 애매한 케이스(슬레이트 이동 등) 1~2.5배라 3이 안전한 분리선이다.
_UNREADABLE_MOVE_RATIO = 3


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
    런은 시간축에서 연속이다(runs[i].end_ms == runs[i+1].start_ms).
    cut_diff=이 런을 연 컷의 지문 세기(판독불가 블록 귀속 판정용, 0=미기록)."""
    start_ms: int
    end_ms: int
    text: str
    cut_diff: int = 0


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


# 닮은꼴 문자쌍 — OCR이 글자와 숫자를 서로 바꿔 읽는 상수적 오독. 슬레이트
# 서체(산세리프 대문자+숫자)에서 실제로 뒤집히는 쌍만 담는다: 실기 FL102는
# 시퀀스 글자 O를 '0', I를 '1'로 읽어 299씬 중 60개가 어긋났다(그중 7개는
# 같은 씬이 두 갈래로 읽혀 세그먼트가 쪼개졌다).
_TO_DIGIT = {"O": "0", "o": "0", "I": "1", "l": "1", "Z": "2",
             "S": "5", "G": "6", "B": "8"}
_TO_UPPER = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}


def _cls(c: str) -> str:
    return ("D" if c.isdigit() else "U" if c.isupper()
            else "L" if c.islower() else "X")


def _coerce_lookalikes(flat: str, template: list[str],
                       pos_values: tuple[frozenset[str], ...]) -> str | None:
    """자릿수는 맞는데 문자종류가 어긋난 자리를 '닮은꼴 상대'로 바꿔 템플릿에
    맞춘다 — 코퍼스 다수 모양이 그 자리를 글자라고 말하면 거기 온 '0'은 O다.

    작품 포맷을 하드코딩하지 않는다: 템플릿이 그 잡의 데이터에서 나오므로,
    진짜 숫자 필드인 작품은 템플릿 자체가 D라 아무것도 바뀌지 않는다. 닮은꼴
    쌍이 없는 문자('4' 등)나 길이가 안 맞는 텍스트는 None(억지 교정 금지).

    pos_values(토큰 위치별 코퍼스 값 집합)로 한 겹 더 막는다: **값이 하나뿐인
    고정 필드**(쇼 번호·'AC'·'v01' 같은 접미)에서는 코퍼스에 없는 새 값을
    만들지 않는다. 'HH030Z'를 닮은꼴로 밀면 'HH0302'가 되는데 그 코퍼스의
    쇼 번호는 언제나 HH0307이다 — 이건 닮은꼴 교체가 아니라 그냥 깨진 판독이다.
    변하는 필드(씬 ID처럼 값이 여럿)는 코퍼스가 정답을 열거해 줄 수 없으므로
    문자종류 템플릿을 근거로 삼는다.
    """
    pieces: list[str] = []
    changed: set[int] = set()
    pos = 0
    for i, shape in enumerate(template):
        piece = ""
        for kind, length in _SHAPE_RE.findall(shape):
            for _ in range(int(length)):
                if pos >= len(flat):
                    return None
                c = flat[pos]
                if _cls(c) != kind:
                    sub = (_TO_DIGIT.get(c) if kind == "D"
                           else _TO_UPPER.get(c) if kind == "U" else None)
                    if sub is None:
                        return None
                    c = sub
                    changed.add(i)
                piece += c
                pos += 1
        pieces.append(piece)
    if pos != len(flat) or not changed:
        return None
    for i in changed:
        vals = pos_values[i] if i < len(pos_values) else frozenset()
        if len(vals) <= 1 and pieces[i] not in vals:
            return None
    return "_".join(pieces)


def _fill_template(flat: str, template: list[str]) -> str | None:
    """squash된 문자열을 템플릿 모양대로 채운다 — 못 채우거나 숫자가 남으면
    None(어디서 끊을지 모호한 자릿수 잔여는 억지 교정 금지)."""
    cls = _cls
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
        return None
    return "_".join(out)


def _reparse(text: str, template: list[str], delimiters: list[str],
             known: frozenset[str] = frozenset(),
             context: frozenset[str] = frozenset(),
             pos_values: tuple[frozenset[str], ...] = ()) -> str | None:
    """구분자를 잃고 붙은 텍스트를 템플릿 모양대로 다시 쪼갠다. 직접 채우기가
    실패하면 '한 글자 삭제' 관용을 시도한다 — OCR이 구분자를 숫자로 환각하는
    삽입 오독(실기 '_'→'1': HH0307_07510040) 대응. 숫자열은 어느 숫자를 지워도
    형태가 맞아 유일성만으로는 판정 불가 — 삭제 후보 중 **코퍼스에서 깨끗하게
    읽힌 텍스트(known)와 일치하는 것이 정확히 하나**일 때만 교정한다(같은
    씬의 정상 판독이 다른 런에 존재한다는 게 근거; Z-오염류 가짜 후보는
    코퍼스에 없어 자동 배제)."""
    toks = tokenize(text, delimiters)
    if (len(toks) == len(template)
            and all(token_shape(t) == s for t, s in zip(toks, template))):
        return None  # 이미 정상
    flat = "".join(_squash_ws(t) for t in toks)
    fixed = _fill_template(flat, template)
    if fixed is not None:
        return fixed
    # 닮은꼴 오독(글자↔숫자) — 길이는 맞고 문자종류만 어긋난 경우. 삭제 관용은
    # 길이가 안 맞을 때의 수단이라 서로 겹치지 않는다.
    coerced = _coerce_lookalikes(flat, template, pos_values)
    if coerced is not None:
        return coerced
    if known:
        matched = {c for i in range(len(flat))
                   if (c := _fill_template(flat[:i] + flat[i + 1:],
                                           template)) is not None and c in known}
        if len(matched) == 1:
            return matched.pop()
        # 코퍼스 유일성이 죽는 경우('HH03041130_0040'은 '1' 삭제→130_0040,
        # '3' 삭제→110_0040 둘 다 실존) — 이웃 런의 깨끗한 판독(context)으로
        # 판별한다. 슬레이트는 씬 내내 같으므로 오독의 정답은 대개 바로 이웃
        # 런에 있다(실기 22:28 블록이 별개 씬으로 살아남던 원인).
        if len(matched) > 1 and context:
            ctx_hit = matched & context
            if len(ctx_hit) == 1:
                return ctx_hit.pop()
    return None


# 이웃-맥락 판별 창(런 수) — 삽입 오독의 정답 후보가 이 안의 깨끗한 판독에
# 있어야 교정한다. 씬 하나가 보통 수~수십 런이라 4면 같은 씬을 벗어나기 어렵고,
# 멀리 있는 동형 라벨(다른 시퀀스의 같은 씬 번호)이 오염원이 되는 것을 막는다.
_CTX_WINDOW = 4


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
    # 코퍼스의 '깨끗한' 텍스트(canonical형) — 삽입 오독 삭제 후보의 근거 집합.
    known = frozenset(
        "_".join(_squash_ws(t) for t in toks)
        for text in texts if text
        for toks in [tokenize(text, delimiters)]
        if len(toks) == len(template)
        and all(token_shape(t) == s for t, s in zip(toks, template)))
    # 토큰 위치별 코퍼스 값 집합 — 닮은꼴 교정이 '고정 필드'에 없는 값을
    # 지어내지 못하게 막는 근거(_coerce_lookalikes 참조).
    pos_values = tuple(frozenset(k.split("_")[i] for k in known)
                       for i in range(len(template)))
    # 이웃 창의 깨끗한 판독 — 삽입 오독 삭제 후보가 코퍼스에서 둘 이상과
    # 일치할 때의 판별 근거(_reparse context). 창은 ±_CTX_WINDOW 런.
    clean: list[str | None] = []
    for text in texts:
        toks = tokenize(text, delimiters) if text else []
        ok = (len(toks) == len(template)
              and all(token_shape(t) == s for t, s in zip(toks, template)))
        clean.append("_".join(_squash_ws(t) for t in toks) if ok else None)
    out: list[str] = []
    for i, text in enumerate(texts):
        ctx = frozenset(
            c for j in range(max(0, i - _CTX_WINDOW),
                             min(len(texts), i + _CTX_WINDOW + 1))
            if j != i and (c := clean[j]) is not None)
        fixed = (_reparse(text, template, delimiters, known, ctx, pos_values)
                 if text else None)
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
    # 판독불가 런은 블록으로 모아뒀다가 다음 유효 런에서 귀속을 판정한다:
    # 같은 키면 그냥 삼켜지고, 키가 바뀌면 ①텍스트 근거 우선 — 블록 안에
    # 읽히긴 했지만 파싱 불가인 런("HH0307_0900190AC V01"처럼 구분자 유실이
    # canonical화 한도를 넘은 오독; 실기 0180 꼬리에 0190 첫 런 3프레임이
    # 붙던 케이스)이 다음 라벨과 squash 접두 일치하면 그 런부터 다음 씬이다.
    # ②텍스트가 전혀 없으면 픽셀 근거 — 블록 '들어가는 컷'이 다음 런의
    # 컷보다 훨씬 셀 때(≥_UNREADABLE_MOVE_RATIO배)만 다음 씬의 머리로 본다
    # (실기 0040→0050: 4248 vs 47). 신호가 약하거나 미기록(구 데이터
    # cut_diff=0)이면 기존대로 앞 씬에 붙인다. 블록 텍스트가 앞 라벨과
    # 일치하면(앞 씬 꼬리 오독) 픽셀이 세도 앞 씬에 남는다 — 텍스트가 항상
    # 픽셀보다 우선한다.
    groups: list[list] = []
    last_key: str | None = None
    pending_start: int | None = None
    pending_cut = 0
    pending_end = 0
    pending_texts: list[tuple[int, str]] = []
    for run in runs:
        toks = tokenize(run.text, rule.delimiters) if run.text else []
        key = grouping_key(toks, indices)
        if key is None:
            if pending_start is None:
                pending_start, pending_cut = run.start_ms, run.cut_diff
            if run.text:
                pending_texts.append((run.start_ms, run.text))
            pending_end = run.end_ms
            continue
        if groups and key == last_key:
            groups[-1][3] = run.end_ms
        else:
            label = build_label(toks, upto)
            start = run.start_ms
            if pending_start is not None:
                prev_label = groups[-1][1] if groups else ""
                moved = next(
                    (st for st, txt in pending_texts
                     if label_matches(txt, label, prev_label, rule.delimiters)),
                    None)
                matches_prev = any(
                    label_matches(txt, prev_label, label, rule.delimiters)
                    for _st, txt in pending_texts)
                if moved is not None:
                    start = moved
                elif (not matches_prev and groups and pending_cut > 0
                        and pending_cut >= _UNREADABLE_MOVE_RATIO * run.cut_diff):
                    start = pending_start
            if groups:
                groups[-1][3] = start
            groups.append([key, label, start, run.end_ms])
        pending_start = None
        pending_texts = []
        last_key = key
    if groups and pending_start is not None:
        groups[-1][3] = pending_end  # 꼬리 블록은 앞 씬에

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
