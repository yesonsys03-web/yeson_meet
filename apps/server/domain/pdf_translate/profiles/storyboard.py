"""Storyboard Pro 익스포트 프로파일 (King of the Hill GABE01 실측 기반).

페이지당 판넬 1장 + 고정 위치 'Dialog'/'Action Notes' 라벨. 템플릿 익스포트라
라벨 x좌표가 전 페이지 동일 — 라벨 블록을 찾고 그 아래 가장 가까운 블록을
내용으로 본다(라벨과 내용이 한 블록으로 붙어 나오는 변형도 처리).
"""
from __future__ import annotations

import logging
import math
import re

from ..backend import PdfDocument, RawBlock
from ..panel_ocr import find_panel_labels
from .base import Overlay, PdfBlock, has_hangul, normalize_ws

logger = logging.getLogger("yeson.pdf.profiles.storyboard")

_FIELDS = (("Dialog", "dialog"), ("Action Notes", "action"))
_FONTSIZE = 12.0          # 기존 수작업 번역본 실측(AdobeMyungjoStd 12pt)
_GAP = 4.0                # 원문 블록과 주석 사이 여백(pt)
_MIN_WIDTH = 280.0        # 주석 박스 최소 폭(아래 배치 폴백)
_MIN_RIGHT_WIDTH = 180.0  # 오른쪽 배치가 성립하려면 필요한 최소 여유폭
_FONT_SIZES = (12.0, 10.0, 9.0, 8.0)  # 축소 폴백 사다리(최후 8pt로 확정)
_DETECT_PAGES = 3

# 패널 콜아웃 라벨(빨강 리더라인) — 패널 래스터 이미지는 헤더/씬 테이블
# (y<95) 아래, Dialog 라벨(있으면 그 위 5pt, 없으면 460pt 고정) 위까지다.
# 템플릿 익스포트라 전 페이지 동일 — 실물(GABE01) 검증으로 확정한 상수.
_PANEL_LABEL_KIND = "panel_label"
_PANEL_Y_TOP = 95.0
_PANEL_Y_BOTTOM_DEFAULT = 460.0
_PANEL_FONTSIZE = 10.0
_PANEL_MIN_WIDTH = 90.0   # 위/오른쪽 배치 라벨 폭 문턱(필드 배치보다 완화)
_PANEL_TOP_MARGIN = 24.0  # 이보다 위로 올라가면(페이지 상단 근접) 오른쪽/아래로 전환
# 실물(GABE01) 실측: 빈 Dialog 필드는 플레이스홀더 블록 없이 통째로
# 생략된다 — 그러면 "가장 가까운 아래 블록"이 다음 필드의 라벨 자체가
# 되어버린다("Action Notes" 문자열이 대사로 오인식). 라벨 텍스트는
# 후보에서 항상 제외한다.
_LABEL_TEXTS = frozenset(label for label, _kind in _FIELDS)


