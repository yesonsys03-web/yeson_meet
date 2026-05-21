# === ANCHOR: GEMINI_LIVE_START ===
"""Gemini Live provider for Slice 3.

The google-genai dependency is imported lazily so unit tests can exercise parsing
without requiring a live API key or network access.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import os
import struct
import time
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
EXPLICIT_VAD_MAX_SPEECH_MS_ENV = "GEMINI_EXPLICIT_VAD_MAX_SPEECH_MS"
GENAI_USE_ENTERPRISE_ENV = "GOOGLE_GENAI_USE_ENTERPRISE"
SEGMENT_MAX_SPEECH_MS_ENV = "GEMINI_SEGMENT_MAX_SPEECH_MS"
SEGMENT_HARD_MAX_SPEECH_MS_ENV = "GEMINI_SEGMENT_HARD_MAX_SPEECH_MS"
SEGMENT_CYCLE_SILENCE_MS_ENV = "GEMINI_SEGMENT_CYCLE_SILENCE_MS"
FAST_PARTIAL_TRANSLATION_ENABLED_ENV = "GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED"
PARTIAL_TRANSLATION_MODEL_ENV = "GEMINI_PARTIAL_TRANSLATION_MODEL"
PARTIAL_MIN_CHARS_ENV = "GEMINI_PARTIAL_MIN_CHARS"
PARTIAL_MIN_WORDS_ENV = "GEMINI_PARTIAL_MIN_WORDS"
PARTIAL_MIN_DELTA_CHARS_ENV = "GEMINI_PARTIAL_MIN_DELTA_CHARS"
PARTIAL_FORCE_CHARS_ENV = "GEMINI_PARTIAL_FORCE_CHARS"
PARTIAL_TRANSLATION_TIMEOUT_MS_ENV = "GEMINI_PARTIAL_TRANSLATION_TIMEOUT_MS"
RECEIVE_POLL_TIMEOUT_MS_ENV = "GEMINI_RECEIVE_POLL_TIMEOUT_MS"
RECEIVE_DRAIN_TIMEOUT_MS_ENV = "GEMINI_RECEIVE_DRAIN_TIMEOUT_MS"
SEGMENT_SPEECH_RMS_DBFS_THRESHOLD_ENV = "GEMINI_SEGMENT_SPEECH_RMS_DBFS_THRESHOLD"
DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_PARTIAL_TRANSLATION_MODEL = "gemini-2.5-flash-lite"
INPUT_SAMPLE_RATE = 16000
INPUT_CHUNK_MS = 20
DEFAULT_VAD_PREFIX_PADDING_MS = 120
DEFAULT_VAD_SILENCE_DURATION_MS = 350
DEFAULT_EXPLICIT_VAD_RMS_DBFS_THRESHOLD = -50.0
DEFAULT_EXPLICIT_VAD_END_SILENCE_MS = 320
DEFAULT_EXPLICIT_VAD_MAX_SPEECH_MS = 2500
DEFAULT_SEGMENT_MAX_SPEECH_MS = 120000
DEFAULT_SEGMENT_HARD_MAX_SPEECH_MS = 300000
DEFAULT_SEGMENT_CYCLE_SILENCE_MS = 400
DEFAULT_PARTIAL_MIN_CHARS = 12
DEFAULT_PARTIAL_MIN_WORDS = 2
DEFAULT_PARTIAL_MIN_DELTA_CHARS = 6
DEFAULT_PARTIAL_FORCE_CHARS = 90
DEFAULT_PARTIAL_TRANSLATION_TIMEOUT_MS = 2000
DEFAULT_RECEIVE_POLL_TIMEOUT_MS = 200
DEFAULT_RECEIVE_DRAIN_TIMEOUT_MS = 12000
DEFAULT_SEGMENT_SPEECH_RMS_DBFS_THRESHOLD = -60.0
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
class AudioSegmentState:
    exhausted: bool = False
    speech_observed: bool = False


@dataclass
class ManualVadState:
    threshold_dbfs: float
    end_silence_ms: int
    max_speech_ms: int
    chunk_ms: int = INPUT_CHUNK_MS
    speech_active: bool = False
    silent_ms: int = 0
    speech_ms: int = 0

    def observe(self, chunk: bytes) -> Literal["start", "end", "restart"] | None:
        is_speech = _pcm16le_rms_dbfs(chunk) >= self.threshold_dbfs
        if is_speech:
            self.silent_ms = 0
            if not self.speech_active:
                self.speech_active = True
                self.speech_ms = self.chunk_ms
                return "start"
            self.speech_ms += self.chunk_ms
            if self.max_speech_ms > 0 and self.speech_ms >= self.max_speech_ms:
                self.speech_ms = self.chunk_ms
                return "restart"
            return None

        if not self.speech_active:
            return None
        self.silent_ms += self.chunk_ms
        if self.silent_ms < self.end_silence_ms:
            return None
        self.speech_active = False
        self.silent_ms = 0
        self.speech_ms = 0
        return "end"

    def finish(self) -> Literal["end"] | None:
        if not self.speech_active:
            return None
        self.speech_active = False
        self.silent_ms = 0
        self.speech_ms = 0
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


def _elapsed_monotonic_ms(start: float, end: float | None = None) -> int:
    return max(0, round(((end if end is not None else time.monotonic()) - start) * 1000))


def _segment_max_chunks() -> int:
    segment_ms = _int_env(SEGMENT_MAX_SPEECH_MS_ENV, DEFAULT_SEGMENT_MAX_SPEECH_MS)
    if segment_ms <= 0:
        return 0
    return max(1, math.ceil(segment_ms / INPUT_CHUNK_MS))


def _segment_hard_max_chunks() -> int:
    hard_ms = _int_env(SEGMENT_HARD_MAX_SPEECH_MS_ENV, DEFAULT_SEGMENT_HARD_MAX_SPEECH_MS)
    if hard_ms <= 0:
        return 0
    return max(1, math.ceil(hard_ms / INPUT_CHUNK_MS))


def _segment_cycle_silence_chunks() -> int:
    silence_ms = _int_env(SEGMENT_CYCLE_SILENCE_MS_ENV, DEFAULT_SEGMENT_CYCLE_SILENCE_MS)
    if silence_ms <= 0:
        return 0
    return max(1, math.ceil(silence_ms / INPUT_CHUNK_MS))


def _receive_poll_timeout_seconds() -> float:
    return max(1, _int_env(RECEIVE_POLL_TIMEOUT_MS_ENV, DEFAULT_RECEIVE_POLL_TIMEOUT_MS)) / 1000


def _partial_translation_timeout_seconds() -> float:
    return max(1, _int_env(PARTIAL_TRANSLATION_TIMEOUT_MS_ENV, DEFAULT_PARTIAL_TRANSLATION_TIMEOUT_MS)) / 1000


def _receive_drain_timeout_seconds() -> float:
    return max(1, _int_env(RECEIVE_DRAIN_TIMEOUT_MS_ENV, DEFAULT_RECEIVE_DRAIN_TIMEOUT_MS)) / 1000


def _segment_speech_threshold_dbfs() -> float:
    return _float_env(
        SEGMENT_SPEECH_RMS_DBFS_THRESHOLD_ENV,
        DEFAULT_SEGMENT_SPEECH_RMS_DBFS_THRESHOLD,
    )


async def _bounded_audio_segment(
    audio: AsyncIterator[bytes],
    state: AudioSegmentState,
    max_chunks: int,
    hard_max_chunks: int = 0,
    silence_chunk_run: int = 0,
) -> AsyncIterator[bytes]:
    """Yield audio chunks until the segment should end.

    - `max_chunks` (soft target): cycle when reached if `silence_chunk_run<=0`,
      else wait for a silent run of `silence_chunk_run` chunks before cycling.
      Silence-aware behavior keeps the cycle out of the middle of a long
      utterance, which otherwise causes a large backlog flush on reconnect.
    - `hard_max_chunks` (backstop): absolute cap regardless of speech state.
      0 disables the cap.
    """
    sent = 0
    speech_threshold = _segment_speech_threshold_dbfs()
    silence_run = 0
    while True:
        if hard_max_chunks > 0 and sent >= hard_max_chunks:
            return
        if max_chunks > 0 and sent >= max_chunks:
            if silence_chunk_run <= 0 or silence_run >= silence_chunk_run:
                return
        try:
            chunk = await audio.__anext__()
        except StopAsyncIteration:
            state.exhausted = True
            return
        sent += 1
        chunk_dbfs = _pcm16le_rms_dbfs(chunk)
        if chunk_dbfs >= speech_threshold:
            silence_run = 0
            if not state.speech_observed:
                state.speech_observed = True
        else:
            silence_run += 1
        yield chunk


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

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        trace_extra: Mapping[str, object] | None = None,
    ) -> None:
        self._api_key: str | None = api_key or os.environ.get("GEMINI_API_KEY")
        self._model: str = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        self._trace_extra = dict(trace_extra or {})

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
        audio_source = audio.__aiter__()
        segment_index = 0
        segment_max_chunks = _segment_max_chunks()
        segment_hard_max_chunks = _segment_hard_max_chunks()
        segment_silence_chunks = _segment_cycle_silence_chunks()

        while True:
            segment_index += 1
            segment_state = AudioSegmentState()
            segment_audio = _bounded_audio_segment(
                audio_source,
                segment_state,
                segment_max_chunks,
                hard_max_chunks=segment_hard_max_chunks,
                silence_chunk_run=segment_silence_chunks,
            )
            trace_extra = {**self._trace_extra, "gemini_segment": segment_index}
            connect_started_at = time.monotonic()
            logger.info(
                "Gemini Live connect starting",
                extra={**trace_extra, "gemini_model": self._model},
            )
            async with client.aio.live.connect(model=self._model, config=config) as session:
                connected_at = time.monotonic()
                logger.info(
                    "Gemini Live connected",
                    extra={
                        **trace_extra,
                        "gemini_model": self._model,
                        "gemini_connect_latency_ms": _elapsed_monotonic_ms(
                            connect_started_at,
                            connected_at,
                        ),
                    },
                )
                async for utterance in _stream_session(
                    session,
                    types,
                    segment_audio,
                    client,
                    trace_extra=trace_extra,
                    connected_at=connected_at,
                    provider_segment=segment_index,
                ):
                    yield utterance
            if segment_state.exhausted:
                return


def _build_live_config(types: Any) -> Any:
    response_modality = os.environ.get(RESPONSE_MODALITY_ENV, "AUDIO").upper()
    modality = types.Modality.TEXT if response_modality == "TEXT" else types.Modality.AUDIO
    explicit_vad_enabled = _explicit_vad_supported()
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


def _explicit_vad_supported() -> bool:
    requested = _bool_env(EXPLICIT_VAD_ENABLED_ENV, False)
    if not requested:
        return False
    if _bool_env(GENAI_USE_ENTERPRISE_ENV, False):
        return True
    logger.warning(
        "Gemini explicit VAD disabled: Developer API does not support explicit_vad_signal",
        extra={"env_name": EXPLICIT_VAD_ENABLED_ENV},
    )
    return False


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
    if not _explicit_vad_supported():
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
        max_speech_ms=_int_env(
            EXPLICIT_VAD_MAX_SPEECH_MS_ENV,
            DEFAULT_EXPLICIT_VAD_MAX_SPEECH_MS,
        ),
    )


async def _stream_session(
    session: Any,
    types: Any,
    audio: AsyncIterator[bytes],
    text_client: Any | None = None,
    trace_extra: Mapping[str, object] | None = None,
    connected_at: float | None = None,
    provider_segment: int = 1,
) -> AsyncIterator[TranslatedUtterance]:
    seq = 0
    current_seq = 0
    text_en = ""
    text_ko = ""
    last_partial_text_en = ""
    partial_text_ko = ""
    started_at = datetime.now(timezone.utc)
    trace = dict(trace_extra or {})
    first_audio_sent = False
    first_input_seen = False
    first_output_seen = False
    first_partial_translation = False
    first_utterance_yielded = False
    speech_observed = False

    async def send_audio() -> None:
        nonlocal first_audio_sent, speech_observed
        manual_vad = _manual_vad_from_env()
        speech_threshold = _segment_speech_threshold_dbfs()
        async for chunk in audio:
            if not speech_observed and _pcm16le_rms_dbfs(chunk) >= speech_threshold:
                speech_observed = True
            signal = manual_vad.observe(chunk) if manual_vad is not None else None
            if signal == "start":
                await session.send_realtime_input(activity_start=types.ActivityStart())
            elif signal == "restart":
                await session.send_realtime_input(activity_end=types.ActivityEnd())
                await session.send_realtime_input(activity_start=types.ActivityStart())
            await session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                )
            )
            if not first_audio_sent:
                first_audio_sent = True
                extra = {**trace, "gemini_first_audio_bytes": len(chunk)}
                if connected_at is not None:
                    extra["gemini_connect_to_first_audio_send_ms"] = _elapsed_monotonic_ms(
                        connected_at
                    )
                logger.info("Gemini Live first audio sent", extra=extra)
            if signal == "end":
                await session.send_realtime_input(activity_end=types.ActivityEnd())
        if manual_vad is not None and manual_vad.finish() == "end":
            await session.send_realtime_input(activity_end=types.ActivityEnd())
        await session.send_realtime_input(audio_stream_end=True)

    import asyncio

    receive_timeout = _receive_poll_timeout_seconds()
    receive_drain_timeout = _receive_drain_timeout_seconds()
    partial_translation_timeout = _partial_translation_timeout_seconds()
    send_task = asyncio.create_task(send_audio())
    try:
        while True:
            receive_iter = session.receive().__aiter__()
            receive_next = asyncio.create_task(receive_iter.__anext__())
            drain_started_at: float | None = None
            while True:
                done, _pending = await asyncio.wait({receive_next}, timeout=receive_timeout)
                if not done:
                    if send_task.done():
                        if (
                            not speech_observed
                            and not first_input_seen
                            and not first_output_seen
                            and not first_utterance_yielded
                        ):
                            receive_next.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await receive_next
                            break
                        if drain_started_at is None:
                            drain_started_at = time.monotonic()
                        if time.monotonic() - drain_started_at >= receive_drain_timeout:
                            receive_next.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await receive_next
                            break
                    continue

                drain_started_at = None
                try:
                    message = receive_next.result()
                except StopAsyncIteration:
                    break
                receive_next = asyncio.create_task(receive_iter.__anext__())
                usage = extract_usage_metadata(message)
                if usage is not None:
                    _log_usage_metadata(usage)
                extracted = extract_live_text(message)
                if extracted.input_text:
                    if not first_input_seen:
                        first_input_seen = True
                        logger.info(
                            "Gemini Live first input transcription",
                            extra={
                                **trace,
                                "gemini_first_input_chars": len(extracted.input_text),
                                "gemini_connect_to_first_input_ms": _elapsed_monotonic_ms(
                                    connected_at
                                ) if connected_at is not None else None,
                            },
                        )
                    text_en = extracted.input_text
                    if (
                        _fast_partial_translation_enabled()
                        and text_client is not None
                        and _should_emit_partial_translation(last_partial_text_en, text_en)
                    ):
                        partial_started_at = time.monotonic()
                        try:
                            translated = await asyncio.wait_for(
                                _translate_partial_text(text_client, text_en),
                                timeout=partial_translation_timeout,
                            )
                        except TimeoutError:
                            logger.warning(
                                "Gemini partial translation timed out",
                                extra={
                                    **trace,
                                    "gemini_partial_translation_timeout_ms": round(
                                        partial_translation_timeout * 1000
                                    ),
                                },
                            )
                            translated = ""
                        except Exception as error:
                            logger.warning(
                                "Gemini partial translation failed",
                                extra={**trace, "error_type": type(error).__name__},
                            )
                            translated = ""
                        if _has_subtitle_text(translated):
                            if not first_partial_translation:
                                first_partial_translation = True
                                logger.info(
                                    "Gemini Live first partial translation",
                                    extra={
                                        **trace,
                                        "gemini_partial_translation_latency_ms": _elapsed_monotonic_ms(
                                            partial_started_at
                                        ),
                                        "gemini_first_partial_chars": len(translated),
                                    },
                                )
                            last_partial_text_en = text_en
                            partial_text_ko = translated
                            if current_seq == 0:
                                seq += 1
                                current_seq = seq
                            ended_at = datetime.now(timezone.utc)
                            if not first_utterance_yielded:
                                first_utterance_yielded = True
                                logger.info(
                                    "Gemini Live first subtitle yielded",
                                    extra={
                                        **trace,
                                        "seq": current_seq,
                                        "is_final": False,
                                        "gemini_connect_to_first_subtitle_ms": _elapsed_monotonic_ms(
                                            connected_at
                                        ) if connected_at is not None else None,
                                    },
                                )
                            yield TranslatedUtterance(
                                seq=current_seq,
                                text_en=text_en,
                                text_ko=translated,
                                started_at=started_at,
                                ended_at=ended_at,
                                is_final=False,
                                provider_segment=provider_segment,
                            )
                output_emitted = False
                if _has_subtitle_text(extracted.output_text):
                    if not first_output_seen:
                        first_output_seen = True
                        logger.info(
                            "Gemini Live first output transcription",
                            extra={
                                **trace,
                                "gemini_first_output_chars": len(extracted.output_text),
                                "gemini_connect_to_first_output_ms": _elapsed_monotonic_ms(
                                    connected_at
                                ) if connected_at is not None else None,
                            },
                        )
                    text_ko += extracted.output_text
                    if current_seq == 0:
                        seq += 1
                        current_seq = seq
                    ended_at = datetime.now(timezone.utc)
                    if not first_utterance_yielded:
                        first_utterance_yielded = True
                        logger.info(
                            "Gemini Live first subtitle yielded",
                            extra={
                                **trace,
                                "seq": current_seq,
                                "is_final": extracted.turn_complete,
                                "gemini_connect_to_first_subtitle_ms": _elapsed_monotonic_ms(
                                    connected_at
                                ) if connected_at is not None else None,
                            },
                        )
                    yield TranslatedUtterance(
                        seq=current_seq,
                        text_en=text_en,
                        text_ko=text_ko,
                        started_at=started_at,
                        ended_at=ended_at,
                        is_final=extracted.turn_complete,
                        provider_segment=provider_segment,
                    )
                    output_emitted = True
                final_text_ko = text_ko or partial_text_ko
                if extracted.turn_complete and final_text_ko:
                    if current_seq == 0:
                        seq += 1
                        current_seq = seq
                    ended_at = datetime.now(timezone.utc)
                    if not output_emitted:
                        if not first_utterance_yielded:
                            first_utterance_yielded = True
                            logger.info(
                                "Gemini Live first subtitle yielded",
                                extra={
                                    **trace,
                                    "seq": current_seq,
                                    "is_final": True,
                                    "gemini_connect_to_first_subtitle_ms": _elapsed_monotonic_ms(
                                        connected_at
                                    ) if connected_at is not None else None,
                                },
                            )
                        yield TranslatedUtterance(
                            seq=current_seq,
                            text_en=text_en,
                            text_ko=final_text_ko,
                            started_at=started_at,
                            ended_at=ended_at,
                            is_final=True,
                            provider_segment=provider_segment,
                        )
                    current_seq = 0
                    text_en = ""
                    text_ko = ""
                    last_partial_text_en = ""
                    partial_text_ko = ""
                    started_at = ended_at
            if not receive_next.done():
                receive_next.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_next
            if send_task.done():
                break
            await asyncio.sleep(0)
            if send_task.done():
                break
    finally:
        if not send_task.done():
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
