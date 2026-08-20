"""엑스시트(exposure sheet) 프로파일 — 스캔 손글씨 노트를 찾아 옆에 병기.

원본은 텍스트 레이어가 없는 300dpi 스캔이다(KOTH_1401 실측: 188p 전체
텍스트 0자). RapidOCR은 손글씨의 **위치**는 사실상 전부 찾지만 **판독**은
못 한다(2026-08-20 실측: SUBTLE→SUBnE, +OFFSET→40f=SET) — 그래서 이
프로파일의 extract는 위치·클러스터링만 책임지고, 블록 텍스트는
handwriting_transcribe(비전 CLI, 기본 agy)가 채운다. 번역·배치·굽기는
공통부 그대로다. ⛔Gemini API는 쓰지 않는다(2026-08-20 사용자 확정, 비용).

사람 납품본 관례(KOTH_1401_A1 번역본, FreeText 주석 3,701개 실측):
- 9pt 파랑 FreeText를 원문 손글씨를 가리지 않고 옆/아래 여백에 병기
- DIALOG/EXP 립싱크 음소 컬럼(A·M·EH…)·셀 번호·서클 마커는 번역하지 않음
- 스파이크 커버리지 96.3%(표본 16p, 사람 주석 273개 중 263 대응). 남은
  누락은 헤더 밴드의 손글씨 화자명 라벨 한 부류(문서화된 한계 — 편집기로
  수동 추가).
"""
from __future__ import annotations

import io
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Overlay, PdfBlock, has_hangul
from .storyboard import _clamp_nondegenerate, _estimate_height

if TYPE_CHECKING:
    from ..backend import PdfDocument

logger = logging.getLogger("yeson.pdf_translate")

NOTE_KIND = "xsheet_note"

_DETECT_DPI = 100   # 헤더 토큰만 읽으면 되므로 저해상으로 충분(페이지당 ~1s)
_OCR_DPI = 200      # panel_ocr._OCR_DPI와 동일 — 손글씨 탐지엔 300 불필요
_DETECT_PAGES = 3
_SCAN_COVER = 0.5    # 페이지 면적의 이 비율 이상을 덮는 이미지 = 스캔본
_FONTSIZE = 9.0     # 사람 납품본 9pt 실측
_MIN_FONTSIZE = 7.0

# ⛔템플릿 좌표를 박지 않는다(2026-08-20). 작품마다 시트 양식이 다르다 —
# KOTH(792×1224pt, 대사 컬럼 1쌍)와 BM802(A3 841.92×1189.92pt, titmouse
# 양식, DIAL 2~5까지 대사 컬럼 5쌍)는 판형·비율·칸 배치가 전부 다르다.
# 페이지 크기 비율로만 늘린 좌표는 엉뚱한 칸을 가리킨다(실측: KOTH 기준
# 음소 밴드가 BM802에서는 DIAL2~3 위로 떨어져, 나머지 대사 컬럼의 립싱크
# 음소가 통째로 번역 대상이 됐다).
#
# 대신 **페이지에서 읽어 유도한다** — 칸 머리글(ACTION·DIALOG·EXP·DIAL n·
# TRUCK·CAMERA NOTES)은 인쇄 활자라 OCR이 conf 0.91~1.00으로 읽는다(양쪽
# 샘플 실측). 그 x·y가 곧 칸 경계다.
_HEADER_LABELS = {"ACTION", "CAMERANOTES", "TRUCK"}
_DIALOG_RE = re.compile(r"^(DIAL[A-Z0-9]{0,3}|EXP)$")
_FOOTER_LABELS = {"PRODNO", "FOOTAGE", "ANIMATOR", "SCENENO", "SHEETNO",
                  "PRODUCTIONNO", "SCENEDIRECTOR", "APPROVED"}
