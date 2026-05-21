# === ANCHOR: LIVE_SESSION_START ===
"""Runtime orchestration between sidecar audio chunks and an AI provider."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from apps.server.ai.providers import STTProvider, TranslatedUtterance

logger = logging.getLogger(__name__)

OnUtterance = Callable[[TranslatedUtterance], Awaitable[None] | None]
DEFAULT_RECONNECT_DELAYS = (0.5, 1.0, 2.0, 5.0)
# Provider 영구 에러(quota/billing/auth) 시 reconnect 백오프. 짧은 백오프로
# 무한 재시도하면 비용/quota만 더 소모하므로 5분 단위로 늦춘다.
PERMANENT_ERROR_BACKOFF_SECONDS = 300.0
# 영구 에러로 식별할 메시지 부분 문자열 (lowercase 매칭).
_PERMANENT_ERROR_SIGNATURES: tuple[str, ...] = (
    "spending cap",
    "quota",
    "billing",
    "permission denied",
    "invalid api key",
    "invalid_api_key",
    "api key not valid",
)


def is_permanent_provider_error(exc: BaseException) -> bool:
    """provider가 던진 예외가 재시도해도 회복 불가능한 종류인지 판별."""
    text = str(exc).lower()
    return any(sig in text for sig in _PERMANENT_ERROR_SIGNATURES)


# === ANCHOR: LIVE_SESSION_AUDIOLIVESESSION_START ===
class AudioLiveSession:
    """Push audio chunks into a provider and emit translated utterances.

    The sidecar WebSocket stays responsible for auth and binary/control dispatch;
    this class owns only the provider queue and background receive loop.
    """

    def __init__(
        self,
        provider: STTProvider,
        on_utterance: OnUtterance,
        lang_hint: str = "en",
        reconnect_delays: Sequence[float] = DEFAULT_RECONNECT_DELAYS,
    ) -> None:
        self._provider: STTProvider = provider
        self._on_utterance: OnUtterance = on_utterance
        self._lang_hint: str = lang_hint
        self._reconnect_delays: tuple[float, ...] = tuple(reconnect_delays)
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2000)
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False

    # === ANCHOR: LIVE_SESSION_START_START ===
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="yeson-ai-live-session")
    # === ANCHOR: LIVE_SESSION_START_END ===

    # === ANCHOR: LIVE_SESSION_PUSH_AUDIO_START ===
    async def push_audio(self, chunk: bytes) -> None:
        if self._task is None:
            raise RuntimeError("audio live session not started")
        await self._queue.put(chunk)
    # === ANCHOR: LIVE_SESSION_PUSH_AUDIO_END ===

    # === ANCHOR: LIVE_SESSION_STOP_START ===
    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        await self._queue.put(None)
        task = self._task
        self._task = None
        await task
    # === ANCHOR: LIVE_SESSION_STOP_END ===

    # === ANCHOR: LIVE_SESSION__AUDIO_STREAM_START ===
    async def _audio_stream(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk
    # === ANCHOR: LIVE_SESSION__AUDIO_STREAM_END ===
    # === ANCHOR: LIVE_SESSION__RUN_START ===
    async def _run(self) -> None:
        failures = 0
        while not self._stopping:
            permanent = False
            try:
                emitted = await self._consume_provider_stream()
                failures = 0 if emitted else failures + 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                if self._stopping:
                    break
                permanent = is_permanent_provider_error(error)
                if permanent:
                    logger.error(
                        "AI live session provider rejected request (permanent)",
                        extra={
                            "error_type": type(error).__name__,
                            "error_message": str(error)[:200],
                        },
                    )
                else:
                    logger.exception("AI live session provider disconnected")
            if not self._stopping:
                delay = (
                    PERMANENT_ERROR_BACKOFF_SECONDS
                    if permanent
                    else self._reconnect_delay(failures)
                )
                await asyncio.sleep(delay)

    async def _consume_provider_stream(self) -> bool:
        emitted = False
        async for utterance in self._provider.stream(self._audio_stream(), self._lang_hint):
            emitted = True
            result = self._on_utterance(utterance)
            if inspect.isawaitable(result):
                await result
        return emitted

    def _reconnect_delay(self, failures: int) -> float:
        if not self._reconnect_delays:
            return 0.0
        index = min(max(failures, 1) - 1, len(self._reconnect_delays) - 1)
        return self._reconnect_delays[index]
    # === ANCHOR: LIVE_SESSION__RUN_END ===
# === ANCHOR: LIVE_SESSION_AUDIOLIVESESSION_END ===
# === ANCHOR: LIVE_SESSION_END ===
