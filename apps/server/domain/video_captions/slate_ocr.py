"""RapidOCR(onnxruntime) 슬레이트 판독 래퍼.

onnxruntime은 이미 faster-whisper 전이의존 + 번들 --collect-all 대상이라
새 시스템 바이너리가 없다. RapidOCR 초기화(모델 로드)는 비싸므로 프로세스당
1회 지연 생성한다. 슬레이트는 배경 대비 뚜렷한 산세리프라 판독이 쉽다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .scene_split import tokenize

logger = logging.getLogger("yeson.video.slate_ocr")

_engine = None


def _get_engine():
    """RapidOCR 지연 싱글턴. import·초기화 실패는 호출자에게 전파한다."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR  # 지연 import (번들 무관 경로 보호)
        _engine = RapidOCR()
    return _engine


# 슬레이트로 인정하는 상단 밴드(프레임 높이 대비 y중심 비율). 슬레이트는 관례상
# 프레임 상단에 붙고, 하단에는 워터마크·번인 자막이 온다 — 실기(HZBN307)에서
# 하단 워터마크가 토큰 수(6>5)로 슬레이트를 이겨 경계가 전멸한 회귀의 방지선.
_TOP_BAND_FRAC = 0.35


def pick_slate_line(
    lines: list[tuple[str, float, float]], delimiters: list[str],
    min_tokens: int, top_frac: float = _TOP_BAND_FRAC,
) -> str:
    """OCR 라인 후보 중 슬레이트 1줄 선택 — 상단 밴드(y_frac ≤ top_frac) 안에서
    토큰 수(내림차순)·신뢰도(내림차순) 우선. min_tokens 미만으로 쪼개지는 라인과
    밴드 밖 라인(하단 워터마크·자막)은 후보에서 제외한다. 밴드 안에 후보가 없으면
    하단으로 폴백하지 않고 ""(판독실패) — hold_keys가 직전 유효값으로 홀드한다."""
    scored = []
    for text, score, y_frac in lines:
        if y_frac > top_frac:
            continue
        n = len(tokenize(text, delimiters))
        if n >= min_tokens:
            scored.append((n, score, text))
    if not scored:
        return ""
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def read_slate_line(
    image_path: str | Path, delimiters: list[str], min_tokens: int = 2,
    top_frac: float = _TOP_BAND_FRAC,
) -> str:
    """이미지 한 장 OCR → 슬레이트 라인. 판독 실패/후보 없음은 "" 반환.

    top_frac은 슬레이트로 인정할 상단 밴드 비율. 사용자가 OCR 영역을 지정해
    프레임이 이미 잘려 들어온 경우 1.0을 줘야 한다 — 크롭 자체가 영역 필터라
    잘린 이미지 안에서는 슬레이트가 어디에 있어도 정상이다.
    """
    try:
        result, _elapse = _get_engine()(str(image_path))
        if not result:
            return ""
        from PIL import Image  # RapidOCR 전이의존(lock 고정) — 지연 import
        with Image.open(image_path) as im:
            height = im.height
        # RapidOCR 반환: [[box(4점 [x,y]), text, score], ...]
        lines = []
        for item in result:
            ys = [float(p[1]) for p in item[0]]
            y_frac = (sum(ys) / len(ys)) / height if height else 1.0
            lines.append((item[1], float(item[2]), y_frac))
        return pick_slate_line(lines, delimiters, min_tokens, top_frac)
    except Exception:  # noqa: BLE001 — 한 프레임 판독 실패가 전체 스캔을 막지 않게
        logger.exception("OCR failed for %s", image_path)
        return ""
