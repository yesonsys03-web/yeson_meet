# === ANCHOR: LIVE_SESSION_START ===
"""Runtime orchestration between sidecar audio chunks and an AI provider."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from apps.server.ai.providers import STTProvider, TranslatedUtterance

logger = logging.getLogger(__name__)

OnUtterance = Callable[[TranslatedUtterance], Awaitable[None] | None]


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
    ) -> None:
        self._provider: STTProvider = provider
        self._on_utterance: OnUtterance = on_utterance
        self._lang_hint: str = lang_hint
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2000)
        self._task: asyncio.Task[None] | None = None

    # === ANCHOR: LIVE_SESSION_START_START ===
    async def start(self) -> None:
        if self._task is not None:
            return
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
        try:
            async for utterance in self._provider.stream(self._audio_stream(), self._lang_hint):
                result = self._on_utterance(utterance)
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI live session failed")
    # === ANCHOR: LIVE_SESSION__RUN_END ===
# === ANCHOR: LIVE_SESSION_AUDIOLIVESESSION_END ===
# === ANCHOR: LIVE_SESSION_END ===
