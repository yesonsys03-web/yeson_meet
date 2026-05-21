# === ANCHOR: TEST_AI_LIVE_SESSION_START ===
"""Slice 3 AI live session orchestration tests."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest

from apps.server.ai.live_session import (
    AudioLiveSession,
    PERMANENT_ERROR_BACKOFF_SECONDS,
    is_permanent_provider_error,
)
from apps.server.ai.providers import TranslatedUtterance


# === ANCHOR: TEST_AI_LIVE_SESSION_FAKEPROVIDER_START ===
class FakeProvider:
    def __init__(self) -> None:
        self.seen_chunks: list[bytes] = []

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        async for chunk in audio:
            self.seen_chunks.append(chunk)
            yield TranslatedUtterance(
                seq=len(self.seen_chunks),
                text_en="hello from provider",
                text_ko="provider에서 안녕",
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                is_final=True,
            )
            return
# === ANCHOR: TEST_AI_LIVE_SESSION_FAKEPROVIDER_END ===


class FlakyProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("provider disconnected")
        async for chunk in audio:
            yield TranslatedUtterance(
                seq=1,
                text_en=f"recovered {len(chunk)} bytes",
                text_ko="복구됨",
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                is_final=True,
            )
            return


# === ANCHOR: TEST_AI_LIVE_SESSION_TEST_AUDIO_LIVE_SESSION_PUSHES_CHUNKS_TO_PROVIDER_AND_EMITS_UTTERANCE_START ===
@pytest.mark.asyncio
async def test_audio_live_session_pushes_chunks_to_provider_and_emits_utterance() -> None:
    provider = FakeProvider()
    emitted: list[TranslatedUtterance] = []

    # === ANCHOR: TEST_AI_LIVE_SESSION_EMIT_START ===
    async def emit(utterance: TranslatedUtterance) -> None:
        emitted.append(utterance)
    # === ANCHOR: TEST_AI_LIVE_SESSION_EMIT_END ===

    session = AudioLiveSession(provider=provider, on_utterance=emit)
    await session.start()
    await session.push_audio(b"\x01" * 640)

    for _ in range(20):
        if emitted:
            break
        await asyncio.sleep(0.01)

    await session.stop()

    assert provider.seen_chunks == [b"\x01" * 640]
    assert len(emitted) == 1
    assert emitted[0].seq == 1
    assert emitted[0].text_en == "hello from provider"
    assert emitted[0].text_ko == "provider에서 안녕"
# === ANCHOR: TEST_AI_LIVE_SESSION_TEST_AUDIO_LIVE_SESSION_PUSHES_CHUNKS_TO_PROVIDER_AND_EMITS_UTTERANCE_END ===


# === ANCHOR: TEST_AI_LIVE_SESSION_TEST_AUDIO_LIVE_SESSION_REJECTS_PUSH_BEFORE_START_START ===
@pytest.mark.asyncio
async def test_audio_live_session_rejects_push_before_start() -> None:
    session = AudioLiveSession(provider=FakeProvider(), on_utterance=lambda _u: None)

    with pytest.raises(RuntimeError, match="not started"):
        await session.push_audio(b"\x00" * 640)
# === ANCHOR: TEST_AI_LIVE_SESSION_TEST_AUDIO_LIVE_SESSION_REJECTS_PUSH_BEFORE_START_END ===


@pytest.mark.asyncio
async def test_audio_live_session_retries_provider_disconnect() -> None:
    provider = FlakyProvider()
    emitted: list[TranslatedUtterance] = []
    session = AudioLiveSession(
        provider=provider,
        on_utterance=emitted.append,
        reconnect_delays=(0.0,),
    )

    await session.start()
    await session.push_audio(b"\x02" * 640)

    for _ in range(20):
        if emitted:
            break
        await asyncio.sleep(0.01)

    await session.stop()

    assert provider.calls >= 2
    assert emitted[0].text_en == "recovered 640 bytes"


@pytest.mark.asyncio
async def test_audio_live_session_drops_oldest_when_queue_full() -> None:
    """Provider가 audio를 소비하지 않아 queue가 가득 차면 가장 오래된 chunk가
    drop되어 sidecar push가 절대 막히지 않아야 한다 (lossy queue 회귀 가드)."""
    release = asyncio.Event()

    class StallingProvider:
        async def stream(
            self,
            audio: AsyncIterator[bytes],
            lang_hint: str,
        ) -> AsyncIterator[TranslatedUtterance]:
            # 호출 즉시 audio를 안 읽고 release를 기다린다 → queue가 가득 참.
            await release.wait()
            async for _ in audio:
                pass
            return
            yield  # pragma: no cover — 본 함수를 async generator로 만들기 위한 마커

    session = AudioLiveSession(
        provider=StallingProvider(),
        on_utterance=lambda _u: None,
        audio_queue_max_chunks=3,
    )
    await session.start()
    # 짧게 양보해서 provider가 release.wait() 진입할 시간을 준다.
    await asyncio.sleep(0)

    for index in range(10):
        await session.push_audio(bytes([index]))

    # queue는 maxsize에 클램프, 나머지는 drop됨.
    assert session._queue.qsize() == 3
    assert session._dropped_chunks == 7

    release.set()
    await session.stop()


def test_is_permanent_provider_error_detects_spending_cap() -> None:
    err = Exception(
        "1011 None. Your project has exceeded its monthly spending cap. "
        "Please go to AI Studio at https://ai.studio/spend"
    )
    assert is_permanent_provider_error(err) is True


def test_is_permanent_provider_error_detects_quota() -> None:
    assert is_permanent_provider_error(Exception("RESOURCE_EXHAUSTED: Quota exceeded")) is True


def test_is_permanent_provider_error_detects_auth() -> None:
    assert is_permanent_provider_error(Exception("API key not valid. Please pass a valid API key.")) is True
    assert is_permanent_provider_error(Exception("Invalid API key supplied")) is True


def test_is_permanent_provider_error_skips_transient() -> None:
    assert is_permanent_provider_error(Exception("1011 None. Internal error encountered.")) is False
    assert is_permanent_provider_error(ConnectionError("provider disconnected")) is False
    assert is_permanent_provider_error(TimeoutError("partial translation timed out")) is False


class PermanentErrorProvider:
    """1번째 호출에서 spending cap 에러를 던지는 fake provider."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        self.calls += 1
        raise Exception(
            "1011 None. Your project has exceeded its monthly spending cap."
        )
        yield  # pragma: no cover — make this an async generator


@pytest.mark.asyncio
async def test_audio_live_session_uses_long_backoff_for_permanent_error(monkeypatch) -> None:
    sleep_calls: list[float] = []
    original_sleep = asyncio.sleep

    async def capture_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        # 실제로는 잠시만 양보해서 테스트가 hang 안 되도록.
        await original_sleep(0)

    from apps.server.ai import live_session as live_session_module

    monkeypatch.setattr(live_session_module.asyncio, "sleep", capture_sleep)

    provider = PermanentErrorProvider()
    session = AudioLiveSession(
        provider=provider,
        on_utterance=lambda u: None,
        reconnect_delays=(0.5,),
    )
    await session.start()
    # provider가 즉시 raise하므로 _run은 곧바로 sleep 진입.
    for _ in range(20):
        if sleep_calls:
            break
        await original_sleep(0.01)
    await session.stop()

    assert sleep_calls, "expected _run to call asyncio.sleep at least once"
    assert sleep_calls[0] == PERMANENT_ERROR_BACKOFF_SECONDS
# === ANCHOR: TEST_AI_LIVE_SESSION_END ===