_HEADER_ROW_TOL = 8.0    # 같은 머리글 줄로 볼 y 오차(pt)
_BAND_PAD = 3.0          # 칸 경계 여유(pt)
_NUM_BIN_PT = 12.0       # 번호 컬럼 탐지용 x 버킷 폭
_NUM_BIN_MIN = 12        # 이 개수 이상이 모여야 컬럼으로 인정
_NUM_BIN_RATIO = 0.8     # 그중 숫자 비율
_PHONETIC_MAX_LEN = 3    # 대사 칸에서 이 길이 이하는 립싱크 음소(번역 안 함)
_CLUSTER_PAD = 6.0   # 세로로 쌓인 손글씨 단어들을 노트 하나로 묶는 근접 반경
# 전사 보내기 전에 버리는 잡티 크롭 기준. 전사는 CLI 세션(=구독 쿼터)을
# 쓰므로 어차피 버려질 것을 보내지 않는 게 곧 처리량이다.
#
# A1 전량 런의 실전사 380장 실측: 버려진 126장(33%)의 중앙값이 10×11pt·원시
# OCR 1글자인 반면, 살아남은 254장은 56×27pt·10글자였다. `면적>=300pt² 또는
# 원시 OCR 영숫자 2자 이상` 규칙이 쓰레기 90/126을 걷어내면서 진짜 노트는
# 254장 중 1장만 잃었다(0.4% — RapidOCR이 `CUT`을 `M`으로 읽은 작은 칸).
# 문턱을 더 올리면(면적 500·폭높이 20×14) 손실이 16~20%로 급등한다.
_MIN_NOTE_AREA = 300.0
_MIN_RAW_ALNUM = 2
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")

# 인쇄 서식 문구(정규화: 영숫자만·대문자). **업계 공통 항목만** 둔다 —
# 스튜디오·작품 로고(KOTH의 `KING`/`HILL`, BM802의 `titmouse`/`BIG MOUTH`)는
# 여기 넣지 않는다. 작품마다 다를뿐더러, 로고는 머리글 줄 위라 유도된
# header_y로 이미 걸러진다.
_TEMPLATE_WORDS = {
    "PRODNO", "FOOTAGE", "ANIMATOR", "SCENENO", "SHEETNO", "DIALOG",
    "EXP", "CAMERANOTES", "ACTION", "CONT", "SCENEDIRECTOR", "APPROVED",
    "PRODUCTIONNO", "ACT", "TRUCK", "BG", "LEVEL",
}
# 감지 토큰도 특정 양식에 매지 않는다 — 엑스시트라면 어느 하우스 것이든
# '대사/EXP 칸 머리글'과 '서식 라벨'이 인쇄돼 있다(KOTH `DIALOG`,
# BM802 `DIALO`·`DIAL 2`처럼 표기는 달라 정규식으로 받는다).
_DETECT_LABELS = {"ANIMATOR", "CAMERANOTES", "SHEETNO", "ACTION",
                  "PRODNO", "FOOTAGE", "SCENENO"}
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

_local = threading.local()


def _new_engine(**kwargs):  # test seam(panel_ocr/slate_ocr 미러)
    from rapidocr_onnxruntime import RapidOCR  # 지연 import(번들 무관 경로 보호)
    return RapidOCR(**kwargs)


def _reset_engines() -> None:  # test seam
    global _local
    _local = threading.local()


def _get_engine():
    engine = getattr(_local, "engine", None)
    if engine is None:
        engine = _new_engine()
        _local.engine = engine
    return engine


def _decode_png(png_bytes: bytes):
    import numpy  # RapidOCR 전이의존 — 지연 import(panel_ocr._decode_png 미러)
    from PIL import Image
    with Image.open(io.BytesIO(png_bytes)) as im:
        return numpy.array(im.convert("RGB"))


def _norm(text: str) -> str:
    return _NON_ALNUM.sub("", text).upper()


def _ocr_page(doc: PdfDocument, page: int, dpi: int) -> list[tuple[tuple[float, float, float, float], str, float]]:
    """페이지를 OCR해 (bbox_pt, text, conf) 목록으로. 좌표는 pt로 환산."""
    arr = _decode_png(doc.render_png(page, dpi=dpi, annots=False))
    result, _ = _get_engine()(arr)
    out = []
    scale = 72.0 / dpi
    for box, text, conf in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        rect = (min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale)
        out.append((rect, str(text), float(conf)))
    return out


