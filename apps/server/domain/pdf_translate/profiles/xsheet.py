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

# 놓을 자리로 인정하는 최소 폭. 사람 납품본 실측(KOTH_1401_A2 전수, 주석
# 2,015개)에서 상자 폭은 중앙값 30pt·1분위 21pt였다 — 사람은 이만한 틈에도
# 끼워 넣는다. 옛 값 45pt는 그 틈을 통째로 버리고 더 먼 자리로 밀어냈다.
_MIN_BOX_W = 24.0
# 빈 자리 판정(place_with_doc). 사람 납품본 실측에서 주석 밑 손글씨 면적
# 비율은 중앙값 6.34%였다 — 사람도 여백이 없으면 겹쳐 쓴다. 2%는 "글자
# 위는 아니다"에 해당하는 선이고, 여기 못 미치는 자리가 없으면 가장 덜
# 덮는 자리를 쓴다(무조건 피하려다 원문에서 멀어지는 게 더 나쁘다).
_INK_OK = 0.02
_INK_DPI = 100        # 잉크 유무 판정에는 이 정도면 충분하다(OCR용 200과 별개)
_INK_DARK = 150       # 이보다 어두우면 잉크
_RULE_FILL = 0.45     # 행·열의 이 비율 이상이 어두우면 인쇄 괘선으로 본다

# 빈 자리를 찾아 세로로 밀어 보는 폭. **절대값이어야 한다** — 처음엔 노트
# 높이에 비례시켰는데(±0.5·±1배), 여러 줄짜리 긴 노트에서는 그 절반이
# 165pt여서 번역문이 원문에서 통째로 떨어져 나갔다(시뮬레이션에서 실제
# 발생: y362 노트의 번역이 y530으로). 사람 납품본 실측(A2 전수)에서
# 원문 상단 대비 주석 상단의 세로 편차는 중앙 15.6pt·8분위 38.7·9분위
# 53.7pt였다 — 사다리를 그 분포 안에 가둔다.
_DY_LADDER = (0.0, 16.0, -16.0, 32.0, -32.0, 52.0, -52.0)


def _ink_ratio(ink, rect: tuple[float, float, float, float],
               page_h: float) -> float:
    """이 사각형 넓이에서 손글씨가 차지하는 비율."""
    scale = _INK_DPI / 72.0
    h, w = ink.shape
    x0 = max(0, int(rect[0] * scale)); y0 = max(0, int(rect[1] * scale))
    x1 = min(w, int(rect[2] * scale)); y1 = min(h, int(rect[3] * scale))
    if x1 <= x0 or y1 <= y0:
        return 1.0
    return float(ink[y0:y1, x0:x1].mean())


def _natural_width(text: str, fontsize: float) -> float:
    """이 글이 **줄바꿈 없이** 앉으려면 필요한 폭.

    번역문은 원문 손글씨의 세로 쌓기를 그대로 물려받아 이미 줄이 나뉘어
    있다(`SMU\\n남자가\\n걸어서`) — 가장 긴 줄만 담으면 사람이 쓰는 좁은
    덩어리가 그대로 나온다. `_estimate_height`와 같은 CJK 근사(글자당 ≈
    fontsize pt)를 쓴다: 두 함수가 어긋나면 폭 계산이 예상한 줄 수와 높이
    계산이 쓰는 줄 수가 달라져 마지막 줄이 상자 밖으로 잘린다."""
    longest = max((len(line) for line in text.split("\n")), default=1)
    return max(1, longest) * fontsize + 4.0

