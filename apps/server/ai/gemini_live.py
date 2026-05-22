# === ANCHOR: GEMINI_LIVE_START ===
"""Gemini Live provider for Slice 3.

The google-genai dependency is imported lazily so unit tests can exercise parsing
without requiring a live API key or network access.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import os
import struct
import time
from typing import Any, Literal, TypedDict

from apps.server.ai.live_session import is_permanent_provider_error
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
SEGMENT_STUCK_WATCHDOG_MS_ENV = "GEMINI_SEGMENT_STUCK_WATCHDOG_MS"
FAST_PARTIAL_TRANSLATION_ENABLED_ENV = "GEMINI_FAST_PARTIAL_TRANSLATION_ENABLED"
PARTIAL_TRANSLATION_MODEL_ENV = "GEMINI_PARTIAL_TRANSLATION_MODEL"
PARTIAL_MIN_CHARS_ENV = "GEMINI_PARTIAL_MIN_CHARS"
PARTIAL_MIN_WORDS_ENV = "GEMINI_PARTIAL_MIN_WORDS"
PARTIAL_MIN_DELTA_CHARS_ENV = "GEMINI_PARTIAL_MIN_DELTA_CHARS"
PARTIAL_FORCE_CHARS_ENV = "GEMINI_PARTIAL_FORCE_CHARS"
PARTIAL_TRANSLATION_TIMEOUT_MS_ENV = "GEMINI_PARTIAL_TRANSLATION_TIMEOUT_MS"
PARTIAL_TRANSLATION_RETRY_BACKOFF_MS_ENV = "GEMINI_PARTIAL_TRANSLATION_RETRY_BACKOFF_MS"
PARTIAL_TRANSLATION_CANCEL_STALE_MS_ENV = "GEMINI_PARTIAL_TRANSLATION_CANCEL_STALE_MS"
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
# Speech가 보내졌는데도 input/output transcription이 안 오는 segment에 대한
# 최대 대기. 시간을 초과하면 force-cycle해서 새 Gemini Live 세션으로 옮긴다.
# Gemini가 내부적으로 결과를 batching하다가 멈춘 듯한 케이스 회피용.
DEFAULT_SEGMENT_STUCK_WATCHDOG_MS = 45000
DEFAULT_PARTIAL_MIN_CHARS = 12
DEFAULT_PARTIAL_MIN_WORDS = 2
DEFAULT_PARTIAL_MIN_DELTA_CHARS = 6
DEFAULT_PARTIAL_FORCE_CHARS = 90
DEFAULT_PARTIAL_TRANSLATION_TIMEOUT_MS = 3000
DEFAULT_PARTIAL_TRANSLATION_RETRY_BACKOFF_MS = 50
DEFAULT_PARTIAL_TRANSLATION_CANCEL_STALE_MS = 600
_PARTIAL_TRANSLATION_CANCEL_MIN_DELTA_CHARS = 6
DEFAULT_RECEIVE_POLL_TIMEOUT_MS = 200
DEFAULT_RECEIVE_DRAIN_TIMEOUT_MS = 2500
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


def _segment_stuck_watchdog_ms() -> int:
    return _int_env(SEGMENT_STUCK_WATCHDOG_MS_ENV, DEFAULT_SEGMENT_STUCK_WATCHDOG_MS)


def _receive_poll_timeout_seconds() -> float:
    return max(1, _int_env(RECEIVE_POLL_TIMEOUT_MS_ENV, DEFAULT_RECEIVE_POLL_TIMEOUT_MS)) / 1000


def _partial_translation_timeout_seconds() -> float:
    return max(1, _int_env(PARTIAL_TRANSLATION_TIMEOUT_MS_ENV, DEFAULT_PARTIAL_TRANSLATION_TIMEOUT_MS)) / 1000


def _partial_translation_retry_backoff_seconds() -> float:
    return max(0, _int_env(
        PARTIAL_TRANSLATION_RETRY_BACKOFF_MS_ENV,
        DEFAULT_PARTIAL_TRANSLATION_RETRY_BACKOFF_MS,
    )) / 1000


def _partial_translation_cancel_stale_seconds() -> float:
    """In-flight partial이 이 시간 이상 흐른 상태에서 충분히 다른 새 텍스트가
    도착하면 in-flight를 cancel하고 새 partial로 빠르게 넘어간다.
    0이면 cancellation 비활성 (legacy 큐 동작 — in-flight 끝까지 기다림).
    """
    return max(0, _int_env(
        PARTIAL_TRANSLATION_CANCEL_STALE_MS_ENV,
        DEFAULT_PARTIAL_TRANSLATION_CANCEL_STALE_MS,
    )) / 1000


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
        # Cumulative segment index — `stream()`이 live_session._run의 reconnect
        # loop에 의해 재호출돼도 누적 증가시킨다. AISequenceNormalizer가
        # `provider_segment` 변화를 segment 경계로 감지하므로, 매 stream() 호출
        # 시 reset되면 disconnect→reconnect 흐름에서 seq=1이 옛 매핑에 묶여
        # 화면 자막이 덮어쓰이는 회귀가 발생한다.
        self._segment_index = 0
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
        segment_max_chunks = _segment_max_chunks()
        segment_hard_max_chunks = _segment_hard_max_chunks()
        segment_silence_chunks = _segment_cycle_silence_chunks()

        while True:
            self._segment_index += 1
            segment_index = self._segment_index
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
    # In-flight partial translation state. fire-and-forget so the receive loop
    # is not blocked by gemini-2.5-flash-lite latency (previously caused a
    # positive-feedback loop after cycle backlog where every input_text update
    # triggered a 2s blocking call). At most one partial in flight per turn.
    partial_task: asyncio.Task[str] | None = None
    partial_input_snapshot: str = ""
    partial_target_seq: int = 0
    partial_started_log_at: float = 0.0
    # Q-1: incremental partial — when the in-flight call was fired with prev_en
    # anchor, the model only outputs the Korean delta. We concatenate that with
    # the captured prev_ko at fire time to get the full Korean.
    partial_was_incremental: bool = False
    partial_prev_ko_anchor: str = ""
    # Q-2': follow-up queue. When partial_task is in flight and new input_text
    # arrives, we don't fire a second partial concurrently (cost) but remember
    # the latest text. As soon as the in-flight one completes we fire a fresh
    # partial on this text. This keeps cancellation cost at zero while still
    # always working on the freshest text.
    pending_partial_text: str = ""
    # Streaming partial translation: the driver task pushes each cumulative
    # text snapshot into this queue as the model emits chunks; the main loop
    # consumes the queue via partial_chunk_get_task and yields TranslatedUtterance
    # updates per chunk. A fresh queue is created for each new partial so old,
    # superseded chunks become unreachable once we rotate to the next partial.
    partial_stream_queue: asyncio.Queue[str] | None = None
    partial_chunk_get_task: asyncio.Task[str] | None = None

    def _publish_partial_chunk(chunk_text: str) -> TranslatedUtterance | None:
        nonlocal first_partial_translation, first_utterance_yielded
        nonlocal last_partial_text_en, partial_text_ko
        if partial_was_incremental and chunk_text:
            translated = partial_prev_ko_anchor + chunk_text
        else:
            translated = chunk_text
        if not (
            _has_subtitle_text(translated)
            and current_seq != 0
            and current_seq == partial_target_seq
        ):
            return None
        if not first_partial_translation:
            first_partial_translation = True
            logger.info(
                "Gemini Live first partial translation",
                extra={
                    **trace,
                    "gemini_partial_translation_latency_ms": _elapsed_monotonic_ms(
                        partial_started_log_at
                    ),
                    "gemini_first_partial_chars": len(translated),
                    "gemini_partial_was_incremental": partial_was_incremental,
                },
            )
        last_partial_text_en = partial_input_snapshot
        partial_text_ko = translated
        ended_at_local = datetime.now(timezone.utc)
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
        return TranslatedUtterance(
            seq=current_seq,
            text_en=partial_input_snapshot,
            text_ko=translated,
            started_at=started_at,
            ended_at=ended_at_local,
            is_final=False,
            provider_segment=provider_segment,
        )

    def _fire_streaming_partial(
        coro_iter_factory: Callable[[], AsyncIterator[Any]],
    ) -> None:
        nonlocal partial_task, partial_stream_queue
        partial_stream_queue = asyncio.Queue()
        partial_task = asyncio.create_task(
            _drive_streaming_partial(
                coro_iter_factory,
                partial_stream_queue,
                trace=trace,
                timeout_s=partial_translation_timeout,
                backoff_s=partial_translation_retry_backoff,
            )
        )

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

    receive_timeout = _receive_poll_timeout_seconds()
    receive_drain_timeout = _receive_drain_timeout_seconds()
    partial_translation_timeout = _partial_translation_timeout_seconds()
    partial_translation_retry_backoff = _partial_translation_retry_backoff_seconds()
    partial_translation_cancel_stale = _partial_translation_cancel_stale_seconds()
    stuck_watchdog_ms = _segment_stuck_watchdog_ms()
    segment_start_at = time.monotonic()
    send_task = asyncio.create_task(send_audio())
    try:
        while True:
            receive_iter = session.receive().__aiter__()
            receive_next = asyncio.create_task(receive_iter.__anext__())
            drain_started_at: float | None = None
            while True:
                wait_set: set[asyncio.Task[Any]] = {receive_next}
                if partial_task is not None:
                    wait_set.add(partial_task)
                    if (
                        partial_chunk_get_task is None
                        and partial_stream_queue is not None
                    ):
                        partial_chunk_get_task = asyncio.create_task(
                            partial_stream_queue.get()
                        )
                if partial_chunk_get_task is not None:
                    wait_set.add(partial_chunk_get_task)
                done, _pending = await asyncio.wait(
                    wait_set,
                    timeout=receive_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    if (
                        stuck_watchdog_ms > 0
                        and speech_observed
                        and not first_input_seen
                        and not first_output_seen
                        and not first_utterance_yielded
                        and (time.monotonic() - segment_start_at) * 1000
                        >= stuck_watchdog_ms
                    ):
                        # Speech가 들어갔는데 Gemini가 input/output을 안 내보냄.
                        # 내부 batching/stuck 가능성 — force-cycle해서 새 세션.
                        logger.warning(
                            "Gemini Live segment stuck — forcing cycle",
                            extra={
                                **trace,
                                "gemini_segment_stuck_watchdog_ms": stuck_watchdog_ms,
                                "gemini_segment_elapsed_ms": _elapsed_monotonic_ms(
                                    segment_start_at
                                ),
                            },
                        )
                        receive_next.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await receive_next
                        break
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
                        if (
                            first_utterance_yielded
                            and current_seq == 0
                            and partial_task is None
                        ):
                            # 이미 한 번 이상 yield했고 마지막 turn이 finalized,
                            # in-flight partial도 없음. 더 기다릴 stragger가 없으므로
                            # 즉시 cycle 종료해 다음 segment의 backlog 누적을 막는다.
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

                # Drain in-flight partial translation chunk first — if both
                # the chunk get and the partial task land in the same wait
                # cycle, we must not lose the chunk.
                if (
                    partial_chunk_get_task is not None
                    and partial_chunk_get_task in done
                ):
                    chunk_text = partial_chunk_get_task.result()
                    partial_chunk_get_task = None
                    utterance = _publish_partial_chunk(chunk_text)
                    if utterance is not None:
                        yield utterance
                if partial_task is not None and partial_task in done:
                    finished_partial = partial_task
                    partial_task = None
                    # On natural completion, drain any tail chunks the driver
                    # pushed between our last consumption and its exit. On
                    # cancellation, skip — leftover chunks reflect text the
                    # cancel-stale path has already declared stale.
                    if (
                        not finished_partial.cancelled()
                        and partial_stream_queue is not None
                    ):
                        while True:
                            try:
                                tail_chunk = partial_stream_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            utterance = _publish_partial_chunk(tail_chunk)
                            if utterance is not None:
                                yield utterance
                    if partial_chunk_get_task is not None:
                        partial_chunk_get_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await partial_chunk_get_task
                        partial_chunk_get_task = None
                    partial_stream_queue = None
                    partial_was_incremental = False
                    partial_prev_ko_anchor = ""
                    try:
                        finished_partial.result()
                    except (TimeoutError, asyncio.TimeoutError):
                        logger.warning(
                            "Gemini partial translation timed out",
                            extra={
                                **trace,
                                "gemini_partial_translation_timeout_ms": round(
                                    partial_translation_timeout * 1000
                                ),
                            },
                        )
                    except asyncio.CancelledError:
                        pass
                    except Exception as error:
                        logger.warning(
                            "Gemini partial translation failed",
                            extra={**trace, "error_type": type(error).__name__},
                        )
                    # Q-2': in-flight 중 쌓아둔 follow-up 텍스트가 있으면 즉시 발사.
                    # 항상 가장 최신 텍스트에 대한 partial이 다음으로 돌아간다.
                    if (
                        pending_partial_text
                        and pending_partial_text != last_partial_text_en
                        and _fast_partial_translation_enabled()
                        and text_client is not None
                        and _should_emit_partial_translation(
                            last_partial_text_en, pending_partial_text
                        )
                    ):
                        followup_text = pending_partial_text
                        pending_partial_text = ""
                        if current_seq == 0:
                            seq += 1
                            current_seq = seq
                        partial_input_snapshot = followup_text
                        partial_target_seq = current_seq
                        partial_started_log_at = time.monotonic()
                        followup_incremental = bool(
                            last_partial_text_en
                            and partial_text_ko
                            and followup_text.startswith(last_partial_text_en)
                            and len(followup_text) > len(last_partial_text_en)
                        )
                        if followup_incremental:
                            delta_en = followup_text[len(last_partial_text_en):]
                            partial_was_incremental = True
                            partial_prev_ko_anchor = partial_text_ko
                            followup_stream_factory = functools.partial(
                                _translate_partial_delta_stream,
                                text_client,
                                last_partial_text_en,
                                partial_text_ko,
                                delta_en,
                            )
                        else:
                            partial_was_incremental = False
                            partial_prev_ko_anchor = ""
                            followup_stream_factory = functools.partial(
                                _translate_partial_text_stream,
                                text_client,
                                followup_text,
                            )
                        _fire_streaming_partial(followup_stream_factory)

                if receive_next not in done:
                    continue

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
                    # Fire-and-forget partial translation. Only one in flight per
                    # turn — subsequent input_text updates while in-flight are
                    # remembered as pending_partial_text and fired immediately
                    # after completion (Q-2'). This stops the polling feedback
                    # loop where blocking awaits starved the receive loop and
                    # exploded API spend, while still always working on the
                    # freshest text without paying for cancelled calls.
                    if (
                        _fast_partial_translation_enabled()
                        and text_client is not None
                        and _should_emit_partial_translation(last_partial_text_en, text_en)
                    ):
                        if partial_task is None:
                            if current_seq == 0:
                                seq += 1
                                current_seq = seq
                            partial_input_snapshot = text_en
                            partial_target_seq = current_seq
                            partial_started_log_at = time.monotonic()
                            # Q-1: incremental when text strictly extends prior
                            # successful translation — only the delta is sent
                            # to the model so output token count stays small
                            # regardless of how much speech has accumulated.
                            use_incremental = bool(
                                last_partial_text_en
                                and partial_text_ko
                                and text_en.startswith(last_partial_text_en)
                                and len(text_en) > len(last_partial_text_en)
                            )
                            if use_incremental:
                                delta_en = text_en[len(last_partial_text_en):]
                                partial_was_incremental = True
                                partial_prev_ko_anchor = partial_text_ko
                                partial_stream_factory = functools.partial(
                                    _translate_partial_delta_stream,
                                    text_client,
                                    last_partial_text_en,
                                    partial_text_ko,
                                    delta_en,
                                )
                            else:
                                partial_was_incremental = False
                                partial_prev_ko_anchor = ""
                                partial_stream_factory = functools.partial(
                                    _translate_partial_text_stream,
                                    text_client,
                                    text_en,
                                )
                            _fire_streaming_partial(partial_stream_factory)
                        else:
                            # Q-2': in-flight partial이 끝날 때까지(혹은 우리가
                            # 아래에서 cancel할 때까지) 최신 텍스트를 기억해뒀다가
                            # 완료 직후 자동으로 follow-up fire.
                            # In-flight가 stale 임계 이상 오래됐고 새 텍스트의
                            # delta가 충분하면 cancel — 어차피 곧 덮어씌워질
                            # 운명이라 기다리는 시간이 낭비됨. 다음 loop iteration
                            # 에서 partial_task가 done(CancelledError)으로
                            # 처리되고 follow-up 분기가 pending_partial_text로
                            # 새 partial을 즉시 발사한다.
                            if partial_translation_cancel_stale > 0:
                                in_flight_elapsed_s = (
                                    time.monotonic() - partial_started_log_at
                                )
                                text_delta_chars = (
                                    len(text_en) - len(partial_input_snapshot)
                                )
                                if (
                                    in_flight_elapsed_s
                                    >= partial_translation_cancel_stale
                                    and text_delta_chars
                                    >= _PARTIAL_TRANSLATION_CANCEL_MIN_DELTA_CHARS
                                ):
                                    partial_task.cancel()
                                    logger.info(
                                        "Gemini partial translation cancelled — "
                                        "fresher text superseded in-flight",
                                        extra={
                                            **trace,
                                            "in_flight_elapsed_ms": round(
                                                in_flight_elapsed_s * 1000
                                            ),
                                            "text_delta_chars": text_delta_chars,
                                        },
                                    )
                            pending_partial_text = text_en
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
                    if partial_task is not None and not partial_task.done():
                        partial_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await partial_task
                    if partial_chunk_get_task is not None:
                        partial_chunk_get_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await partial_chunk_get_task
                        partial_chunk_get_task = None
                    partial_stream_queue = None
                    partial_task = None
                    partial_was_incremental = False
                    partial_prev_ko_anchor = ""
                    pending_partial_text = ""
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
        if partial_task is not None and not partial_task.done():
            partial_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await partial_task
        if partial_chunk_get_task is not None:
            partial_chunk_get_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await partial_chunk_get_task
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


async def _translate_partial_text_stream(
    text_client: Any, text: str
) -> AsyncIterator[Any]:
    """Streaming counterpart of _translate_partial_text. Yields raw response
    chunks (each .text holds the delta from the model); callers accumulate
    cumulative text themselves. The first await happens inside the loop
    because generate_content_stream is an async call that returns the async
    iterator after the initial round trip.
    """
    from google.genai import types
    stream = await text_client.aio.models.generate_content_stream(
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
    async for chunk in stream:
        yield chunk


async def _translate_partial_delta_stream(
    text_client: Any,
    prev_en: str,
    prev_ko: str,
    delta_en: str,
) -> AsyncIterator[Any]:
    """Streaming counterpart of _translate_partial_delta — same incremental
    contract (model outputs Korean delta only), just chunk-by-chunk."""
    from google.genai import types
    stream = await text_client.aio.models.generate_content_stream(
        model=os.environ.get(
            PARTIAL_TRANSLATION_MODEL_ENV,
            DEFAULT_PARTIAL_TRANSLATION_MODEL,
        ),
        contents=(
            "You are extending a Korean meeting subtitle in real time. "
            "Translate ONLY the new English continuation into Korean, so it can be "
            "appended to the existing Korean subtitle. Do not repeat the earlier "
            "Korean. Output Korean only. Preserve common studio terms in English: "
            "layout, retake, render, comp, rig, shot, asset.\n\n"
            f"Earlier English (already translated, context only): {prev_en}\n"
            f"Earlier Korean (your previous output): {prev_ko}\n"
            f"New English continuation to translate: {delta_en}"
        ),
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=160,
        ),
    )
    async for chunk in stream:
        yield chunk


def _is_transient_server_error(exc: BaseException) -> bool:
    """Match Gemini 5xx ServerError so the retry helper can target it without
    sweeping up unrelated exceptions (RuntimeError from fakes, permission
    errors, etc.). Tries the real class first; falls back to a name check so
    a test using a stand-in class named "ServerError" still hits the retry path.
    """
    try:
        from google.genai.errors import ServerError as GenAIServerError
        if isinstance(exc, GenAIServerError):
            return True
    except ImportError:
        pass
    return type(exc).__name__ == "ServerError"


async def _drive_streaming_partial(
    coro_iter_factory: Callable[[], AsyncIterator[Any]],
    queue: asyncio.Queue[str],
    *,
    trace: Mapping[str, Any],
    timeout_s: float,
    backoff_s: float,
    attempts: int = 2,
) -> None:
    """Consume a streaming partial-translation async generator and push each
    cumulative text snapshot to queue as it grows. Completion is signalled by
    this task itself transitioning to done; main loop drains any final queue
    items and inspects task.result() / exception. Transient 5xx ServerError
    triggers one retry (same backoff as the non-streaming retry); cancellation,
    timeout, permanent errors, and unrelated exceptions are NOT retried.
    Total wall-time is bounded by timeout_s — once exceeded, raises
    asyncio.TimeoutError without forcing the inner stream to finish.
    """
    deadline = time.monotonic() + timeout_s
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        accumulated_parts: list[str] = []
        try:
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError(
                    "partial translation deadline exceeded before stream start"
                )
            stream_iter = coro_iter_factory()
            async for chunk in stream_iter:
                if time.monotonic() >= deadline:
                    raise asyncio.TimeoutError(
                        "partial translation deadline exceeded during stream"
                    )
                text = getattr(chunk, "text", "") or ""
                if text:
                    accumulated_parts.append(text)
                    await queue.put("".join(accumulated_parts))
            return
        except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except Exception as error:
            last_error = error
            if (
                attempt >= attempts
                or is_permanent_provider_error(error)
                or not _is_transient_server_error(error)
            ):
                raise
            logger.info(
                "Gemini partial translation stream retrying",
                extra={
                    **trace,
                    "error_type": type(error).__name__,
                    "attempt": attempt,
                    "max_attempts": attempts,
                    "backoff_ms": round(backoff_s * 1000),
                },
            )
            await asyncio.sleep(backoff_s)
    assert last_error is not None
    raise last_error
# === ANCHOR: GEMINI_LIVE_END ===