@dataclass(frozen=True)
class _Geometry:
    """페이지에서 읽어낸 시트 칸 구조(작품마다 다르다)."""
    header_y: float          # 이 위는 로고·머리글 — 노트 아님
    footer_y: float          # 이 아래는 푸터 라벨 — 노트 아님
    dialog_band: tuple[float, float] | None   # 대사·EXP 칸 전체 x 구간
    num_bands: tuple[tuple[float, float], ...]  # 프레임 번호 컬럼들
    col_edges: tuple[float, ...]              # 배치 한계로 쓸 칸 경계 x

    def limit_x1(self, cx: float, page_w: float) -> float:
        for edge in self.col_edges:
            if edge > cx + _BAND_PAD:
                return edge
        return page_w - 8.0


def _is_scanned(doc: PdfDocument, page: int) -> bool:
    """이 페이지가 스캔본인가 — 페이지를 거의 덮는 이미지가 있으면 그렇다."""
    page_w, page_h = doc.page_size(page)
    area = page_w * page_h
    if area <= 0:
        return False
    for x0, y0, x1, y1 in doc.image_rects(page):
        if (x1 - x0) * (y1 - y0) >= area * _SCAN_COVER:
            return True
    return False


def _derive_geometry(items, page_w: float, page_h: float) -> _Geometry | None:
    """OCR 결과에서 칸 구조를 유도한다. 머리글 줄을 못 찾으면 None."""
    labelled = [(r, _norm(t)) for r, t, _c in items]
    header_hits = [(r, n) for r, n in labelled
                   if n in _HEADER_LABELS or _DIALOG_RE.match(n)]
    if not header_hits:
        return None
    # 머리글은 한 줄에 몰려 있다 — 가장 많이 모인 y 밴드를 고른다
    best, best_y = [], None
    for r0, _n0 in header_hits:
        cy = (r0[1] + r0[3]) / 2
        row = [(r, n) for r, n in header_hits
               if abs((r[1] + r[3]) / 2 - cy) <= _HEADER_ROW_TOL]
        if len(row) > len(best):
            best, best_y = row, cy
    if best_y is None or len(best) < 2:
        return None
    header_y = max(r[3] for r, _ in best)

    dialog = [r for r, n in best if _DIALOG_RE.match(n)]
    dialog_band = ((min(r[0] for r in dialog) - _BAND_PAD,
                    max(r[2] for r in dialog) + _BAND_PAD) if dialog else None)

    # 푸터: 페이지 아래쪽에 있는 서식 라벨(같은 라벨이 머리글에 있는 양식도
    # 있어서 — KOTH는 ANIMATOR가 머리글이다 — 위치로 가른다)
    foot = [r for r, n in labelled
            if n in _FOOTER_LABELS and r[1] > page_h * 0.8]
    footer_y = min((r[1] for r in foot), default=page_h)

    # 프레임 번호 컬럼: 숫자만 촘촘히 쌓인 x 구간(양식마다 위치·개수가 다르다)
    bins: dict[int, list[str]] = {}
    for r, n in labelled:
        if not (header_y < r[1] < footer_y):
            continue
        bins.setdefault(int(((r[0] + r[2]) / 2) // _NUM_BIN_PT), []).append(n)
    num_bands = []
    for b, vals in sorted(bins.items()):
        if len(vals) < _NUM_BIN_MIN:
            continue
        digits = sum(1 for v in vals if v.isdigit())
        if digits / len(vals) >= _NUM_BIN_RATIO:
            num_bands.append((b * _NUM_BIN_PT - _BAND_PAD,
                              (b + 1) * _NUM_BIN_PT + _BAND_PAD))
    merged: list[tuple[float, float]] = []
    for lo, hi in num_bands:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))

    edges = sorted({r[0] - _BAND_PAD for r, _ in best} |
                   {lo for lo, _ in merged} |
                   ({dialog_band[0]} if dialog_band else set()))
    return _Geometry(header_y=header_y, footer_y=footer_y,
                     dialog_band=dialog_band, num_bands=tuple(merged),
                     col_edges=tuple(e for e in edges if e > 0))