# 엑스시트 하우스 용어(KO→KO) — KOTH_1401_A2 사람 납품본 전수 대조
# (2026-08-21, 사람 주석 2,015개 vs 우리 출력 1,697개). house_style.py가
# 아니라 여기 두는 이유: `대사`는 스토리보드에선 **정답**이라(DIALOG 칸의
# 표준 역어) 전역 치환하면 다른 포맷을 망친다. 엑스시트에서만 사람이
# `대화`를 쓴다 — 포맷 스코프가 계약이다.
#
# 채택 기준은 house_style.py와 같다: 한쪽이 정확히 0인 항목만 넣는다.
#   STL(settle) 사람 `안착` 50 / 우리 0  ↔ 우리 `세틀` 9·`스틸` 6 / 사람 0
#   DIAL        사람 `대화` 29 / 우리 0  ↔ 우리 `대사` 18·`다이얼` 3 / 사람 0
#   TURN        사람 `턴한다` 57 / 우리 0 ↔ 우리 `돈다` 22 / 사람 0
#   STEP        사람 `스텝` 62 / 우리 5  ↔ 우리 `걸음` 46 / 사람 0
#   GESTURE     사람 `제스쳐` 9 / 우리 0 ↔ 우리 `제스처` 8 / 사람 0
#   OVERSHOOT   사람 `오버슛` 18 / 우리 0 ↔ 우리 `오버슈트` 2 / 사람 0
#   BLINK       사람 `눈깜박` 155 / 우리 0 ↔ 우리는 한 문서 안에서 다섯 형태로
#               흔들린다(`눈 깜빡` 59·`깜빡임` 40·`눈을 깜빡인다` 25·`눈깜빡`
#               8·`눈 깜빡임` 3) — 표기 통일이 곧 사람 표기와의 일치다.
# `스틸`·`다이얼`은 표기 흔들림이 아니라 **오역**이다(STL=settle을 still로,
# DIAL=dialogue를 dial로 읽었다). 사전이 없으면 다음 작품에서도 재발한다.
#
# ⛔`머리`→`고개`는 넣지 않는다: 사람 `고개` 156 / `머리` 9(머리카락 4 제외)로
# 사람 쪽이 0이 아니다. 실제로 사람은 `머리 기웃`·`척의 머리가`처럼 문맥에
# 따라 `머리`를 쓴다 — 위 기준 미달이라 보류한다(누락 84건, 최대 미해결 항목).
_HOUSE_KO_XSHEET: tuple[tuple[re.Pattern[str], str], ...] = (
    # 눈깜박: 긴 형태부터 — 짧은 규칙이 먼저 돌면 `눈 깜빡임`이 `눈깜박임`으로
    # 남는다. 앞 규칙이 `빡`을 `박`으로 바꾸므로 뒤 규칙과 겹치지 않는다.
    (re.compile(r"눈\s*을\s*깜[빡박]인다"), "눈깜박"),
    (re.compile(r"눈\s*깜[빡박](?:임|인다)?"), "눈깜박"),
    (re.compile(r"깜[빡박]임"), "눈깜박"),
    (re.compile(r"깜빡"), "눈깜박"),
    (re.compile(r"오버슈트"), "오버슛"),
    (re.compile(r"제스처"), "제스쳐"),
    (re.compile(r"다이얼"), "대화"),
    (re.compile(r"대사"), "대화"),
    (re.compile(r"돈다"), "턴한다"),
    # `걸음걸이`는 사람도 쓰는 정상 낱말이라 건드리지 않는다.
    (re.compile(r"걸음(?!걸이)"), "스텝"),
    # HEAD: 사람 `고개` 156 / `머리` 9(머리카락 4 제외) ↔ 우리 `머리` 126.
    # 한쪽이 0은 아니지만 house_style의 `신밖`→`씬밖` 선례와 같은 **다수 표기
    # 통일**이고, 그 선례(1건 소수)보다 훨씬 일방적이다(9 대 156). 사람의 9건에
    # 규칙성이 있는지 문맥까지 실측했으나 없었다 — `기웃`과 함께 쓸 때조차
    # `고개` 17 대 `머리` 2로 `고개`가 우세하다.
    # ⚠`머리카락`은 사람·우리 4건씩 정확히 일치하는 정상 낱말이라 반드시 뺀다.
    (re.compile(r"머리(?!카락)"), "고개"),
)
# 원문 코드별 규칙 — 같은 한국어라도 원문이 무엇이냐에 따라 사람 표기가
# 갈린다(TILT는 `기웃`, LEAN은 `기울인다`). 무조건 치환하면 한쪽이 깨진다.
_HOUSE_KO_XSHEET_BY_SRC: tuple[tuple[re.Pattern[str],
                                     tuple[tuple[re.Pattern[str], str], ...]], ...] = (
    # STL(settle): 사람 `안착` 50 / 우리 0 ↔ 우리 `세틀` 9·`스틸` 6·`정지` 5.
    # ⚠단어 경계가 계약이다: 부분 문자열로 찾으면 `HUSTLE`·`CASTLE`·`WRESTLE`가
    # STL 노트로 오인돼 멀쩡한 `정지`를 `안착`으로 덮는다(테스트가 잡은 함정).
    (re.compile(r"\bSTL\b", re.IGNORECASE), (
        (re.compile(r"[&+]\s*세틀"), "안착"),
        (re.compile(r"세틀"), "안착"),
        (re.compile(r"스틸"), "안착"),
        (re.compile(r"정지"), "안착"),
    )),
    # LEAN: 사람 `기울인다` 68 / 우리 5 ↔ 우리 `기댄다` 32·`기댐` 4 / 사람 0.
    # 기대다(lean on)가 아니라 몸을 기울이는 동작이다.
    (re.compile(r"\bLEANS?\b", re.IGNORECASE), (
        (re.compile(r"기댄다"), "기울인다"),
        (re.compile(r"기댐"), "기울인다"),
    )),
    # TILT: 사람 `기웃` 41 / 우리 0 ↔ 우리 `기울임` 43(사람 1)·`틸트` 9 / 사람 0.
    # `틸트`는 표기 흔들림이 아니라 음역이다 — 사람은 한 번도 쓰지 않는다.
    (re.compile(r"\bTILTS?\b", re.IGNORECASE), (
        (re.compile(r"기울임"), "기웃"),
        (re.compile(r"틸트"), "기웃"),
    )),
)

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
                          engine: str | None = None) -> list[PdfBlock]:
        from ..handwriting_transcribe import transcribe
        return transcribe(blocks, job_dir, should_continue=should_continue,
                          on_progress=on_progress, engine=engine)

    def place_with_doc(self, block: PdfBlock, ko_text: str,
                       page_size: tuple[float, float],
                       doc: PdfDocument) -> Overlay:
        """`place`의 후보 중 **손글씨가 없는 자리**를 고른다(선택 훅).

        왜 필요한가: 상자를 글에 맞춰 좁히는 것만으로는 겹침이 풀리지
        않는다 — A2 전수 시뮬레이션에서 폭은 사람과 같아졌지만(중앙 31 대
        30pt) 손글씨 위 비율은 93.1%→90.6%로 거의 그대로였다(사람 78.1%).
        좁아진 만큼 세로로 길어져 여전히 글자 위에 앉기 때문이다. 사람은
        그럴 때 **옆의 빈 칸으로 옮겨 쓴다** — 그걸 재현한다.

        `place`(문서 없이)는 그대로 남긴다: 첫 후보를 돌려주므로 기존
        호출자·테스트의 계약이 바뀌지 않는다."""
        page_h = page_size[1]
        try:
            ink = self._page_ink(doc, block.page)
        except Exception:  # noqa: BLE001 — 그림을 못 얻으면 옛 경로로
            logger.warning("pdf-translate: page %d 잉크 마스크 실패 — 기본 배치",
                           block.page)
            return self.place(block, ko_text, page_size)
        best: tuple[float, Overlay] | None = None
        for rect, fontsize in self._candidates(block, ko_text, page_size):
            score = _ink_ratio(ink, rect, page_h)
            if score <= _INK_OK:
                return Overlay(page=block.page, rect=rect,
                               text=ko_text, fontsize=fontsize)
            ov = Overlay(page=block.page, rect=rect,
                         text=ko_text, fontsize=fontsize)
            if best is None or score < best[0]:
                best = (score, ov)
        # 전부 손글씨 위라면 그중 가장 덜 덮는 자리(사람도 여백이 없으면 겹쳐 쓴다)
        return best[1] if best else self.place(block, ko_text, page_size)

    def _page_ink(self, doc: PdfDocument, page: int):
        """이 페이지의 손글씨 마스크. 블록이 페이지 순서로 오므로 한 장만 캔다."""
        cached = getattr(self, "_ink_cache", None)
        if cached is not None and cached[0] == page:
            return cached[1]
        arr = _decode_png(doc.render_png(page, dpi=_INK_DPI, annots=False))
        gray = arr[:, :, :3].mean(axis=2) if arr.ndim == 3 else arr
        ink = gray < _INK_DARK
        # 인쇄 괘선(가로 줄·세로 칸선)은 어디에나 있어서 빼지 않으면 모든
        # 자리가 '잉크 있음'이 된다 — 실측에서 우리·사람 모두 100%로 나왔다.
        ink[ink.mean(axis=1) > _RULE_FILL, :] = False
        ink[:, ink.mean(axis=0) > _RULE_FILL] = False
        self._ink_cache = (page, ink)
        return ink

    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        """사람 관례(원문 옆 병기) 재현: 오른쪽 여백 → 왼쪽 여백 → 아래 순.

        열 경계(block.limit_x1)를 넘지 않는다 — 액션 노트의 번역이 DIALOG
        음소 컬럼 위로 넘어가면 립싱크 표를 가린다.

        **상자는 남은 자리가 아니라 글에 맞춘다**(`_natural_width`). 예전에는
        양쪽 다 가용 폭을 통째로 먹었고(오른쪽 = 칸 경계까지, 왼쪽 = 페이지
        좌단까지) 그 결과 A2 전수에서 폭 중앙값이 사람 30pt 대 우리 104.8pt,
        폭 100pt 초과가 사람 2.5% 대 우리 52.7%였다. 넓은 상자에 글이 왼쪽
        정렬로 그려지니 번역문이 원문에서 멀리 떨어져 앉거나 옆 노트의
        손글씨 위로 올라탔다(사용자 실물 지적, 2026-08-21).

        왼쪽 배치는 **원문에 붙인다**. 예전 코드는 상자를 페이지 좌단
        `8.0`에 고정해서, 원문이 오른쪽에 있을수록 번역문이 멀어졌다 —
        전수에서 우리 주석의 45.4%가 페이지 가장자리에 붙었고 사람은
        0.1%(2,015개 중 2개)뿐이었다."""
        rect, fontsize = next(iter(self._candidates(block, ko_text, page_size)))
        return Overlay(page=block.page, rect=rect, text=ko_text,
                       fontsize=fontsize)

    def _candidates(self, block: PdfBlock, ko_text: str,
                    page_size: tuple[float, float]):
        """놓을 만한 자리를 **선호 순서로** 흘린다 — 오른쪽·왼쪽·아래.

        `place`는 첫 후보를 쓰고, `place_with_doc`는 이 중 손글씨가 없는
        자리를 고른다. 한 곳에서 만들어야 두 경로가 갈라지지 않는다.

        같은 변에서도 세로로 조금씩 밀어 본다: 엑스시트는 노트가 세로로
        빽빽해서, 옆자리가 막혀도 반 줄 아래는 비어 있는 경우가 흔하다.
        """
        page_w, page_h = page_size
        bx0, by0, bx1, by1 = block.bbox
        limit_x1 = block.limit_x1 if block.limit_x1 is not None else page_w - 8.0

        for fontsize in (_FONTSIZE, 8.0, _MIN_FONTSIZE):
            want = _natural_width(ko_text, fontsize)
            for dy in _DY_LADDER:
                top = by0 + dy
                if top < 8.0:
                    continue
                # 오른쪽: 원문 끝에 붙여 필요한 만큼만
                avail = limit_x1 - (bx1 + 3.0)
                if avail >= _MIN_BOX_W:
                    width = min(want, avail)
                    height = _estimate_height(ko_text, width, fontsize)
                    if top + height <= page_h - 8.0:
                        yield (_clamp_nondegenerate(
                            bx1 + 3.0, top, bx1 + 3.0 + width, top + height,
                            page_h), fontsize)
                # 왼쪽: 원문 시작에 붙여 왼쪽으로 필요한 만큼만
                avail = (bx0 - 3.0) - 8.0
                if avail >= _MIN_BOX_W:
                    width = min(want, avail)
                    right = bx0 - 3.0
                    height = _estimate_height(ko_text, width, fontsize)
                    if top + height <= page_h - 8.0:
                        yield (_clamp_nondegenerate(
                            right - width, top, right, top + height,
                            page_h), fontsize)
        # 아래: 글에 맞춘 폭(원문 폭을 넘지 않되 최소 _MIN_BOX_W)
        width = max(_MIN_BOX_W, min(_natural_width(ko_text, _MIN_FONTSIZE),
                                    max(bx1 - bx0, _MIN_BOX_W)))
        x1 = min(limit_x1, bx0 + width)
        height = _estimate_height(ko_text, x1 - bx0, _MIN_FONTSIZE)
        yield (_clamp_nondegenerate(bx0, by1 + 2.0, x1, by1 + 2.0 + height,
                                    page_h), _MIN_FONTSIZE)

    def refine_ko(self, block: PdfBlock, ko_text: str) -> str:
        """엑스시트 하우스 용어로 KO→KO 교정(`_HOUSE_KO_XSHEET` 근거 참조).

        `FormatProfile` Protocol에는 등록하지 않는다 — storyboard.refine_ko와
        같은 선택 훅이고, overlay_plan이 `getattr`로 발견한다.

        왜 house_style.py(전역)가 아닌가: `대사`는 스토리보드 DIALOG 칸의
        정답이라 전역 치환하면 다른 포맷이 깨진다. 반대로 엑스시트에서는
        사람이 예외 없이 `대화`를 쓴다(29/0). 포맷마다 정답이 다른 용어는
        프로파일이 들고 있어야 한다.

        `_HOUSE_KO_XSHEET_BY_SRC`가 원문 조건부인 이유: 같은 한국어라도 원문에
        따라 사람 표기가 갈린다(TILT는 `기웃`, LEAN은 `기울인다`). `정지`·`스틸`
        처럼 다른 문맥에선 정당한 낱말도 있어서, 원문 없이 무조건 치환하면
        멀쩡한 말을 덮는다."""
        out = ko_text
        for pat, rep in _HOUSE_KO_XSHEET:
            out = pat.sub(rep, out)
        src = block.text or ""
        for src_pat, rules in _HOUSE_KO_XSHEET_BY_SRC:
            if not src_pat.search(src):
                continue
            for pat, rep in rules:
                out = pat.sub(rep, out)
        return out


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
