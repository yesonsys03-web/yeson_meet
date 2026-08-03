"""Storyboard Pro 익스포트 프로파일 (GABE01 1단 / FL102 1·3단 실측 기반).

판넬마다 'Dialog'/'Action Notes' 라벨 한 벌. **페이지당 판넬 수는 1장일 수도
3장일 수도 있다** — 라벨 앵커를 페이지에서 전부 열거하고(`_field_anchors`)
앵커마다 자기 열(x 근접) 안에서 아래쪽 블록을 내용으로 본다(라벨과 내용이
한 블록으로 붙어 나오는 변형도 처리).

⚠2026-07-31 이전에는 "페이지당 판넬 1장"을 전제해 첫 라벨에서 멈췄고, 그
탓에 3단 문서에서 2·3열이 통째로 누락됐다(FL102 실측: 사람 납품본 기준 필드
59건 중 34건). 새 템플릿을 붙일 때 이 전제를 되살리지 말 것.
페이지 방향(가로/세로)도 판정에 쓰지 않는다 — `detect()` docstring 참조.
"""
from __future__ import annotations

import logging
import math
import re

from ..backend import PdfDocument, RawBlock
from ..panel_ocr import (
    decode_panel_label_lines,
    find_panel_labels,
    repair_corrupt_words,
)
from .base import Overlay, PdfBlock, has_hangul, normalize_ws

logger = logging.getLogger("yeson.pdf.profiles.storyboard")

_FIELDS = (("Dialog", "dialog"), ("Action Notes", "action"))
_FONTSIZE = 12.0          # 기존 수작업 번역본 실측(AdobeMyungjoStd 12pt)
_GAP = 4.0                # 원문 블록과 주석 사이 여백(pt)
_MIN_WIDTH = 280.0        # 주석 박스 최소 폭(아래 배치 폴백)
_MIN_RIGHT_WIDTH = 180.0  # 오른쪽 배치가 성립하려면 필요한 최소 여유폭
_FONT_SIZES = (12.0, 10.0, 9.0, 8.0)  # 축소 폴백 사다리(최후 8pt로 확정)
_BELOW_FONT_SIZES = (12.0, 10.0)  # 아래 배치 사다리 — 10pt에서 끊는다
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

# 필드 박스로 인정할 사각형의 최소 크기 — 패널 테두리·표 셀 같은 작은
# 도형을 후보에서 뺀다(GABE01 실측: 필드 박스는 폭 961pt·높이 62pt).
_FIELD_BOX_MIN_WIDTH = 300.0
_FIELD_BOX_MIN_HEIGHT = 15.0

# 같은 열로 볼 x 허용폭 — 라벨 x0과 이만큼 안이면 그 필드의 내용으로 본다.
# 실물 3단(FL102) 열 간격은 315.4pt라 열끼리 섞이지 않는다.
_COL_TOL = 60.0


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


# 씬 테이블 헤더(y<95의 "Scene"/"Panel" 한 벌) — 판넬 템플릿 페이지의
# 표지가 아님을 보이는 두 번째 신호. _has_field_label만으로는 부족하다:
# Storyboard Pro는 **빈 필드를 통째로 생략**하므로(이 파일 43~46행 실측),
# 대사도 액션노트도 없는 순수 그림 페이지에는 Dialog/Action Notes 라벨이
# 아예 없다. 그런 페이지야말로 판넬 안 콜아웃 라벨이 사는 곳인데
# _has_field_label 게이트가 OCR을 통째로 막고 있었다.
#
# FL104_FNL_Nrev(3단 209p) 실측 2026-08-03: 사람이 주석을 단 페이지 중
# 우리 출력이 **한 글자도 없는** 페이지가 34장이었고, 그 34장 전부
# (34/34) 원문에 추출 가능한 본문 텍스트가 0이었다 — 전부 이 게이트에
# 걸린 순수 그림 페이지다(누락 주석 97개 = 전체 격차 246개의 39%).
# 같은 34장 전부가 이 씬 테이블 헤더를 갖고 있어 이 신호로 되살아난다.
#
# 표지 회귀(아래 게이트 주석 참조)는 그대로 막힌다 — 실측한 표지 페이지의
# 전체 텍스트는 `FL104_FNL_Nrev` + `Date: June 08 2026`뿐이라 이 헤더가
# 없다. 즉 이 신호는 "판넬 템플릿 페이지"만 통과시킨다.
#
# y<_PANEL_Y_TOP으로 밴드를 제한하는 게 핵심이다 — 판넬 그림 안이나 필드
# 본문에 우연히 "Scene"/"Panel"이라는 낱말이 있어도 헤더로 오인하지
# 않는다(헤더는 항상 씬 테이블 행에 있다).
#
# ⚠필드 라벨 관례(정확 일치 또는 startswith)를 여기 그대로 쓰면 안 된다 —
# 실물(FL104) 추출에서 헤더는 낱말별로 깔끔히 쪼개지지 않는다: `Scene`은
# 자기 블록이지만 `Panel`은 **씬 번호와 한 블록으로 묶여** `'17\nPanel'`로
# 나온다(즉 라벨이 블록 선두가 아니다). 그래서 토큰 소속으로 본다.
_HEADER_TOKENS = frozenset({"Scene", "Panel"})