def _has_field_label(raws: list[RawBlock]) -> bool:
    """페이지가 실제 스토리보드 템플릿 페이지인지 판정(Dialog/Action Notes
    라벨 중 하나라도 존재) — detect()와 동일한 라벨 매칭 관례(정확 일치 또는
    라벨로 시작하는 병합형)를 그대로 재사용한다(새 매칭 변형 도입 금지)."""
    for b in raws:
        t = normalize_ws(b.text)
        for label, _kind in _FIELDS:
            if t == label or t.startswith(label):
                return True
    return False


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
                content = _field_content(raws, label, next_label,
                                         is_action=(kind == "action"))
                if content is None:
                    continue
                # normalize_ws는 \n도 공백으로 접어버려 _merge_pieces가
                # 슬러그라인 뒤에 넣은 개행(Task 19)을 지운다 — 줄 단위로
                # 정규화하고 \n 구분자는 그대로 둔다(개행이 없으면 기존과
                # 동일한 단일 정규화).
                text = "\n".join(
                    normalize_ws(line) for line in content.text.split("\n"))
                if not text or has_hangul(text):
                    continue
                out.append(PdfBlock(page=page, kind=kind, text=text,
                                    bbox=content.bbox))
            # 표지/타이틀 페이지(리뷰 후속, 2026-07-30): Dialog/Action Notes
            # 라벨이 아예 없으면 실제 패널 템플릿 페이지가 아니다 —
            # _panel_region이 이때 기본값(y_bottom=460)으로 열리는데, 표지의
            # 빨간 로고/타이틀 텍스트("KING"/"HILL")가 그 안에서 라벨로
            # 오인식되던 회귀(page 0 실물 확인)를 막는다.
            if _has_field_label(raws):
                page_w, _page_h = doc.page_size(page)
                region = _panel_region(raws, page_w)
                for raw in find_panel_labels(doc, page, region):
                    text = normalize_ws(raw.text)
                    if not text or has_hangul(text):
                        continue
                    out.append(PdfBlock(page=page, kind=_PANEL_LABEL_KIND,
                                        text=text, bbox=raw.bbox))
        return out

    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        """필드(dialog/action)는 오른쪽 우선 배치, 패널 콜아웃 라벨은
        라벨 바로 위 우선 배치로 분기한다.

        오른쪽 우선(2026-07-30 실기 피드백): 필드 박스는 페이지 전폭이고
        원문은 좌측 절반만 차지하는 실물 관례를 따라, 원문 오른쪽 빈 공간에
        y 정렬로 나란히 배치한다. 오른쪽 여유가 부족하면 기존처럼 블록
        아래에 배치(자세한 이유는 _place_right_or_below 참고)."""
        if block.kind == _PANEL_LABEL_KIND:
            return self._place_panel_label(block, ko_text, page_size)
        return _place_right_or_below(block, ko_text, page_size,
                                     min_right_width=_MIN_RIGHT_WIDTH)

    def _place_panel_label(self, block: PdfBlock, ko_text: str,
                           page_size: tuple[float, float]) -> Overlay:
        """패널 콜아웃 라벨(예: HANK'S TRUCK)은 수작업본 관례상 라벨
        **바로 위**에 고정 10pt로 놓는다(필드처럼 12→8pt 축소 사다리를
        타지 않음 — 라벨은 짧은 단어 1~2개라 축소가 불필요).

        라벨이 페이지 상단 가까이(y<95 헤더 바로 아래) 있으면 '위'가
        페이지 밖(_PANEL_TOP_MARGIN 미만)으로 나갈 수 있다 — 이때는 필드용
        오른쪽/아래 배치 로직을 그대로 재사용한다(폭 문턱만 90pt로 완화:
        필드 박스는 전폭이라 180pt 여유가 흔하지만 라벨은 패널 중간 어디든
        있을 수 있어 더 좁은 여유에서도 오른쪽을 시도해야 한다)."""
        page_w, page_h = page_size
        bx0, by0, bx1, _by1 = block.bbox
        x0 = bx0
        x1 = min(page_w - 8.0, max(bx1, bx0 + _PANEL_MIN_WIDTH))
        height = _estimate_height(ko_text, x1 - x0, _PANEL_FONTSIZE)
        y1 = by0 - 2.0
        y0 = y1 - height
        if y0 < _PANEL_TOP_MARGIN:
            return _place_right_or_below(block, ko_text, page_size,
                                         min_right_width=_PANEL_MIN_WIDTH)
        rect = _clamp_nondegenerate(x0, y0, x1, y1, page_h)
        return Overlay(page=block.page, rect=rect, text=ko_text,
                       fontsize=_PANEL_FONTSIZE)


def _place_right_or_below(
    block: PdfBlock, ko_text: str, page_size: tuple[float, float],
    *, min_right_width: float,
) -> Overlay:
    """오른쪽 여유(>= min_right_width)가 있으면 원문 오른쪽에 y 정렬로,
    없으면 원문 아래에 배치한다. 필드(dialog/action) 배치의 원래 로직이며,
    패널 라벨의 '위 배치가 페이지 밖으로 나가는' 폴백 경로도 이 함수를
    그대로 재사용한다(폭 문턱만 호출부에서 다르게 준다).

    오른쪽 경로는 x축이 이미 원문과 분리돼 있어(x0 = block.x1 + 8) y를
    얼마든 움직여도 교차 위험이 없다 — 8pt에서도 안 맞으면 페이지 상단
    쪽으로 밀어 올려 실제 텍스트가 다 보이게 한다. 아래 경로는 밀지
    않는다 — 그게 원문을 덮는 원래 버그였다(잘리더라도 원문 비침범이
    우선)."""
    page_w, page_h = page_size
    bx0, by0, bx1, by1 = block.bbox
    right_w = page_w - 8.0 - (bx1 + 8.0)
    if right_w >= min_right_width:
        x0 = bx1 + 8.0
        x1 = page_w - 8.0
        y0 = by0  # 원문 첫 줄과 y 정렬
        allow_shift = True
    else:
        x0 = bx0
        x1 = min(page_w - 8.0, max(bx1, x0 + _MIN_WIDTH))
        y0 = by1 + _GAP
        allow_shift = False
    rect, fontsize = _fit_rect(x0, y0, x1, page_h, ko_text,
                               allow_shift=allow_shift)
    needed = _estimate_height(ko_text, rect[2] - rect[0], fontsize)
    available = rect[3] - rect[1]
    if needed - available > 0.5:  # 부동소수 오차 여유
        logger.warning(
            "pdf-translate: page %d %s 주석이 8pt에서도 다 안 들어감"
            "(clip) — 필요 %.0fpt / 가용 %.0fpt",
            block.page, block.kind, needed, available)
    return Overlay(page=block.page, rect=rect, text=ko_text,
                   fontsize=fontsize)


