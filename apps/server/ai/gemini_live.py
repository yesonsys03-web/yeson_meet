# === ANCHOR: GEMINI_LIVE_START ===
"""Gemini Live provider for Slice 3.

The google-genai dependency is imported lazily so unit tests can exercise parsing
without requiring a live API key or network access.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from typing import Any, Literal, TypedDict

from apps.server.ai.providers import TranslatedUtterance

MODEL_ENV = "GEMINI_LIVE_MODEL"
INPUT_TOKEN_RATE_ENV = "GEMINI_INPUT_USD_PER_1M_TOKENS"
OUTPUT_TOKEN_RATE_ENV = "GEMINI_OUTPUT_USD_PER_1M_TOKENS"
DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
INPUT_SAMPLE_RATE = 16000
logger = logging.getLogger(__name__)
SYSTEM_PROMPT = """You are a real-time meeting assistant for a Korean animation/VFX studio.

Translate English speech into concise Korean subtitle-style text.
Preserve technical keywords in English (layout, retake, delivery, render, etc.).
Keep subtitles to at most two short lines. If the speaker mixes Korean and
English, keep the Korean as-is and translate only the English parts.
"""


class GeminiConfigHealth(TypedDict):
    configured: bool
    status: Literal["configured", "missing_api_key"]
    model: str
    input_sample_rate: int


@dataclass(frozen=True)
class LiveText:
    input_text: str
    output_text: str
    turn_complete: bool


@dataclass(frozen=True)
class LiveUsage:
    prompt_token_count: int | None
    candidates_token_count: int | None
    total_token_count: int | None


def gemini_config_health() -> GeminiConfigHealth:
    """Return non-secret Gemini Live configuration health."""
    configured = bool(os.environ.get("GEMINI_API_KEY"))
    return {
        "configured": configured,
        "status": "configured" if configured else "missing_api_key",
        "model": os.environ.get(MODEL_ENV, DEFAULT_MODEL),
        "input_sample_rate": INPUT_SAMPLE_RATE,
    }


def extract_live_text(message: Any) -> LiveText:
    """Extract transcription/translation text from a google-genai Live message."""
    server_content = getattr(message, "server_content", None)
    if server_content is None:
        return LiveText("", "", False)

    input_text = _text_attr(getattr(server_content, "input_transcription", None))
    output_text = _text_attr(getattr(server_content, "output_transcription", None))

    model_turn = getattr(server_content, "model_turn", None)
    parts = getattr(model_turn, "parts", None) or []
    model_text = "".join(
        text for text in (_text_attr(part) for part in parts) if text
    )
    if model_text:
        output_text = model_text

    return LiveText(
        input_text=input_text,
        output_text=output_text,
        turn_complete=bool(getattr(server_content, "turn_complete", False)),
    )


def extract_usage_metadata(message: Any) -> LiveUsage | None:
    """Extract non-secret Gemini usage metadata when the SDK provides it."""
    usage = getattr(message, "usage_metadata", None) or getattr(
        message, "usageMetadata", None
    )
    if usage is None:
        return None

    prompt_tokens = _int_attr(usage, "prompt_token_count", "promptTokenCount")
    candidate_tokens = _int_attr(
        usage, "candidates_token_count", "candidatesTokenCount"
    )
    total_tokens = _int_attr(usage, "total_token_count", "totalTokenCount")
    if prompt_tokens is None and candidate_tokens is None and total_tokens is None:
        return None

    return LiveUsage(
        prompt_token_count=prompt_tokens,
        candidates_token_count=candidate_tokens,
        total_token_count=total_tokens,
    )


def _text_attr(value: Any) -> str:
    text = getattr(value, "text", "") if value is not None else ""
    return text or ""


def _int_attr(value: Any, snake_name: str, camel_name: str) -> int | None:
    raw = getattr(value, snake_name, None)
    if raw is None:
        raw = getattr(value, camel_name, None)
    if raw is None:
        return None
    return int(raw)


def _estimate_usage_cost_usd(usage: LiveUsage) -> float | None:
    input_rate = float(os.environ.get(INPUT_TOKEN_RATE_ENV, "0") or "0")
    output_rate = float(os.environ.get(OUTPUT_TOKEN_RATE_ENV, "0") or "0")
    if input_rate <= 0 and output_rate <= 0:
        return None

    prompt_cost = ((usage.prompt_token_count or 0) / 1_000_000) * input_rate
    output_cost = ((usage.candidates_token_count or 0) / 1_000_000) * output_rate
    return round(prompt_cost + output_cost, 8)


def _log_usage_metadata(usage: LiveUsage) -> None:
    logger.info(
        "Gemini usage metadata",
        extra={
            "gemini_prompt_token_count": usage.prompt_token_count,
            "gemini_candidates_token_count": usage.candidates_token_count,
            "gemini_total_token_count": usage.total_token_count,
            "gemini_estimated_cost_usd": _estimate_usage_cost_usd(usage),
        },
    )


class GeminiLiveProvider:
    """STT+translation provider backed by Google Gen AI Live API."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key: str | None = api_key or os.environ.get("GEMINI_API_KEY")
        self._model: str = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is required for GeminiLiveProvider")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(parts=[types.Part(text=SYSTEM_PROMPT)]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async with client.aio.live.connect(model=self._model, config=config) as session:
            async for utterance in _stream_session(session, types, audio):
                yield utterance


async def _stream_session(
    session: Any,
    types: Any,
    audio: AsyncIterator[bytes],
) -> AsyncIterator[TranslatedUtterance]:
    seq = 0
    current_seq = 0
    text_en = ""
    text_ko = ""
    started_at = datetime.now(timezone.utc)

    async def send_audio() -> None:
        async for chunk in audio:
            await session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                )
            )
        await session.send_realtime_input(audio_stream_end=True)

    import asyncio

    send_task = asyncio.create_task(send_audio())
    try:
        async for message in session.receive():
            usage = extract_usage_metadata(message)
            if usage is not None:
                _log_usage_metadata(usage)
            extracted = extract_live_text(message)
            if extracted.input_text:
                text_en = extracted.input_text
            output_emitted = False
            if extracted.output_text:
                text_ko += extracted.output_text
                if current_seq == 0:
                    seq += 1
                    current_seq = seq
                ended_at = datetime.now(timezone.utc)
                yield TranslatedUtterance(
                    seq=current_seq,
                    text_en=text_en,
                    text_ko=text_ko,
                    started_at=started_at,
                    ended_at=ended_at,
                    is_final=extracted.turn_complete,
                )
                output_emitted = True
            if extracted.turn_complete and (text_en or text_ko):
                if current_seq == 0:
                    seq += 1
                    current_seq = seq
                ended_at = datetime.now(timezone.utc)
                if not output_emitted:
                    yield TranslatedUtterance(
                        seq=current_seq,
                        text_en=text_en,
                        text_ko=text_ko,
                        started_at=started_at,
                        ended_at=ended_at,
                        is_final=True,
                    )
                current_seq = 0
                text_en = ""
                text_ko = ""
                started_at = ended_at
    finally:
        _ = send_task.cancel()
# === ANCHOR: GEMINI_LIVE_END ===
