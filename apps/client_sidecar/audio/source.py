# === ANCHOR: AUDIO_SOURCE_START ===
"""AudioSource abstract base — yields 640-byte 16kHz mono PCM s16le chunks."""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator


class AudioSource(abc.ABC):
    """Async iterator producing 640-byte 16kHz mono s16le PCM chunks.

    Implementations: SoundDeviceSource (BlackHole/Voicemeeter), NativePipeSource
    (ScreenCaptureKit/WASAPI native helper subprocess).
    """

    @abc.abstractmethod
    def chunks(self) -> AsyncIterator[bytes]:
        """Yield 640-byte PCM chunks. Iterator lifetime tied to source lifecycle."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release underlying resources (stop stream, kill subprocess, etc.)."""
# === ANCHOR: AUDIO_SOURCE_END ===
