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


def pick_slate_line(
    lines: list[tuple[str, float]], delimiters: list[str], min_tokens: int,
) -> str:
    """OCR 라인 후보 중 슬레이트 1줄 선택 — 토큰 수(내림차순)·신뢰도(내림차순)
    우선. min_tokens 미만으로 쪼개지는 라인은 후보에서 제외한다."""
    scored = []
    for text, score in lines:
        n = len(tokenize(text, delimiters))
        if n >= min_tokens:
            scored.append((n, score, text))
    if not scored:
        return ""
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def read_slate_line(
    image_path: str | Path, delimiters: list[str], min_tokens: int = 2,
) -> str:
    """이미지 한 장 OCR → 슬레이트 라인. 판독 실패/후보 없음은 "" 반환."""
    try:
        result, _elapse = _get_engine()(str(image_path))
    except Exception:  # noqa: BLE001 — 한 프레임 판독 실패가 전체 스캔을 막지 않게
        logger.exception("OCR failed for %s", image_path)
        return ""
    if not result:
        return ""
    # RapidOCR 반환: [[box, text, score], ...]
    lines = [(item[1], float(item[2])) for item in result]
    return pick_slate_line(lines, delimiters, min_tokens)
