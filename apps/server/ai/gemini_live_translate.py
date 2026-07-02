# === ANCHOR: GEMINI_LIVE_TRANSLATE_START ===
"""Gemini 3.5 Live Translate provider — continuous speech-to-caption stream.

Unlike ``gemini_live`` (turn-based: ~10s audio segments, transcription arrives
as one batch after each segment closes), ``gemini-3.5-live-translate-preview``
translates continuously WHILE the speaker talks: Korean caption text streams
~1.5-3s behind the speech (measured 2026-07-02). There are no turns and no
utterance boundaries in the model output — just an endless trickle of small
EN (input transcription) and KO (output transcription) text fragments — so
this module's core job is assembling that trickle into seq'd partial/final
``TranslatedUtterance``s for the existing pacer/report/DB pipeline.

The model accepts no system instructions or tools ("pure translation"), so the
prompt glossary cannot steer terminology; known-bad literal renderings are
patched post-hoc via ``glossary.apply_ko_corrections`` instead.

One live session per ``stream()`` call: on any session error the exception
propagates to live_session's reconnect loop, which calls ``stream()`` again —
``provider_segment`` increments per call so AISequenceNormalizer re-offsets.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from apps.server.ai.glossary import apply_ko_corrections
from apps.server.ai.providers import TranslatedUtterance

INPUT_SAMPLE_RATE = 16000
MODEL_ENV = "GEMINI_LIVE_TRANSLATE_MODEL"
TARGET_LANGUAGE_ENV = "GEMINI_LIVE_TRANSLATE_TARGET"
FORCE_FINAL_CHARS_ENV = "GEMINI_LT_FORCE_FINAL_CHARS"
MIN_FINAL_CHARS_ENV = "GEMINI_LT_MIN_FINAL_CHARS"
MAX_UTTERANCE_MS_ENV = "GEMINI_LT_MAX_UTTERANCE_MS"
IDLE_FINAL_MS_ENV = "GEMINI_LT_IDLE_FINAL_MS"
PARTIAL_MIN_DELTA_CHARS_ENV = "GEMINI_LT_PARTIAL_MIN_DELTA_CHARS"

DEFAULT_MODEL = "gemini-3.5-live-translate-preview"
DEFAULT_TARGET_LANGUAGE = "ko"
# A caption line is force-finalized past this length even without sentence
# punctuation, so a long rambling clause cannot grow one line unboundedly.
DEFAULT_FORCE_FINAL_CHARS = 90
# ...and is NOT finalized at a sentence boundary until it reaches this length,
# so short sentences merge into one fuller caption line instead of flashing by
# as one-clause morsels ("감질" feedback, 2026-07-02). This gates only the
# sentence-boundary cut: the force/age caps and the idle flush still finalize
# short text, so a speaker pausing after a short sentence is unaffected. Does
# not add latency — text appears via partials; this only moves the line break.
DEFAULT_MIN_FINAL_CHARS = 45
# ...and past this age, so a slow trickle cannot pin one seq forever. Matches
# the gemini_live hard cap so downstream pacing assumptions carry over.
DEFAULT_MAX_UTTERANCE_MS = 12000
# No new KO text for this long (speaker pause / meeting lull) → finalize what
# we have. Driven by the receive-poll timeout, so resolution is RECEIVE_POLL_S.
DEFAULT_IDLE_FINAL_MS = 2000
# Re-publish the growing partial only every N new chars — keeps DB/bus churn
# near the fragment rate (~2/s) without dropping visible progress.
DEFAULT_PARTIAL_MIN_DELTA_CHARS = 4
RECEIVE_POLL_S = 0.5
logger = logging.getLogger(__name__)

# Sentence boundary inside accumulated KO text: terminal punctuation not
# sandwiched between digits ("1.5" must not split). Korean output from the
# model reliably carries .?!… so ending-form detection is unnecessary.
_SENTENCE_END_RE = re.compile(r"(?<![0-9])[.?!…]|(?<=[0-9])[.?!…](?![0-9])")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _last_sentence_end(text: str) -> int:
    """Index just past the last sentence-terminal punctuation, or -1."""
    last = -1
    for match in _SENTENCE_END_RE.finditer(text):
        last = match.end()
    return last


@dataclass
class _EmitState:
    seq: int = 1
    en_buffer: str = ""
    ko_buffer: str = ""
    emitted_ko_len: int = 0
    started_at: datetime | None = None
    started_monotonic: float | None = None
    last_ko_at: float | None = None


class TranscriptAssembler:
    """Fold continuous EN/KO transcription fragments into utterances.

    ``feed``/``poll`` return the utterances to publish, partials first, at most
    one final per call (a final resets the buffer, so at most one boundary is
    consumed per fragment — fragments are a few words, never multi-sentence
    beyond one boundary in practice; any tail stays buffered for the next seq).
    """

    def __init__(
        self,
        provider_segment: int,
        force_final_chars: int | None = None,
        min_final_chars: int | None = None,
        max_utterance_ms: int | None = None,
        idle_final_ms: int | None = None,
        partial_min_delta_chars: int | None = None,
    ) -> None:
        self._segment = provider_segment
        self._force_final_chars = force_final_chars or _int_env(
            FORCE_FINAL_CHARS_ENV, DEFAULT_FORCE_FINAL_CHARS
        )
        self._min_final_chars = (
            min_final_chars
            if min_final_chars is not None
            else _int_env(MIN_FINAL_CHARS_ENV, DEFAULT_MIN_FINAL_CHARS)
        )
        self._max_utterance_s = (
            max_utterance_ms
            if max_utterance_ms is not None
            else _int_env(MAX_UTTERANCE_MS_ENV, DEFAULT_MAX_UTTERANCE_MS)
        ) / 1000
        self._idle_final_s = (
            idle_final_ms
            if idle_final_ms is not None
            else _int_env(IDLE_FINAL_MS_ENV, DEFAULT_IDLE_FINAL_MS)
        ) / 1000
        self._partial_min_delta = (
            partial_min_delta_chars
            if partial_min_delta_chars is not None
            else _int_env(PARTIAL_MIN_DELTA_CHARS_ENV, DEFAULT_PARTIAL_MIN_DELTA_CHARS)
        )
        self._state = _EmitState()

    def feed(
        self,
        en_text: str | None,
        ko_text: str | None,
        now_monotonic: float | None = None,
    ) -> list[TranslatedUtterance]:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        state = self._state
        if en_text:
            state.en_buffer += en_text
        if ko_text:
            if not state.ko_buffer.strip():
                state.started_at = datetime.now(timezone.utc)
                state.started_monotonic = now
            state.ko_buffer += ko_text
            state.last_ko_at = now
        if not state.ko_buffer.strip():
            return []

        boundary = _last_sentence_end(state.ko_buffer)
        aged = (
            state.started_monotonic is not None
            and now - state.started_monotonic >= self._max_utterance_s
        )
        boundary_len = (
            len(state.ko_buffer[:boundary].strip()) if boundary > 0 else 0
        )
        if boundary_len > 1 and boundary_len >= self._min_final_chars:
            return self._finalize(split_at=boundary)
        if len(state.ko_buffer.strip()) >= self._force_final_chars or aged:
            return self._finalize(split_at=len(state.ko_buffer))
        return self._maybe_partial()

    def poll(self, now_monotonic: float | None = None) -> list[TranslatedUtterance]:
        """Timer tick from the receive loop — applies the idle-finalize rule."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        state = self._state
        if not state.ko_buffer.strip() or state.last_ko_at is None:
            return []
        if now - state.last_ko_at >= self._idle_final_s:
            return self._finalize(split_at=len(state.ko_buffer))
        return []

    def flush(self) -> list[TranslatedUtterance]:
        """Finalize whatever remains (stream ending)."""
        if not self._state.ko_buffer.strip():
            return []
        return self._finalize(split_at=len(self._state.ko_buffer))

    def _maybe_partial(self) -> list[TranslatedUtterance]:
        state = self._state
        if len(state.ko_buffer) - state.emitted_ko_len < self._partial_min_delta:
            return []
        state.emitted_ko_len = len(state.ko_buffer)
        return [self._utterance(state.ko_buffer, state.en_buffer, is_final=False)]

    def _finalize(self, split_at: int) -> list[TranslatedUtterance]:
        state = self._state
        ko_final = state.ko_buffer[:split_at]
        ko_rest = state.ko_buffer[split_at:]
        # EN pairing is approximate (EN fragments lead KO slightly): give this
        # utterance the EN buffer up to ITS last sentence boundary and carry the
        # tail — which usually belongs to the sentence still being spoken —
        # into the next seq.
        en_boundary = _last_sentence_end(state.en_buffer)
        if ko_rest.strip() and en_boundary > 0:
            en_final, en_rest = state.en_buffer[:en_boundary], state.en_buffer[en_boundary:]
        else:
            en_final, en_rest = state.en_buffer, ""
        utterance = self._utterance(ko_final, en_final, is_final=True)
        state.seq += 1
        state.en_buffer = en_rest
        state.ko_buffer = ko_rest
        state.emitted_ko_len = 0
        if ko_rest.strip():
            state.started_at = datetime.now(timezone.utc)
            state.started_monotonic = time.monotonic()
        else:
            state.started_at = None
            state.started_monotonic = None
            state.last_ko_at = None
        return [utterance]

    def _utterance(
        self, ko: str, en: str, *, is_final: bool
    ) -> TranslatedUtterance:
        state = self._state
        return TranslatedUtterance(
            seq=state.seq,
            text_en=en.strip(),
            text_ko=apply_ko_corrections(ko.strip()),
            started_at=state.started_at or datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            is_final=is_final,
            provider_segment=self._segment,
        )


