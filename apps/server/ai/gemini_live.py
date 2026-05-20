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
import math
import os
import struct
from typing import Any, Literal, TypedDict

from apps.server.ai.providers import TranslatedUtterance

MODEL_ENV = "GEMINI_LIVE_MODEL"
INPUT_TOKEN_RATE_ENV = "GEMINI_INPUT_USD_PER_1M_TOKENS"
OUTPUT_TOKEN_RATE_ENV = "GEMINI_OUTPUT_USD_PER_1M_TOKENS"
RESPONSE_MODALITY_ENV = "GEMINI_RESPONSE_MODALITY"
VAD_PREFIX_PADDING_MS_ENV = "GEMINI_VAD_PREFIX_PADDING_MS"
VAD_SILENCE_DURATION_MS_ENV = "GEMINI_VAD_SILENCE_DURATION_MS"
EXPLICIT_VAD_ENABLED_ENV = "GEMINI_EXPLICIT_VAD_ENABLED"
EXPLICIT_VAD_RMS_DBFS_THRESHOLD_ENV = "GEMINI_EXPLICIT_VAD_RMS_DBFS_THRESHOLD"
EXPLICIT_VAD_END_SILENCE_MS_ENV = "GEMINI_EXPLICIT_VAD_END_SILENCE_MS"
FAST_PARTIAL_TRANSLATION_ENABLED_ENV = "GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED"
PARTIAL_TRANSLATION_MODEL_ENV = "GEMINI_PARTIAL_TRANSLATION_MODEL"
PARTIAL_MIN_CHARS_ENV = "GEMINI_PARTIAL_MIN_CHARS"
PARTIAL_MIN_WORDS_ENV = "GEMINI_PARTIAL_MIN_WORDS"
PARTIAL_MIN_DELTA_CHARS_ENV = "GEMINI_PARTIAL_MIN_DELTA_CHARS"
PARTIAL_FORCE_CHARS_ENV = "GEMINI_PARTIAL_FORCE_CHARS"
DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_PARTIAL_TRANSLATION_MODEL = "gemini-2.5-flash-lite"
INPUT_SAMPLE_RATE = 16000
INPUT_CHUNK_MS = 20
DEFAULT_VAD_PREFIX_PADDING_MS = 120
DEFAULT_VAD_SILENCE_DURATION_MS = 350
DEFAULT_EXPLICIT_VAD_RMS_DBFS_THRESHOLD = -50.0
DEFAULT_EXPLICIT_VAD_END_SILENCE_MS = 320
DEFAULT_PARTIAL_MIN_CHARS = 16
DEFAULT_PARTIAL_MIN_WORDS = 3
DEFAULT_PARTIAL_MIN_DELTA_CHARS = 10
DEFAULT_PARTIAL_FORCE_CHARS = 90
logger = logging.getLogger(__name__)
SYSTEM_PROMPT = """You are a real-time meeting assistant for a Korean animation/VFX studio.

Translate English speech into concise Korean subtitle-style text.
Preserve only common studio pipeline terms in English (layout, retake, delivery,
render, comp, rig, shot, asset). Translate general business and engineering
phrases into Korean.
Keep subtitles to at most two short lines. If the speaker mixes Korean and
English, keep the Korean as-is and translate only the English parts.
Do not invent company names, benchmark claims, or context not present in the
speaker's words.
Return only the Korean subtitle text. Do not repeat the source English.
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


@dataclass
class ManualVadState:
    threshold_dbfs: float
    end_silence_ms: int
    chunk_ms: int = INPUT_CHUNK_MS
    speech_active: bool = False
    silent_ms: int = 0

    def observe(self, chunk: bytes) -> Literal["start", "end"] | None:
        is_speech = _pcm16le_rms_dbfs(chunk) >= self.threshold_dbfs
        if is_speech:
            self.silent_ms = 0
            if not self.speech_active:
                self.speech_active = True
                return "start"
            return None

        if not self.speech_active:
            return None
        self.silent_ms += self.chunk_ms
        if self.silent_ms < self.end_silence_ms:
            return None
        self.speech_active = False
        self.silent_ms = 0
        return "end"

    def finish(self) -> Literal["end"] | None:
        if not self.speech_active:
            return None
        self.speech_active = False
        self.silent_ms = 0
        return "end"


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


def _pcm16le_rms_dbfs(chunk: bytes) -> float:
    if not chunk:
        return -120.0
    sample_count = len(chunk) // 2
    if sample_count == 0:
        return -120.0
    samples = struct.unpack(f"<{sample_count}h", chunk[: sample_count * 2])
    mean_square = sum(sample * sample for sample in samples) / sample_count
    if mean_square <= 0:
        return -120.0
    return 20 * math.log10(math.sqrt(mean_square) / 32768.0)


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
        config = _build_live_config(types)

        async with client.aio.live.connect(model=self._model, config=config) as session:
            async for utterance in _stream_session(session, types, audio, client):
                yield utterance


def _build_live_config(types: Any) -> Any:
    response_modality = os.environ.get(RESPONSE_MODALITY_ENV, "AUDIO").upper()
    modality = types.Modality.TEXT if response_modality == "TEXT" else types.Modality.AUDIO
    explicit_vad_enabled = _bool_env(EXPLICIT_VAD_ENABLED_ENV, False)
    config_kwargs: dict[str, Any] = {
        "response_modalities": [modality],
        "system_instruction": types.Content(parts=[types.Part(text=SYSTEM_PROMPT)]),
        "input_audio_transcription": types.AudioTranscriptionConfig(),
        "output_audio_transcription": (
            None if modality == types.Modality.TEXT else types.AudioTranscriptionConfig()
        ),
        "realtime_input_config": types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=explicit_vad_enabled,
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=_int_env(
                    VAD_PREFIX_PADDING_MS_ENV,
                    DEFAULT_VAD_PREFIX_PADDING_MS,
                ),
                silence_duration_ms=_int_env(
                    VAD_SILENCE_DURATION_MS_ENV,
                    DEFAULT_VAD_SILENCE_DURATION_MS,
                ),
            ),
            activity_handling=types.ActivityHandling.NO_INTERRUPTION,
            turn_coverage=types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
        ),
    }
    if explicit_vad_enabled:
        config_kwargs["explicit_vad_signal"] = True
    return types.LiveConnectConfig(**config_kwargs)


def _int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Invalid integer environment value", extra={"env_name": name})
        return default
    return max(0, value)


def _float_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        logger.warning("Invalid float environment value", extra={"env_name": name})
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.lower() not in {"0", "false", "no", "off"}


def _manual_vad_from_env() -> ManualVadState | None:
    if not _bool_env(EXPLICIT_VAD_ENABLED_ENV, False):
        return None
    return ManualVadState(
        threshold_dbfs=_float_env(
            EXPLICIT_VAD_RMS_DBFS_THRESHOLD_ENV,
            DEFAULT_EXPLICIT_VAD_RMS_DBFS_THRESHOLD,
        ),
        end_silence_ms=_int_env(
            EXPLICIT_VAD_END_SILENCE_MS_ENV,
            DEFAULT_EXPLICIT_VAD_END_SILENCE_MS,
        ),
    )


async def _stream_session(
    session: Any,
    types: Any,
    audio: AsyncIterator[bytes],
    text_client: Any | None = None,
) -> AsyncIterator[TranslatedUtterance]:
    seq = 0
    current_seq = 0
    text_en = ""
    text_ko = ""
    last_partial_text_en = ""
    started_at = datetime.now(timezone.utc)

    async def send_audio() -> None:
        manual_vad = _manual_vad_from_env()
        async for chunk in audio:
            signal = manual_vad.observe(chunk) if manual_vad is not None else None
            if signal == "start":
                await session.send_realtime_input(activity_start=types.ActivityStart())
            await session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                )
            )
            if signal == "end":
                await session.send_realtime_input(activity_end=types.ActivityEnd())
        if manual_vad is not None and manual_vad.finish() == "end":
            await session.send_realtime_input(activity_end=types.ActivityEnd())
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
                if (
                    _fast_partial_translation_enabled()
                    and text_client is not None
                    and _should_emit_partial_translation(last_partial_text_en, text_en)
                ):
                    translated = await _translate_partial_text(text_client, text_en)
                    if _has_subtitle_text(translated):
                        last_partial_text_en = text_en
                        if current_seq == 0:
                            seq += 1
                            current_seq = seq
                        ended_at = datetime.now(timezone.utc)
                        yield TranslatedUtterance(
                            seq=current_seq,
                            text_en=text_en,
                            text_ko=translated,
                            started_at=started_at,
                            ended_at=ended_at,
                            is_final=False,
                        )
            output_emitted = False
            if _has_subtitle_text(extracted.output_text):
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
            if extracted.turn_complete and text_ko:
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
                last_partial_text_en = ""
                started_at = ended_at
    finally:
        _ = send_task.cancel()


def _fast_partial_translation_enabled() -> bool:
    return _bool_env(FAST_PARTIAL_TRANSLATION_ENABLED_ENV, True)


def _should_emit_partial_translation(last_emitted_text: str, next_text: str) -> bool:
    text = next_text.strip()
    if not text or text == last_emitted_text:
        return False

    force_chars = _int_env(PARTIAL_FORCE_CHARS_ENV, DEFAULT_PARTIAL_FORCE_CHARS)
    if len(text) >= force_chars and len(text) > len(last_emitted_text):
        return True

    min_chars = _int_env(PARTIAL_MIN_CHARS_ENV, DEFAULT_PARTIAL_MIN_CHARS)
    min_words = _int_env(PARTIAL_MIN_WORDS_ENV, DEFAULT_PARTIAL_MIN_WORDS)
    if len(text) < min_chars or len(text.split()) < min_words:
        return False

    if not last_emitted_text:
        return True

    min_delta = _int_env(PARTIAL_MIN_DELTA_CHARS_ENV, DEFAULT_PARTIAL_MIN_DELTA_CHARS)
    return len(text) - len(last_emitted_text) >= min_delta


def _has_soft_boundary(text: str) -> bool:
    stripped = text.rstrip()
    if stripped.endswith(('.', '?', '!', ',', ';', ':')):
        return True
    return len(stripped) >= _int_env(PARTIAL_FORCE_CHARS_ENV, DEFAULT_PARTIAL_FORCE_CHARS)


def _has_subtitle_text(text: str) -> bool:
    normalized = " ".join(text.split()).strip().lower()
    if not normalized:
        return False
    return normalized not in {
        "(자막 없음)",
        "자막 없음",
        "[자막 없음]",
        "(번역 없음)",
        "번역 없음",
        "(no subtitle)",
        "no subtitle",
        "(no subtitles)",
        "no subtitles",
        "(no caption)",
        "no caption",
        "(no captions)",
        "no captions",
    }


async def _translate_partial_text(text_client: Any, text: str) -> str:
    from google.genai import types

    response = await text_client.aio.models.generate_content(
        model=os.environ.get(
            PARTIAL_TRANSLATION_MODEL_ENV,
            DEFAULT_PARTIAL_TRANSLATION_MODEL,
        ),
        contents=(
            "Translate this English meeting transcript fragment into concise Korean "
            "subtitle text. Return only Korean. Preserve only common studio terms "
            "such as layout, retake, render, comp, rig, shot, asset.\n\n"
            f"English: {text}"
        ),
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=160,
        ),
    )
    translated = getattr(response, "text", "") or ""
    return translated.strip()
# === ANCHOR: GEMINI_LIVE_END ===
