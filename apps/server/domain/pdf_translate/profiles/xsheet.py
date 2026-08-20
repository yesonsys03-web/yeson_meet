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
_FONTSIZE = 9.0     # 사람 납품본 9pt 실측
_MIN_FONTSIZE = 7.0

# 템플릿 좌표(pt) — 792×1224pt(타블로이드) KOTH 시트 실측값. 다른 스캔
# 크기에 대비해 사용할 때 페이지 크기 비율로 스케일한다(_scaled).
_BASE_W, _BASE_H = 792.0, 1224.0
_Y_HEADER = 121.0    # 인쇄 헤더 밴드 하단 — 위는 템플릿 라벨·에피소드 타이틀
_Y_FOOTER = 1186.0   # 인쇄 푸터 밴드 상단
# 프레임 번호 세로 컬럼 2줄(좌: 1~15 반복, 우: 통산 1~80) — 인쇄 숫자
_NUM_BANDS = ((351.6, 372.0), (649.2, 670.8))
# DIALOG/EXP 립싱크 음소 글자(A·M·EH…) — 사람도 번역하지 않는다
_PHONETIC_BAND = (369.6, 432.0)
# 배치 상한(limit_x1)을 정하는 열 경계: 액션 구역은 DIALOG 컬럼 직전까지,
# 중간(프레임 카운트 그리드)은 우측 번호 컬럼 직전까지, 카메라 노트 구역은
# 페이지 우단까지.
_ACTION_X1 = 351.6
_MIDDLE_X1 = 649.2
_CLUSTER_PAD = 6.0   # 세로로 쌓인 손글씨 단어들을 노트 하나로 묶는 근접 반경

# 인쇄 템플릿 문구(정규화: 영숫자만·대문자) — OCR conf 1.00으로 읽히는
# 활자들이라 화이트리스트 일치로 안전하게 걸러진다.
_TEMPLATE_WORDS = {
    "KING", "HILL", "PRODNO", "FOOTAGE", "ANIMATOR", "SCENENO", "SHEETNO",
    "DIALOG", "EXP", "CAMERANOTES", "ACTION", "CONT", "SCENEDIRECTOR",
    "APPROVED", "PRODUCTIONNO", "ACT",
}
_DETECT_TOKENS = {"ANIMATOR", "DIALOG", "EXP", "CAMERANOTES", "SHEETNO"}
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
    arr = _decode_png(doc.render_png(page, dpi=dpi))
    result, _ = _get_engine()(arr)
    out = []
    scale = 72.0 / dpi
    for box, text, conf in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        rect = (min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale)
        out.append((rect, str(text), float(conf)))
    return out


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
        total_chars = sum(
            len(b.text.strip())
            for p in range(pages) for b in doc.raw_blocks(p))
        if total_chars > 20:
            return False
        for p in range(pages):
            hits = 0
            for _rect, text, _conf in _ocr_page(doc, p, _DETECT_DPI):
                if _norm(text) in _DETECT_TOKENS:
                    hits += 1
            if hits >= 2:
                return True
        return False

    def extract(self, doc: PdfDocument) -> list[PdfBlock]:
        """전 페이지 OCR → 템플릿/음소/번호 컬럼 제외 → 근접 클러스터링.

        블록 text는 RapidOCR 원시 판독(손글씨라 신뢰 불가)이다 — 표시·번역
        전에 반드시 transcribe_blocks가 비전 CLI 전사로 교체한다. 원시
        판독을 그대로 두는 이유는 한글 재투입 안전장치(has_hangul)와
        디버깅 단서로 쓰기 위해서다."""
        blocks: list[PdfBlock] = []
        for page in range(doc.page_count):
            page_w, page_h = doc.page_size(page)
            sx, sy = page_w / _BASE_W, page_h / _BASE_H
            candidates = []
            for rect, text, _conf in _ocr_page(doc, page, _OCR_DPI):
                if self._is_template(rect, text, sx, sy):
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
                cx = (x0 + x1) / 2
                if cx <= _ACTION_X1 * sx:
                    limit_x1 = _ACTION_X1 * sx
                elif cx <= _MIDDLE_X1 * sx:
                    limit_x1 = _MIDDLE_X1 * sx
                else:
                    limit_x1 = page_w - 8.0
                blocks.append(PdfBlock(
                    page=page, kind=NOTE_KIND, text=raw,
                    bbox=(x0, y0, x1, y1), limit_x1=limit_x1))
        return blocks

    @staticmethod
    def _is_template(rect: tuple[float, float, float, float], text: str,
                     sx: float, sy: float) -> bool:
        x0, y0, x1, y1 = rect
        if y1 < _Y_HEADER * sy or y0 > _Y_FOOTER * sy:
            return True
        n = _norm(text)
        if n in _TEMPLATE_WORDS:
            return True
        cx = (x0 + x1) / 2
        for a, b in _NUM_BANDS:
            if a * sx <= cx <= b * sx and (n.isdigit() or len(n) <= 2):
                return True
        pa, pb = _PHONETIC_BAND
        return bool(pa * sx <= cx <= pb * sx and len(n) <= 3)

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