class GeminiLiveTranslateProvider:
    """STT+translation provider backed by Gemini 3.5 Live Translate."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        trace_extra: Mapping[str, object] | None = None,
        client: Any | None = None,
    ) -> None:
        self._api_key: str | None = api_key or os.environ.get("GEMINI_API_KEY")
        self._model: str = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        self._target_language: str = os.environ.get(
            TARGET_LANGUAGE_ENV, DEFAULT_TARGET_LANGUAGE
        )
        self._client = client
        # Cumulative across stream() re-calls (live_session reconnect loop) so
        # AISequenceNormalizer sees each reconnect as a new segment.
        self._segment_index = 0
        self._trace_extra = dict(trace_extra or {})

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        if self._client is None and not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is required for GeminiLiveTranslateProvider"
            )

        from google.genai import types

        client = self._client
        if client is None:
            from google import genai

            client = genai.Client(api_key=self._api_key)

        self._segment_index += 1
        trace = {**self._trace_extra, "gemini_lt_segment": self._segment_index}
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            translation_config=types.TranslationConfig(
                target_language_code=self._target_language
            ),
            input_audio_transcription={},
            output_audio_transcription={},
        )
        assembler = TranscriptAssembler(provider_segment=self._segment_index)
        connect_started_at = time.monotonic()
        logger.info(
            "Gemini Live Translate connect starting",
            extra={**trace, "gemini_model": self._model},
        )
        async with client.aio.live.connect(model=self._model, config=config) as session:
            logger.info(
                "Gemini Live Translate connected",
                extra={
                    **trace,
                    "gemini_model": self._model,
                    "gemini_connect_latency_ms": round(
                        (time.monotonic() - connect_started_at) * 1000
                    ),
                },
            )

            async def send_audio() -> None:
                async for chunk in audio:
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk,
                            mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                        )
                    )
                await session.send_realtime_input(audio_stream_end=True)

            send_task = asyncio.create_task(send_audio())
            first_caption_yielded = False
            receive_iter = session.receive().__aiter__()
            receive_next: asyncio.Task[Any] = asyncio.create_task(
                receive_iter.__anext__()
            )
            drain_deadline: float | None = None
            try:
                while True:
                    done, _pending = await asyncio.wait(
                        {receive_next}, timeout=RECEIVE_POLL_S
                    )
                    utterances: list[TranslatedUtterance] = []
                    if receive_next in done:
                        try:
                            message = receive_next.result()
                        except StopAsyncIteration:
                            # receive() iterators are per-turn in the SDK; the
                            # translate model is turnless in practice, but if
                            # one ever ends, start the next unless the meeting
                            # audio is already over.
                            if send_task.done():
                                break
                            receive_iter = session.receive().__aiter__()
                            receive_next = asyncio.create_task(receive_iter.__anext__())
                            continue
                        receive_next = asyncio.create_task(receive_iter.__anext__())
                        # The Live API warns with goAway before enforcing its
                        # session-duration cap and then aborts with 1008 ("client
                        # failed to close after GoAway", observed 2026-07-02 at
                        # the ~10min mark). Recycle proactively: end this stream
                        # cleanly — buffered text is flushed below and
                        # live_session redials in DEFAULT_RECONNECT_DELAYS[0] —
                        # instead of losing the buffer to the abort.
                        if getattr(message, "go_away", None) is not None:
                            logger.info(
                                "Gemini Live Translate go_away — recycling session",
                                extra={
                                    **trace,
                                    "gemini_time_left": str(
                                        getattr(message.go_away, "time_left", None)
                                    ),
                                },
                            )
                            break
                        server_content = getattr(message, "server_content", None)
                        en_text = _transcription_text(server_content, "input_transcription")
                        ko_text = _transcription_text(server_content, "output_transcription")
                        utterances = assembler.feed(en_text, ko_text)
                    else:
                        utterances = assembler.poll()
                    for utterance in utterances:
                        if not first_caption_yielded:
                            first_caption_yielded = True
                            logger.info(
                                "Gemini Live Translate first caption",
                                extra={
                                    **trace,
                                    "gemini_connect_to_first_subtitle_ms": round(
                                        (time.monotonic() - connect_started_at) * 1000
                                    ),
                                },
                            )
                        yield utterance
                    # Once the meeting audio ends, give the model a short
                    # window to deliver the translation tail, then stop.
                    if send_task.done() and not send_task.cancelled():
                        send_error = send_task.exception()
                        if send_error is not None:
                            raise send_error
                        if drain_deadline is None:
                            drain_deadline = time.monotonic() + 3.0
                        elif utterances:
                            drain_deadline = time.monotonic() + 3.0
                        elif time.monotonic() >= drain_deadline:
                            break
            finally:
                send_task.cancel()
                receive_next.cancel()
                for task in (send_task, receive_next):
                    with contextlib.suppress(BaseException):
                        await task
        for utterance in assembler.flush():
            yield utterance
        logger.info("Gemini Live Translate stream ended", extra=trace)


def _transcription_text(server_content: Any, attr: str) -> str | None:
    transcription = getattr(server_content, attr, None)
    text = getattr(transcription, "text", None)
    return text if isinstance(text, str) and text else None
# === ANCHOR: GEMINI_LIVE_TRANSLATE_END ===
