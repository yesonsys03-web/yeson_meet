"""발화 단위 병합 — Task 17 (사람 번역본 전수 비교검토 P1+P8).

한 발화(대사)가 여러 패널 페이지에 걸치면(`97 JOSEPH` → `97 JOSEPH (Cont.)`
→ ...) 추출은 소스를 페이지 조각으로 나눈다. 조각을 각각 번역하면 어순이
붕괴하고((CONT.) 조각만 있는 페이지는 "화자 (계속)"류만 남는다), 사람
번역본 관례(예외 없음)는 발화 전문을 번역해 그 발화가 걸친 모든 페이지에
동일하게 반복 기재한다 — 이 모듈은 그 관례를 결정적 파이프라인으로
재현한다: dialog 블록을 큐(번호+화자) 기준으로 체인 묶고, 번역 입력을
그룹(발화) 단위로 낮춘다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .profiles.base import PdfBlock

# 큐 헤더 파싱(체인 키 전용): 선두 큐번호 + 화자명, 뒤따르는
# "(CONT.)"/"(Cont.)"는 이 매치 자체에는 포함되지 않는다(마지막 \b가
# 화자명 직후 경계에서 먼저 만족되기 때문). 화자명에 공백·/·.·#가 섞이면
# 이 lazy 매치는 첫 \b에서 멈춰 화자명 중간에서 끊긴다 — 하지만 모든
# 조각에서 *일관되게* 같은 위치에서 끊기므로 체인 키(큐번호, 화자)로서는
# 무해하다(체인은 정상 형성된다). 본문에서 헤더를 실제로 제거하는 것은
# 아래 _strip_cue_header가 이 잘린 키에 기대지 않고 별도로 담당한다.
_CUE_RE = re.compile(
    r"^(\d+)\s+([A-Z][A-Z'/.()&\- ]*?)(?:\s*\((?:CONT|Cont)\.?\))?\b")

# _strip_cue_header 전용: 화자 런 소비는 이 조각 자신의 텍스트만으로
# 판단한다 — 공백으로 토큰을 나눠 소문자가 섞이지 않은 "전부 대문자류"
# 토큰(`_HEADER_TOKEN_RE`)을 삼킨다. 다음 토큰이 본문 소문자 단어라면
# 자연히 거기서 멈춘다(예: "BILL/DALE/BOOMHAUER Oh..."의 "Oh"). 화자 런
# 뒤에 곧바로 오는 연속된 괄호 토큰 구간(`_ANNOTATION_TOKEN_RE`)에서는
# (CONT.)/(Cont.) 계속 표시(`_CONT_TOKEN_RE`)만 제거하고, (O.S.)/(V.O.)
# 등 다른 주석은 조각 고유의 본문 정보로 보존한다. 주의: `_HEADER_TOKEN_RE`는
# 형태(대소문자·기호)만 보고 의미를 모른다 — 첫 화자 토큰은 항상 포함하되,
# 그 다음 토큰부터는 (a) 런 직후에 괄호 주석이 오거나 (b) 그 토큰이 같은
# 체인의 다른 멤버 런에서 이미 확인된 화자 토큰일 때만 잇는다
# (`_speaker_run_bounds`/`_confirmed_speaker_tokens`). 이 가드가 없으면
# "(CONT.)"가 없는 조각에서 본문 선두의 대문자 단독 단어("12 HANK NO. I
# mean it."의 "NO.", "9 DONNA OK fine."의 "OK")까지 화자 런으로 삼켜
# 본문에서 사라진다 — 실제로 관측된 회귀였다.
_HEADER_TOKEN_RE = re.compile(r"^[A-Z0-9'&/.\-#]+$")
_ANNOTATION_TOKEN_RE = re.compile(r"^\([^()]*\)$")
_CONT_TOKEN_RE = re.compile(r"^\((?:CONT|Cont)\.?\)$")

# 추출 과정에서 화자명과 괄호 주석 사이 공백이 없는 조각이 실제로
# 나온다(예: "34 BOBBY(CONT.) ...you."). 공백 기반 토큰화가 이를
# 한 토큰으로 붙여버려 화자 런·주석 인식이 전부 실패하므로, 소비 전에
# 공백이 아닌 문자 바로 뒤에 붙은 "("를 띄어 놓는다.
_GLUED_PAREN_RE = re.compile(r"(?<=[^\s(])(\()")


def _normalize_ws(text: str) -> str:
    """포함관계 비교 전용 정규화: 공백뿐 아니라 말줄임표(...) 전후 공백
    유무 차이도 흡수한다 — 실물 코퍼스에서 같은 재인쇄가 페이지마다
    `But... look`/`...look`처럼 붙거나 떨어져 나타난다(예: GABE01_A1
    p85-93 DONNA 체인)."""
    text = re.sub(r"\.{2,}", " ... ", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class UtteranceGroup:
    member_indices: list[int]   # blocks 리스트 내 인덱스 (페이지 순)
    merged_text: str            # 번역 입력 (화자 헤더 1회 + 조각 이어붙임)


def _parse_cue(text: str) -> tuple[str, str] | None:
    """dialog 블록 텍스트 선두의 (큐번호, 화자) — 매치 실패 시 None."""
    m = _CUE_RE.match(text)
    if m is None:
        return None
    return m.group(1), m.group(2)


def _speaker_run_bounds(tokens: list[str]) -> int:
    """tokens[0]=큐번호인 토큰 리스트에서, 소문자가 섞이지 않은 "전부
    대문자류" 토큰이 최대 몇 번째까지 이어지는지(배타적 끝 인덱스)를
    반환한다. 첫 화자 토큰(tokens[1])은 형태가 맞으면 무조건 포함하고,
    그 다음부터는 길이 1인 토큰("I" 등 본문 대명사와 형태가 같다)에서
    멈춘다 — 이 함수는 "최대로 가능한" 경계만 계산할 뿐, 그 경계까지
    실제로 신뢰할지는 호출부가 별도로 판단한다."""
    n = len(tokens)
    i = 1
    if i < n and _HEADER_TOKEN_RE.match(tokens[i]):
        i += 1
        while i < n and len(tokens[i]) > 1 and _HEADER_TOKEN_RE.match(tokens[i]):
            i += 1
    return i


def _confirmed_speaker_tokens(text: str) -> list[str]:
    """이 텍스트 하나만으로 화자 런 전체를 신뢰할 수 있는 경우(런 직후
    괄호 주석이 옴)에만 그 화자 토큰들(첫 토큰 제외)을 반환한다 — 체인의
    다른 멤버가 이 토큰들을 알 수 있게 누적시키기 위함(아래
    `_strip_cue_header`의 조건 (b))."""
    tokens = _GLUED_PAREN_RE.sub(r" \1", text).split()
    n = len(tokens)
    max_end = _speaker_run_bounds(tokens)
    if max_end < n and _ANNOTATION_TOKEN_RE.match(tokens[max_end]):
        return tokens[2:max_end]
    return []


def _strip_cue_header(
    text: str, seen_text: str, known_speaker_tokens: set[str],
) -> str:
    """체인 후속 조각에서 큐 헤더(큐번호 + 화자 런)를 제거한 나머지에서
    (CONT.)/(Cont.) 계속 표시를 제거한다. 화자 런 소비는 첫 화자 토큰은
    무조건 포함하고, 그 다음 토큰부터는 (a) 런 직후에 괄호 주석이 오거나
    (b) `known_speaker_tokens`(체인의 다른 멤버 런에서 이미 확인된 화자
    토큰)에 있을 때만 잇는다 — 위 정규식 주석 참고. (O.S.)/(V.O.) 등 다른
    주석은 원칙적으로 조각 고유의 본문 정보로 보존하되, `seen_text`(체인에
    지금까지 누적된 텍스트)에 이미 등장한 토큰이면 같은 주석의 반복이므로
    함께 제거한다(예: 첫 멤버가 이미 "(O.S.)"를 달고 있었다면 후속 조각의
    "(O.S.)" 반복은 새 정보가 아니다). (CONT.)-헤더만 있는 조각(뒤에 다른
    본문이 없음)은 빈 문자열을 반환한다."""
    if _CUE_RE.match(text) is None:
        return text.strip()

    tokens = _GLUED_PAREN_RE.sub(r" \1", text).split()
    n = len(tokens)
    max_end = _speaker_run_bounds(tokens)
    if max_end < n and _ANNOTATION_TOKEN_RE.match(tokens[max_end]):
        i = max_end
    else:
        i = 1
        if i < max_end:
            i = 2  # 첫 화자 토큰은 무조건 포함
            while i < max_end and tokens[i] in known_speaker_tokens:
                i += 1

    seen_tokens = seen_text.split()
    kept: list[str] = []
    while i < n and _ANNOTATION_TOKEN_RE.match(tokens[i]):
        if not _CONT_TOKEN_RE.match(tokens[i]) and tokens[i] not in seen_tokens:
            kept.append(tokens[i])
        i += 1

    return " ".join(kept + tokens[i:]).strip()


def _overlap_trim(acc: str, piece: str) -> str:
    """acc(누적 텍스트)의 꼬리와 piece(새 조각)의 시작이 토큰 단위로
    겹치는 부분을 찾아 잘라내고, piece가 새로 기여하는 부분만 반환한다.

    소스가 누적 대사를 다음 패널에 재인쇄할 때 그 재인쇄가 (a) 이미
    누적된 문장 전체와 같거나(완전 포함) (b) 그 문장을 반복한 뒤 새
    내용을 이어 붙이는(부분 중첩) 두 형태 모두를 흡수한다 — (a)는 빈
    문자열을, (b)는 겹친 접두를 뺀 나머지를 반환한다. 겹치는 부분이
    없으면 piece를 그대로 반환(원문 형식 보존)."""
    acc_tokens = acc.split()
    piece_tokens = piece.split()
    if not piece_tokens:
        return ""

    max_k = min(len(acc_tokens), len(piece_tokens))
    for k in range(max_k, 0, -1):
        if acc_tokens[-k:] == piece_tokens[:k]:
            return " ".join(piece_tokens[k:])

    # 원문 토큰 그대로는 겹침을 못 찾았다면, 말줄임표 전후 공백 유무
    # 차이 등으로 인한 완전 포함인지 정규화 비교로 한 번 더 확인한다
    # (부분 중첩까지는 재구성하지 않는다 — 완전 포함만 여기서 흡수).
    if _normalize_ws(piece) in _normalize_ws(acc):
        return ""

    return piece.strip()


def group_utterances(
    blocks: list[PdfBlock],
) -> tuple[list[UtteranceGroup], list[str]]:
    """blocks 전체(모든 kind) → (그룹 목록, 그룹별 번역 입력 텍스트).

    dialog 외 kind와 큐 패턴이 없는 dialog는 각자 1블록짜리 그룹. 추출
    순서상 연속된 dialog 블록이 같은 (큐번호, 화자)를 공유하면 한 그룹으로
    묶여, 첫 조각의 전체 텍스트에 후속 조각들의 큐 헤더 제거분을 공백으로
    이어 붙인다(전부 헤더-only면 첫 조각 원문 그대로 — 어쩔 수 없는
    케이스). 소스가 누적 대사를 다음 패널에 재인쇄하는 경우(완전
    반복이든, 반복 뒤 새 내용이 이어지는 부분 중첩이든) 그 재인쇄와
    겹치는 부분은 `_overlap_trim`이 잘라내 새로 기여하는 부분만 남긴다 —
    멤버로는 여전히 집계되지만 겹치는 부분은 merged_text에 반영하지 않는다.
    사이에 낀 action/panel 블록은 dialog 시퀀스 기준으로 체인을 끊지
    않는다 — 각자 단독 그룹으로 그 자리에 남는다. 다른 큐의 dialog(또는
    큐 패턴이 없는 dialog)가 끼면 체인이 끝난다.

    반환되는 그룹 목록의 순서는 각 그룹이 시작된 위치(첫 멤버의 blocks
    인덱스) 기준 — 사이에 낀 action/panel 그룹이 아직 열려 있는 체인보다
    먼저 등장했다면 그 순서 그대로 나온다.
    """
    groups: list[UtteranceGroup | None] = []
    chain_key: tuple[str, str] | None = None
    chain_slot = -1
    chain_indices: list[int] = []
    chain_merged = ""
    chain_speaker_tokens: set[str] = set()

    def flush_chain() -> None:
        nonlocal chain_key, chain_slot, chain_merged, chain_speaker_tokens
        if chain_slot >= 0:
            groups[chain_slot] = UtteranceGroup(
                member_indices=list(chain_indices),
                merged_text=chain_merged)
        chain_key = None
        chain_slot = -1
        chain_indices.clear()
        chain_merged = ""
        chain_speaker_tokens = set()

    for i, block in enumerate(blocks):
        if block.kind != "dialog":
            groups.append(UtteranceGroup(member_indices=[i], merged_text=block.text))
            continue
        key = _parse_cue(block.text)
        if key is None:
            flush_chain()
            groups.append(UtteranceGroup(member_indices=[i], merged_text=block.text))
            continue
        if chain_slot >= 0 and key == chain_key:
            chain_indices.append(i)
            piece = _strip_cue_header(block.text, chain_merged, chain_speaker_tokens)
            chain_speaker_tokens.update(_confirmed_speaker_tokens(block.text))
            if piece:
                contribution = _overlap_trim(chain_merged, piece)
                if contribution:
                    chain_merged = f"{chain_merged} {contribution}"
        else:
            flush_chain()
            chain_key = key
            chain_slot = len(groups)
            groups.append(None)  # 체인이 닫힐 때 채워짐(flush_chain)
            chain_indices.append(i)
            chain_merged = block.text
            chain_speaker_tokens = set(_confirmed_speaker_tokens(block.text))

    flush_chain()

    resolved: list[UtteranceGroup] = []
    for g in groups:
        assert g is not None  # 마지막 flush_chain()이 열린 슬롯을 모두 채운다
        resolved.append(g)
    texts = [g.merged_text for g in resolved]
    return resolved, texts
