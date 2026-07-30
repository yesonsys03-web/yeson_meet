"""Storyboard Pro 익스포트 프로파일 (King of the Hill GABE01 실측 기반).

페이지당 판넬 1장 + 고정 위치 'Dialog'/'Action Notes' 라벨. 템플릿 익스포트라
라벨 x좌표가 전 페이지 동일 — 라벨 블록을 찾고 그 아래 가장 가까운 블록을
내용으로 본다(라벨과 내용이 한 블록으로 붙어 나오는 변형도 처리).
"""
from __future__ import annotations

import math

from ..backend import PdfDocument, RawBlock
from .base import Overlay, PdfBlock, has_hangul, normalize_ws

_FIELDS = (("Dialog", "dialog"), ("Action Notes", "action"))
_FONTSIZE = 12.0          # 기존 수작업 번역본 실측(AdobeMyungjoStd 12pt)
_GAP = 4.0                # 원문 블록과 주석 사이 여백(pt)
_MIN_WIDTH = 280.0        # 주석 박스 최소 폭(아래 배치 폴백)
_MIN_RIGHT_WIDTH = 180.0  # 오른쪽 배치가 성립하려면 필요한 최소 여유폭
_FONT_SIZES = (12.0, 10.0, 9.0, 8.0)  # 축소 폴백 사다리(최후 8pt로 확정)
_DETECT_PAGES = 3
# 실물(GABE01) 실측: 빈 Dialog 필드는 플레이스홀더 블록 없이 통째로
# 생략된다 — 그러면 "가장 가까운 아래 블록"이 다음 필드의 라벨 자체가
# 되어버린다("Action Notes" 문자열이 대사로 오인식). 라벨 텍스트는
# 후보에서 항상 제외한다.
_LABEL_TEXTS = frozenset(label for label, _kind in _FIELDS)


def _looks_like_field_label(text: str) -> bool:
    """라벨 그대로이거나(빈 필드 다음 라벨) 라벨로 시작하면(다음 필드가
    라벨+내용 한 블록으로 붙어 나온 변형) 후보에서 제외 — 안 그러면 Dialog가
    비었을 때 병합된 'Action Notes: ...' 블록을 통째로 대사로 잘못 집어온다
    (리뷰어 실측 재현 케이스)."""
    return any(text == lbl or text.startswith(lbl) for lbl in _LABEL_TEXTS)


class StoryboardProfile:
    name = "storyboard"
    label = "스토리보드 (Storyboard Pro)"

    def detect(self, doc: PdfDocument) -> bool:
        w, h = doc.page_size(0)
        if w <= h:  # 가로형이 아니면 아님
            return False
        found: set[str] = set()
        for page in range(min(_DETECT_PAGES, doc.page_count)):
            for b in doc.raw_blocks(page):
                t = normalize_ws(b.text)
                for label, _kind in _FIELDS:
                    if t == label or t.startswith(label):
                        found.add(label)
        return len(found) == len(_FIELDS)

    def extract(self, doc: PdfDocument) -> list[PdfBlock]:
        out: list[PdfBlock] = []
        for page in range(doc.page_count):
            raws = doc.raw_blocks(page)
            for i, (label, kind) in enumerate(_FIELDS):
                next_label = _FIELDS[i + 1][0] if i + 1 < len(_FIELDS) else None
                content = _field_content(raws, label, next_label)
                if content is None:
                    continue
                text = normalize_ws(content.text)
                if not text or has_hangul(text):
                    continue
                out.append(PdfBlock(page=page, kind=kind, text=text,
                                    bbox=content.bbox))
        return out

    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        """오른쪽 우선 배치(2026-07-30 실기 피드백): 필드 박스는 페이지
        전폭이고 원문은 좌측 절반만 차지하는 실물 관례를 따라, 원문 오른쪽
        빈 공간에 y 정렬로 나란히 배치한다. 오른쪽 여유가 부족하면 기존처럼
        블록 아래에 배치. 어느 경로든 페이지 하단을 넘으면 예전처럼 위로
        밀어 올리지 않고(그게 원문을 덮는 원인이었다) 폰트를 축소해
        page_h - 4 안에 맞춘다."""
        page_w, page_h = page_size
        bx0, by0, bx1, by1 = block.bbox
        right_w = page_w - 8.0 - (bx1 + 8.0)
        if right_w >= _MIN_RIGHT_WIDTH:
            x0 = bx1 + 8.0
            x1 = page_w - 8.0
            y0 = by0  # 원문 첫 줄과 y 정렬
        else:
            x0 = bx0
            x1 = min(page_w - 8.0, max(bx1, x0 + _MIN_WIDTH))
            y0 = by1 + _GAP
        rect, fontsize = _fit_rect(x0, y0, x1, page_h, ko_text)
        return Overlay(page=block.page, rect=rect, text=ko_text,
                       fontsize=fontsize)