def _has_scene_table_header(raws: list[RawBlock]) -> bool:
    seen: set[str] = set()
    for b in raws:
        if b.bbox[1] >= _PANEL_Y_TOP:
            continue
        seen |= _HEADER_TOKENS.intersection(normalize_ws(b.text).split())
        if seen == _HEADER_TOKENS:
            return True
    return False


def _is_panel_page(raws: list[RawBlock]) -> bool:
    """판넬 템플릿 페이지인가(= 판넬 콜아웃 OCR을 돌려도 되는 페이지인가).

    필드 라벨이 있으면 당연히 템플릿 페이지고, 없더라도 씬 테이블 헤더가
    있으면 대사·액션노트가 비어 있을 뿐인 판넬 페이지다."""
    return _has_field_label(raws) or _has_scene_table_header(raws)


# 세로로 겹쳐 붙은 판넬 라벨은 **한 덩어리**다 — 각각 따로 주석을 달면
# 서로 위에 포개져 읽을 수 없다(사용자 신고 2026-08-03: "너무 다닥다닥
# 붙어 있어서 가독성이 떨어진다").
#
# 실물(FL104 p20): 빨간 라벨 상자 하나가 `SB INC 3` / `SPC ZMB` 두 줄이고
# OCR은 이를 별개 히트로 준다(y 172.8~181.0 / 178.9~186.8 — **서로 겹친다**).
# 각 히트가 "자기 라벨 바로 위"에 10pt 주석을 놓으니 두 주석이 같은 자리에
# 찍혔다. 사람은 같은 자리에 **2줄 한 덩어리**(줄 간격 12.5pt)를 라벨 위에
# 얹는다 — 병합이 곧 사람 관례다.
#
# 문턱: 위 줄의 아래(y1)와 아래 줄의 위(y0) 차이가 이 값 이하이고 x가 겹치면
# 한 덩어리. 실측 쌍은 -2.5~-0.7pt(= 살짝 겹침)라 0보다 넉넉히 잡되, 멀리
# 떨어진 다른 캐릭터의 라벨(같은 페이지 최소 간격 ~50pt)은 묶이지 않게 한다.
_STACK_MAX_GAP = 6.0


def _label_width(ko_text: str) -> float:
    """판넬 라벨 주석에 필요한 폭 — 가장 긴 줄 기준(CJK 글자당 ≈ fontsize pt,
    `_estimate_height`와 같은 근사) + 여유 4pt. 여유가 없으면 근사 오차로
    한 글자가 다음 줄로 접힌다."""
    longest = max((len(line) for line in ko_text.split("\n")), default=0)
    return longest * _PANEL_FONTSIZE + 4.0


def _union_bbox(blocks: list[RawBlock]) -> tuple[float, float, float, float]:
    return (min(b.bbox[0] for b in blocks), min(b.bbox[1] for b in blocks),
            max(b.bbox[2] for b in blocks), max(b.bbox[3] for b in blocks))


