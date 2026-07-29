"""PDF 블록 배치 번역 — 자막 도메인의 엔진(create_translator)을 그대로 쓰되
프롬프트와 리질리언트 배치는 이 도메인 소유다.

_translate_resilient(자막 모듈 private)를 import하지 않고 동일 알고리즘을
여기 둔다 — 자막 쪽 리팩토링이 PDF 번역을 흔들지 않게 도메인을 분리한다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from apps.server.ai.glossary import apply_ko_corrections, glossary_block
from apps.server.domain.video_captions.translate import (
    TranslationError,
    TranslationProvider,
)

logger = logging.getLogger("yeson.pdf.translate")


def build_pdf_prompt(texts: list[str]) -> str:
    """제작 문서(스토리보드 대사·액션 노트) 배열 → KO 번역 지시 프롬프트."""
    numbered = json.dumps(texts, ensure_ascii=False)
    return (
        "Translate each English text block from an animation production "
        "document (storyboard dialog and action notes) into natural Korean "
        "for Korean animation staff.\n"
        "Keep imperative production notes in polite 요청형 (예: '...하세요'). "
        "Keep dialogue natural and faithful to the tone of the source.\n"
        "Do NOT translate asset IDs, scene/panel codes, or file-name-like "
        "tokens (e.g. TGNO_PizzaBox_CL_V01, 5LBW03_07_01) — copy them "
        "unchanged.\n"
        "Input is a JSON array of strings; return ONLY a JSON array of the "
        "same length with the Korean translations in the same order.\n"
        "Return ONLY the JSON array. No prose, no markdown fences.\n"
        "Use this glossary:\n"
        + glossary_block()
        + "\n\nInput:\n" + numbered
    )


async def _resilient(provider: TranslationProvider, texts: list[str],
                     cause: str | None = None) -> list[str]:
    """개수 불일치/오류에 견디는 배치 — 반으로 쪼개 재시도, 1줄 실패는 원문 유지."""
    if not texts:
        return []
    try:
        result = await provider.translate_batch(texts)
        if len(result) == len(texts):
            return result
        cause = f"반환 개수 불일치({len(result)} != {len(texts)})"
    except TranslationError as exc:
        cause = str(exc)
    if len(texts) == 1:
        logger.warning("pdf-translate: 1블록 번역 실패(%s) — 원문 유지: %r",
                       cause or "원인 미상", texts[0][:60])
        return list(texts)
    mid = len(texts) // 2
    left = await _resilient(provider, texts[:mid], cause)
    right = await _resilient(provider, texts[mid:], cause)
    return left + right


async def translate_texts(
    texts: list[str],
    provider: TranslationProvider,
    *,
    chunk_size: int = 50,
    progress_cb: Callable[[float], Awaitable[None]] | None = None,
) -> list[str]:
    out: list[str] = []
    for i in range(0, len(texts), chunk_size):
        chunk = texts[i:i + chunk_size]
        translated = await _resilient(provider, chunk)
        out.extend(apply_ko_corrections(t.strip()) for t in translated)
        logger.info("pdf-translate: %d/%d blocks", len(out), len(texts))
        if progress_cb is not None:
            await progress_cb(len(out) / len(texts))
    return out
