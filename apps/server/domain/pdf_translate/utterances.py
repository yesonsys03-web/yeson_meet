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

# 큐 헤더 파싱: 선두 큐번호 + 화자명, 뒤따르는 "(CONT.)"/"(Cont.)"는 이
# 매치 자체에는 포함되지 않는다(마지막 \b가 화자명 직후 경계에서 먼저
# 만족되기 때문) — (CONT.) 제거는 _strip_cue_header가 별도로 담당한다.
_CUE_RE = re.compile(
    r"^(\d+)\s+([A-Z][A-Z'/.()&\- ]*?)(?:\s*\((?:CONT|Cont)\.?\))?\b")
_CONT_MARKER_RE = re.compile(r"^\s*\((?:CONT|Cont)\.?\)\s*")


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
    """체인 후속 조각에서 큐 헤더(큐번호+화자, 뒤따르는 (CONT.) 포함)를
    제거한 나머지. (CONT.)-헤더만 있는 조각은 빈 문자열을 반환한다."""
    m = _CUE_RE.match(text)
    if m is None:
        return text.strip()
    rest = _CONT_MARKER_RE.sub("", text[m.end():], count=1)
    return rest.strip()


def group_utterances(
    blocks: list[PdfBlock],
) -> tuple[list[UtteranceGroup], list[str]]:
    """blocks 전체(모든 kind) → (그룹 목록, 그룹별 번역 입력 텍스트).

    dialog 외 kind와 큐 패턴이 없는 dialog는 각자 1블록짜리 그룹. 추출
    순서상 연속된 dialog 블록이 같은 (큐번호, 화자)를 공유하면 한 그룹으로
    묶여, 첫 조각의 전체 텍스트에 후속 조각들의 큐 헤더 제거분을 공백으로
    이어 붙인다(전부 헤더-only면 첫 조각 원문 그대로 — 어쩔 수 없는
    케이스). 사이에 낀 action/panel 블록은 dialog 시퀀스 기준으로 체인을
    끊지 않는다 — 각자 단독 그룹으로 그 자리에 남는다. 다른 큐의 dialog
    (또는 큐 패턴이 없는 dialog)가 끼면 체인이 끝난다.

    반환되는 그룹 목록의 순서는 각 그룹이 시작된 위치(첫 멤버의 blocks
    인덱스) 기준 — 사이에 낀 action/panel 그룹이 아직 열려 있는 체인보다
    먼저 등장했다면 그 순서 그대로 나온다.
    """
    groups: list[UtteranceGroup | None] = []
    chain_key: tuple[str, str] | None = None
    chain_slot = -1
    chain_indices: list[int] = []
    chain_pieces: list[str] = []

    def flush_chain() -> None:
        nonlocal chain_key, chain_slot
        if chain_slot >= 0:
            groups[chain_slot] = UtteranceGroup(
                member_indices=list(chain_indices),
                merged_text=" ".join(chain_pieces))
        chain_key = None
        chain_slot = -1
        chain_indices.clear()
        chain_pieces.clear()

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
            if piece:
                chain_pieces.append(piece)
        else:
            flush_chain()
            chain_key = key
            chain_slot = len(groups)
            groups.append(None)  # 체인이 닫힐 때 채워짐(flush_chain)
            chain_indices.append(i)
            chain_pieces.append(block.text)

    flush_chain()

    resolved: list[UtteranceGroup] = []
    for g in groups:
        assert g is not None  # 마지막 flush_chain()이 열린 슬롯을 모두 채운다
        resolved.append(g)
    texts = [g.merged_text for g in resolved]
    return resolved, texts