def _group_stacked_labels(labels: list[RawBlock]) -> list[list[RawBlock]]:
    """세로로 맞닿은 라벨들을 묶는다(x 겹침 + y 간격 <= _STACK_MAX_GAP)."""
    groups: list[list[RawBlock]] = []
    for raw in sorted(labels, key=lambda r: (r.bbox[1], r.bbox[0])):
        for g in groups:
            gx0, _gy0, gx1, gy1 = _union_bbox(g)
            if (min(raw.bbox[2], gx1) - max(raw.bbox[0], gx0) > 0
                    and raw.bbox[1] - gy1 <= _STACK_MAX_GAP):
                g.append(raw)
                break
        else:
            groups.append([raw])
    return groups


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
        """판정 근거는 'Dialog'+'Action Notes' 라벨 2종의 존재뿐이다.

        2026-07-31: 예전엔 `w <= h`면(세로형) 즉시 False였다 — GABE01
        (1008x612)에 맞춘 가드였는데 실물 FL102의 `1_PANEL` 익스포트가
        **612x792 세로형**이라 정당한 스토리보드가 "지원하지 않는 PDF
        포맷입니다"로 거부됐다. 게다가 방향은 판별력도 없다: 타당성 문서가
        조사한 4개 포맷(스토리보드·대본·컬러노트·리드시트)이 전부 가로형
        이라 가로/세로로는 서로 구분되지 않는다."""
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
            # 깨진 추출 문자 복구(Task 20) — 필드 선택보다 **먼저** 돌려야
            # 한다. 깨진 큐 헤더(`9= HANK 7Cont.8`)는 utterances의 큐 파싱을
            # 통과하지 못해 발화 체인을 끊고, 뒤따르는 페이지를 헤더-only
            # 그룹(`행크:(계속)`)으로 만든다 — 복구가 선행돼야 그 연쇄가
            # 애초에 생기지 않는다. 깨진 단어가 없는 페이지는 원본 리스트를
            # 그대로 돌려받는다(대다수 페이지 비용 0).
            raws = repair_corrupt_words(doc, page, doc.raw_blocks(page))
            rects = doc.page_rects(page)
            for i, (label, kind) in enumerate(_FIELDS):
                next_label = _FIELDS[i + 1][0] if i + 1 < len(_FIELDS) else None
                # 라벨 앵커는 페이지당 **여러 개**일 수 있다 — 1단 템플릿은
                # 1개, 3단(FL102)은 열마다 1개씩 3개다. 예전엔 첫 앵커에서
                # 멈춰 2·3열이 통째로 누락됐다(실측: 뽑힌 48블록이 전부 1열).
                for anchor, rest in _field_anchors(raws, label):
                    box = _field_box(rects, anchor.bbox)
                    col_x0 = anchor.bbox[0]
                    # 다음 라벨은 **같은 열에서** 찾는다 — 열 무관으로 찾으면
                    # 엉뚱한 열의 라벨이 상한이 된다(이 함수의 예전 docstring이
                    # 예고했던 실패다).
                    next_y0 = _next_label_y0(raws, next_label, col_x0=col_x0)
                    content = _field_content(
                        raws, anchor, rest, is_action=(kind == "action"),
                        window_y1=_content_window(next_y0, box))
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
                    # 다음 필드 라벨 상한은 박스 유무와 무관하게 필드당 한 번만
                    # 계산해 둘을 min으로 합친다(리뷰 후속, Important 1(b)) —
                    # 예전엔 "박스 있으면 박스만, 없으면 라벨만" 상호배타였는데,
                    # 그러면 박스가 너무 낙낙해 다음 필드를 침범해도 그대로
                    # 상한이 돼버린다. 더 좁은 쪽이 항상 이기게 한다.
                    next_limit = None if next_y0 is None else next_y0 - _GAP
                    if box is not None:
                        # 실측(GABE01, 매 7페이지 표본): dialog 필드에서
                        # next_label_y0 - _GAP는 박스 하단보다 언제나 정확히
                        # 2.0pt 아래다 — 즉 이 min은 실물에서는 사실상 no-op이고
                        # (박스가 항상 이김), 박스가 지나치게 낙낙해 다음 필드를
                        # 침범할 수 있는 병리적 케이스에서만 실제로 작동한다.
                        limit_y = (box[3] if next_limit is None
                                   else min(box[3], next_limit))
                        limit_x1 = box[2]
                    else:
                        # 도형이 없으면 다음 필드 라벨을 상한으로. 마지막 필드는
                        # 뒤에 라벨이 없어 None으로 남는다 = 기존 우측 배치 그대로.
                        limit_y = next_limit
                        limit_x1 = None
                    out.append(PdfBlock(page=page, kind=kind, text=text,
                                        bbox=content.bbox,
                                        limit_y=limit_y, limit_x1=limit_x1))
            # 표지/타이틀 페이지(리뷰 후속, 2026-07-30): 실제 패널 템플릿
            # 페이지가 아니면 _panel_region이 기본값(y_bottom=460)으로 열리고,
            # 표지의 빨간 로고/타이틀 텍스트("KING"/"HILL")가 그 안에서
            # 라벨로 오인식되던 회귀(page 0 실물 확인)를 막아야 한다.
            #
            # ⚠판정을 _has_field_label에서 _is_panel_page로 넓혔다
            # (2026-08-03, FL104 실측) — 필드 라벨만 보면 **대사도 액션노트도
            # 없는 순수 그림 페이지**까지 표지로 오인해 OCR을 건너뛴다.
            # FL104 209p에서 그런 페이지가 34장이었고 사람이 단 주석 97개를
            # 통째로 놓치고 있었다. 씬 테이블 헤더를 두 번째 신호로 인정하면
            # 그 34장이 되살아나고 표지는 여전히 걸러진다(_is_panel_page 주석).
            if _is_panel_page(raws):
                page_w, _page_h = doc.page_size(page)
                region = _panel_region(raws, page_w)
                labels = [r for r in find_panel_labels(
                              doc, page, region,
                              panels=_panel_subregions(doc, page, region))
                          if normalize_ws(r.text) and not has_hangul(r.text)]
                for group in _group_stacked_labels(labels):
                    texts = [normalize_ws(r.text) for r in group]
                    # 판넬 약어(SPCZMB·TTINCA·IN…)는 결정적으로 해독한다 —
                    # 해독 대상이 아니면 ko=None이라 평소대로 번역기를 탄다
                    # (예: `CAMERA FIELD GUIDE`는 영어 문장이라 LLM이 옮긴다).
                    lines = decode_panel_label_lines(texts)
                    ko = "\n".join(lines) if lines else None
                    out.append(PdfBlock(
                        page=page, kind=_PANEL_LABEL_KIND,
                        text="\n".join(texts), bbox=_union_bbox(group), ko=ko))
        return out

    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        """필드(dialog/action)는 **필드 박스 안 원문 아래**를 우선하고, 자리가
        없으면 오른쪽, 패널 콜아웃 라벨은 라벨 바로 위 우선으로 분기한다.

        아래 우선(2026-07-31): 사람 납품본은 아래 여유가 있으면 원문 바로
        아래 전폭 12pt로 쓴다(GABE01 전 1037페이지 실측) — 좁은 우측 칸에
        여러 줄로 접히는 것보다 읽기 쉽다. 2026-07-30에 우측을 우선으로 둔
        이유였던 '원문 가림'은 아래 경로의 시프트업을 제거하면서(fd7b1cd,
        allow_shift=False) 이미 해소됐다 — 지금의 아래 경로는 원문을 덮지
        않는다. 아래가 안 되면 기존 우측 경로 그대로."""
        if block.kind == _PANEL_LABEL_KIND:
            return self._place_panel_label(block, ko_text, page_size)
        below = _place_below_in_box(block, ko_text, page_size)
        if below is not None:
            return below
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
        # 폭은 **글자가 필요한 만큼만** 쓴다.
        #
        # 예전엔 무조건 _PANEL_MIN_WIDTH(90pt)를 확보했는데, 판넬 라벨은
        # `파티광3`처럼 짧아서(10pt CJK 4글자 ≈ 40pt) 남는 50pt가 옆
        # 캐릭터의 라벨 자리를 침범한다 — FL104 p20에서 인접 라벨 주석
        # 5쌍이 실제로 겹쳤다(사용자 신고: "다닥다닥 붙어 가독성이 떨어진다").
        # 사람 납품본도 텍스트 폭만큼만 쓰고 36~82pt 간격으로 나란히 둔다.
        #
        # 90pt는 여기서 버리되 **오른쪽 배치 문턱으로는 그대로** 쓴다(아래
        # 폴백 호출) — 그쪽은 "오른쪽에 놓을 만한 여유가 있나"를 재는 값이라
        # 성격이 다르다.
        x1 = min(page_w - 8.0, max(bx1, bx0 + _label_width(ko_text)))
        height = _estimate_height(ko_text, x1 - x0, _PANEL_FONTSIZE)
        y1 = by0 - 2.0
        y0 = y1 - height
        if y0 < _PANEL_TOP_MARGIN:
            return _place_right_or_below(block, ko_text, page_size,
                                         min_right_width=_PANEL_MIN_WIDTH)
        rect = _clamp_nondegenerate(x0, y0, x1, y1, page_h)
        return Overlay(page=block.page, rect=rect, text=ko_text,
                       fontsize=_PANEL_FONTSIZE)