class XsheetProfile:
    name = "xsheet"
    label = "엑스시트 (Exposure Sheet)"

    def detect(self, doc: PdfDocument) -> bool:
        """텍스트 레이어가 없고(스캔) 저해상 OCR에서 시트 헤더 활자가 2개
        이상 읽히면 엑스시트로 본다. 텍스트가 있는 문서는 OCR 없이 즉시
        False — 스토리보드가 레지스트리에서 먼저 검사되므로 여기 오는
        텍스트 문서는 미지원 포맷뿐이지만, 그래도 OCR 비용은 아낀다."""
        pages = min(doc.page_count, _DETECT_PAGES)
        if pages == 0:
            return False
        for p in range(pages):
            # 판정 기준은 "텍스트 레이어가 없다"가 아니라 **페이지가 스캔
            # 이미지다**. 개정본·부분 번역본은 스캔 위에 텍스트가 얹혀 있어
            # (BM802_O005 실측: 한글 주석 70~138자 + 파일명 스탬프) 텍스트
            # 유무로 자르면 멀쩡한 엑스시트를 통째로 거른다. 스캔이 아닌
            # 페이지는 OCR 비용도 쓰지 않고 건너뛴다.
            if not _is_scanned(doc, p):
                continue
            seen: set[str] = set()
            for _rect, text, _conf in _ocr_page(doc, p, _DETECT_DPI):
                n = _norm(text)
                if n in _DETECT_LABELS:
                    seen.add(n)
                elif _DIALOG_RE.match(n):
                    seen.add("DIALOGCOL")      # 표기가 달라도 한 종류로
            if len(seen) >= 2:
                return True
        return False

    def extract(self, doc: PdfDocument) -> list[PdfBlock]:
        """전 페이지 OCR → 템플릿/음소/번호 컬럼 제외 → 근접 클러스터링.

        블록 text는 RapidOCR 원시 판독(손글씨라 신뢰 불가)이다 — 표시·번역
        전에 반드시 transcribe_blocks가 비전 CLI 전사로 교체한다. 원시
        판독을 그대로 두는 이유는 한글 재투입 안전장치(has_hangul)와
        디버깅 단서로 쓰기 위해서다."""
        blocks: list[PdfBlock] = []
        geom: _Geometry | None = None
        for page in range(doc.page_count):
            page_w, page_h = doc.page_size(page)
            items = _ocr_page(doc, page, _OCR_DPI)
            # 양식은 문서 안에서 일정하다 — 머리글이 안 읽힌 페이지(표지 등)는
            # 직전 페이지에서 얻은 구조를 그대로 쓴다.
            geom = _derive_geometry(items, page_w, page_h) or geom
            if geom is None:
                continue
            candidates = []
            for rect, text, _conf in items:
                if _is_template(rect, text, geom):
                    continue
                candidates.append((rect, text))
            for grp in _cluster(candidates):
                x0 = min(r[0] for r, _ in grp)
                y0 = min(r[1] for r, _ in grp)
                x1 = max(r[2] for r, _ in grp)
                y1 = max(r[3] for r, _ in grp)
                raw = " ".join(
                    t for _, t in sorted(grp, key=lambda it: (it[0][1], it[0][0])))
                if has_hangul(raw):
                    continue  # 번역 완료본 재투입 안전장치(공통 규칙)
                if ((x1 - x0) * (y1 - y0) < _MIN_NOTE_AREA
                        and len(_ALNUM_RE.findall(raw)) < _MIN_RAW_ALNUM):
                    continue  # 잡티(서클 마커·셀 번호 한 글자) — 전사 낭비
                blocks.append(PdfBlock(
                    page=page, kind=NOTE_KIND, text=raw, bbox=(x0, y0, x1, y1),
                    limit_x1=geom.limit_x1((x0 + x1) / 2, page_w)))
        return blocks

    # ---- 전사 훅(pdf_run이 getattr로 발견하는 optional 계약) ------------
    #
    # doc 락을 쥐는 렌더 단계와, 락이 필요 없는 느린 CLI 단계를 **반드시**
    # 분리한다 — 전사는 문서당 수십 분이라 한 훅으로 합치면 그동안 페이지
    # 미리보기 라우트(GET /page)가 doc 락에 통째로 막힌다.

    def render_transcribe_crops(self, doc: PdfDocument,
                                blocks: list[PdfBlock], job_dir: Path) -> None:
        from ..handwriting_transcribe import render_crops
        render_crops(doc, blocks, job_dir)

    def transcribe_blocks(self, blocks: list[PdfBlock], job_dir: Path,
                          should_continue: Callable[[], bool] | None = None,
                          on_progress: Callable[[float], None] | None = None,
                          ) -> list[PdfBlock]:
        from ..handwriting_transcribe import transcribe
        return transcribe(blocks, job_dir, should_continue=should_continue,
                          on_progress=on_progress)

    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        """사람 관례(원문 옆 병기) 재현: 오른쪽 여백 → 왼쪽 여백 → 아래 순.

        열 경계(block.limit_x1)를 넘지 않는다 — 액션 노트의 번역이 DIALOG
        음소 컬럼 위로 넘어가면 립싱크 표를 가린다."""
        page_w, page_h = page_size
        bx0, by0, bx1, by1 = block.bbox
        limit_x1 = block.limit_x1 if block.limit_x1 is not None else page_w - 8.0

        for fontsize in (_FONTSIZE, 8.0, _MIN_FONTSIZE):
            # 오른쪽: 원문 끝에서 열 경계까지
            width = limit_x1 - (bx1 + 3.0)
            if width >= 45.0:
                height = _estimate_height(ko_text, width, fontsize)
                if by0 + height <= page_h - 8.0:
                    rect = _clamp_nondegenerate(
                        bx1 + 3.0, by0, limit_x1, by0 + height, page_h)
                    return Overlay(page=block.page, rect=rect,
                                   text=ko_text, fontsize=fontsize)
            # 왼쪽: 페이지/열 좌단에서 원문 시작까지
            width = (bx0 - 3.0) - 8.0
            if width >= 45.0:
                height = _estimate_height(ko_text, width, fontsize)
                if by0 + height <= page_h - 8.0:
                    rect = _clamp_nondegenerate(
                        8.0, by0, bx0 - 3.0, by0 + height, page_h)
                    return Overlay(page=block.page, rect=rect,
                                   text=ko_text, fontsize=fontsize)
        # 아래: 원문 폭 그대로(최소 60pt), 축소 사다리 최하단 크기로
        x1 = min(limit_x1, max(bx1, bx0 + 60.0))
        height = _estimate_height(ko_text, x1 - bx0, _MIN_FONTSIZE)
        rect = _clamp_nondegenerate(bx0, by1 + 2.0, x1, by1 + 2.0 + height, page_h)
        return Overlay(page=block.page, rect=rect, text=ko_text,
                       fontsize=_MIN_FONTSIZE)


