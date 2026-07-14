"""Batch EN->KO subtitle translation.

TranslationProvider is a deliberate plug point: v2 can add an on-device Apple
Translation provider without touching the pipeline. Default is Gemini Flash
batch generateContent with the shared animation-production glossary injected —
batch calls do not suffer the Live-prompt-bloat stall, so the full glossary
block is safe here.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from typing import Awaitable, Callable, Protocol

from apps.server.ai.glossary import apply_ko_corrections, glossary_block
from .srt import SubSegment

logger = logging.getLogger("yeson.video.translate")

TRANSLATE_MODEL_ENV = "YESON_VIDEO_TRANSLATE_MODEL"
DEFAULT_TRANSLATE_MODEL = "gemini-2.5-flash"


class TranslationError(RuntimeError):
    pass


class TranslationProvider(Protocol):
    async def translate_batch(self, texts: list[str]) -> list[str]: ...


def build_translation_prompt(texts: list[str]) -> str:
    """EN 자막 배열 → KO 번역 지시 프롬프트 (JSON in/out + 용어사전 주입).

    Gemini와 구독 CLI provider(claude/codex/agy/opencode) 양쪽이 공유한다.
    """
    numbered = json.dumps(texts, ensure_ascii=False)
    return (
        "Translate each English subtitle line into natural Korean subtitle text, "
        "concise enough to read on screen.\n"
        "When the English contains onomatopoeia, sound effects, or emphatic / "
        "expressive wording (e.g. boom, whoosh, splash, buzz, sparkle, thud), "
        "render it with the natural Korean 의성어·의태어 (예: 쿵, 쉬익, 첨벙, 윙, "
        "반짝반짝, 쿵쾅) instead of a flat literal translation. Preserve the same "
        "vividness and tone as the source.\n"
        "Input is a JSON array of strings; return ONLY a JSON array of the same "
        "length with the Korean translations in the same order.\n"
        "Return ONLY the JSON array. No prose, no markdown fences.\n"
        "Use this glossary:\n"
        + glossary_block()
        + "\n\nInput:\n" + numbered
    )


def apply_ko_guard(texts: list[str], out: list[str], *, marker: str) -> list[str]:
    """번역 결과를 환각 가드(guard_mlx_ko)로 검증 — 불합격 줄은 원문(EN)을 유지한다
    (검수 단계에서 눈에 띄게). MLX·Ollama 로컬 배치 번역이 공유한다. ``marker``는
    로깅용 provider 태그(예: ``mlx_video_guard_reject``).
    """
    from apps.server.ai.mlx_live_translate import guard_mlx_ko

    guarded: list[str] = []
    for src, ko in zip(texts, out):
        reason = guard_mlx_ko(src, ko)
        if reason is not None:
            logger.info("%s reason=%s src=%r", marker, reason, src[:60])
            guarded.append(src)
        else:
            guarded.append(ko)
    return guarded


class GeminiFlashTranslator:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._model = model or os.environ.get(TRANSLATE_MODEL_ENV, DEFAULT_TRANSLATE_MODEL)

    async def _generate(self, prompt: str) -> str:  # test seam
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        response = await client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        return response.text or ""

    async def translate_batch(self, texts: list[str]) -> list[str]:
        prompt = build_translation_prompt(texts)
        raw = await self._generate(prompt)
        try:
            out = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TranslationError(f"Gemini returned non-JSON: {raw[:200]!r}") from exc
        if not isinstance(out, list) or len(out) != len(texts):
            raise TranslationError(
                f"translation count mismatch: sent {len(texts)}, got "
                f"{len(out) if isinstance(out, list) else type(out).__name__}"
            )
        return [str(t) for t in out]


async def _translate_resilient(
    provider: TranslationProvider, texts: list[str],
) -> list[str]:
    """개수 불일치/오류에 견디는 배치 번역.

    LLM이 두 줄을 합치거나 한 줄을 누락하면 반환 개수가 어긋나 자막이 밀린다.
    그럴 땐 청크를 반으로 쪼개 재번역(입력이 달라져 정상 개수로 수렴)하고,
    1줄까지 쪼개도 실패하면 그 줄만 원문을 유지해 작업 전체 중단을 막는다.
    """
    if not texts:
        return []
    try:
        result = await provider.translate_batch(texts)
        if len(result) == len(texts):
            return result
    except TranslationError:
        pass
    if len(texts) == 1:
        logger.warning("translate: 1줄 번역 실패 — 원문 유지: %r", texts[0][:60])
        return list(texts)
    mid = len(texts) // 2
    left = await _translate_resilient(provider, texts[:mid])
    right = await _translate_resilient(provider, texts[mid:])
    return left + right


async def translate_segments(
    segments: list[SubSegment],
    provider: TranslationProvider,
    *,
    chunk_size: int = 50,
    progress_cb: Callable[[float], Awaitable[None]] | None = None,
) -> list[SubSegment]:
    out: list[SubSegment] = []
    for i in range(0, len(segments), chunk_size):
        chunk = segments[i:i + chunk_size]
        translated = await _translate_resilient(provider, [s.text for s in chunk])
        for seg, ko in zip(chunk, translated):
            out.append(replace(seg, text=apply_ko_corrections(ko.strip())))
        logger.info("translate: %d/%d segments", len(out), len(segments))
        if progress_cb is not None:
            await progress_cb(len(out) / len(segments))
    return out