def _place_below_in_box(block: PdfBlock, ko_text: str,
                        page_size: tuple[float, float]) -> Overlay | None:
    """필드 박스 안 원문 **아래**에 전폭으로 놓을 수 있으면 그 Overlay를,
    자리가 없으면 None(호출부가 기존 우측 경로로 폴백)을 돌려준다.

    상한(block.limit_y)을 모르면 None — 상한 없이 아래로 놓으면 박스를 넘어
    다음 필드를 침범한다. 폭은 설계 §6.1대로 **필드 박스 우측까지 전폭**을
    쓴다 — 원문 자체의 x1로 좁히면(레거시 `_place_right_or_below`의 아래
    폴백처럼 `max(bx1, bx0 + _MIN_WIDTH)`로 캡) 짧은 원문 한 줄 뒤에 긴
    번역이 와도 폭이 넓어지지 않아 12pt 한 줄에 들어갈 수 있는 문장이
    불필요하게 2줄로 접혀 10pt까지 축소된다(실물 GABE01 373p 실측: 사람은
    같은 자리에 12pt 한 줄로 썼는데 이 폭 캡 탓에 10pt가 나오던 버그,
    2026-07-31 리뷰로 발견). `_place_right_or_below`의 **폭 캡**은 이 문제와
    무관—그쪽은 원문과 겹치지 않으려고 일부러 원문 폭 기준을 쓰는 것이라
    손대지 않는다(단 오른쪽 **끝**은 그쪽도 이제 같은 `limit_x1` 상한을
    쓴다 — 열 경계를 넘지 않기 위해서다)."""
    if block.limit_y is None:
        return None
    page_w, page_h = page_size
    bx0, _by0, _bx1, by1 = block.bbox
    y0 = by1 + _GAP
    limit = min(block.limit_y, page_h - 4.0)
    room = limit - y0
    if room <= 0:
        return None
    # 필드 박스가 보고하는 limit_x1과 무관하게 페이지 폭 한도는 항상
    # 지켜야 한다 — min으로 감싸 되살린다(단순 대체였다면 limit_x1이
    # 페이지 밖/경계에 걸릴 때 주석이 페이지 밖으로 나갈 수 있었다).
    right = min(page_w - 8.0,
                block.limit_x1 - 8.0 if block.limit_x1 is not None
                else page_w - 8.0)
    x1 = right  # 설계 §6.1: 박스 우측까지 전폭 — 원문 x1로 좁히지 않는다.
    if x1 <= bx0:
        return None
    for fontsize in _BELOW_FONT_SIZES:
        height = _estimate_height(ko_text, x1 - bx0, fontsize)
        if height <= room:
            rect = _clamp_nondegenerate(bx0, y0, x1, y0 + height, page_h)
            return Overlay(page=block.page, rect=rect, text=ko_text,
                           fontsize=fontsize)
    return None


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
    우선).

    오른쪽 끝은 페이지가 아니라 **필드 박스 우측**이다(2026-07-31,
    `_place_below_in_box`와 같은 규칙). 3단(FL102) 문서에서 이 상한이 없으면
    아래 배치가 실패한 블록의 주석이 `page_w`까지 뻗어 **옆 열 판넬을
    가로지른다**(p36 2열 대사에서 실측: rect x1=1000.0 ≫ 열 우측 657.8,
    사람은 같은 자리를 열 안 535.8에 뒀다).

    설계 §6.5는 "우측도 같이 조이면 클리핑이 늘어난다"고 이연했으나,
    GABE01(1단) 표본 80페이지 실측 결과 우측 경로 27건에서 **폰트 변화
    0건·새 클리핑 0건**이었고 오히려 그 27건 전부가 지금 박스 우측
    (985.1)을 넘어 1000.0까지 뻗고 있었다 — 조이는 쪽이 순수 이득이다."""
    page_w, page_h = page_size
    bx0, by0, bx1, by1 = block.bbox
    # 필드 박스가 보고하는 limit_x1과 무관하게 페이지 폭 한도는 항상
    # 지킨다(limit_x1이 페이지 밖에 걸려도 주석은 페이지 안에 남는다).
    right = min(page_w - 8.0,
                block.limit_x1 - 8.0 if block.limit_x1 is not None
                else page_w - 8.0)
    right_w = right - (bx1 + 8.0)
    if right_w >= min_right_width:
        x0 = bx1 + 8.0
        x1 = right
        y0 = by0  # 원문 첫 줄과 y 정렬
        allow_shift = True
    else:
        x0 = bx0
        x1 = min(right, max(bx1, x0 + _MIN_WIDTH))
        y0 = by1 + _GAP
        allow_shift = False
    # 아래 경로는 **필드 박스 하단(limit_y)** 을 넘지 않는다.
    #
    # ⚠2026-08-03 이전에는 이 경로가 페이지 하단만 봤다. `_place_below_in_box`가
    # limit_y 안에 못 넣어 None을 돌려주면 그 폴백인 여기가 같은 자리에
    # 다시 놓으면서 상한 없이 아래로 뻗어, 대사 주석이 자기 필드 박스를
    # 넘어 **다음 필드(Action Notes) 박스 위에 겹쳐 찍혔다**(FL104 p2 실물
    # 스크린샷 확인 — 사용자 신고). 겹친 두 글자는 서로를 못 읽게 만든다.
    #
    # 상한을 주면 대신 잘릴 수 있는데, 이 파일이 이미 세워 둔 원칙이 그쪽이다:
    # "잘리더라도 원문 비침범이 우선"(_place_right_or_below docstring).
    # 다음 필드 박스를 덮는 것도 같은 종류의 침범이다. 잘리는 경우는 아래
    # 경고 로그(clip)가 이미 남긴다.
    #
    # 오른쪽 경로에는 적용하지 않는다 — 그쪽은 x축이 원문과 분리돼 있고
    # GABE01 27건 실측으로 굳어진 경로라, 근거 없이 조이지 않는다.
    rect, fontsize = _fit_rect(x0, y0, x1, page_h, ko_text,
                               allow_shift=allow_shift,
                               bottom_limit=None if allow_shift else block.limit_y)
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
    # 라벨 매칭은 이 파일의 관례(정확 일치 **또는** 라벨로 시작하는 병합형)를
    # 따른다 — `_has_field_label`/`_looks_like_field_label`과 같은 규칙이다.
    #
    # ⚠2026-08-03 이전에는 `== "Dialog"` 정확 일치만 봤다. 그런데 라벨과 내용이
    # 한 블록으로 붙어 나오는 변형이 실물에 흔하다(FL104 p16: 블록 텍스트가
    # `'Dialog 240 BELLE Keep moving, Manny! …'`). 그런 페이지에서는 매칭이
    # 실패해 기본값 460으로 열렸고, **OCR 영역이 필드 박스까지 삼켰다** —
    # 실측으로 `Dialog`·`240 BELLE`·`walk cycle A 1/2`·대사 본문까지 OCR
    # 히트로 잡히고 있었다(지금은 검정이라 색 문턱에서 버려질 뿐이라, 색
    # 문턱을 완화하는 순간 필드 텍스트가 판넬 라벨로 중복 주석된다).
    #
    # Dialog뿐 아니라 Action Notes도 본다 — 대사가 없는 페이지는 Dialog 필드가
    # 통째로 생략되므로(Storyboard Pro), Dialog만 찾으면 그 페이지도 460으로
    # 열린다. 필드 라벨 중 **가장 위**를 상한으로 삼아야 판넬 그림만 남는다.
    y_bottom = _PANEL_Y_BOTTOM_DEFAULT
    for b in raws:
        t = normalize_ws(b.text)
        if any(t == label or t.startswith(label) for label, _kind in _FIELDS):
            y_bottom = min(y_bottom, b.bbox[1] - 5.0)
    return (0.0, _PANEL_Y_TOP, page_w, y_bottom)


# 판넬 칸으로 인정할 최소 넓이(OCR 영역 대비). 그림 속 아이콘·로고 이미지가
# 칸으로 둔갑해 OCR 호출만 늘리는 것을 막는다(3단 칸 = 영역의 약 27%).
_PANEL_MIN_AREA_RATIO = 0.05
# 한 페이지에서 따로 읽을 칸의 최대 개수 — 비용 상한(3단=3, 1단=1이 실물).
_PANEL_MAX_SUBREGIONS = 8


def _panel_subregions(doc: PdfDocument, page: int,
                      region: tuple[float, float, float, float]
                      ) -> tuple[tuple[float, float, float, float], ...]:
    """판넬 그림 칸 ∩ OCR 영역 — 칸을 따로 읽게 할 후보들.

    Storyboard Pro 익스포트는 칸 하나를 래스터 이미지 하나로 굽는다(실측:
    FL102·FL104 3단이 전 페이지 동일하게 302.1×168.3pt 3칸, 1단은
    536×274.7pt 1칸). 그래서 칸 좌표를 상수로 박지 않고 **문서에서 읽는다** —
    템플릿이 바뀌어도 따라간다.

    "따로 읽어서 이득이 있는 크기냐"는 여기서 판단하지 않는다 — 그건 OCR
    엔진의 성질이라 panel_ocr가 가린다(find_panel_labels). 이 함수는 포맷
    지식("무엇이 판넬 칸인가")만 맡는다.

    백엔드가 `image_rects`를 제공하지 않으면(다른 구현으로 교체된 경우)
    조용히 빈 튜플 = 현행 전폭 1회 판독으로 내려간다."""
    finder = getattr(doc, "image_rects", None)
    if finder is None:
        return ()
    region_area = max(0.0, (region[2] - region[0]) * (region[3] - region[1]))
    if region_area <= 0:
        return ()
    out: list[tuple[float, float, float, float]] = []
    for rect in finder(page):
        sub = (max(rect[0], region[0]), max(rect[1], region[1]),
               min(rect[2], region[2]), min(rect[3], region[3]))
        w, h = sub[2] - sub[0], sub[3] - sub[1]
        if w <= 0 or h <= 0:
            continue
        if w * h < region_area * _PANEL_MIN_AREA_RATIO:
            continue
        out.append(sub)
    return tuple(out[:_PANEL_MAX_SUBREGIONS])


def _next_label_y0(raws: list[RawBlock], next_label: str | None,
                   *, col_x0: float | None = None) -> float | None:
    """다음 필드 라벨의 y0 — 라벨+내용이 한 블록으로 붙어 나오는 변형도
    경계로 인정한다. _field_content의 창 상한과 배치 상한이 **같은 규칙**을
    쓰도록 한 곳에 모은 것이다(규칙이 갈라지면 내용 창과 배치 상한이 어긋난다).

    `col_x0`을 주면 **그 열을 먼저** 찾고, 그 열에 다음 라벨이 없을 때만
    열 무관으로 되돌아간다(2026-07-31).

    - 열 우선인 이유: 예전엔 x를 아예 보지 않아서, 3단(FL102) 템플릿에서
      1열 Dialog의 상한으로 2·3열의 Action Notes 라벨이 잡힐 수 있었다 —
      이 함수의 예전 docstring이 정확히 그 실패("장차 라벨이 여러 열에
      걸친 템플릿을 만나면")를 예고해 뒀다.
    - 그래도 폴백을 남기는 이유: 상한이 아예 없어지면 Dialog 필드의 창이
      아래로 열려 **다음 필드의 내용을 삼킨다**(빈 Dialog 회귀, Bug A/B).
      라벨 x가 열 허용폭 밖으로 흔들리는 템플릿에서 그 안전성질을 잃지
      않도록, 같은 열에서 못 찾으면 예전 규칙 그대로 되돌아간다. 실물
      3단에서는 열마다 두 라벨이 다 있어 폴백이 발동하지 않는다."""
    if next_label is None:
        return None
    fallback: float | None = None
    for b in raws:
        t = normalize_ws(b.text)
        if t == next_label or (t.startswith(next_label)
                               and len(t) > len(next_label)):
            if col_x0 is None or abs(b.bbox[0] - col_x0) < _COL_TOL:
                return b.bbox[1]
            if fallback is None:
                fallback = b.bbox[1]
    return fallback


def _content_window(next_y0: float | None,
                    box: tuple[float, float, float, float] | None,
                    ) -> float | None:
    """내용 후보를 모을 창의 하한(y) — 다음 라벨과 필드 박스 하단 중 **더
    위쪽**(작은 값). 둘 다 없으면 None(창 무제한).

    박스 하단을 창에 넣는 이유(2026-07-31): 마지막 필드(Action Notes)는
    뒤에 라벨이 없어 창이 페이지 끝까지 열려 있었다. 3단 문서에서는 페이지
    푸터(`... Property of Netflix ...`, x0=405.8)가 2열 x0(354.4)과 51.4pt
    차이라 x 허용폭 60pt 안에 들어와 **푸터가 대사 내용으로 빨려 들어갔다**
    (FL102 79페이지에서 77건 실측). 박스 하단으로 막으면 사라진다.

    기존 코퍼스에는 무해함을 실측으로 확인했다 — GABE01(1단) 표본 149
    페이지의 Action Notes 148건에서 이 상한으로 잃는 블록은 **0건**이다."""
    bounds = [v for v in (next_y0, box[3] if box is not None else None)
              if v is not None]
    return min(bounds) if bounds else None


def _field_anchors(raws: list[RawBlock], label: str
                   ) -> list[tuple[RawBlock, str | None]]:
    """페이지 안의 `label` 앵커를 **전부** 돌려준다 — (앵커 블록, 붙어 나온
    내용 조각). 1단 템플릿은 1개, 3단(FL102)은 열마다 1개씩 3개다.

    2026-07-31: 예전 `_field_content`는 첫 앵커를 찾는 즉시 `break`했고
    `extract()`도 `_FIELDS`를 페이지당 한 번만 돌아서, 3단 문서에서 뽑히는
    블록이 **전부 1열**이었다(FL102_FNL_A 실측: 48블록의 열별 분포가
    `{39.0: 48}`, 사람 납품본 기준 필드 59건 중 2·3열 34건 누락).

    반환 순서는 `raws` 순서 그대로다 — 열 순서를 가정하지 않는다(호출부는
    앵커마다 독립적으로 자기 열을 처리한다)."""
    out: list[tuple[RawBlock, str | None]] = []
    for b in raws:
        t = normalize_ws(b.text)
        if t == label:
            out.append((b, None))
        elif t.startswith(label) and len(t) > len(label):
            rest = t[len(label):].lstrip(" :")
            if rest:
                out.append((b, rest))
    return out


def _field_box(rects: list[tuple[float, float, float, float]],
               bbox: tuple[float, float, float, float],
               ) -> tuple[float, float, float, float] | None:
    """원문 bbox를 감싸는 **가장 작은** 필드 박스 사각형 — 없으면 None.
    가장 작은 것을 고르는 이유: 페이지 테두리처럼 전체를 감싸는 큰 사각형이
    같이 잡히면 상한이 페이지 하단까지 열려 다음 필드를 침범한다."""
    x0, y0, x1, y1 = bbox
    best = None
    for r in rects:
        if (r[2] - r[0] < _FIELD_BOX_MIN_WIDTH
                or r[3] - r[1] < _FIELD_BOX_MIN_HEIGHT):
            continue
        if (r[0] <= x0 + 1.0 and r[1] <= y0 + 1.0
                and x1 <= r[2] + 1.0 and y1 <= r[3] + 1.0
                and (best is None or (r[3] - r[1]) < (best[3] - best[1]))):
            best = r
    return best


def _field_content(raws: list[RawBlock], anchor: RawBlock, rest: str | None, *,
                   is_action: bool = False,
                   window_y1: float | None = None) -> RawBlock | None:
    """**주어진 앵커**의 아래, 창(`window_y1`) 앞까지, 그리고 **같은 열**에
    있는 모든 콘텐츠 블록을 y좌표 오름차순으로 병합해 하나의 RawBlock으로
    반환한다(2026-07-30 E2E 실측: Dialog 754페이지 중 45%가 화자 줄과 대사가
    별개 블록으로 나뉜다 — 최근접 1블록만 집으면 실제 대사가 누락된다).
    라벨+내용이 한 블록이면 그 나머지(`rest`)가 첫 조각이 되고, 그 아래 창
    안의 추가 후보들도 이어 붙인다.

    2026-07-31: 앵커를 스스로 찾지 않고 **인자로 받는다**. 예전엔 페이지에서
    첫 라벨을 찾자마자 `break`해 3단 템플릿의 2·3열을 볼 수 없었다 —
    앵커 열거는 `_field_anchors`가, 창 계산은 `_content_window`가 맡는다.

    실물(GABE01) 실측: 필드가 비어 있으면 플레이스홀더 없이 통째로
    생략된다 — 그래서 후보를 창 위로 제한해야 한다. 안 그러면 Dialog가
    비었을 때 그 아래 Action Notes '내용'까지 건너뛰어 잘못 집어온다
    (라벨 텍스트만 걸러서는 못 막는다)."""
    lx0, _ly0, _lx1, ly1 = anchor.bbox
    candidates = [b for b in raws
                  if b.bbox[1] >= ly1 - 1.0
                  and (window_y1 is None or b.bbox[1] < window_y1 - 1.0)
                  and abs(b.bbox[0] - lx0) < _COL_TOL
                  and not _looks_like_field_label(normalize_ws(b.text))]
    pieces = ([RawBlock(text=rest, bbox=anchor.bbox)] if rest else []) + \
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
             *, allow_shift: bool, bottom_limit: float | None = None
             ) -> tuple[tuple[float, float, float, float], float]:
    """x0/x1 고정 상태에서 하단 한계 안에 들어오도록 폰트를 12→10→9→8pt로
    줄여가며 재추정한다.

    `bottom_limit`은 페이지 하단보다 **더 위**에 있는 한계(= 이 블록이 속한
    필드 박스의 하단)다. 주지 않으면 종전대로 페이지 하단만 본다.

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
    # ⚠`bottom_limit > y0`일 때만 적용한다 — 시작점보다 **위**에 있는 하한은
    # 아무것도 못 가두는 모순된 값이라 무시하는 게 맞다.
    #
    # 그냥 min으로 접으면 max_y1 < y0가 되고, 아래의 크래시 방지 안전망이
    # `y0 = max_y1 - min_height`로 **주석을 원문 위로 밀어 올린다** — 실제로
    # FL104 p16 대사 주석이 필드 박스를 벗어나 판넬 그림 위에 찍히는 회귀가
    # 났다(사용자 신고 "Dialog 번역 누락"). 그 페이지의 limit_y(281.7)는 자기
    # 블록 bbox(285.7~361.3)보다도 위였다 — 1열 Dialog의 다음 라벨을 못 찾아
    # 열 무관 폴백이 **다른 열의** Action Notes 라벨 y를 집어온 값이다.
    #
    # 이 경로의 원칙은 "위로 밀지 않는다"(allow_shift=False)이므로, 모순된
    # 하한 때문에 그 원칙이 깨지는 일은 없어야 한다. 하한을 무시하면 종전처럼
    # 원문 아래에 놓이고, 넘칠 땐 페이지 하단에서 잘린다.
    if bottom_limit is not None and bottom_limit > y0:
        max_y1 = min(max_y1, bottom_limit)
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
