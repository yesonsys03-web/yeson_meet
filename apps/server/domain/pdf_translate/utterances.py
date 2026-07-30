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
_CONT_MARKER_RE = re.compile(r"^\s*\((?:CONT|Cont)\.?\)\s*")

# _strip_cue_header 전용: 화자 런 전체를 안전하게 소비하려면 "괄호 주석이
# 최소 하나는 뒤따른다"는 신호가 필요하다 — 그렇지 않으면 본문의 대문자
# 단독 단어("I", "Well" 등)를 화자명 일부로 오인해 삼킬 위험이 있다(실제
# 이 코퍼스의 다중 토큰 화자 조각은 전부 (CONT.)/(O.S.) 류 주석을
# 동반한다). 공백으로 토큰을 나눠 (1) 소문자가 섞이지 않은 "전부
# 대문자류" 토큰을 화자 런으로 소비하고 (2) 곧이어 오는 괄호 토큰들을
# 주석으로 소비한다.
_HEADER_TOKEN_RE = re.compile(r"^[A-Z0-9'&/.\-#]+$")
_ANNOTATION_TOKEN_RE = re.compile(r"^\([^()]*\)$")


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


def _strip_cue_header(text: str) -> str:
    """체인 후속 조각에서 큐 헤더(큐번호 + 화자 런 전체 + (O.S.)/(V.O.)/
    (CONT.) 등 괄호 주석 전부)를 제거한 나머지. (CONT.)-헤더만 있는
    조각은 빈 문자열을 반환한다.

    화자 런 뒤에 괄호 주석이 하나도 없으면(=진짜 계속(cont) 표시가 없는
    예외 케이스) 화자 런의 끝을 안전하게 구분할 신호가 없으므로, 기존
    _CUE_RE의 lazy 경계로 대체 동작한다(단일 토큰 화자에서는 이 경로로도
    정확하다)."""
    m = _CUE_RE.match(text)
    if m is None:
        return text.strip()

    tokens = text.split()
    n = len(tokens)
    i = 1  # tokens[0] = 큐번호
    while i < n and _HEADER_TOKEN_RE.match(tokens[i]):
        i += 1
    saw_annotation = False
    while i < n and _ANNOTATION_TOKEN_RE.match(tokens[i]):
        saw_annotation = True
        i += 1

    if not saw_annotation:
        rest = _CONT_MARKER_RE.sub("", text[m.end():], count=1)
        return rest.strip()

    return " ".join(tokens[i:]).strip()


def group_utterances(
    blocks: list[PdfBlock],
) -> tuple[list[UtteranceGroup], list[str]]:
    """blocks 전체(모든 kind) → (그룹 목록, 그룹별 번역 입력 텍스트).

    dialog 외 kind와 큐 패턴이 없는 dialog는 각자 1블록짜리 그룹. 추출
    순서상 연속된 dialog 블록이 같은 (큐번호, 화자)를 공유하면 한 그룹으로
    묶여, 첫 조각의 전체 텍스트에 후속 조각들의 큐 헤더 제거분을 공백으로
    이어 붙인다(전부 헤더-only면 첫 조각 원문 그대로 — 어쩔 수 없는
    케이스). 소스가 누적 대사를 다음 패널에 재인쇄하는 경우 그 조각은
    이미 누적된 텍스트에 포함돼 있으므로 새 내용을 기여하지 않는다 —
    멤버로는 여전히 집계되지만 merged_text에는 반영하지 않고 건너뛴다.
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

    def flush_chain() -> None:
        nonlocal chain_key, chain_slot, chain_merged
        if chain_slot >= 0:
            groups[chain_slot] = UtteranceGroup(
                member_indices=list(chain_indices),
                merged_text=chain_merged)
        chain_key = None
        chain_slot = -1
        chain_indices.clear()
        chain_merged = ""

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
            piece = _strip_cue_header(block.text)
            if piece and _normalize_ws(piece) not in _normalize_ws(chain_merged):
                chain_merged = f"{chain_merged} {piece}"
        else:
            flush_chain()
            chain_key = key
            chain_slot = len(groups)
            groups.append(None)  # 체인이 닫힐 때 채워짐(flush_chain)
            chain_indices.append(i)
            chain_merged = block.text

    flush_chain()

    resolved: list[UtteranceGroup] = []
    for g in groups:
        assert g is not None  # 마지막 flush_chain()이 열린 슬롯을 모두 채운다
        resolved.append(g)
    texts = [g.merged_text for g in resolved]
    return resolved, texts