def _is_template(rect: tuple[float, float, float, float], text: str,
                 geom: _Geometry) -> bool:
    """인쇄 서식·번역 대상이 아닌 것을 걸러낸다(좌표는 geom에서 온다)."""
    x0, y0, x1, y1 = rect
    if y1 < geom.header_y or y0 > geom.footer_y:
        return True
    n = _norm(text)
    if n in _TEMPLATE_WORDS or n in _FOOTER_LABELS or _DIALOG_RE.match(n):
        return True
    cx = (x0 + x1) / 2
    for lo, hi in geom.num_bands:
        if lo <= cx <= hi and (n.isdigit() or len(n) <= 2):
            return True
    if geom.dialog_band is not None:
        lo, hi = geom.dialog_band
        # 대사 칸에서 짧은 토큰 = 립싱크 음소(aa·ay·uw…) — 사람도 번역하지
        # 않는다. 화자 이름(HARRIS…)은 길어서 살아남는다(BM802 실측: 사람이
        # `해리슨`으로 옮겼다).
        if lo <= cx <= hi and len(n) <= _PHONETIC_MAX_LEN:
            return True
    return False


def _cluster(items: list[tuple[tuple[float, float, float, float], str]],
             pad: float = _CLUSTER_PAD):
    """패딩 rect가 겹치는 것끼리 연결 요소로 묶는다 — 엑스시트 노트는
    세로로 단어를 쌓아 쓰는 관례라(SUBTLE/TREMBLE/ON/HANK…) 줄 단위 OCR
    박스를 노트 하나로 재조립해야 사람 주석 1개와 1:1이 된다."""
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        xi0, yi0, xi1, yi1 = items[i][0]
        for j in range(i + 1, n):
            xj0, yj0, xj1, yj1 = items[j][0]
            if (xi0 - pad < xj1 + pad and xj0 - pad < xi1 + pad
                    and yi0 - pad < yj1 + pad and yj0 - pad < yi1 + pad):
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return list(groups.values())