def _field_content(raws: list[RawBlock], label: str,
                    next_label: str | None = None) -> RawBlock | None:
    """라벨 아래, '다음 라벨' 앞까지의 창 안에 있는 **모든** 콘텐츠 블록을
    y좌표 오름차순으로 병합해 하나의 RawBlock으로 반환한다(2026-07-30
    E2E 실측: Dialog 754페이지 중 45%가 화자 줄과 대사가 별개 블록으로
    나뉜다 — 최근접 1블록만 집으면 실제 대사가 누락된다).
    라벨+내용이 한 블록이면 라벨 접두를 뗀 나머지를 첫 조각으로 삼고,
    그 아래 창 안의 추가 후보들도 이어 붙인다.

    실물(GABE01) 실측: 필드가 비어 있으면 플레이스홀더 없이 통째로
    생략된다 — 그래서 후보를 다음 필드 라벨의 y좌표 위로 제한해야 한다.
    안 그러면 Dialog가 비었을 때 그 아래 Action Notes '내용'까지
    건너뛰어 잘못 집어온다(라벨 텍스트만 걸러서는 못 막는다)."""
    label_block = None
    first_piece: RawBlock | None = None
    for b in raws:
        t = normalize_ws(b.text)
        if t == label:
            label_block = b
            break
        if t.startswith(label) and len(t) > len(label):
            rest = t[len(label):].lstrip(" :")
            if rest:
                first_piece = RawBlock(text=rest, bbox=b.bbox)
                break
    anchor = label_block if label_block is not None else first_piece
    if anchor is None:
        return None
    lx0, _ly0, _lx1, ly1 = anchor.bbox
    upper_bound = None
    if next_label is not None:
        for b in raws:
            t = normalize_ws(b.text)
            # 다음 라벨이 내용과 한 블록으로 붙어 나오는 변형도 경계로
            # 인정해야 한다 — 안 그러면 upper_bound가 None으로 남아
            # 현재 필드의 창이 무한정 열려서 다음 필드 아래쪽 블록까지
            # 잘못 병합해온다(리뷰어 실측 재현: 병합 라벨형 다음-필드
            # 경계 인식 실패로 인한 크로스필드 누수).
            if t == next_label or (t.startswith(next_label)
                                    and len(t) > len(next_label)):
                upper_bound = b.bbox[1]
                break
    candidates = [b for b in raws
                  if b.bbox[1] >= ly1 - 1.0
                  and (upper_bound is None or b.bbox[1] < upper_bound - 1.0)
                  and abs(b.bbox[0] - lx0) < 60.0
                  and not _looks_like_field_label(normalize_ws(b.text))]
    pieces = ([first_piece] if first_piece is not None else []) + \
        sorted(candidates, key=lambda b: b.bbox[1])
    if not pieces:
        return None
    return _merge_pieces(pieces)


def _merge_pieces(pieces: list[RawBlock]) -> RawBlock:
    """y좌표 순 후보 블록들을 공백 1개로 이어붙이고 bbox는 union으로.
    place()가 이 union 아래에 주석을 놓으므로 원본 블록 간 겹침이
    자연히 해소된다."""
    text = " ".join(normalize_ws(p.text) for p in pieces)
    x0 = min(p.bbox[0] for p in pieces)
    y0 = min(p.bbox[1] for p in pieces)
    x1 = max(p.bbox[2] for p in pieces)
    y1 = max(p.bbox[3] for p in pieces)
    return RawBlock(text=text, bbox=(x0, y0, x1, y1))


def _fit_rect(x0: float, y0: float, x1: float, page_h: float,
             ko_text: str) -> tuple[tuple[float, float, float, float], float]:
    """x0/x1/y0 고정 상태에서 페이지 하단(page_h - 4) 안에 들어오도록
    폰트를 12→10→9→8pt로 줄여가며 재추정한다. 8pt에서도 못 맞으면 8pt로
    확정하고 y1만 페이지 하단으로 클램프한다(더 이상 위로 밀어 올리지
    않는다 — 그게 원문을 덮는 원인이었다). x0가 block.x1보다 오른쪽이거나
    y0가 block.y1보다 아래이므로, 이 함수가 어떤 fontsize를 고르든 rect는
    원문 bbox와 교차하지 않는다(호출부의 기하 구성이 보장)."""
    max_y1 = page_h - 4.0
    width = x1 - x0
    fontsize = _FONT_SIZES[-1]
    height = 0.0
    for fontsize in _FONT_SIZES:
        height = _estimate_height(ko_text, width, fontsize)
        if y0 + height <= max_y1:
            break
    y1 = max(y0, min(y0 + height, max_y1))
    return (x0, y0, x1, y1), fontsize


def _estimate_height(text: str, width: float, fontsize: float) -> float:
    """CJK 근사 폭(글자당 ≈ fontsize pt)으로 줄수 → 박스 높이."""
    chars_per_line = max(8, int(width / fontsize))
    lines = max(1, math.ceil(len(text) / chars_per_line))
    return (lines + 0.5) * fontsize * 1.25
