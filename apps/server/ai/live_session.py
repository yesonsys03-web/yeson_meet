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
# Invoked once each time the provider is rejected with a permanent error
# (billing/quota/auth) so the caller can surface a status to the operator.
OnPermanentError = Callable[[BaseException], Awaitable[None] | None]
DEFAULT_RECONNECT_DELAYS = (0.5, 1.0, 2.0, 5.0)
# Audio queue capacity (chunks of ~20ms PCM each). 1500 ≈ 30s. Provider가 늦으면
# 큐가 가득 차고, 그 시점부터 가장 오래된 chunk를 drop해서 "최근 30초 audio"만
# 유지한다. 큐를 무제한 또는 큰 값으로 두면 Gemini가 늦을 때 backlog가 누적되어
# 자막이 분 단위로 밀리는 현상이 생긴다 (실측됨).
DEFAULT_AUDIO_QUEUE_MAX_CHUNKS = 1500
# Drop이 N개 누적되면 한 번 WARNING 로그 (스팸 방지).
_DROP_LOG_EVERY = 50
# Provider 영구 에러(quota/billing/auth) 시 reconnect 백오프. 짧은 백오프로
# 무한 재시도하면 비용/quota만 더 소모하므로 5분 단위로 늦춘다.
PERMANENT_ERROR_BACKOFF_SECONDS = 300.0
# 영구 에러로 식별할 메시지 부분 문자열 (lowercase 매칭).
_PERMANENT_ERROR_SIGNATURES: tuple[str, ...] = (
    "spending cap",
    "quota",
    "billing",
    # "prepayment credits are depleted": Gemini Live 1011 close reason. The
    # WebSocket close-reason 123-byte limit truncates the trailing "billing" to
    # "billi", so match the (intact, earlier) credit-depletion phrase instead.
    "prepayment",
    "credit",
    "depleted",
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
        audio_queue_max_chunks: int = DEFAULT_AUDIO_QUEUE_MAX_CHUNKS,
        on_permanent_error: OnPermanentError | None = None,
    ) -> None:
        self._provider: STTProvider = provider
        self._on_utterance: OnUtterance = on_utterance
        self._on_permanent_error: OnPermanentError | None = on_permanent_error
        self._lang_hint: str = lang_hint
        self._reconnect_delays: tuple[float, ...] = tuple(reconnect_delays)
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=max(1, audio_queue_max_chunks)
        )
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False
        self._dropped_chunks: int = 0
        self._next_drop_log_at: int = _DROP_LOG_EVERY

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
        # Lossy push: provider가 따라잡지 못해 큐가 가득 차면 가장 오래된 chunk를
        # drop해서 슬롯을 확보한다. 이렇게 하면 sidecar가 차단되어 audio capture가
        # 멈추거나 backlog가 분 단위로 누적되는 것을 막는다 (자막이 늦더라도
        # 최신 음성 기준 transcribe).
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._dropped_chunks += 1
            if self._dropped_chunks >= self._next_drop_log_at:
                logger.warning(
                    "Audio queue lossy drop — provider can't keep up",
                    extra={"dropped_chunks_total": self._dropped_chunks},
                )
                self._next_drop_log_at += _DROP_LOG_EVERY
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # 극히 드문 race. 이 chunk도 drop.
            self._dropped_chunks += 1
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
                    await self._emit_permanent_error(error)
                else:
                    logger.exception("AI live session provider disconnected")
            if not self._stopping:
                delay = (
                    PERMANENT_ERROR_BACKOFF_SECONDS
                    if permanent
                    else self._reconnect_delay(failures)
                )
                await asyncio.sleep(delay)

    async def _emit_permanent_error(self, error: BaseException) -> None:
        # Best-effort notifier: a failure here must never kill the session loop
        # (it would re-raise out of _run and stop reconnect attempts entirely).
        if self._on_permanent_error is None:
            return
        try:
            result = self._on_permanent_error(error)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("on_permanent_error callback failed")

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
