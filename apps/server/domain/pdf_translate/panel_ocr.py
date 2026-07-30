"""패널 콜아웃 라벨(빨강 리더라인 주석) OCR 추출 — 빨강 프리필터 + RapidOCR.

Storyboard Pro 패널은 페이지당 단일 래스터 이미지로 구워져 있어(텍스트/주석/
벡터 전무) 일반 텍스트 추출로는 콜아웃 라벨("HANK'S TRUCK", "CAR006A",
"1000SB")을 전혀 못 읽는다(원본 p1 해부 실측). 라벨은 빨간 글자 + 빨간
사각/리더라인 관례라, 저해상도(dpi 60) 빨강 픽셀 카운트로 라벨 없는
대다수 페이지를 값싸게 걸러내고(프리필터), 통과한 페이지만 dpi 200으로
재렌더해 RapidOCR을 전체 실행한 뒤 히트별 빨강 비율로 검정 그림선 오탐을
배제한다.

RapidOCR 엔진 지연 싱글턴은 video_captions/slate_ocr.py의 초기화 패턴을
미러한다(프로세스당 1회 로드, 스레드 로컬).
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading

import numpy as np

from .backend import PdfDocument, RawBlock

logger = logging.getLogger("yeson.pdf.panel_ocr")

_local = threading.local()

ENV_ENABLED = "YESON_PDF_PANEL_OCR"

_PREFILTER_DPI = 60
_OCR_DPI = 200
# 빨강 판정(프리필터·히트 비율 공용, 실측 재설계 2026-07-30): 절대 채도
# 기준(구 R>150,G<100,B<100)은 얇은 빨간 글자의 안티에일리어싱 희석에
# 취약하다 — p18 실측: 60dpi 프리필터 크롭은 옛 마스크로 5px뿐(문턱 50
# 미달, 전멸), 심지어 200dpi OCR 히트 자체도 옛 마스크 기준 비율
# 0.053~0.083(문턱 0.15 근처에도 한참 못 미침, 라벨 6종 전부). R−G/R−B
# 차이 기준으로 바꾸면 같은 픽셀들이 안정적으로 잡힌다(같은 페이지 60dpi
# 33px, 200dpi 히트 비율 0.237~0.348) — 검정 그림선(사인·표지판 텍스트
# 등)은 그레이스케일이라 R≈G≈B, 두 마스크 다 항상 0에 가까워 오탐이
# 늘지 않는다(p1·p93·p18의 검정 히트 전부 실측 0.000). 차이 기준 마스크는
# 절대 채도 마스크의 초집합이라(R>150&G<100&B<100 ⟹ R−G>50&R−B>50) 기존에
# 잡히던 히트는 전부 그대로 잡힌다.
_RED_R_MIN = 120
_RED_DIFF_MIN = 50
# 프리필터 문턱(60dpi 픽셀 카운트): p18 실측 33px에 맞춰 50→10 완화 —
# 오탐 페이지는 OCR 1~2초 비용뿐이라 완화 방향이 안전(전 문서 재측정으로
# 폭주 없음 확인, task-19-report.md 참고).
_PREFILTER_MIN_PIXELS = 10
# 히트별 빨강 비율 문턱(200dpi 크롭): 확인된 잡음(검정 그림선, 전부
# 0.000~0.071 — YOSEMITE 0.071은 이 태스크 범위 밖이라 손대지 않음)과
# 확인된 유효 라벨(p1·p18·p93·p410 전부 0.219~0.391) 사이 실측 간극에
# 여유를 두고 0.15로 설정.
_HIT_RED_RATIO_MIN = 0.15

_HANGUL = re.compile(r"[가-힣]")


def _new_engine(**kwargs):  # test seam(slate_ocr 미러)
    from rapidocr_onnxruntime import RapidOCR  # 지연 import(번들 무관 경로 보호)
    return RapidOCR(**kwargs)


def _reset_engines() -> None:  # test seam
    global _local
    _local = threading.local()


def _get_engine():
    """RapidOCR 지연 생성 — 스레드마다 자기 엔진(slate_ocr과 동일한 이유:
    엔진 하나당 모델 로드 비용 ~수백ms, 스레드 로컬로 두면 오염 위험이 없다)."""
    engine = getattr(_local, "engine", None)
    if engine is None:
        engine = _new_engine()
        _local.engine = engine
    return engine


def _panel_ocr_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "1") != "0"


def _decode_png(png_bytes: bytes) -> np.ndarray:
    from PIL import Image  # RapidOCR 전이의존(lock 고정) — 지연 import
    with Image.open(io.BytesIO(png_bytes)) as im:
        return np.array(im.convert("RGB"))


def _red_mask(arr: np.ndarray) -> np.ndarray:
    """빨강 마스크(프리필터·히트 비율 공용) — R−G/R−B 차이 기준이라
    안티에일리어싱으로 희석된 얇은 빨간 글자도 잡고, 그레이스케일인 검정
    그림선(R≈G≈B)은 밝기와 무관하게 걸러진다."""
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    return (r > _RED_R_MIN) & (r - g > _RED_DIFF_MIN) & (r - b > _RED_DIFF_MIN)


def _crop_region_px(arr: np.ndarray, region: tuple[float, float, float, float],
                    dpi: int) -> np.ndarray:
    """region(pdf pt) → 렌더된 배열의 픽셀 슬라이스(dpi 배율 + 경계 클램프)."""
    scale = dpi / 72.0
    rx0, ry0, rx1, ry1 = region
    h, w = arr.shape[:2]
    x0 = max(0, min(w, round(rx0 * scale)))
    y0 = max(0, min(h, round(ry0 * scale)))
    x1 = max(x0, min(w, round(rx1 * scale)))
    y1 = max(y0, min(h, round(ry1 * scale)))
    return arr[y0:y1, x0:x1]


def find_panel_labels(
    doc: PdfDocument, page: int,
    region: tuple[float, float, float, float],
) -> list[RawBlock]:
    """region(pdf pt, (x0,y0,x1,y1)) 안에서 빨강 콜아웃 라벨을 OCR로 찾는다.

    킬스위치 YESON_PDF_PANEL_OCR=0이면 렌더조차 하지 않고 즉시 []. 프리필터
    (dpi 60 저해상도 렌더 + 빨강 픽셀 카운트)를 통과 못 하면 비싼 dpi 200
    렌더·OCR을 아예 건너뛴다 — 대다수 페이지(라벨 없음)의 비용을 없앤다.
    """
    if not _panel_ocr_enabled():
        return []

    low_arr = _decode_png(doc.render_png(page, dpi=_PREFILTER_DPI))
    low_crop = _crop_region_px(low_arr, region, _PREFILTER_DPI)
    if (low_crop.size == 0
            or int(_red_mask(low_crop).sum()) < _PREFILTER_MIN_PIXELS):
        return []

    hi_arr = _decode_png(doc.render_png(page, dpi=_OCR_DPI))
    hi_crop = _crop_region_px(hi_arr, region, _OCR_DPI)
    if hi_crop.size == 0:
        return []

    try:
        result, _elapse = _get_engine()(hi_crop)
    except Exception:  # 한 페이지 OCR 실패가 추출 전체를 막지 않게
        logger.exception("panel OCR failed for page %d", page)
        return []
    if not result:
        return []

    scale = _OCR_DPI / 72.0
    region_x0, region_y0, _rx1, _ry1 = region
    crop_h, crop_w = hi_crop.shape[:2]
    out: list[RawBlock] = []
    for box, text, _score in result:
        text = text.strip()
        if not text or _HANGUL.search(text):
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        bx0 = max(0, min(crop_w, int(min(xs))))
        bx1 = max(bx0, min(crop_w, int(max(xs))))
        by0 = max(0, min(crop_h, int(min(ys))))
        by1 = max(by0, min(crop_h, int(max(ys))))
        sub = hi_crop[by0:by1, bx0:bx1]
        if sub.size == 0:
            continue
        # 히트 bbox 내 빨강 픽셀 비율 — 패널 라벨은 빨간 글자이므로 검정
        # 그림선(사인·표지판 텍스트 등)이 우연히 OCR 히트가 돼도 여기서 걸러진다.
        if float(_red_mask(sub).mean()) < _HIT_RED_RATIO_MIN:
            continue
        out.append(RawBlock(
            text=text,
            bbox=(bx0 / scale + region_x0, by0 / scale + region_y0,
                  bx1 / scale + region_x0, by1 / scale + region_y0),
        ))
    return out
