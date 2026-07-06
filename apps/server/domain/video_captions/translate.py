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
        numbered = json.dumps(texts, ensure_ascii=False)
        prompt = (
            "Translate each English subtitle line into concise Korean subtitle text.\n"
            "Input is a JSON array of strings; return ONLY a JSON array of the same "
            "length with the Korean translations in the same order.\n"
            "Use this glossary:\n"
            + glossary_block()
            + "\n\nInput:\n" + numbered
        )
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
        translated = await provider.translate_batch([s.text for s in chunk])
        if len(translated) != len(chunk):
            raise TranslationError("provider returned wrong count")
        for seg, ko in zip(chunk, translated):
            out.append(replace(seg, text=apply_ko_corrections(ko.strip())))
        logger.info("translate: %d/%d segments", len(out), len(segments))
        if progress_cb is not None:
            await progress_cb(len(out) / len(segments))
    return out
