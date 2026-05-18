# === ANCHOR: GEMINI_LIVE_START ===
"""Gemini Live provider for Slice 3.

The google-genai dependency is imported lazily so unit tests can exercise parsing
without requiring a live API key or network access.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any, Literal, TypedDict

from apps.server.ai.providers import TranslatedUtterance

MODEL_ENV = "GEMINI_LIVE_MODEL"
DEFAULT_MODEL = "gemini-live-2.5-flash-preview"
INPUT_SAMPLE_RATE = 16000
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


def _text_attr(value: Any) -> str:
    text = getattr(value, "text", "") if value is not None else ""
    return text or ""


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
            response_modalities=[types.Modality.TEXT],
            system_instruction=types.Content(parts=[types.Part(text=SYSTEM_PROMPT)]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
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

    import asyncio

    send_task = asyncio.create_task(send_audio())
    try:
        async for message in session.receive():
            extracted = extract_live_text(message)
            if extracted.input_text:
                text_en = extracted.input_text
            if extracted.output_text:
                text_ko += extracted.output_text
            if extracted.turn_complete and (text_en or text_ko):
                seq += 1
                ended_at = datetime.now(timezone.utc)
                yield TranslatedUtterance(
                    seq=seq,
                    text_en=text_en,
                    text_ko=text_ko,
                    started_at=started_at,
                    ended_at=ended_at,
                    is_final=True,
                )
                text_en = ""
                text_ko = ""
                started_at = ended_at
    finally:
        send_task.cancel()
# === ANCHOR: GEMINI_LIVE_END ===
