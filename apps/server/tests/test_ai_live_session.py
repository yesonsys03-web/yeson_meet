# === ANCHOR: TEST_AI_LIVE_SESSION_START ===
"""Slice 3 AI live session orchestration tests."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest

from apps.server.ai.live_session import AudioLiveSession
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
# === ANCHOR: TEST_AI_LIVE_SESSION_END ===