def _panel_region(raws: list[RawBlock], page_w: float
                  ) -> tuple[float, float, float, float]:
    """패널 OCR 영역 — 헤더/씬 테이블(y<95) 아래부터 Dialog 라벨 위(5pt
    여유) 또는 없으면 460pt 고정까지, x는 페이지 전폭(실물 GABE01 실측
    기반 템플릿 고정 상수)."""
    y_bottom = _PANEL_Y_BOTTOM_DEFAULT
    for b in raws:
        if normalize_ws(b.text) == "Dialog":
            y_bottom = b.bbox[1] - 5.0
            break
    return (0.0, _PANEL_Y_TOP, page_w, y_bottom)


def _field_content(raws: list[RawBlock], label: str,
                    next_label: str | None = None, *,
                    is_action: bool = False) -> RawBlock | None:
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
    return _merge_pieces(pieces, is_action=is_action)


# 슬러그라인 패턴(Task 19) — 씬 헤딩("INT. STRICKLAND PROPANE - SALES
# FLOOR - MORNING")은 "INT."/"EXT."로 시작하거나(주요 형태), 그 접두가
# 없이 전부 대문자·하이픈으로만 된 헤딩 변형도 실물에 있다(사람 납품본
# 실측: human_ko가 첫 조각을 "\r"로 분리해 별도 줄 취급 — pairs_all.jsonl
# page=2 action). 이 두 형태 중 하나면 슬러그라인으로 본다.
#
# 리뷰 후속(라운드 1 Minor 7): "전부 대문자+하이픈" 분기는 이론상 카메라
# 지시문("CU - HANK" 등)에도 발동할 수 있다는 지적 — 전 문서(1037페이지)
# 실측으로 대조했다: 개행 join은 총 4건만 발동했고(사람 쪽 개행 주석은
# 58건 — 우리가 훨씬 적게 발동, 과다 발동 아님), 4건 전부 "INT."/"EXT."
# 접두 분기였다(하이픈-전용 분기는 이 코퍼스에서 단 한 번도 발동하지
# 않음). 카메라 지시문 오탐 우려는 이 코퍼스에서 실증되지 않았다 — 실제
# 격차는 과다 발동이 아니라 과소 발동(사람 58건 대비 4건, 라벨+내용이
# 이미 한 블록으로 붙어 나와 join 지점 자체가 없는 경우가 대부분으로
# 추정)이다.
_SLUGLINE_PREFIX_RE = re.compile(r"^(?:INT|EXT)\.")


def _looks_like_slugline(text: str) -> bool:
    if _SLUGLINE_PREFIX_RE.match(text):
        return True
    return ("-" in text and text == text.upper()
            and any(c.isalpha() for c in text))


def _merge_pieces(pieces: list[RawBlock], *, is_action: bool = False) -> RawBlock:
    """y좌표 순 후보 블록들을 이어붙이고 bbox는 union으로. place()가 이
    union 아래에 주석을 놓으므로 원본 블록 간 겹침이 자연히 해소된다.

    action 필드 한정(Task 19, 사람 납품본 실측: human_ko가 슬러그라인
    뒤를 "\\r"로 분리): 첫 조각이 슬러그라인 패턴이고 뒤따르는 조각이
    있으면 그 경계만 "\\n"으로 잇는다(그 외 조각 사이는 기존대로 공백
    1개) — 슬러그라인이 아니거나 dialog 필드면 기존 동작 그대로."""
    if is_action and len(pieces) > 1 and _looks_like_slugline(
            normalize_ws(pieces[0].text)):
        first = normalize_ws(pieces[0].text)
        rest = " ".join(normalize_ws(p.text) for p in pieces[1:])
        text = first + "\n" + rest
    else:
        text = " ".join(normalize_ws(p.text) for p in pieces)
    x0 = min(p.bbox[0] for p in pieces)
    y0 = min(p.bbox[1] for p in pieces)
    x1 = max(p.bbox[2] for p in pieces)
    y1 = max(p.bbox[3] for p in pieces)
    return RawBlock(text=text, bbox=(x0, y0, x1, y1))


def _fit_rect(x0: float, y0: float, x1: float, page_h: float, ko_text: str,
             *, allow_shift: bool
             ) -> tuple[tuple[float, float, float, float], float]:
    """x0/x1 고정 상태에서 페이지 하단(page_h - 4) 안에 들어오도록 폰트를
    12→10→9→8pt로 줄여가며 재추정한다.

    allow_shift=True(오른쪽 경로): x축이 이미 원문과 분리돼 있어(호출부
    보장) y를 얼마든 움직여도 교차 위험이 없다 — 8pt에서도 안 맞으면
    페이지 상단(0) 쪽으로 밀어 올려 텍스트가 실제로 다 보이게 한다.

    allow_shift=False(아래 경로): 위로 밀지 않는다 — 그게 원문을 덮던
    원래 버그였다. 다만 y0 자체가 이미 페이지 하단 안전 마진을 넘는 극단
    케이스(리뷰어 실측 재현: 원문 블록이 페이지 맨 끝에 거의 붙어 GAP을
    더하면 페이지 밖으로 나가는 경우)에서도 반드시 유효한(퇴화하지 않은)
    온페이지 rect를 반환해야 한다 — 안 그러면 PyMuPDF add_freetext_annot이
    'rect is infinite or empty'로 터져 이미 끝난 번역까지 포함해 잡
    전체가 날아간다(2026-07-30 리뷰 Finding 1). 이건 "위로 밀어 가독성
    확보"가 아니라 순수 크래시 방지 안전망이라 최소 1줄만 확보한다."""
    max_y1 = page_h - 4.0
    width = x1 - x0
    fontsize = _FONT_SIZES[-1]
    height = 0.0
    for fontsize in _FONT_SIZES:
        height = _estimate_height(ko_text, width, fontsize)
        if y0 + height <= max_y1:
            return _clamp_nondegenerate(x0, y0, x1, y0 + height, page_h), fontsize

    if allow_shift:
        y0 = max(0.0, min(y0, max_y1 - height))
        y1 = min(y0 + height, max_y1)
        return _clamp_nondegenerate(x0, y0, x1, y1, page_h), fontsize

    if y0 >= max_y1:  # 크래시 방지 안전망 — 최소 1줄을 페이지 안으로
        min_height = _estimate_height("x", width, fontsize)
        y0 = max(0.0, max_y1 - min_height)
    y1 = min(y0 + height, max_y1)
    return _clamp_nondegenerate(x0, y0, x1, y1, page_h), fontsize


def _clamp_nondegenerate(
    x0: float, y0: float, x1: float, y1: float, page_h: float,
) -> tuple[float, float, float, float]:
    """마지막 안전망 — 위 로직이 어떤 경로를 거치든, 반환되는 rect는
    반드시 y1 > y0(퇴화 아님)이고 [0, page_h] 안이어야 한다. 정상 경로에서는
    이미 그렇게 계산돼 있어 사실상 no-op이지만, 페이지 자체가 극단적으로
    작은 이론적 엣지케이스까지 add_freetext_annot 크래시로 이어지지 않게
    한다(2026-07-30 리뷰 Finding 1 — 마지막 방어선)."""
    y0 = max(0.0, min(y0, page_h))
    y1 = max(y0 + 1.0, min(y1, page_h))
    return (x0, y0, x1, y1)


def _estimate_height(text: str, width: float, fontsize: float) -> float:
    """CJK 근사 폭(글자당 ≈ fontsize pt)으로 줄수 → 박스 높이.

    리뷰 후속(Task 19 round 1, Important 1): 개행(`\\n`, 슬러그라인 join)을
    그냥 1글자로 세면(옛 `ceil(len(text)/cpl)`) 실제 렌더 줄 수를 과소
    추정한다 — 실제 줄 수는 줄마다 따로 올림한 값의 합이고, 이는 전체
    길이 기준 추정치보다 개행 하나당 최대 1줄 크다(구체 사례: cpl=25,
    각 줄 26자 → 전체기준 `ceil(53/25)=3`줄이지만 실제는 `2+2=4`줄).
    `_fit_rect`가 이 추정치로 폰트 축소 여부를 판단하므로, 과소 추정은
    "12pt로 충분하다"는 잘못된 결론 → 납품 PDF에서 마지막 줄이 rect
    밖으로 잘리는 결과로 이어진다. 줄 단위로 합산해 고친다(개행 없는
    텍스트는 `text.split("\\n")`이 `[text]` 하나뿐이라 기존과 동일하게
    계산된다 — 회귀 없음)."""
    chars_per_line = max(8, int(width / fontsize))
    lines = sum(max(1, math.ceil(len(line) / chars_per_line))
                for line in text.split("\n"))
    return (lines + 0.5) * fontsize * 1.25
